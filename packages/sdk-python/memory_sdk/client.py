from uuid import UUID,uuid4
from urllib.parse import urlparse
import httpx
from pydantic import ValidationError
from contracts.models import (CaptureEpisodeRequest,AddMemoryRequest,CorrectMemoryRequest,ForgetMemoryRequest,ContextRequest,
    EpisodeAccepted,MemoryCreated,MemoryPage,ContextEnvelope,JobEnvelope,HistoryEnvelope,SourcesEnvelope,ErasureEnvelope,APIErrorEnvelope)

class MemoryAPIError(Exception):
    def __init__(self,status,code,request_id):
        self.status=status;self.code=code;self.request_id=request_id;self.retryable=status>=500
        super().__init__(f'Memory API {status}: {code} (request {request_id})')
class MemoryTransportError(Exception):
    def __init__(self,request_id):
        self.request_id=request_id;super().__init__('Memory transport unavailable (request '+request_id+')')
class MemoryContractError(Exception):
    def __init__(self,request_id):
        self.request_id=request_id;super().__init__('Memory response contract mismatch (request '+request_id+')')

class MemoryClient:
    def __init__(self,token:str,base_url='http://127.0.0.1:8000',timeout=5.0,client=None):
        url=urlparse(str(client.base_url) if client is not None else base_url)
        if url.scheme!='https' and not (url.scheme=='http' and url.hostname in {'127.0.0.1','localhost','::1'}):
            raise ValueError('HTTPS is required except for local development')
        self.client=client or httpx.Client(base_url=base_url,timeout=timeout,follow_redirects=False)
        self.owns_client=client is None;self.token=token
    def close(self):
        if self.owns_client:self.client.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def _call(self,method,path,model,body=None,params=None,request_id=None):
        rid=str(UUID(str(request_id))) if request_id else str(uuid4())
        headers={'Authorization':'Bearer '+self.token,'X-Request-ID':rid}
        try:response=self.client.request(method,path,json=body.model_dump(mode='json') if body else None,params=params,headers=headers,follow_redirects=False)
        except httpx.TransportError:raise MemoryTransportError(rid) from None
        try:returned_id=str(UUID(response.headers.get('X-Request-ID',rid)))
        except ValueError:returned_id=rid
        try:data=response.json()
        except ValueError:raise MemoryContractError(returned_id) from None
        if not response.is_success:
            try:error=APIErrorEnvelope.model_validate(data).error
            except ValidationError:raise MemoryContractError(returned_id) from None
            raise MemoryAPIError(response.status_code,error.code,str(error.request_id))
        try:return model.model_validate(data)
        except ValidationError:raise MemoryContractError(returned_id) from None
    def create_episode(self,request:CaptureEpisodeRequest,request_id:UUID|None=None)->EpisodeAccepted:
        return self._call('POST','/v1/episodes',EpisodeAccepted,request,request_id=request_id)
    def add_memory(self,request:AddMemoryRequest,request_id:UUID|None=None)->MemoryCreated:
        return self._call('POST','/v1/memories',MemoryCreated,request,request_id=request_id)
    def get_memory(self,memory_id:UUID,request_id:UUID|None=None)->MemoryCreated:
        return self._call('GET',f'/v1/memories/{memory_id}',MemoryCreated,request_id=request_id)
    def list_memories(self,subject_id:UUID,limit=20,cursor=None,kind=None)->MemoryPage:
        params={'subject_id':str(subject_id),'limit':limit}
        if cursor:params['cursor']=cursor
        if kind:params['kind']=kind
        return self._call('GET','/v1/memories',MemoryPage,params=params)
    def search(self,subject_id:UUID,query:str,limit=20)->MemoryPage:
        return self._call('GET','/v1/search',MemoryPage,params={'subject_id':str(subject_id),'q':query,'limit':limit})
    def build_context(self,request:ContextRequest)->ContextEnvelope:
        return self._call('POST','/v1/context',ContextEnvelope,request)
    def update_memory(self,memory_id:UUID,request:CorrectMemoryRequest)->MemoryCreated:
        return self._call('PATCH',f'/v1/memories/{memory_id}',MemoryCreated,request)
    def forget_memory(self,memory_id:UUID,request:ForgetMemoryRequest)->ErasureEnvelope:
        return self._call('POST',f'/v1/memories/{memory_id}/forget',ErasureEnvelope,request)
    def job_status(self,job_id:UUID)->JobEnvelope:
        return self._call('GET',f'/v1/jobs/{job_id}',JobEnvelope)
    def history(self,memory_id:UUID)->HistoryEnvelope:
        return self._call('GET',f'/v1/memories/{memory_id}/history',HistoryEnvelope)
    def sources(self,memory_id:UUID)->SourcesEnvelope:
        return self._call('GET',f'/v1/memories/{memory_id}/sources',SourcesEnvelope)
    def erasure_status(self,memory_id:UUID)->ErasureEnvelope:
        return self._call('GET',f'/v1/memories/{memory_id}/erasure',ErasureEnvelope)
    def retry_erasure(self,memory_id:UUID,request:ForgetMemoryRequest)->ErasureEnvelope:
        return self._call('POST',f'/v1/memories/{memory_id}/erasure/retry',ErasureEnvelope,request)
