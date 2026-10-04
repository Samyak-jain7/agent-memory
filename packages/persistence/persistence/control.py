import os
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from sqlalchemy import text
from contracts.models import VersionConflict,PolicyRejected
from model_gateway import Candidate,Policy

class Control:
    @contextmanager
    def _control(self,principal,memory_id,action,request_id,active=False):
        try:
            with self.engine.begin() as c:
                self._scope(c,principal.tenant_id)
                row=c.execute(text('SELECT * FROM memories WHERE id=:id FOR UPDATE'),{'id':memory_id}).mappings().first()
                if not row or not self._authorized(c,principal,row['subject_id']) or (active and row['lifecycle_state']!='active'):raise PermissionError()
                yield c,row
        except PermissionError:
            self._record_denial(principal,None,action,memory_id,request_id);raise

    def correct(self,principal,memory_id,request,request_id):
        try:
            with self._control(principal,memory_id,'memory.correct',request_id,active=True) as (c,m):
                if m['current_version_id']!=request.expected_version_id:raise VersionConflict()
                reason=Policy().reason(Candidate(content=request.content,confidence=request.confidence,importance=request.importance))
                if not self._consented(c,m['subject_id']):reason='consent_required'
                if reason:raise PolicyRejected(reason)
                source=c.execute(text('SELECT id FROM episodes WHERE id=:id AND subject_id=:subject AND erased_at IS NULL'),{'id':request.source.episode_id,'subject':m['subject_id']}).scalar_one_or_none()
                if not source:raise PermissionError()
                old=c.execute(text('SELECT valid_from FROM memory_versions WHERE id=:id'),{'id':m['current_version_id']}).scalar_one()
                at=max(datetime.now(timezone.utc),old+timedelta(microseconds=1));version=uuid4()
                c.execute(text('UPDATE memory_versions SET valid_to=:at WHERE id=:id'),{'at':at,'id':m['current_version_id']})
                c.execute(text("INSERT INTO memory_versions(id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at,supersedes_version_id) VALUES (:id,:tenant,:memory,:content,'explicit',:confidence,:importance,:at,:actor,:at,:old)"),
                    {'id':version,'tenant':principal.tenant_id,'memory':memory_id,'content':request.content,'confidence':request.confidence,'importance':request.importance,'at':at,'actor':principal.actor_id,'old':m['current_version_id']})
                c.execute(text('INSERT INTO memory_sources(tenant_id,memory_version_id,episode_id) SELECT tenant_id,:new,episode_id FROM memory_sources WHERE memory_version_id=:old'),{'new':version,'old':m['current_version_id']})
                c.execute(text('INSERT INTO memory_sources(tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:source) ON CONFLICT DO NOTHING'),{'tenant':principal.tenant_id,'version':version,'source':source})
                self._index(c,principal.tenant_id,version,request.content)
                c.execute(text('UPDATE memories SET current_version_id=:version WHERE id=:id'),{'version':version,'id':memory_id})
                self._audit(c,principal,m['subject_id'],'memory.correct',memory_id,request_id,'succeeded')
            return self.get_memory(principal,memory_id,request_id)
        except PolicyRejected as error:
            self._record_denial(principal,None,'memory.policy',memory_id,request_id);raise

    def forget(self,principal,memory_id,request,request_id):
        with self._control(principal,memory_id,'memory.forget',request_id) as (c,m):
            if m['current_version_id']!=request.expected_version_id:raise VersionConflict()
            existing=c.execute(text('SELECT * FROM erasure_jobs WHERE memory_id=:id'),{'id':memory_id}).mappings().first()
            if existing:return dict(existing)
            # Lock the source before invalidation, matching approval's source-first lock order.
            sources=c.execute(text('SELECT e.id FROM episodes e WHERE e.id IN (SELECT episode_id FROM memory_sources WHERE memory_version_id IN (SELECT id FROM memory_versions WHERE memory_id=:id)) ORDER BY e.id'),{'id':memory_id}).scalars().all()
            for source in sources:
                c.execute(text('SELECT lock_review_episode(:id)'),{'id':source})
                c.execute(text('INSERT INTO memory_review_blocks(tenant_id,episode_id) VALUES (:tenant,:episode) ON CONFLICT DO NOTHING'),{'tenant':principal.tenant_id,'episode':source})
            # Invalidate sibling suggestions immediately; review cannot resurrect forgotten evidence.
            c.execute(text("UPDATE memory_suggestions SET state='invalidated',content=NULL,decided_at=clock_timestamp() WHERE state='pending' AND episode_id IN (SELECT episode_id FROM memory_sources WHERE memory_version_id IN (SELECT id FROM memory_versions WHERE memory_id=:id))"),{'id':memory_id})
            self._reconcile_review_jobs(c)
            retention=int(os.getenv('ERASURE_RETENTION_SECONDS','0'))
            if retention<0 or retention>31536000:raise ValueError('Invalid retention configuration')
            c.execute(text("UPDATE memories SET lifecycle_state='suppressed',suppressed_at=now() WHERE id=:id"),{'id':memory_id})
            job=c.execute(text("INSERT INTO erasure_jobs(id,tenant_id,memory_id,actor_id,request_id,state,available_at) VALUES (:id,:tenant,:memory,:actor,:request,'pending',now()+:retention*interval '1 second') RETURNING *"),
                {'id':uuid4(),'tenant':principal.tenant_id,'memory':memory_id,'actor':principal.actor_id,'request':request_id,'retention':retention}).mappings().one()
            self._audit(c,principal,m['subject_id'],'memory.forget',memory_id,request_id,'suppressed')
            return dict(job)

    def history(self,principal,memory_id,request_id):
        with self._control(principal,memory_id,'memory.history',request_id) as (c,m):
            rows=c.execute(text("""SELECT v.id,v.content,v.origin,v.actor_id,v.valid_from,v.valid_to,v.supersedes_version_id,
             ARRAY(SELECT episode_id FROM memory_sources WHERE memory_version_id=v.id ORDER BY episode_id) source_episode_ids
             FROM memory_versions v WHERE v.memory_id=:id ORDER BY v.valid_from,v.id"""),{'id':memory_id}).mappings().all()
            versions=[dict(row) for row in rows]
            if m['lifecycle_state']!='active':
                for v in versions:v['content']=None;v['source_episode_ids']=[]
            self._audit(c,principal,m['subject_id'],'memory.history',memory_id,request_id,'succeeded')
            return {'memory_id':memory_id,'lifecycle_state':m['lifecycle_state'],'versions':versions,'request_id':request_id}

    def sources(self,principal,memory_id,request_id):
        with self._control(principal,memory_id,'memory.sources',request_id,active=True) as (c,m):
            allowed=c.execute(text('SELECT can_inspect_sources FROM actor_subject_grants WHERE actor_id=:actor AND subject_id=:subject'),{'actor':principal.actor_id,'subject':m['subject_id']}).scalar_one_or_none()
            if not allowed:raise PermissionError()
            rows=c.execute(text('SELECT e.id,e.created_at,e.content messages FROM episodes e JOIN memory_sources s ON s.episode_id=e.id WHERE s.memory_version_id=:version AND e.erased_at IS NULL ORDER BY e.created_at,e.id'),{'version':m['current_version_id']}).mappings().all()
            self._audit(c,principal,m['subject_id'],'memory.sources',memory_id,request_id,'succeeded')
            return {'episodes':[dict(row) for row in rows],'request_id':request_id}

    def erasure_status(self,principal,memory_id,request_id):
        with self._control(principal,memory_id,'memory.erasure_status',request_id) as (c,m):
            row=c.execute(text('SELECT * FROM erasure_jobs WHERE memory_id=:id'),{'id':memory_id}).mappings().first()
            if not row:raise PermissionError()
            self._audit(c,principal,m['subject_id'],'memory.erasure_status',memory_id,request_id,'succeeded')
            return dict(row)

    def erase_next(self,tenant_id):
        with self.engine.begin() as c:
            self._scope(c,tenant_id)
            row=c.execute(text("SELECT * FROM erasure_jobs WHERE state='pending' AND available_at<=now() ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1")).mappings().first()
            if not row:return False
            c.execute(text('SELECT erase_suppressed_memory(:tenant,:memory,:actor,:request)'),{'tenant':tenant_id,'memory':row['memory_id'],'actor':row['actor_id'],'request':row['request_id']})
            return True


    def retry_erasure(self,principal,memory_id,request,request_id):
        with self._control(principal,memory_id,'memory.erasure_retry',request_id) as (c,m):
            if m['lifecycle_state']!='suppressed' or m['current_version_id']!=request.expected_version_id:raise VersionConflict()
            row=c.execute(text("UPDATE erasure_jobs SET state='pending',actor_id=:actor,request_id=:request,error_code=NULL WHERE memory_id=:id AND state='blocked' RETURNING *"),
                {'id':memory_id,'actor':principal.actor_id,'request':request_id}).mappings().first()
            if row is None:raise VersionConflict()
            self._audit(c,principal,m['subject_id'],'memory.erasure_retry',memory_id,request_id,'explicitly_reauthorized')
            return dict(row)
