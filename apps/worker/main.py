import argparse
import os
import time
from uuid import UUID
from deploy.readiness import enforce_environment
from sqlalchemy import create_engine
from opentelemetry import context, propagate
from persistence.repositories import Repository
from model_gateway import Candidate, Policy, extraction_provider_from_env, verifier_from_env
from model_gateway.live import ProviderQuotaError
from observability import operation, extract_context

class Worker:
    def __init__(self,repository,provider=None,policy=None,max_attempts=3,lease_seconds=60,verifier=None):
        self.repository=repository;self.provider=provider or extraction_provider_from_env();self.policy=policy or Policy()
        self.verifier=verifier if verifier is not None else verifier_from_env()
        self.max_attempts=max_attempts;self.lease_seconds=lease_seconds
    def run_once(self,tenant_id):
        job=self.repository.claim_job(tenant_id,self.lease_seconds,self.max_attempts)
        if not job: return False
        token=context.attach(extract_context(job['trace_context']))
        try:
            with operation('memory.worker',tenant_id=tenant_id,job_id=job['id'],request_id=job['request_id']):
                try:
                    if not self.repository.can_form_job(job):
                        self.repository.finish_job(job,[],self.policy,[{'outcome':'consent_or_permission_required'}])
                        return True
                    with operation('memory.extract',provider=self.provider.name,job_id=job['id'],request_id=job['request_id']) as span:
                        safe=[];rejections=[]
                        for index,message in enumerate(job['content']):
                            if self.policy.reason(Candidate(content=message['content']))=='sensitive_rejected':
                                rejections.append({'index':index,'stage':'input_filter','outcome':'input_sensitive_rejected'})
                            else:safe.append(message)
                        candidates=self.provider.extract(safe)
                        span.set_attribute('evaluation.version',self.policy.version)
                        span.set_attribute('model.cost_state','unknown' if self.provider.name!='offline-development-v1' else 'offline_no_cost')
                        for key,value in getattr(self.provider,'usage',{}).items():
                            if key in {'prompt_tokens','completion_tokens','total_tokens'}:span.set_attribute('model.'+key,int(value))
                    eligible=[]
                    for index,candidate in enumerate(candidates):
                        reason=self.policy.reason(candidate)
                        if reason:rejections.append({'index':index,'stage':'candidate_filter','outcome':reason})
                        else:eligible.append(candidate)
                    candidates=eligible
                    if self.verifier and candidates:
                        if not self.repository.can_form_job(job):
                            self.repository.finish_job(job,[],self.policy,[{'outcome':'consent_or_permission_required'}]);return True
                        with operation('memory.verify',provider=self.verifier.name,job_id=job['id'],request_id=job['request_id']) as span:
                            candidates,outcomes=self.verifier.verify(safe,candidates);rejections.extend(outcomes)
                            span.set_attribute('evaluation.version',self.verifier.version)
                            span.set_attribute('model.cost_state','unknown')
                            for key,value in self.verifier.usage.items():span.set_attribute('model.'+key,value)
                    self.repository.finish_job(job,candidates,self.policy,rejections)
                except ProviderQuotaError:
                    self.repository.fail_job(job,'free_quota_unavailable',1)
                except Exception as error:
                    self.repository.fail_job(job,type(error).__name__,self.max_attempts)
        finally: context.detach(token)
        return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tenant',required=True,type=UUID)
    parser.add_argument('--once',action='store_true');args=parser.parse_args()
    enforce_environment(os.environ['DATABASE_URL'])
    engine=create_engine(os.environ['DATABASE_URL']);worker=Worker(Repository(engine))
    try:
        while True:
            worked=worker.run_once(args.tenant)
            if args.once: break
            if not worked: time.sleep(1)
    finally: engine.dispose()

if __name__=='__main__': main()
