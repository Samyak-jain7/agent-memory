from concurrent.futures import ThreadPoolExecutor,TimeoutError
from contextvars import copy_context
from threading import BoundedSemaphore
from contracts.models import ContextEnvelope
from observability import operation

class ContextComposer:
    def __init__(self,repository):
        self.repository=repository
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='memory-context')
        self.slots=BoundedSemaphore(8)
    def close(self):self.pool.shutdown(wait=False,cancel_futures=True)
    def build(self,principal,request,request_id):
        def retrieve():
            profile=self.repository.profile(principal,request.subject_id,request_id)
            try:return profile,self.repository.search(principal,request.subject_id,request.query,request_id),False
            except PermissionError:raise
            except Exception:return [],[],True
        with operation('memory.context',request_id=request_id,subject_id=request.subject_id):
            if not self.slots.acquire(blocking=False):profile,memories,degraded=[],[],True
            else:
                future=self.pool.submit(copy_context().run,retrieve)
                future.add_done_callback(lambda _:self.slots.release())
                try:profile,memories,degraded=future.result(timeout=request.deadline_ms/1000)
                except PermissionError:raise
                except Exception:
                    future.cancel();profile,memories,degraded=[],[],True
            lines=[];core=[];ids=[];citations={};used=0;truncated=False
            seen=set()
            for is_core,items in [(True,profile),(False,memories)]:
                for memory in items:
                    if memory.id in seen:continue
                    seen.add(memory.id)
                    line=f"[{memory.id}; sources {','.join(str(s) for s in memory.source_episode_ids)}] {memory.content}"
                    size=len(line.encode())+(1 if lines else 0)
                    if used+size>request.token_budget:truncated=True;continue
                    used+=size;lines.append(line);ids.append(memory.id);citations[str(memory.id)]=memory.source_episode_ids
                    if is_core:core.append(memory.id)
            if degraded:
                with operation('memory.context.degraded',request_id=request_id,outcome='degraded'):pass
            return ContextEnvelope(text='\n'.join(lines),core_profile=core,memory_ids=ids,citations=citations,
                token_budget=request.token_budget,budget_used=used,degraded=degraded,truncated=truncated,request_id=request_id)
