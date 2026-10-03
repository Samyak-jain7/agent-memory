from datetime import datetime,timezone
from time import monotonic,sleep
import pytest
from sqlalchemy import text
from apps.api.auth import Principal
from contracts.models import AddMemoryRequest,CaptureEpisodeRequest,Message,SourceInput,ContextRequest,CursorError
from memory_domain.context import ContextComposer
from persistence.repositories import Repository
from model_gateway import OfflineProvider


def make(engine,ids,content='I prefer tea',kind='preference'):
    repo=Repository(engine);principal=Principal(ids['tenant_a'],ids['actor_a'])
    episode=repo.capture_episode(principal,CaptureEpisodeRequest(session_id=ids['session_a'],subject_id=ids['subject_a'],messages=[Message(role='user',content=content)],idempotency_key=content),'retrieval')
    return repo.add_memory(principal,AddMemoryRequest(subject_id=ids['subject_a'],kind=kind,content=content,confidence=1,source=SourceInput(episode_id=episode.episode.id)),'retrieval')


def test_union_of_lexical_and_vector_candidates_and_stable_rank(engine,identities):
    lexical=make(engine,identities,'I prefer astronomy');semantic=make(engine,identities,'My unrelated hobby')
    provider=OfflineProvider()
    with engine.begin() as c:
        c.execute(text('UPDATE memory_indexes SET embedding=CAST(:vector AS vector) WHERE version_id=:id'),{'id':lexical.version_id,'vector':str(provider.embed('totallydifferent'))})
        c.execute(text('UPDATE memory_indexes SET embedding=CAST(:vector AS vector) WHERE version_id=:id'),{'id':semantic.version_id,'vector':str(provider.embed('astronomy'))})
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a']);at=datetime.now(timezone.utc)
    results=repo.search(p,identities['subject_a'],'astronomy','q',as_of=at)
    assert {r.id for r in results}=={lexical.id,semantic.id}
    assert all(r.source_episode_ids for r in results)
    assert [r.id for r in results]==[r.id for r in repo.search(p,identities['subject_a'],'astronomy','q',as_of=at)]
    with pytest.raises(PermissionError):repo.search(Principal(identities['tenant_b'],identities['actor_b']),identities['subject_a'],'astronomy','q')


def test_cursor_pages_are_bound_to_subject_and_filters(engine,identities):
    make(engine,identities,'I prefer tea');make(engine,identities,'I prefer coffee')
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    page,cursor=repo.list_memories(p,identities['subject_a'],'q',limit=1)
    more,end=repo.list_memories(p,identities['subject_a'],'q',limit=1,cursor=cursor)
    assert page[0].id!=more[0].id and end is None
    with pytest.raises(CursorError):repo.list_memories(p,identities['subject_a2'],'q',cursor=cursor)
    with pytest.raises(CursorError):repo.list_memories(p,identities['subject_a'],'q',cursor=cursor+'x')


def test_profile_first_and_conservative_context_budget(engine,identities):
    core=make(engine,identities,'My name is Alex','core');make(engine,identities,'I prefer tea')
    composer=ContextComposer(Repository(engine));p=Principal(identities['tenant_a'],identities['actor_a'])
    try:
        result=composer.build(p,ContextRequest(subject_id=identities['subject_a'],query='tea',token_budget=150),'ctx')
        assert result.core_profile==[core.id]
        assert result.budget_used==len(result.text.encode())<=150
        assert result.text.startswith('['+str(core.id))
        assert result.citations[str(core.id)]==core.source_episode_ids
    finally:composer.close()


def test_context_degrades_with_bounded_deadline(engine,identities):
    class Slow(Repository):
        def search(self,*args,**kwargs):sleep(.4);raise RuntimeError('unavailable')
    composer=ContextComposer(Slow(engine));start=monotonic()
    try:
        result=composer.build(Principal(identities['tenant_a'],identities['actor_a']),ContextRequest(subject_id=identities['subject_a'],query='tea',deadline_ms=50),'ctx')
        assert result.degraded and result.text=='' and monotonic()-start<.2
    finally:composer.close()


def test_embedding_failure_is_atomic_for_explicit_add(engine,identities):
    class Broken(OfflineProvider):
        def embed(self,text):raise RuntimeError('unavailable')
    make(engine,identities,'I prefer tea')
    repo=Repository(engine,Broken());p=Principal(identities['tenant_a'],identities['actor_a'])
    with engine.connect() as c:episode=c.execute(text('SELECT id FROM episodes LIMIT 1')).scalar_one()
    with pytest.raises(RuntimeError):repo.add_memory(p,AddMemoryRequest(subject_id=identities['subject_a'],kind='preference',content='I prefer coffee',confidence=1,source=SourceInput(episode_id=episode)),'q')
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==1



def test_degraded_context_never_contains_profile_personalization(engine,identities):
    make(engine,identities,'My name is private-person','core')
    class Unavailable(Repository):
        def search(self,*args,**kwargs):raise RuntimeError('unavailable')
    composer=ContextComposer(Unavailable(engine))
    try:
        result=composer.build(Principal(identities['tenant_a'],identities['actor_a']),ContextRequest(subject_id=identities['subject_a'],query='tea'),'ctx')
        assert result.degraded and result.text=='' and not result.core_profile and not result.memory_ids and not result.citations
    finally:composer.close()


@pytest.mark.parametrize('method',['list','search','profile'])
@pytest.mark.parametrize('subject',['subject_a2','subject_b'])
def test_read_authorization_denials_are_durable_and_payload_free(engine,identities,method,subject):
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    with pytest.raises(PermissionError):
        if method=='list':repo.list_memories(p,identities[subject],'denied-request')
        elif method=='search':repo.search(p,identities[subject],'never-log-query','denied-request')
        else:repo.profile(p,identities[subject],'denied-request')
    with engine.connect() as c:
        events=c.execute(text("SELECT action,outcome,subject_id FROM audit_events WHERE request_id='denied-request'")).all()
        assert events==[('memory.'+method,'denied',None)]


def test_cursor_is_bound_to_tenant_even_when_subject_uuid_repeats(engine,identities):
    make(engine,identities,'I prefer tea');make(engine,identities,'I prefer coffee')
    repo=Repository(engine);p=Principal(identities['tenant_a'],identities['actor_a'])
    _,cursor=repo.list_memories(p,identities['subject_a'],'cursor',limit=1)
    with engine.begin() as c:
        c.execute(text('INSERT INTO subjects(id,tenant_id) VALUES (:subject,:tenant)'),{'subject':identities['subject_a'],'tenant':identities['tenant_b']})
        c.execute(text('INSERT INTO actor_subject_grants VALUES (:tenant,:actor,:subject)'),{'subject':identities['subject_a'],'tenant':identities['tenant_b'],'actor':identities['actor_b']})
    with pytest.raises(CursorError):repo.list_memories(Principal(identities['tenant_b'],identities['actor_b']),identities['subject_a'],'cursor',cursor=cursor)
