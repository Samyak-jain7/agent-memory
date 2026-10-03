import base64
import hashlib
import hmac
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from apps.api.auth import Principal
from apps.api.main import create_app
from contracts.models import CaptureEpisodeRequest, Message
from persistence.repositories import Repository


DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")



def headers(ids, tenant="tenant_a", actor="actor_a"):
    os.environ["MEMORY_AUTH_SECRET"] = "test-secret"
    from apps.api.auth import issue_token
    token=issue_token(ids[tenant],ids[actor])
    return {"Authorization":"Bearer "+token,"X-Request-ID":"00000000-0000-4000-8000-000000000001"}


def episode_body(ids, key="episode-1"):
    return {"session_id": str(ids["session_a"]), "subject_id": str(ids["subject_a"]), "messages": [{"role": "user", "content": "I prefer tea"}], "idempotency_key": key}


def test_capture_is_atomic_and_idempotent(engine, identities):
    repository = Repository(engine)
    principal = Principal(identities["tenant_a"], identities["actor_a"])
    request = CaptureEpisodeRequest(session_id=identities["session_a"], subject_id=identities["subject_a"], messages=[Message(role="user", content="hello")], idempotency_key="atomic")
    repository.after_episode_insert = lambda: (_ for _ in ()).throw(RuntimeError("forced failure"))
    with pytest.raises(RuntimeError):
        repository.capture_episode(principal, request, "req-fail")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM episodes")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM formation_jobs")).scalar_one() == 0
    repository.after_episode_insert = lambda: None
    first = repository.capture_episode(principal, request, "req-one")
    replay = repository.capture_episode(principal, request, "req-two")
    assert replay.replayed and replay.episode.id == first.episode.id and replay.job.id == first.job.id
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM episodes")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM formation_jobs")).scalar_one() == 1


def test_concurrent_capture_returns_one_episode_and_job(engine, identities):
    principal = Principal(identities["tenant_a"], identities["actor_a"])
    request = CaptureEpisodeRequest(session_id=identities["session_a"], subject_id=identities["subject_a"], messages=[Message(role="user", content="hello")], idempotency_key="concurrent")
    barrier = Barrier(2)

    def capture(request_id):
        barrier.wait()
        return Repository(engine).capture_episode(principal, request, request_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(capture, ("req-one", "req-two")))
    assert results[0].episode.id == results[1].episode.id
    assert results[0].job.id == results[1].job.id
    assert sorted(result.replayed for result in results) == [False, True]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM episodes")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM formation_jobs")).scalar_one() == 1


def test_rls_blocks_cross_tenant_rows_for_non_owner_role(engine, identities):
    with engine.connect() as connection:
        role = connection.execute(text("""SELECT target.rolcanlogin, target.rolbypassrls, membership.inherit_option, membership.set_option
            FROM pg_roles target JOIN pg_auth_members membership ON membership.roleid=target.oid
            JOIN pg_roles member ON member.oid=membership.member
            WHERE target.rolname='memory_app' AND member.rolname=current_user""")).one()
        assert role == (False, False, False, True)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET LOCAL ROLE memory_app"))
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": str(identities["tenant_a"])})
        assert connection.execute(text("SELECT count(*) FROM subjects")).scalar_one() == 2
        assert connection.execute(text("SELECT count(*) FROM subjects WHERE tenant_id=:tenant"), {"tenant": identities["tenant_b"]}).scalar_one() == 0
        transaction.rollback()


def test_idempotency_key_rejects_different_payload(engine, identities):
    client = TestClient(create_app(DATABASE_URL))
    original = episode_body(identities, "payload-key")
    assert client.post("/v1/episodes", json=original, headers=headers(identities)).status_code == 202
    changed = episode_body(identities, "payload-key")
    changed["messages"][0]["content"] = "I prefer coffee"
    conflict = client.post("/v1/episodes", json=changed, headers=headers(identities))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM episodes")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM formation_jobs")).scalar_one() == 1


def test_capture_rejects_session_for_another_subject_and_persists_audit(engine, identities):
    client = TestClient(create_app(DATABASE_URL))
    body = episode_body(identities, "wrong-session-subject")
    body["subject_id"] = str(identities["subject_a2"])
    response = client.post("/v1/episodes", json=body, headers=headers(identities))
    assert response.status_code == 404
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM episodes")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE action='episode.capture' AND outcome='denied' AND request_id='00000000-0000-4000-8000-000000000001'")).scalar_one() == 1


