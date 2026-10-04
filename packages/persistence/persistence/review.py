"""Supervised suggestions are excluded from memory retrieval until explicitly approved."""
from contextlib import contextmanager
from uuid import uuid4
from sqlalchemy import text
from contracts.models import VersionConflict, PolicyRejected
from model_gateway import Candidate, Policy, review_sensitive

class Review:
    @contextmanager
    def _review_scope(self,principal,action,resource_id,request_id):
        try:
            with self.engine.begin() as c:
                self._scope(c,principal.tenant_id)
                yield c
        except PermissionError:
            self._record_denial(principal,None,action,resource_id,request_id)
            raise

    def _review_allowed(self,c,principal,subject):
        # Lock grants/identities so revocation cannot race a committed decision.
        return c.execute(text('SELECT can_review_subject(:actor,:subject)'),{'actor':principal.actor_id,'subject':subject}).scalar_one()

    def _reconcile_review_jobs(self,c):
        c.execute(text("UPDATE formation_jobs j SET state=CASE WHEN EXISTS(SELECT 1 FROM memory_suggestions s WHERE s.formation_job_id=j.id AND s.state='approved') THEN 'succeeded' ELSE 'rejected' END WHERE j.state='awaiting_review' AND NOT EXISTS(SELECT 1 FROM memory_suggestions s WHERE s.formation_job_id=j.id AND s.state='pending')"))

    def expire_suggestions(self,tenant_id):
        with self.engine.begin() as c:
            self._scope(c,tenant_id)
            c.execute(text("UPDATE memory_suggestions SET state='expired',content=NULL,decided_at=clock_timestamp() WHERE state='pending' AND expires_at<=clock_timestamp()"))
            self._reconcile_review_jobs(c)

    def list_suggestions(self,principal,subject,request_id,limit=20):
        self.expire_suggestions(principal.tenant_id)
        with self._review_scope(principal,'suggestion.list',None,request_id) as c:
            if not self._review_allowed(c,principal,subject):raise PermissionError()
            rows=c.execute(text("SELECT s.* FROM memory_suggestions s JOIN episodes e ON e.id=s.episode_id AND e.tenant_id=s.tenant_id WHERE s.subject_id=:subject AND s.state='pending' AND s.expires_at>clock_timestamp() AND e.erased_at IS NULL ORDER BY s.created_at,s.id LIMIT :limit"),{'subject':subject,'limit':limit}).mappings().all()
            self._audit(c,principal,subject,'suggestion.list',None,request_id,'succeeded')
            return [dict(row) for row in rows]

    def suggestion_sources(self,principal,suggestion_id,request_id):
        with self._review_scope(principal,'suggestion.sources',suggestion_id,request_id) as c:
            row=c.execute(text("SELECT s.subject_id,e.id,e.created_at,e.content messages FROM memory_suggestions s JOIN episodes e ON e.id=s.episode_id AND e.tenant_id=s.tenant_id WHERE s.id=:id AND s.state='pending' AND s.expires_at>clock_timestamp() AND e.erased_at IS NULL"),{'id':suggestion_id}).mappings().first()
            if not row or not self._review_allowed(c,principal,row['subject_id']):raise PermissionError()
            # Never display sensitive source messages in the review workspace.
            messages=[m for m in row['messages'] if not review_sensitive(m['content'])]
            self._audit(c,principal,row['subject_id'],'suggestion.sources',suggestion_id,request_id,'succeeded')
            return {'episodes':[{'id':row['id'],'created_at':row['created_at'],'messages':messages}],'request_id':request_id}

    def decide_suggestion(self,principal,suggestion_id,decision,request_id):
        if decision not in {'approve','reject'}:raise ValueError('Invalid review decision')
        self.expire_suggestions(principal.tenant_id)
        with self._review_scope(principal,'suggestion.'+decision,suggestion_id,request_id) as c:
            identity=c.execute(text('SELECT episode_id,subject_id FROM memory_suggestions WHERE id=:id'),{'id':suggestion_id}).mappings().first()
            if not identity or not self._review_allowed(c,principal,identity['subject_id']):raise PermissionError()
            # Source first: erasure also locks the episode before invalidating suggestions.
            source_available=c.execute(text('SELECT lock_review_episode(:id)'),{'id':identity['episode_id']}).scalar_one()
            row=c.execute(text('SELECT * FROM memory_suggestions WHERE id=:id FOR UPDATE'),{'id':suggestion_id}).mappings().one()
            target='approved' if decision=='approve' else 'rejected'
            if not source_available:raise VersionConflict()
            blocked=c.execute(text('SELECT EXISTS(SELECT 1 FROM memory_review_blocks WHERE episode_id=:id)'),{'id':row['episode_id']}).scalar_one()
            if row['state']==target:
                return {'suggestion_id':suggestion_id,'state':target,'memory_id':row['memory_id'],'request_id':request_id}
            if row['state']!='pending' or blocked or not c.execute(text('SELECT :expiry>clock_timestamp()'),{'expiry':row['expires_at']}).scalar_one():raise VersionConflict()
            memory_id=None
            if decision=='approve':
                candidate=Candidate(kind=row['kind'],content=row['content'],confidence=row['confidence'],importance=row['importance'])
                reason=Policy().reason(candidate)
                if review_sensitive(candidate.content):reason='sensitive_rejected'
                if reason:raise PolicyRejected(reason)
                memory_id,version_id=uuid4(),uuid4()
                c.execute(text("INSERT INTO memories(id,tenant_id,subject_id,kind,lifecycle_state,formation_job_id,candidate_index) VALUES (:id,:tenant,:subject,:kind,'active',:job,:index)"),{'id':memory_id,'tenant':principal.tenant_id,'subject':row['subject_id'],'kind':row['kind'],'job':row['formation_job_id'],'index':row['candidate_index']})
                c.execute(text("INSERT INTO memory_versions(id,tenant_id,memory_id,content,origin,confidence,importance,valid_from,actor_id,created_at) VALUES (:id,:tenant,:memory,:content,'explicit',:confidence,:importance,clock_timestamp(),:actor,clock_timestamp())"),{'id':version_id,'tenant':principal.tenant_id,'memory':memory_id,'content':row['content'],'confidence':row['confidence'],'importance':row['importance'],'actor':principal.actor_id})
                c.execute(text('INSERT INTO memory_sources(tenant_id,memory_version_id,episode_id) VALUES (:tenant,:version,:source)'),{'tenant':principal.tenant_id,'version':version_id,'source':row['episode_id']})
                c.execute(text('UPDATE memories SET current_version_id=:version WHERE id=:id'),{'id':memory_id,'version':version_id})
                self._index(c,principal.tenant_id,version_id,row['content'])
                # An embedding delay must not extend the seven-day approval window.
                if not c.execute(text('SELECT :expiry>clock_timestamp()'),{'expiry':row['expires_at']}).scalar_one():raise VersionConflict()
            c.execute(text('UPDATE memory_suggestions SET state=:state,content=NULL,decided_at=clock_timestamp(),decided_by=:actor,memory_id=:memory WHERE id=:id'),{'state':target,'actor':principal.actor_id,'memory':memory_id,'id':suggestion_id})
            c.execute(text("UPDATE formation_jobs SET state=CASE WHEN EXISTS(SELECT 1 FROM memory_suggestions WHERE formation_job_id=:job AND state='approved') THEN 'succeeded' ELSE 'rejected' END WHERE id=:job AND NOT EXISTS(SELECT 1 FROM memory_suggestions WHERE formation_job_id=:job AND state='pending')"),{'job':row['formation_job_id']})
            self._audit(c,principal,row['subject_id'],'suggestion.'+decision,suggestion_id,request_id,target)
            return {'suggestion_id':suggestion_id,'state':target,'memory_id':memory_id,'request_id':request_id}
