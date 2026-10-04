from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.auth import Principal
from apps.worker.main import Worker
from contracts.models import ForgetMemoryRequest,AddMemoryRequest,SourceInput,VersionConflict
from model_gateway import Candidate,OfflineProvider
from persistence.repositories import Repository
from test_worker import capture
from test_foundation import headers

class Extract(OfflineProvider):
    def extract(self,messages):
        return [Candidate(content='I prefer tea'),Candidate(content='My hobby is hiking'),Candidate(content='My diagnosis is SYNTHETIC'),Candidate(content='API key: SYNTHETIC')]

def queue(engine,ids):
    captured=capture(engine,ids);repo=Repository(engine)
    with engine.begin() as c:c.execute(text('UPDATE actor_subject_grants SET can_review_memory=true'))
    class Forbidden:
        def verify(self,*args):raise AssertionError('supervised mode must not call Jev')
    assert Worker(repo,Extract(),verifier=Forbidden(),formation_mode='supervised').run_once(ids['tenant_a'])
    principal=Principal(ids['tenant_a'],ids['actor_a'])
    rows=repo.list_suggestions(principal,ids['subject_a'],'list')
    assert len(rows)==2
    return repo,principal,rows,captured

def test_supervised_api_lifecycle_and_repeated_decisions(engine,identities):
    repo,principal,rows,captured=queue(engine,identities)
    assert repo.list_memories(principal,identities['subject_a'],'list')[0]==[]
    assert repo.job_status(principal,captured.job.id,'job')['state']=='awaiting_review'
    with TestClient(create_app(os.environ['TEST_DATABASE_URL'])) as client:
        h=headers(identities);a=str(rows[0]['id']);b=str(rows[1]['id'])
        data=client.get('/v1/suggestions',params={'subject_id':str(identities['subject_a'])},headers=h).json()
        assert data['items'][0]['human_approval_required'] and not data['items'][0]['scores_calibrated']
        assert client.get(f'/v1/suggestions/{a}/sources',headers=h).json()['episodes'][0]['id']==str(captured.episode.id)
        assert client.post(f'/v1/suggestions/{a}/approve',json={'confirm':False},headers=h).status_code==422
        response=client.post(f'/v1/suggestions/{a}/approve',json={'confirm':True},headers=h)
        assert response.status_code==200,response.text
        approved=response.json();again=client.post(f'/v1/suggestions/{a}/approve',json={'confirm':True},headers=h).json()
        assert again['memory_id']==approved['memory_id']
        assert client.post(f'/v1/suggestions/{a}/reject',json={'confirm':True},headers=h).status_code==409
        assert client.post(f'/v1/suggestions/{b}/reject',json={'confirm':True},headers=h).status_code==200
        assert client.post(f'/v1/suggestions/{b}/reject',json={'confirm':True},headers=h).status_code==200
        memory=client.get('/v1/memories/'+approved['memory_id'],headers=h).json()['memory']
        assert memory['origin']=='explicit' and memory['actor_id']==str(principal.actor_id)
        assert memory['source_episode_ids']==[str(captured.episode.id)]
        assert client.post('/v1/memories/'+approved['memory_id']+'/forget',json={'confirm':True,'expected_version_id':memory['version_id']},headers=h).status_code==202
        repo.erase_next(principal.tenant_id)
        assert client.get('/v1/memories/'+approved['memory_id'],headers=h).status_code==404
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==1
        assert c.execute(text('SELECT count(*) FROM memory_suggestions WHERE content IS NOT NULL')).scalar_one()==0

def test_permissions_expiry_and_payload_cleanup(engine,identities):
    repo,p,rows,_=queue(engine,identities)
    with pytest.raises(PermissionError):repo.list_suggestions(Principal(identities['tenant_b'],identities['actor_b']),identities['subject_a'],'denied')
    ungranted=uuid4()
    with engine.begin() as c:c.execute(text('INSERT INTO actors(id,tenant_id) VALUES (:id,:tenant)'),{'id':ungranted,'tenant':p.tenant_id})
    with pytest.raises(PermissionError):repo.decide_suggestion(Principal(p.tenant_id,ungranted),rows[0]['id'],'approve','denied')
    for column in ['can_review_memory','can_inspect_sources']:
        with engine.begin() as c:c.execute(text(f'UPDATE actor_subject_grants SET {column}=false'))
        with pytest.raises(PermissionError):repo.decide_suggestion(p,rows[0]['id'],'approve','denied')
        with engine.begin() as c:c.execute(text(f'UPDATE actor_subject_grants SET {column}=true'))
    with engine.begin() as c:c.execute(text("UPDATE memory_suggestions SET expires_at=clock_timestamp()-interval '1 second'"))
    with pytest.raises(VersionConflict):repo.decide_suggestion(p,rows[0]['id'],'approve','expired')
    with pytest.raises(PermissionError):repo.suggestion_sources(p,rows[0]['id'],'expired')
    assert repo.list_suggestions(p,identities['subject_a'],'list')==[]
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM memory_suggestions WHERE state='expired' AND content IS NULL")).scalar_one()==2
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='rejected'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0

def test_forget_invalidates_pending_suggestions_before_erasure(engine,identities):
    repo,p,rows,captured=queue(engine,identities)
    memory=repo.add_memory(p,AddMemoryRequest(subject_id=identities['subject_a'],kind='profile',content='I prefer tea',confidence=1,source=SourceInput(episode_id=captured.episode.id)),'add')
    repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'forget')
    for row in rows:
        with pytest.raises(VersionConflict):repo.decide_suggestion(p,row['id'],'approve','late')
    assert repo.list_suggestions(p,identities['subject_a'],'list')==[]
    repo.erase_next(p.tenant_id)
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_suggestions WHERE content IS NOT NULL')).scalar_one()==0

