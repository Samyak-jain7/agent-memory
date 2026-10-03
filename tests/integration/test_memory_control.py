from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import text
from apps.api.auth import Principal
from contracts.models import CorrectMemoryRequest,SourceInput,ForgetMemoryRequest,VersionConflict,ContextRequest
from persistence.repositories import Repository
from memory_domain.context import ContextComposer
from test_retrieval import make


def test_correction_preserves_history_and_one_current_version(engine,identities):
    memory=make(engine,identities,'I prefer tea');repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    changed=repo.correct(p,memory.id,CorrectMemoryRequest(expected_version_id=memory.version_id,content='I prefer coffee',confidence=1,source=SourceInput(episode_id=memory.source_episode_ids[0])),'correction')
    history=repo.history(p,memory.id,'history')['versions']
    assert len(history)==2 and history[0]['content']=='I prefer tea'
    assert history[0]['valid_to']==history[1]['valid_from']
    assert history[1]['supersedes_version_id']==history[0]['id']==memory.version_id
    assert changed.version_id==history[1]['id'] and changed.content=='I prefer coffee'
    assert repo.sources(p,memory.id,'sources')['episodes'][0]['id']==memory.source_episode_ids[0]


def test_concurrent_corrections_have_one_winner(engine,identities):
    memory=make(engine,identities);barrier=Barrier(2);p=Principal(identities['tenant_a'],identities['actor_a'])
    def change(content):
        barrier.wait()
        try:
            Repository(engine).correct(p,memory.id,CorrectMemoryRequest(expected_version_id=memory.version_id,content=content,confidence=1,source=SourceInput(episode_id=memory.source_episode_ids[0])),'race');return 'ok'
        except VersionConflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(change,['I prefer coffee','I prefer cocoa']))
    assert sorted(results)==['conflict','ok']
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memory_versions WHERE valid_to IS NULL')).scalar_one()==1


def test_forget_immediately_excludes_all_read_paths_and_erases_payloads(engine,identities):
    memory=make(engine,identities,'My name is private-person','core');repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    job=repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'forget')
    assert job['state']=='pending' and repo.get_memory(p,memory.id,'get') is None
    assert repo.list_memories(p,identities['subject_a'],'list')[0]==[]
    assert repo.search(p,identities['subject_a'],'private-person','search')==[]
    assert repo.profile(p,identities['subject_a'],'profile')==[]
    composer=ContextComposer(repo)
    try:assert composer.build(p,ContextRequest(subject_id=identities['subject_a'],query='private-person'),'ctx').text==''
    finally:composer.close()
    assert repo.history(p,memory.id,'history')['versions'][0]['content'] is None
    with pytest.raises(PermissionError):repo.sources(p,memory.id,'sources')
    assert repo.erase_next(identities['tenant_a'])
    assert repo.erasure_status(p,memory.id,'status')['state']=='completed'
    with engine.connect() as c:
        assert c.execute(text('SELECT content FROM memory_versions')).scalar_one()=='[erased]'
        assert c.execute(text('SELECT content FROM episodes')).scalar_one()==[]
        assert c.execute(text('SELECT count(*) FROM memory_indexes')).scalar_one()==0
        assert c.execute(text('SELECT count(*) FROM memory_sources')).scalar_one()==0
        assert c.execute(text('SELECT state FROM formation_jobs')).scalar_one()=='rejected'


def test_shared_episode_is_retained_for_other_active_memory(engine,identities):
    memory=make(engine,identities);repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    from contracts.models import AddMemoryRequest
    other=repo.add_memory(p,AddMemoryRequest(subject_id=identities['subject_a'],kind='preference',content='I prefer hiking',confidence=1,source=SourceInput(episode_id=memory.source_episode_ids[0])),'other')
    repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'forget');repo.erase_next(identities['tenant_a'])
    assert repo.erasure_status(p,memory.id,'status')['retained_shared_episodes']==1
    assert repo.get_memory(p,other.id,'get').content=='I prefer hiking'
    with engine.connect() as c:assert c.execute(text('SELECT erased_at FROM episodes')).scalar_one() is None


