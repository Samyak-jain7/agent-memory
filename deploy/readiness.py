"""Fail closed until production approvals AND deployment evidence exist."""
import json,math
from pathlib import Path
from urllib.parse import urlparse
REQUIRED=['latency_p95_ms','throughput_rps','availability','retention_seconds','erasure_seconds','backup_retention_seconds','extraction_precision','retrieval_relevance','citation_correctness','correction_rate']

def validate(config):
    errors=[]
    if config.get('mode')!='production':errors.append('production configuration required')
    approval=config.get('approval',{})
    if not all(isinstance(approval.get(k),str) and approval[k].strip() for k in ['approved_by','approved_at','evidence']):errors.append('human approval evidence missing')
    targets=config.get('targets',{});observed=config.get('observed',{})
    for name in REQUIRED:
        target=targets.get(name);actual=observed.get(name)
        if isinstance(target,bool) or not isinstance(target,(int,float)) or not math.isfinite(target) or target<0:errors.append('approved target missing or invalid: '+name);continue
        if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or actual<0:errors.append('measured value missing or invalid: '+name);continue
        minimum=name in {'throughput_rps','availability','extraction_precision','retrieval_relevance','citation_correctness'}
        if (minimum and actual<target) or (not minimum and actual>target):errors.append('target unmet: '+name)
        if name in {'latency_p95_ms','throughput_rps','availability','erasure_seconds'} and target<=0:errors.append('positive target required: '+name)
        if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness','correction_rate'} and (target>1 or actual>1):errors.append('fraction exceeds one: '+name)
    parsed=urlparse(config.get('public_api_url',''))
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:errors.append('public HTTPS evidence missing')
    if config.get('database_sslmode')!='verify-full':errors.append('verified database TLS required')
    for key in ['storage_encryption_verified','backup_encryption_verified','restore_verified','live_provider_evaluated','telemetry_export_verified']:
        if config.get(key) is not True:errors.append('deployment evidence missing: '+key)
    for key in ['storage_encryption_evidence','backup_encryption_evidence','provider_data_policy','retention_policy','erasure_policy','backup_expiry_policy','deployment_evidence']:
        if not isinstance(config.get(key),str) or not config[key].strip():errors.append('approval/evidence missing: '+key)
    return errors

def enforce_environment(database_url):
    import os
    from sqlalchemy import create_engine,text
    from sqlalchemy.engine import make_url
    if os.environ.get('MEMORY_ENV','development')!='production':return
    path=os.environ.get('PRODUCTION_READINESS_FILE')
    if not path:raise RuntimeError('Production readiness approval file required')
    config=json.loads(Path(path).read_text());errors=validate(config)
    if make_url(database_url).query.get('sslmode')!='verify-full':errors.append('Runtime database URL requires verify-full TLS')
    if os.environ.get('MODEL_PROVIDER','offline')=='offline':errors.append('Live evaluated provider required')
    if len(os.environ.get('MEMORY_AUTH_SECRET',''))<32:errors.append('Production signing secret must be at least 32 characters')
    if os.environ.get('MEMORY_TELEMETRY')!='console':errors.append('Reviewed telemetry configuration required')
    try:
        if int(os.environ.get('ERASURE_RETENTION_SECONDS','-1'))!=config.get('observed',{}).get('retention_seconds'):errors.append('Runtime retention does not match verified policy')
    except ValueError:errors.append('Invalid runtime retention')
    if errors:raise RuntimeError('Production readiness blocked: '+'; '.join(errors))
    engine=create_engine(database_url)
    try:
        with engine.connect() as c:
            if c.execute(text('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')).scalar_one_or_none() is not True:raise RuntimeError('Database TLS is not active')
            if c.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():raise RuntimeError('Runtime role must not bypass RLS')
    finally:engine.dispose()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('config',type=Path);args=parser.parse_args()
    errors=validate(json.loads(args.config.read_text()))
    print(json.dumps({'production_ready':not errors,'blockers':errors},indent=2));raise SystemExit(bool(errors))