def test_api_denies_cross_tenant_without_disclosure_and_audits_writes(engine, identities):
    client = TestClient(create_app(DATABASE_URL))
    captured = client.post("/v1/episodes", json=episode_body(identities), headers=headers(identities))
    assert captured.status_code == 202
    memory = client.post("/v1/memories", json={"subject_id": str(identities["subject_a"]), "kind": "preference", "content": "Prefers tea", "source": {"episode_id": captured.json()["episode"]["id"]}, "confidence": 1.0}, headers=headers(identities))
    assert memory.status_code == 201
    memory_id = memory.json()["memory"]["id"]
    own = client.get(f"/v1/memories/{memory_id}", headers=headers(identities))
    other = client.get(f"/v1/memories/{memory_id}", headers=headers(identities, "tenant_b", "actor_b"))
    missing = client.get(f"/v1/memories/{uuid4()}", headers=headers(identities, "tenant_b", "actor_b"))
    assert own.status_code == 200
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    cross_add = client.post("/v1/memories", json={"subject_id": str(identities["subject_a"]), "kind": "preference", "content": "stolen", "source": {"episode_id": captured.json()["episode"]["id"]}, "confidence": 1.0}, headers=headers(identities, "tenant_b", "actor_b"))
    assert cross_add.status_code == 404
    with engine.connect() as connection:
        events = connection.execute(text("SELECT action,request_id,outcome FROM audit_events ORDER BY created_at")).all()
    assert ("episode.capture", "00000000-0000-4000-8000-000000000001", "accepted") in events
    assert ("memory.add", "00000000-0000-4000-8000-000000000001", "succeeded") in events
    assert ("memory.read", "00000000-0000-4000-8000-000000000001", "succeeded") in events
    assert ("memory.add", "00000000-0000-4000-8000-000000000001", "denied") in events


def test_read_requires_active_actor_and_denial_is_audited(engine, identities):
    client = TestClient(create_app(DATABASE_URL))
    captured = client.post("/v1/episodes", json=episode_body(identities), headers=headers(identities))
    memory = client.post("/v1/memories", json={"subject_id": str(identities["subject_a"]), "kind": "preference", "content": "Prefers tea", "source": {"episode_id": captured.json()["episode"]["id"]}, "confidence": 1.0}, headers=headers(identities))
    with engine.begin() as connection:
        connection.execute(text("UPDATE actors SET active=false WHERE tenant_id=:tenant AND id=:actor"), {"tenant": identities["tenant_a"], "actor": identities["actor_a"]})
    response = client.get(f"/v1/memories/{memory.json()['memory']['id']}", headers=headers(identities))
    assert response.status_code == 404
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE action='memory.read' AND outcome='denied' AND request_id='00000000-0000-4000-8000-000000000001'")).scalar_one() == 1


def test_active_memory_requires_complete_source_linked_version(engine, identities):
    with pytest.raises(Exception, match="active memory requires"):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO memories(id,tenant_id,subject_id,kind,lifecycle_state) VALUES (:id,:tenant_a,:subject_a,'preference','active')"), {**identities, "id": uuid4()})


def test_supersession_cannot_cross_tenants(engine, identities):
    values = {**identities, "memory_a": uuid4(), "memory_b": uuid4(), "version_a": uuid4(), "version_b": uuid4()}
    with pytest.raises(Exception, match="memory_versions_tenant_id_supersedes_version_id_fkey"):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO memories(id,tenant_id,subject_id,kind,lifecycle_state) VALUES (:memory_a,:tenant_a,:subject_a,'preference','suppressed'),(:memory_b,:tenant_b,:subject_b,'preference','suppressed')"), values)
            connection.execute(text("""INSERT INTO memory_versions(id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at)
                VALUES (:version_a,:tenant_a,:memory_a,'A','explicit',1,1,now(),:actor_a,now())"""), values)
            connection.execute(text("""INSERT INTO memory_versions(id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,supersedes_version_id,actor_id,created_at)
                VALUES (:version_b,:tenant_b,:memory_b,'B','explicit',1,1,now(),:version_a,:actor_b,now())"""), values)


@pytest.fixture
def stored_memory(identities):
    client = TestClient(create_app(DATABASE_URL))
    captured = client.post("/v1/episodes", json=episode_body(identities), headers=headers(identities))
    assert captured.status_code == 202
    created = client.post("/v1/memories", json={"subject_id": str(identities["subject_a"]),
        "kind": "preference", "content": "Prefers tea", "confidence": 1,
        "source": {"episode_id": captured.json()["episode"]["id"]}}, headers=headers(identities))
    assert created.status_code == 201
    return client, captured.json(), created.json()["memory"]


