from uuid import uuid4
from sqlalchemy import text
from apps.api.auth import Principal
from apps.worker.main import Worker
from contracts.models import CaptureEpisodeRequest,Message
from persistence.repositories import Repository
from model_gateway import Candidate,Policy,OfflineProvider


def capture(engine,ids,content='I prefer tea',key='worker'):
    return Repository(engine).capture_episode(Principal(ids['tenant_a'],ids['actor_a']),
        CaptureEpisodeRequest(session_id=ids['session_a'],subject_id=ids['subject_a'],messages=[Message(role='user',content=content)],idempotency_key=key),'worker-request')

def test_worker_persists_observed_memory_and_is_idempotent(engine,identities):
    result=capture(engine,identities);repo=Repository(engine);worker=Worker(repo)
    assert worker.run_once(identities['tenant_a'])
    assert not worker.run_once(identities['tenant_a'])
    job=repo.job_status(Principal(identities['tenant_a'],identities['actor_a']),result.job.id,'status')
    assert job['state']=='succeeded' and len(job['outcomes'])==1
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM memory_versions WHERE origin='observed'")).scalar_one()==1
    assert repo.job_status(Principal(identities['tenant_b'],identities['actor_b']),result.job.id,'other') is None

def test_expired_lease_is_reclaimed_and_old_worker_fenced(engine,identities):
    capture(engine,identities);repo=Repository(engine)
    old=repo.claim_job(identities['tenant_a'],60,3)
    with engine.begin() as c:c.execute(text("UPDATE formation_jobs SET lease_expires_at=now()-interval '1 second'"))
    new=repo.claim_job(identities['tenant_a'],60,3)
    assert old['lease_token']!=new['lease_token']
    assert not repo.finish_job(old,[Candidate(content='I prefer tea')],Policy())
    assert repo.finish_job(new,[Candidate(content='I prefer tea')],Policy())
    assert not repo.finish_job(new,[Candidate(content='I prefer tea')],Policy())
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_versions')).scalar_one()==1

def test_retry_and_dead_letter_without_memory_writes(engine,identities):
    class Failing(OfflineProvider):
        def extract(self,messages):raise RuntimeError('provider payload must not be logged')
    capture(engine,identities);worker=Worker(Repository(engine),Failing(),max_attempts=2)
    worker.run_once(identities['tenant_a'])
    with engine.begin() as c:
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='retryable_failure'
        c.execute(text('UPDATE formation_jobs SET available_at=now()'))
    worker.run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='dead_letter'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert c.execute(text('SELECT error_code FROM formation_jobs')).scalar_one()=='RuntimeError'

def test_sensitive_and_unhelpful_candidates_are_rejected(engine,identities):
    capture(engine,identities,'My password=extremely-private')
    Worker(Repository(engine)).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='rejected'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert 'extremely-private' not in str(c.execute(text('SELECT outcomes FROM formation_jobs')).scalar_one())

def test_final_attempt_crash_becomes_dead_letter(engine,identities):
    capture(engine,identities);repo=Repository(engine);repo.claim_job(identities['tenant_a'],60,1)
    with engine.begin() as c:c.execute(text("UPDATE formation_jobs SET lease_expires_at=now()-interval '1 second'"))
    assert repo.claim_job(identities['tenant_a'],60,1) is None
    with engine.connect() as c:assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='dead_letter'


def test_candidate_transaction_rolls_back_and_replays(engine,identities):
    class Two(OfflineProvider):
        def extract(self,messages):return [Candidate(content='I prefer tea'),Candidate(content='My hobby is hiking')]
    capture(engine,identities);repo=Repository(engine);worker=Worker(repo,Two())
    repo.after_candidate_insert=lambda index: (_ for _ in ()).throw(RuntimeError()) if index==1 else None
    worker.run_once(identities['tenant_a'])
    with engine.begin() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        c.execute(text('UPDATE formation_jobs SET available_at=now()'))
    repo.after_candidate_insert=lambda index:None
    worker.run_once(identities['tenant_a'])
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_versions')).scalar_one()==2


def test_consent_revocation_during_extraction(engine,identities):
    class Revoke(OfflineProvider):
        def extract(self,messages):
            with engine.begin() as c:c.execute(text('UPDATE subjects SET memory_consent=false'))
            return [Candidate(content='I prefer tea')]
    capture(engine,identities);Worker(Repository(engine),Revoke()).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='rejected'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert c.execute(text('SELECT outcomes FROM formation_jobs')).scalar_one()[0]['outcome']=='consent_required'


def test_explicit_write_rejects_sensitive_content(engine,identities):
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    from test_foundation import DATABASE_URL,headers,episode_body
    client=TestClient(create_app(DATABASE_URL))
    source=client.post('/v1/episodes',json=episode_body(identities),headers=headers(identities)).json()['episode']['id']
    body={'subject_id':str(identities['subject_a']),'kind':'profile','content':'password=do-not-persist','confidence':1,'source':{'episode_id':source}}
    response=client.post('/v1/memories',json=body,headers=headers(identities))
    assert response.status_code==422 and response.json()['detail']['code']=='sensitive_rejected'
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert 'do-not-persist' not in str(c.execute(text('SELECT * FROM audit_events')).all())


def test_correlated_trace_and_untrusted_request_id_privacy(engine,identities,caplog):
    import logging
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    from test_foundation import DATABASE_URL,headers,episode_body
    exporter=InMemorySpanExporter();provider=TracerProvider();provider.add_span_processor(SimpleSpanProcessor(exporter));trace.set_tracer_provider(provider)
    caplog.set_level(logging.INFO,logger='agent_memory')
    client=TestClient(create_app(DATABASE_URL));h=headers(identities);h['X-Request-ID']='password=never-log-this';h['baggage']='secret=password-value';h['tracestate']='vendor=password-value'
    response=client.post('/v1/episodes',json=episode_body(identities),headers=h)
    assert response.status_code==202
    assert response.headers['X-Request-ID']!='password=never-log-this'
    with engine.connect() as c:
        saved=c.execute(text('SELECT trace_context FROM formation_jobs')).scalar_one()
        assert set(saved)=={'traceparent'}
        assert 'password-value' not in str(saved)
    Worker(Repository(engine)).run_once(identities['tenant_a'])
    spans=exporter.get_finished_spans()
    assert {'memory.api','memory.worker','memory.extract','memory.persist'} <= {s.name for s in spans}
    assert len({s.context.trace_id for s in spans})==1
    assert 'never-log-this' not in caplog.text+str([dict(s.attributes) for s in spans])
    assert 'I prefer tea' not in caplog.text+str([dict(s.attributes) for s in spans])



def test_revoked_consent_never_calls_provider(engine,identities):
    class Forbidden(OfflineProvider):
        def extract(self,messages):raise AssertionError('must not call provider')
    capture(engine,identities)
    with engine.begin() as c:c.execute(text('UPDATE subjects SET memory_consent=false'))
    Worker(Repository(engine),Forbidden()).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='rejected'
        assert c.execute(text('SELECT error_code FROM formation_jobs')).scalar_one() is None
