import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from contracts.models import EpisodeResource, JobResource, MemoryResource, PolicyRejected
from observability import carrier, operation
from memory_domain.service import CaptureResult, IdempotencyConflict


from .retrieval import Retrieval
from .control import Control
from .review import Review


class Repository(Retrieval,Control,Review):
    def __init__(self, engine: Engine, provider=None):
        self.engine = engine
        self.provider = provider
        self.after_episode_insert = lambda: None
        self.after_candidate_insert = lambda index: None

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

    @staticmethod
    def _consented(connection, subject_id):
        return connection.execute(text("SELECT memory_consent FROM subjects WHERE id=:id"),{'id':subject_id}).scalar_one_or_none() is True

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
                text("INSERT INTO formation_jobs (id,tenant_id,episode_id,state,request_id,trace_context) VALUES (:id,:tenant,:episode,'pending',:request,CAST(:trace AS jsonb)) RETURNING id,episode_id,state,created_at"),
                {"id": job_id, "tenant": principal.tenant_id, "episode": episode_id, "request": request_id, "trace": json.dumps(carrier())},
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
                source = connection.execute(text("SELECT id FROM episodes WHERE id=:id AND tenant_id=:tenant AND subject_id=:subject AND erased_at IS NULL"), {"id": request.source.episode_id, "tenant": principal.tenant_id, "subject": request.subject_id}).scalar_one_or_none()
                if source is None:
                    raise PermissionError
                from model_gateway import Candidate, Policy
                reason=Policy().reason(Candidate(kind=request.kind,content=request.content,confidence=request.confidence,importance=request.importance))
                if not self._consented(connection,request.subject_id): reason='consent_required'
                if reason: raise PolicyRejected(reason)
                memory_id, version_id = uuid4(), uuid4()
                created_at = datetime.now(timezone.utc)
                connection.execute(text("INSERT INTO memories (id,tenant_id,subject_id,kind,lifecycle_state,created_at) VALUES (:id,:tenant,:subject,:kind,'active',:at)"), {"id": memory_id, "tenant": principal.tenant_id, "subject": request.subject_id, "kind": request.kind, "at": created_at})
                connection.execute(text("""INSERT INTO memory_versions (id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at)
                VALUES (:id,:tenant,:memory,:content,'explicit',:confidence,:importance,:at,:actor,:at)"""), {"id": version_id, "tenant": principal.tenant_id, "memory": memory_id, "content": request.content, "confidence": request.confidence, "importance": request.importance, "at": created_at, "actor": principal.actor_id})
                connection.execute(text("INSERT INTO memory_sources (tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:episode)"), {"tenant": principal.tenant_id, "version": version_id, "episode": source})
                connection.execute(text("UPDATE memories SET current_version_id=:version WHERE id=:memory AND tenant_id=:tenant"), {"version": version_id, "memory": memory_id, "tenant": principal.tenant_id})
                self._index(connection,principal.tenant_id,version_id,request.content)
                self._audit(connection, principal, request.subject_id, "memory.add", memory_id, request_id, "succeeded")
                return MemoryResource(id=memory_id, tenant_id=principal.tenant_id, subject_id=request.subject_id, created_at=created_at, kind=request.kind, indexing_state="ready", lifecycle_state="active", version_id=version_id, content=request.content, origin="explicit", confidence=request.confidence, importance=request.importance, actor_id=principal.actor_id, source_episode_ids=[source])
        except PermissionError:
            self._record_denial(principal, request.subject_id, "memory.add", None, request_id)
            raise

        except PolicyRejected as error:
            with self.engine.begin() as connection:
                self._scope(connection,principal.tenant_id)
                self._audit(connection,principal,request.subject_id,'memory.policy',None,request_id,str(error))
            raise

    def get_memory(self, principal, memory_id: UUID, request_id: str):
        with self.engine.begin() as connection:
            self._scope(connection, principal.tenant_id)
            row = connection.execute(text("""SELECT m.id,m.tenant_id,m.subject_id,m.created_at,m.kind,m.lifecycle_state,v.id version_id,v.content,v.origin,v.confidence,v.importance,v.actor_id,
                COALESCE((SELECT state FROM memory_indexes WHERE version_id=v.id),'pending') indexing_state, array_agg(ms.episode_id) source_episode_ids FROM memories m JOIN memory_versions v ON v.id=m.current_version_id JOIN memory_sources ms ON ms.memory_version_id=v.id
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


    def job_status(self,principal,job_id,request_id):
        with self.engine.begin() as c:
            self._scope(c,principal.tenant_id)
            row=c.execute(text("SELECT j.*,e.subject_id FROM formation_jobs j JOIN episodes e ON e.id=j.episode_id AND e.tenant_id=j.tenant_id WHERE j.id=:id"),{'id':job_id}).mappings().first()
            if not row or not self._authorized(c,principal,row['subject_id']):
                self._audit(c,principal,None,'job.read',job_id,request_id,'denied');return None
            self._audit(c,principal,row['subject_id'],'job.read',job_id,request_id,'succeeded')
            return {k:row[k] for k in ['id','episode_id','state','attempts','created_at','completed_at','error_code','outcomes']}

    def claim_job(self,tenant_id,lease_seconds,max_attempts):
        with self.engine.begin() as c:
            self._scope(c,tenant_id)
            # Expired final attempts are terminal, including a crash on the last lease.
            c.execute(text("UPDATE formation_jobs SET state='dead_letter',error_code='lease_expired',completed_at=now() WHERE attempts>=:max AND state='leased' AND lease_expires_at<now()"),{'max':max_attempts})
            row=c.execute(text("""SELECT j.*,e.subject_id,e.actor_id,e.content FROM formation_jobs j
                JOIN episodes e ON e.id=j.episode_id AND e.tenant_id=j.tenant_id
                WHERE j.attempts<:max AND ((j.state IN ('pending','retryable_failure') AND j.available_at<=now())
                    OR (j.state='leased' AND j.lease_expires_at<now()))
                ORDER BY j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1"""),{'max':max_attempts}).mappings().first()
            if not row:return None
            job=dict(row);job['lease_token']=uuid4()
            c.execute(text("UPDATE formation_jobs SET state='leased',attempts=attempts+1,lease_token=:token,lease_expires_at=now()+:seconds*interval '1 second' WHERE id=:id"),{'id':job['id'],'token':job['lease_token'],'seconds':lease_seconds})
            return job

    def finish_job(self,job,candidates,policy,input_rejections=None,supervised=False):
        from apps.api.auth import Principal
        principal=Principal(job['tenant_id'],job['actor_id'])
        with operation('memory.persist',tenant_id=job['tenant_id'],job_id=job['id'],request_id=job['request_id']):
            with self.engine.begin() as c:
                self._scope(c,job['tenant_id'])
                # Source first, matching erasure and review decisions.
                source_available=c.execute(text('SELECT lock_review_episode(:id)'),{'id':job['episode_id']}).scalar_one()
                current=c.execute(text("SELECT state,lease_token,lease_expires_at>now() live FROM formation_jobs WHERE id=:id FOR UPDATE"),{'id':job['id']}).mappings().one()
                if current['state']!='leased' or current['lease_token']!=job['lease_token'] or not current['live']: return False
                outcomes=list(input_rejections or [])
                if not source_available or (supervised and c.execute(text('SELECT EXISTS(SELECT 1 FROM memory_review_blocks WHERE episode_id=:id)'),{'id':job['episode_id']}).scalar_one()):
                    candidates=[];outcomes.append({'outcome':'source_erased'})
                for index,candidate in enumerate(candidates):
                    from model_gateway import Candidate
                    candidate=Candidate.model_validate(candidate)
                    reason=policy.reason(candidate)
                    if not self._authorized(c,principal,job['subject_id']):reason='permission_revoked'
                    elif not self._consented(c,job['subject_id']):reason='consent_required'
                    if supervised:
                        from model_gateway import review_sensitive
                        if review_sensitive(candidate.content):reason='sensitive_rejected'
                    if reason:outcomes.append({'index':index,'outcome':reason});continue
                    if supervised:
                        suggestion_id=uuid4()
                        c.execute(text('INSERT INTO memory_suggestions(id,tenant_id,subject_id,episode_id,formation_job_id,candidate_index,kind,content,confidence,importance) VALUES (:id,:tenant,:subject,:episode,:job,:index,:kind,:content,:confidence,:importance)'),{'id':suggestion_id,'tenant':job['tenant_id'],'subject':job['subject_id'],'episode':job['episode_id'],'job':job['id'],'index':index,'kind':candidate.kind,'content':candidate.content,'confidence':candidate.confidence,'importance':candidate.importance})
                        self.after_candidate_insert(index)
                        outcomes.append({'index':index,'outcome':'awaiting_review','suggestion_id':str(suggestion_id)})
                        continue
                    memory_id,version_id=uuid4(),uuid4();at=datetime.now(timezone.utc)
                    c.execute(text("INSERT INTO memories(id,tenant_id,subject_id,kind,lifecycle_state,formation_job_id,candidate_index) VALUES (:id,:tenant,:subject,:kind,'active',:job,:index)"),{'id':memory_id,'tenant':job['tenant_id'],'subject':job['subject_id'],'kind':candidate.kind,'job':job['id'],'index':index})
                    c.execute(text("INSERT INTO memory_versions(id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at) VALUES (:id,:tenant,:memory,:content,'observed',:confidence,:importance,:at,:actor,:at)"),{'id':version_id,'tenant':job['tenant_id'],'memory':memory_id,'content':candidate.content,'confidence':candidate.confidence,'importance':candidate.importance,'at':at,'actor':job['actor_id']})
                    c.execute(text("INSERT INTO memory_sources(tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:episode)"),{'tenant':job['tenant_id'],'version':version_id,'episode':job['episode_id']})
                    c.execute(text("UPDATE memories SET current_version_id=:version WHERE id=:id"),{'id':memory_id,'version':version_id})
                    self._audit(c,principal,job['subject_id'],'memory.formed',memory_id,job['request_id'],'succeeded')
                    self._index(c,job['tenant_id'],version_id,candidate.content)
                    self.after_candidate_insert(index)
                    outcomes.append({'index':index,'outcome':'accepted','memory_id':str(memory_id),'evaluation_version':policy.version})
                state='awaiting_review' if any(o['outcome']=='awaiting_review' for o in outcomes) else ('succeeded' if any(o['outcome']=='accepted' for o in outcomes) else 'rejected')
                c.execute(text("UPDATE formation_jobs SET state=:state,outcomes=CAST(:outcomes AS jsonb),completed_at=now(),error_code=NULL,lease_token=NULL WHERE id=:id"),{'id':job['id'],'state':state,'outcomes':json.dumps(outcomes)})
                self._audit(c,principal,job['subject_id'],'job.complete',job['id'],job['request_id'],state)
                return True

    def fail_job(self,job,error_code,max_attempts):
        with self.engine.begin() as c:
            self._scope(c,job['tenant_id'])
            c.execute(text("""UPDATE formation_jobs SET state=CASE WHEN attempts>=:max THEN 'dead_letter' ELSE 'retryable_failure' END,
                available_at=now()+least(power(2,attempts),60)*interval '1 second',error_code=:error,lease_token=NULL,
                completed_at=CASE WHEN attempts>=:max THEN now() ELSE NULL END
                WHERE id=:id AND state='leased' AND lease_token=:token AND lease_expires_at>now()"""),
                {'id':job['id'],'token':job['lease_token'],'max':max_attempts,'error':error_code})


    def can_form_job(self,job):
        from apps.api.auth import Principal
        with self.engine.begin() as c:
            self._scope(c,job['tenant_id'])
            return self._authorized(c,Principal(job['tenant_id'],job['actor_id']),job['subject_id']) and self._consented(c,job['subject_id'])