def test_revoked_subject_grant_denies_read_and_capture(engine, identities, stored_memory):
    client, captured, memory = stored_memory
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM actor_subject_grants WHERE tenant_id=:tenant AND actor_id=:actor"),
            {"tenant": identities["tenant_a"], "actor": identities["actor_a"]})
    assert client.get(f"/v1/memories/{memory['id']}", headers=headers(identities)).status_code == 404
    assert client.post("/v1/episodes", json=episode_body(identities, "denied"), headers=headers(identities)).status_code == 404
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM audit_events WHERE outcome='denied'")).scalar_one() == 2


@pytest.mark.parametrize("state", ["leased", "succeeded", "rejected", "retryable_failure", "dead_letter"])
def test_capture_replay_returns_current_processing_state(engine, identities, state):
    client = TestClient(create_app(DATABASE_URL))
    initial = client.post("/v1/episodes", json=episode_body(identities), headers=headers(identities)).json()
    with engine.begin() as connection:
        connection.execute(text("UPDATE formation_jobs SET state=:state WHERE id=:id"),
            {"state": state, "id": initial["job"]["id"]})
    replay = client.post("/v1/episodes", json=episode_body(identities), headers=headers(identities))
    assert replay.status_code == 200
    assert replay.json()["episode"]["id"] == initial["episode"]["id"]
    assert replay.json()["job"]["state"] == state


@pytest.mark.parametrize("statement", [
    "UPDATE episodes SET content='[]'::jsonb",
    "DELETE FROM episodes",
    "UPDATE audit_events SET outcome='changed'",
    "DELETE FROM audit_events",
    "UPDATE memory_versions SET content='changed'",
    "DELETE FROM memory_versions",
    "DELETE FROM memory_sources",
    "INSERT INTO actor_subject_grants SELECT tenant_id,id,:subject FROM actors",
])
def test_application_role_cannot_mutate_evidence_or_grants(engine, identities, stored_memory, statement):
    with pytest.raises(Exception, match="permission denied"):
        with engine.begin() as connection:
            Repository._scope(connection, identities["tenant_a"])
            connection.execute(text(statement), {"subject": identities["subject_a2"]})


def test_tenant_table_is_isolated(engine, identities):
    with engine.begin() as connection:
        Repository._scope(connection, identities["tenant_a"])
        assert connection.execute(text("SELECT id FROM tenants")).scalars().all() == [identities["tenant_a"]]
    with pytest.raises(Exception, match="permission denied"):
        with engine.begin() as connection:
            Repository._scope(connection, identities["tenant_a"])
            connection.execute(text("UPDATE tenants SET name='changed'"))


def test_last_evidence_cannot_be_removed_even_by_owner(engine, identities, stored_memory):
    _, _, memory = stored_memory
    with pytest.raises(Exception, match="active memory requires source evidence"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM memory_sources WHERE memory_version_id=:id"), {"id": memory["version_id"]})


def test_source_cannot_reference_another_subject(engine, identities, stored_memory):
    client, _, memory = stored_memory
    session = uuid4()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO actor_subject_grants VALUES (:tenant,:actor,:subject)"),
            {"tenant": identities["tenant_a"], "actor": identities["actor_a"], "subject": identities["subject_a2"]})
        connection.execute(text("INSERT INTO sessions(id,tenant_id,subject_id) VALUES (:id,:tenant,:subject)"),
            {"id": session, "tenant": identities["tenant_a"], "subject": identities["subject_a2"]})
    body = episode_body(identities, "other-subject")
    body.update(session_id=str(session), subject_id=str(identities["subject_a2"]))
    other = client.post("/v1/episodes", json=body, headers=headers(identities))
    assert other.status_code == 202
    with pytest.raises(Exception, match="source evidence must belong to the memory subject"):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO memory_sources(tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:episode)"),
                {"tenant": identities["tenant_a"], "version": memory["version_id"], "episode": other.json()["episode"]["id"]})



def test_memory_subject_cannot_change_after_evidence_link(engine, identities, stored_memory):
    _, _, memory = stored_memory
    with pytest.raises(Exception, match="permission denied"):
        with engine.begin() as connection:
            Repository._scope(connection, identities["tenant_a"])
            connection.execute(text("UPDATE memories SET subject_id=:subject WHERE id=:id"),
                {"subject": identities["subject_a2"], "id": memory["id"]})
    with engine.connect() as connection:
        assert connection.execute(text("SELECT subject_id FROM memories WHERE id=:id"),
            {"id": memory["id"]}).scalar_one() == identities["subject_a"]
