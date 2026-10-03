import base64
import hashlib
import hmac
import json
import math
import os
from datetime import datetime,timezone
from contextlib import contextmanager
from uuid import UUID
from sqlalchemy import text
from contracts.models import CursorError,MemoryResource
from observability import operation

FIELDS="""m.id,m.tenant_id,m.subject_id,m.created_at,m.kind,m.lifecycle_state,
v.id version_id,v.content,v.origin,v.confidence,v.importance,v.actor_id,
COALESCE(i.state,'pending') indexing_state,
ARRAY(SELECT s.episode_id FROM memory_sources s WHERE s.tenant_id=v.tenant_id AND s.memory_version_id=v.id ORDER BY s.episode_id) source_episode_ids"""
FROM="memories m JOIN memory_versions v ON v.id=m.current_version_id AND v.tenant_id=m.tenant_id LEFT JOIN memory_indexes i ON i.version_id=v.id AND i.tenant_id=v.tenant_id"
ACTIVE="m.subject_id=:subject AND m.lifecycle_state='active'"

def vector_literal(values):
    if len(values)!=64 or any(not math.isfinite(float(v)) for v in values) or not any(float(v) for v in values):
        raise ValueError('Embedding must be a finite nonzero 64-dimensional vector')
    return json.dumps([float(v) for v in values])

def cursor_encode(values):
    raw=json.dumps(values,sort_keys=True).encode()
    encoded=base64.urlsafe_b64encode(raw).decode().rstrip('=')
    signature=hmac.new(os.environ['MEMORY_AUTH_SECRET'].encode(),encoded.encode(),hashlib.sha256).hexdigest()
    return encoded+'.'+signature

def cursor_decode(value,tenant,subject,kind):
    try:
        encoded,signature=value.split('.',1)
        expected=hmac.new(os.environ['MEMORY_AUTH_SECRET'].encode(),encoded.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        item=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if item['tenant']!=str(tenant) or item['subject']!=str(subject) or item['kind']!=kind:raise ValueError()
        return datetime.fromisoformat(item['at']),UUID(item['id'])
    except (ValueError,KeyError,TypeError):raise CursorError('invalid_cursor') from None

class Retrieval:
    @contextmanager
    def _read(self,principal,subject,action,request_id):
        try:
            with self.engine.begin() as c:
                self._scope(c,principal.tenant_id)
                if not self._authorized(c,principal,subject):raise PermissionError()
                yield c
        except PermissionError:
            self._record_denial(principal,None,action,None,request_id)
            raise

    def _index(self,c,tenant,version,content):
        from model_gateway import provider_from_env
        provider=self.provider or provider_from_env()
        with operation('memory.embed',tenant_id=tenant,provider=provider.name):
            embedding=vector_literal(provider.embed(content))
        c.execute(text("""INSERT INTO memory_indexes(tenant_id,version_id,search_document,embedding,provider,state)
            VALUES (:tenant,:version,to_tsvector('english',:content),CAST(:embedding AS vector),:provider,'ready')
            ON CONFLICT(version_id) DO UPDATE SET search_document=excluded.search_document,embedding=excluded.embedding,provider=excluded.provider,state='ready'"""),
            {'tenant':tenant,'version':version,'content':content,'embedding':embedding,'provider':provider.name})

    def list_memories(self,principal,subject,request_id,limit=20,cursor=None,kind=None):
        limit=max(1,min(limit,100));after=cursor_decode(cursor,principal.tenant_id,subject,kind) if cursor else None
        with self._read(principal,subject,'memory.list',request_id) as c:
            where=ACTIVE+(' AND m.kind=:kind' if kind else '')
            params={'subject':subject,'kind':kind,'limit':limit+1}
            if after:
                where+=' AND (m.created_at,m.id) > (:after_at,:after_id)';params.update(after_at=after[0],after_id=after[1])
            rows=c.execute(text('SELECT '+FIELDS+' FROM '+FROM+' WHERE '+where+' ORDER BY m.created_at,m.id LIMIT :limit'),params).mappings().all()
            items=[MemoryResource(**row) for row in rows[:limit]]
            next_cursor=None
            if len(rows)>limit:
                last=items[-1];next_cursor=cursor_encode({'tenant':str(principal.tenant_id),'subject':str(subject),'kind':kind,'id':str(last.id),'at':last.created_at.isoformat()})
            self._audit(c,principal,subject,'memory.list',None,request_id,'succeeded')
            return items,next_cursor

    def search(self,principal,subject,query,request_id,limit=20,as_of=None):
        from model_gateway import provider_from_env
        limit=max(1,min(limit,100));provider=self.provider or provider_from_env()
        with operation('memory.search',tenant_id=principal.tenant_id,subject_id=subject,request_id=request_id):
            with self._read(principal,subject,'memory.search',request_id) as c:
                c.execute(text("SET LOCAL statement_timeout='200ms'"))
                embedding=vector_literal(provider.embed(query or 'profile'))
                # Union of FTS and vector candidates; fixed policy and stable UUID tie-break.
                rows=c.execute(text('SELECT '+FIELDS+""",ts_rank_cd(i.search_document,plainto_tsquery('english',:query)) lexical,
                    (1-(i.embedding <=> CAST(:embedding AS vector))) semantic
                    FROM """+FROM+""" WHERE """+ACTIVE+""" AND i.state='ready'
                    AND (i.search_document @@ plainto_tsquery('english',:query)
                      OR (i.embedding <=> CAST(:embedding AS vector)) < .95)
                    ORDER BY (ts_rank_cd(i.search_document,plainto_tsquery('english',:query))+1-(i.embedding <=> CAST(:embedding AS vector))) DESC,m.id
                    LIMIT 200"""),{'subject':subject,'query':query,'embedding':embedding}).mappings().all()
                reference=as_of or datetime.now(timezone.utc)
                def rank(row):
                    recency=1/(1+max(0,(reference-row['created_at']).total_seconds())/86400)
                    relevance=.5*min(1,row['lexical']*10)+.5*max(0,row['semantic'])
                    # Reinforcement is cited source count; it is independent of read traffic.
                    reinforcement=min(1,len(row['source_episode_ids'])/5)
                    return .6*relevance+.15*row['importance']+.1*row['confidence']+.1*recency+.05*reinforcement
                rows=sorted(rows,key=lambda row:(-rank(row),str(row['id'])))[:limit]
                self._audit(c,principal,subject,'memory.search',None,request_id,'succeeded')
                return [MemoryResource(**row) for row in rows]

    def profile(self,principal,subject,request_id):
        with self._read(principal,subject,'memory.profile',request_id) as c:
            rows=c.execute(text('SELECT '+FIELDS+' FROM '+FROM+' WHERE '+ACTIVE+" AND m.kind IN ('core','profile') ORDER BY v.importance DESC,m.id LIMIT 20"),{'subject':subject}).mappings().all()
            self._audit(c,principal,subject,'memory.profile',None,request_id,'succeeded')
            return [MemoryResource(**row) for row in rows]
