"""Versioned deterministic regression evaluation; no paid model calls."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from apps.api.auth import Principal
from apps.worker.main import Worker
from persistence.repositories import Repository
from contracts.models import CaptureEpisodeRequest,Message,ContextRequest
from memory_domain.context import ContextComposer
from scripts.database import migrate,bootstrap

def evaluate():
    data=json.loads((Path(__file__).parent/'golden-v1.json').read_text());url=os.environ.get('TEST_DATABASE_URL','')
    if not url or not (make_url(url).database or '').startswith('memory_test'):raise RuntimeError('Disposable memory_test database required')
    engine=create_engine(url);migrate(engine);repo=Repository(engine);correct=predicted=relevant=cited=positive=0
    try:
        for case in data['cases']:
            ids=bootstrap(engine,consent=True);principal=Principal(ids['tenant'],ids['actor']);composer=ContextComposer(repo)
            try:
                result=repo.capture_episode(principal,CaptureEpisodeRequest(subject_id=ids['subject'],session_id=ids['session'],messages=[Message(role='user',content=case['text'])],idempotency_key=str(uuid4())),str(uuid4()))
                Worker(repo).run_once(ids['tenant']);items=repo.list_memories(principal,ids['subject'],str(uuid4()))[0];predicted+=len(items)
                correct+=sum(m.content==case['text'] and case['expected'] for m in items)
                if case['expected']:
                    positive+=1;found=repo.search(principal,ids['subject'],case['query'],str(uuid4()));relevant+=bool(found and found[0].content==case['text'])
                    context=composer.build(principal,ContextRequest(subject_id=ids['subject'],query=case['query']),str(uuid4()))
                    cited+=bool(found and str(found[0].id) in context.citations and str(result.episode.id) in [str(v) for v in context.citations[str(found[0].id)]])
            finally:composer.close()
        metrics={'extraction_precision':correct/max(predicted,1),'retrieval_relevance':relevant/max(positive,1),'citation_correctness':cited/max(positive,1),'correction_rate':(predicted-correct)/max(predicted,1)}
        passed=all(value<=data['development_thresholds'][key] if key=='correction_rate' else value>=data['development_thresholds'][key] for key,value in metrics.items())
        report={'version':data['version'],'scope':data['scope'],'model':'offline-development-v1','metrics':metrics,'development_passed':passed,'production_approved':False}
        print(json.dumps(report,indent=2));return passed
    finally:engine.dispose()

if __name__=='__main__':raise SystemExit(not evaluate())
