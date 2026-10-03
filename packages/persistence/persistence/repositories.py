import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from contracts.models import EpisodeResource, JobResource, MemoryResource
from memory_domain.service import CaptureResult, IdempotencyConflict


class Repository:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.after_episode_insert = lambda: None

    @staticmethod
    def _scope(connection, tenant_id: UUID) -> None:
        connection.execute(text("SET LOCAL ROLE memory_app"))
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)})

    @staticmethod
    def _authorized(connection, principal, subject_id: UUID) -> bool:
        return connection.execute(
            text("""SELECT EXISTS (
                SELECT 1 FROM actors a JOIN actor_subject_grants g ON g.tenant_id=a.tenant_id AND g.actor_id=a.id
                JOIN subjects s ON s.tenant_id=g.tenant_id AND s.id=g.subject_id
                WHERE a.id=:actor AND s.id=:subject AND a.tenant_id=:tenant AND a.active AND s.active
            )"""),
            {"actor": principal.actor_id, "subject": subject_id, "tenant": principal.tenant_id},
        ).scalar_one()

    def capture_episode(self, principal, request, request_id: str) -> CaptureResult:
        try:
            with self.engine.begin() as connection:
                self._scope(connection, principal.tenant_id)
                if not self._authorized(connection, principal, request.subject_id):
                    raise PermissionError
                session_matches = connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM sessions WHERE id=:session AND tenant_id=:tenant AND subject_id=:subject)"),
                    {"session": request.session_id, "tenant": principal.tenant_id, "subject": request.subject_id},
                ).scalar_one()
                if not session_matches:
                    raise PermissionError
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                    {"scope": f"{principal.tenant_id}:{request.idempotency_key}"},
                )
                existing = connection.execute(
                text("SELECT id, session_id, subject_id, actor_id, content, created_at FROM episodes WHERE tenant_id=:tenant AND idempotency_key=:key"),
                {"tenant": principal.tenant_id, "key": request.idempotency_key},
                ).mappings().first()
                if existing:
                    content = [message.model_dump() for message in request.messages]
                    if (
                        existing["session_id"] != request.session_id
                        or existing["subject_id"] != request.subject_id
                        or existing["actor_id"] != principal.actor_id
                        or existing["content"] != content
                    ):
                        raise IdempotencyConflict
                    job = connection.execute(text("SELECT id, episode_id, state, created_at FROM formation_jobs WHERE episode_id=:id"), {"id": existing["id"]}).mappings().one()
                    episode = {key: value for key, value in existing.items() if key != "content"}
                    return CaptureResult(EpisodeResource(tenant_id=principal.tenant_id, **episode), JobResource(**job), True)
                episode_id, job_id = uuid4(), uuid4()
                episode = connection.execute(
                text("""INSERT INTO episodes (id,tenant_id,subject_id,actor_id,session_id,content,source_type,idempotency_key)
                    VALUES (:id,:tenant,:subject,:actor,:session,CAST(:content AS jsonb),'messages',:key)
                    RETURNING id,session_id,subject_id,actor_id,created_at"""),
                {"id": episode_id, "tenant": principal.tenant_id, "subject": request.subject_id, "actor": principal.actor_id, "session": request.session_id, "content": json.dumps([m.model_dump() for m in request.messages]), "key": request.idempotency_key},
                ).mappings().one()
                self.after_episode_insert()
                job = connection.execute(
                text("INSERT INTO formation_jobs (id,tenant_id,episode_id,state) VALUES (:id,:tenant,:episode,'pending') RETURNING id,episode_id,state,created_at"),
                {"id": job_id, "tenant": principal.tenant_id, "episode": episode_id},
                ).mappings().one()
                self._audit(connection, principal, request.subject_id, "episode.capture", episode_id, request_id, "accepted")
                return CaptureResult(EpisodeResource(tenant_id=principal.tenant_id, **episode), JobResource(**job), False)
        except PermissionError:
            self._record_denial(principal, request.subject_id, "episode.capture", None, request_id)
            raise

    def add_memory(self, principal, request, request_id: str) -> MemoryResource:
        try:
            with self.engine.begin() as connection:
                self._scope(connection, principal.tenant_id)
                if not self._authorized(connection, principal, request.subject_id):
                    raise PermissionError
                source = connection.execute(text("SELECT id FROM episodes WHERE id=:id AND tenant_id=:tenant AND subject_id=:subject"), {"id": request.source.episode_id, "tenant": principal.tenant_id, "subject": request.subject_id}).scalar_one_or_none()
                if source is None:
                    raise PermissionError
                memory_id, version_id = uuid4(), uuid4()
                created_at = datetime.now(timezone.utc)
                connection.execute(text("INSERT INTO memories (id,tenant_id,subject_id,kind,lifecycle_state,created_at) VALUES (:id,:tenant,:subject,:kind,'active',:at)"), {"id": memory_id, "tenant": principal.tenant_id, "subject": request.subject_id, "kind": request.kind, "at": created_at})
                connection.execute(text("""INSERT INTO memory_versions (id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at)
                VALUES (:id,:tenant,:memory,:content,'explicit',:confidence,:importance,:at,:actor,:at)"""), {"id": version_id, "tenant": principal.tenant_id, "memory": memory_id, "content": request.content, "confidence": request.confidence, "importance": request.importance, "at": created_at, "actor": principal.actor_id})
                connection.execute(text("INSERT INTO memory_sources (tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:episode)"), {"tenant": principal.tenant_id, "version": version_id, "episode": source})
                connection.execute(text("UPDATE memories SET current_version_id=:version WHERE id=:memory AND tenant_id=:tenant"), {"version": version_id, "memory": memory_id, "tenant": principal.tenant_id})
                self._audit(connection, principal, request.subject_id, "memory.add", memory_id, request_id, "succeeded")
                return MemoryResource(id=memory_id, tenant_id=principal.tenant_id, subject_id=request.subject_id, created_at=created_at, kind=request.kind, lifecycle_state="active", version_id=version_id, content=request.content, origin="explicit", confidence=request.confidence, importance=request.importance, actor_id=principal.actor_id, source_episode_ids=[source])
        except PermissionError:
            self._record_denial(principal, request.subject_id, "memory.add", None, request_id)
            raise

    def get_memory(self, principal, memory_id: UUID, request_id: str):
        with self.engine.begin() as connection:
            self._scope(connection, principal.tenant_id)
            row = connection.execute(text("""SELECT m.id,m.tenant_id,m.subject_id,m.created_at,m.kind,m.lifecycle_state,v.id version_id,v.content,v.origin,v.confidence,v.importance,v.actor_id,
                array_agg(ms.episode_id) source_episode_ids FROM memories m JOIN memory_versions v ON v.id=m.current_version_id JOIN memory_sources ms ON ms.memory_version_id=v.id
                WHERE m.id=:id AND m.tenant_id=:tenant AND m.lifecycle_state='active' GROUP BY m.id,v.id"""), {"id": memory_id, "tenant": principal.tenant_id}).mappings().first()
            if row and self._authorized(connection, principal, row["subject_id"]):
                self._audit(connection, principal, row["subject_id"], "memory.read", memory_id, request_id, "succeeded")
                return MemoryResource(**row)
            self._audit(connection, principal, None, "memory.read", memory_id, request_id, "denied")
            return None

    def _record_denial(self, principal, subject_id, action, resource_id, request_id):
        with self.engine.begin() as connection:
            self._scope(connection, principal.tenant_id)
            self._audit(connection, principal, subject_id, action, resource_id, request_id, "denied")

    @staticmethod
    def _audit(connection, principal, subject_id, action, resource_id, request_id, outcome):
        connection.execute(text("""INSERT INTO audit_events (id,tenant_id,actor_id,subject_id,action,resource_type,resource_id,request_id,outcome)
            VALUES (:id,:tenant,:actor,:subject,:action,:type,:resource,:request,:outcome)"""), {"id": uuid4(), "tenant": principal.tenant_id, "actor": principal.actor_id, "subject": subject_id, "action": action, "type": action.split('.')[0], "resource": resource_id, "request": request_id, "outcome": outcome})