def test_concurrent_approval_exactly_once(engine,identities):
    repo,p,rows,_=queue(engine,identities)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:repo.decide_suggestion(p,rows[0]['id'],'approve',str(n)),range(2)))
    assert results[0]['memory_id']==results[1]['memory_id']
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==1

def test_review_sensitive_source_filtered_and_consent_revocation(engine,identities):
    repo,p,rows,captured=queue(engine,identities)
    with engine.begin() as c:c.execute(text('UPDATE subjects SET memory_consent=false'))
    with pytest.raises(PermissionError):repo.decide_suggestion(p,rows[0]['id'],'approve','revoked')
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert 'SYNTHETIC' not in str(c.execute(text('SELECT * FROM memory_suggestions')).all())

def test_stale_worker_races_erasure_without_resurrection(engine,identities):
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a']);captured=capture(engine,identities)
    memory=repo.add_memory(p,AddMemoryRequest(subject_id=identities['subject_a'],kind='profile',content='I prefer tea',confidence=1,source=SourceInput(episode_id=captured.episode.id)),'add')
    stale=repo.claim_job(p.tenant_id,60,3)
    repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'forget')
    from model_gateway import Policy
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(repo.erase_next,p.tenant_id),pool.submit(repo.finish_job,stale,[Candidate(content='I prefer tea')],Policy(),None,True)]
        for future in futures:future.result(timeout=5)
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM memory_suggestions WHERE state='pending'")).scalar_one()==0
        assert c.execute(text("SELECT count(*) FROM memories WHERE lifecycle_state='active'")).scalar_one()==0
        assert c.execute(text('SELECT erased_at FROM episodes')).scalar_one() is not None


def test_default_review_denied_and_audited(engine,identities):
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    with pytest.raises(PermissionError):repo.list_suggestions(p,identities['subject_a'],'review-denial')
    with engine.connect() as c:
        assert c.execute(text("SELECT outcome FROM audit_events WHERE request_id='review-denial'")).scalar_one()=='denied'

def test_sensitive_source_never_reaches_extractor_or_review(engine,identities):
    from contracts.models import CaptureEpisodeRequest,Message
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    captured=repo.capture_episode(p,CaptureEpisodeRequest(session_id=identities['session_a'],subject_id=identities['subject_a'],messages=[Message(role='user',content='I prefer tea'),Message(role='user',content='My medical diagnosis is SYNTHETIC'),Message(role='user',content='My email address is synthetic@example.test')],idempotency_key='sensitive-review'),'capture')
    class AssertFiltered(OfflineProvider):
        def extract(self,messages):
            assert len(messages)==1 and messages[0]['content']=='I prefer tea'
            return [Candidate(content='I prefer tea')]
    with engine.begin() as c:c.execute(text('UPDATE actor_subject_grants SET can_review_memory=true'))
    Worker(repo,AssertFiltered(),formation_mode='supervised').run_once(p.tenant_id)
    suggestion=repo.list_suggestions(p,identities['subject_a'],'list')[0]
    evidence=repo.suggestion_sources(p,suggestion['id'],'sources')
    assert evidence['episodes'][0]['messages']==[{'role':'user','content':'I prefer tea'}]
    with engine.connect() as c:
        lifetime=c.execute(text('SELECT expires_at-created_at FROM memory_suggestions')).scalar_one()
        assert abs(lifetime.total_seconds()-7*86400)<1

def test_supervised_transaction_rollback_and_stale_lease(engine,identities):
    from model_gateway import Policy
    repo=Repository(engine);captured=capture(engine,identities);job=repo.claim_job(identities['tenant_a'],60,3)
    repo.after_candidate_insert=lambda index:(_ for _ in ()).throw(RuntimeError())
    with pytest.raises(RuntimeError):repo.finish_job(job,[Candidate(content='I prefer tea')],Policy(),supervised=True)
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_suggestions')).scalar_one()==0
    with engine.begin() as c:c.execute(text("UPDATE formation_jobs SET lease_expires_at=now()-interval '1 second'"))
    current=repo.claim_job(identities['tenant_a'],60,3)
    repo.after_candidate_insert=lambda index:None
    assert not repo.finish_job(job,[Candidate(content='I prefer tea')],Policy(),supervised=True)
    assert repo.finish_job(current,[Candidate(content='I prefer tea')],Policy(),supervised=True)
    assert not repo.finish_job(current,[Candidate(content='I prefer tea')],Policy(),supervised=True)
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_suggestions')).scalar_one()==1

def test_expiry_during_embedding_rolls_back_approval(engine,identities):
    import time
    repo,p,rows,_=queue(engine,identities)
    with engine.begin() as c:c.execute(text("UPDATE memory_suggestions SET expires_at=clock_timestamp()+interval '250 milliseconds' WHERE id=:id"),{'id':rows[0]['id']})
    class Delayed(OfflineProvider):
        def embed(self,content):
            time.sleep(.35)
            return super().embed(content)
    repo.provider=Delayed()
    with pytest.raises(VersionConflict):repo.decide_suggestion(p,rows[0]['id'],'approve','late-embedding')
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert c.execute(text('SELECT count(*) FROM memory_indexes')).scalar_one()==0
    repo.expire_suggestions(p.tenant_id)
    with engine.connect() as c:assert c.execute(text('SELECT state FROM memory_suggestions WHERE id=:id'),{'id':rows[0]['id']}).scalar_one()=='expired'
