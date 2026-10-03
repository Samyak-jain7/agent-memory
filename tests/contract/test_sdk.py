import json
from pathlib import Path
from uuid import UUID,uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.auth import issue_token
from contracts.models import CaptureEpisodeRequest,Message,AddMemoryRequest,SourceInput,CorrectMemoryRequest,ForgetMemoryRequest,ContextRequest
from memory_sdk import MemoryClient,MemoryAPIError
from persistence.repositories import Repository
from model_gateway import OfflineProvider
import os

DATABASE_URL=os.environ.get('TEST_DATABASE_URL','')

def test_sdk_public_lifecycle_and_request_identity(engine,identities):
    os.environ['MEMORY_AUTH_SECRET']='test-secret'
    token=issue_token(identities['tenant_a'],identities['actor_a'])
    with TestClient(create_app(DATABASE_URL),base_url="http://127.0.0.1:8000") as transport,MemoryClient(token,client=transport) as sdk:
        rid=uuid4();episode=sdk.create_episode(CaptureEpisodeRequest(session_id=identities['session_a'],subject_id=identities['subject_a'],messages=[Message(role='user',content='I prefer tea')],idempotency_key='sdk'),request_id=rid)
        assert episode.request_id==str(rid)
        added=sdk.add_memory(AddMemoryRequest(subject_id=identities['subject_a'],kind='core',content='I prefer tea',confidence=1,source=SourceInput(episode_id=episode.episode.id)))
        memory=added.memory;assert sdk.get_memory(memory.id).memory==memory
        assert sdk.list_memories(identities['subject_a']).items[0].id==memory.id
        assert sdk.search(identities['subject_a'],'tea').items[0].id==memory.id
        assert memory.id in sdk.build_context(ContextRequest(subject_id=identities['subject_a'],query='tea')).memory_ids
        assert sdk.job_status(episode.job.id).job.state=='pending'
        assert sdk.sources(memory.id).episodes[0].id==episode.episode.id
        correction=sdk.update_memory(memory.id,CorrectMemoryRequest(expected_version_id=memory.version_id,content='I prefer coffee',confidence=1,source=SourceInput(episode_id=episode.episode.id))).memory
        assert len(sdk.history(memory.id).versions)==2
        assert sdk.forget_memory(memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=correction.version_id)).erasure.state=='pending'
        with pytest.raises(MemoryAPIError) as denied:sdk.get_memory(memory.id)
        assert denied.value.status==404 and denied.value.code=='resource_not_found' and UUID(denied.value.request_id)
        Repository(engine).erase_next(identities['tenant_a'])
        assert sdk.erasure_status(memory.id).erasure.state=='completed'


def test_openapi_snapshot_matches_current_typed_contract(engine):
    schema=create_app(DATABASE_URL).openapi()
    assert schema==json.loads(Path('packages/contracts/openapi.json').read_text())
    assert schema['components']['securitySchemes']['HTTPBearer']['scheme']=='bearer'
    for path,methods in schema['paths'].items():
        for method,operation in methods.items():
            assert operation['security']==[{'HTTPBearer':[]}]
            assert 'requestBody' in operation if method in {'post','patch'} else True
            assert operation['responses']['422']['content']['application/json']['schema']['$ref'].endswith('APIErrorEnvelope')


def test_invalid_and_expired_authentication_is_safe_and_audited(engine,identities,monkeypatch):
    os.environ['MEMORY_AUTH_SECRET']='test-secret'
    import apps.api.auth as auth
    monkeypatch.setattr(auth.time,'time',lambda:1000)
    expired=issue_token(identities['tenant_a'],identities['actor_a'],ttl_seconds=1)
    monkeypatch.setattr(auth.time,'time',lambda:2000)
    with TestClient(create_app(DATABASE_URL)) as client:
        for headers in [{},{'Authorization':'Bearer malformed'},{'Authorization':'Bearer '+expired}]:
            response=client.get('/v1/memories',params={'subject_id':str(identities['subject_a'])},headers=headers)
            assert response.status_code==401 and response.json()['error']['code']=='invalid_credentials'
            assert UUID(response.json()['error']['request_id'])
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM authentication_events')).scalar_one()==3



def test_injected_client_cannot_bypass_https_or_follow_redirects():
    import httpx
    with httpx.Client(base_url='http://remote.example') as client:
        with pytest.raises(ValueError):MemoryClient('fixture',base_url='https://remote.example',client=client)
    calls=[]
    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'Location':'https://elsewhere.example'},json={'error':{'code':'redirect_refused','request_id':str(uuid4())}})
    with httpx.Client(base_url='https://memory.example',transport=httpx.MockTransport(redirect),follow_redirects=True) as client:
        with pytest.raises(MemoryAPIError) as error:MemoryClient('fixture',client=client).get_memory(uuid4())
        assert error.value.status==302 and len(calls)==1


@pytest.mark.parametrize('response',[{'error':'unexpected private text'},{'error':{}},{'error':{'code':'password=private','request_id':'not-safe'}}])
def test_malformed_error_envelopes_raise_safe_contract_errors(response):
    import httpx
    from memory_sdk import MemoryContractError
    with httpx.Client(base_url='https://memory.example',transport=httpx.MockTransport(lambda request:httpx.Response(503,json=response,headers={'X-Request-ID':'password=private'}))) as client:
        with pytest.raises(MemoryContractError) as error:MemoryClient('fixture',client=client).get_memory(uuid4())
        assert 'private' not in str(error.value) and UUID(error.value.request_id)


def test_unparseable_json_and_transport_failure_are_typed_and_safe():
    import httpx
    from memory_sdk import MemoryContractError,MemoryTransportError
    with httpx.Client(base_url='https://memory.example',transport=httpx.MockTransport(lambda request:httpx.Response(200,text='private-payload'))) as client:
        with pytest.raises(MemoryContractError):MemoryClient('fixture',client=client).get_memory(uuid4())
    def fail(request):raise httpx.ConnectError('private-transport-detail')
    with httpx.Client(base_url='https://memory.example',transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(MemoryTransportError) as error:MemoryClient('fixture',client=client).get_memory(uuid4())
        assert 'private' not in str(error.value)