def test_source_inspection_requires_separate_grant(engine,identities):
    memory=make(engine,identities);repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    with engine.begin() as c:c.execute(text('UPDATE actor_subject_grants SET can_inspect_sources=false'))
    with pytest.raises(PermissionError):repo.sources(p,memory.id,'denied')
    with engine.connect() as c:assert c.execute(text("SELECT outcome FROM audit_events WHERE action='memory.sources'")).scalar_one()=='denied'


def test_cross_tenant_control_paths_never_disclose_memory(engine,identities):
    memory=make(engine,identities);repo=Repository(engine);p=Principal(identities['tenant_b'],identities['actor_b'])
    with pytest.raises(PermissionError):repo.history(p,memory.id,'denied')
    with pytest.raises(PermissionError):repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'denied')
    with pytest.raises(PermissionError):repo.sources(p,memory.id,'denied')
    with pytest.raises(Exception,match='suppressed memory required'):
        with engine.begin() as c:
            repo._scope(c,identities['tenant_b']);c.execute(text('SELECT erase_suppressed_memory(:tenant,:memory,:actor,:request)'),{'tenant':identities['tenant_b'],'memory':memory.id,'actor':p.actor_id,'request':'denied'})



def test_erasure_blocks_revoked_actor_and_continues_other_work(engine,identities):
    first=make(engine,identities,'I prefer tea');second=make(engine,identities,'I prefer coffee')
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a']);other_actor=uuid4()
    with engine.begin() as c:
        c.execute(text('INSERT INTO actors(id,tenant_id) VALUES (:actor,:tenant)'),{'actor':other_actor,'tenant':p.tenant_id})
        c.execute(text('INSERT INTO actor_subject_grants(tenant_id,actor_id,subject_id) VALUES (:tenant,:actor,:subject)'),{'actor':other_actor,'tenant':p.tenant_id,'subject':identities['subject_a']})
    other=Principal(p.tenant_id,other_actor)
    repo.forget(p,first.id,ForgetMemoryRequest(confirm=True,expected_version_id=first.version_id),'first-forget')
    repo.forget(other,second.id,ForgetMemoryRequest(confirm=True,expected_version_id=second.version_id),'second-forget')
    with engine.begin() as c:c.execute(text('UPDATE actors SET active=false WHERE id=:actor'),{'actor':p.actor_id})
    assert repo.erase_next(p.tenant_id)
    status=repo.erasure_status(other,first.id,'status')
    assert status['state']=='blocked' and status['error_code']=='authorization_revoked'
    assert repo.erase_next(p.tenant_id)
    assert repo.erasure_status(other,second.id,'status')['state']=='completed'
    retried=repo.retry_erasure(other,first.id,ForgetMemoryRequest(confirm=True,expected_version_id=first.version_id),'explicit-retry')
    assert retried['actor_id']==other.actor_id and retried['state']=='pending'
    assert repo.erase_next(p.tenant_id)
    assert repo.erasure_status(other,first.id,'status')['state']=='completed'
    with engine.connect() as c:
        assert c.execute(text("SELECT outcome FROM audit_events WHERE action='memory.erasure_retry'")).scalar_one()=='explicitly_reauthorized'


def test_erasure_fences_stale_formation_worker(engine,identities):
    memory=make(engine,identities);repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    stale=repo.claim_job(p.tenant_id,60,3)
    repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'forget')
    repo.erase_next(p.tenant_id)
    from model_gateway import Candidate,Policy
    assert not repo.finish_job(stale,[Candidate(content='I prefer tea')],Policy())
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM memories WHERE lifecycle_state='active'")).scalar_one()==0
        assert c.execute(text('SELECT count(*) FROM memory_versions')).scalar_one()==1



def test_erasure_function_requires_recorded_job_identity(engine,identities):
    memory=make(engine,identities);repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    repo.forget(p,memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=memory.version_id),'actual-request')
    with pytest.raises(Exception,match='job identity required'):
        with engine.begin() as c:
            repo._scope(c,p.tenant_id)
            c.execute(text('SELECT erase_suppressed_memory(:tenant,:memory,:actor,:request)'),{'tenant':p.tenant_id,'memory':memory.id,'actor':p.actor_id,'request':'forged-request'})
    assert repo.erasure_status(p,memory.id,'status')['state']=='pending'
