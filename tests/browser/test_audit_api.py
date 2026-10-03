import os
from uuid import uuid4
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.auth import issue_token
from contracts.models import CaptureEpisodeRequest,AddMemoryRequest,SourceInput,Message
from persistence.repositories import Repository

def test_audit_route_is_subject_and_tenant_scoped(engine,identities,monkeypatch):
    monkeypatch.setenv('MEMORY_AUTH_SECRET','test-secret')
    from apps.api.auth import Principal
    principal=Principal(tenant_id=identities['tenant_a'],actor_id=identities['actor_a'])
    repo=Repository(engine)
    captured=repo.capture_episode(principal,CaptureEpisodeRequest(session_id=identities['session_a'],subject_id=identities['subject_a'],messages=[Message(role='user',content='I prefer tea')],idempotency_key='audit-console'),str(uuid4()))
    memory=repo.add_memory(principal,AddMemoryRequest(subject_id=identities['subject_a'],kind='profile',content='I prefer tea',confidence=1,source=SourceInput(episode_id=captured.episode.id)),str(uuid4()))
    with TestClient(create_app(os.environ['TEST_DATABASE_URL'])) as client:
        path=f'/v1/memories/{memory.id}/audit'
        own={'Authorization':'Bearer '+issue_token(identities['tenant_a'],identities['actor_a'])}
        result=client.get(path,headers=own);assert result.status_code==200
        assert any(e['action']=='memory.add' for e in result.json()['events'])
        assert 'I prefer tea' not in result.text
        other={'Authorization':'Bearer '+issue_token(identities['tenant_b'],identities['actor_b'])}
        denied=client.get(path,headers=other);missing=client.get(f'/v1/memories/{uuid4()}/audit',headers=other)
        assert denied.status_code==missing.status_code==404
        assert denied.json()['error']['code']==missing.json()['error']['code']=='resource_not_found'
