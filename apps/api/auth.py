import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from uuid import UUID
from fastapi import Depends,HTTPException,Request
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from observability import operation

@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    actor_id: UUID

def issue_token(tenant_id:UUID,actor_id:UUID,ttl_seconds=3600,secret=None):
    if not 1<=ttl_seconds<=86400:raise ValueError('Token lifetime must be at most one day')
    secret=(secret or os.environ['MEMORY_AUTH_SECRET']).encode()
    now=int(time.time());payload={'tenant':str(tenant_id),'actor':str(actor_id),'iat':now,'exp':now+ttl_seconds,'v':1}
    encoded=base64.urlsafe_b64encode(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
    return encoded+'.'+hmac.new(secret,encoded.encode(),hashlib.sha256).hexdigest()

def _record_failure(request):
    from sqlalchemy import text
    from uuid import uuid4
    with request.app.state.database_engine.begin() as c:
        c.execute(text('SET LOCAL ROLE memory_app'))
        c.execute(text("INSERT INTO authentication_events(id,request_id,action) VALUES (:id,:request,'authentication.denied')"),{'id':uuid4(),'request':request.state.request_id})

bearer=HTTPBearer(auto_error=False)
def principal_from_headers(request:Request,credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
    try:
        if credentials is None or credentials.scheme.lower()!='bearer' or len(credentials.credentials)>4096:raise ValueError()
        encoded,signature=credentials.credentials.split('.',1)
        secret=os.environ['MEMORY_AUTH_SECRET'].encode()
        expected=hmac.new(secret,encoded.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if not isinstance(payload,dict) or payload.get('v')!=1:raise ValueError()
        if type(payload['exp']) is not int or type(payload['iat']) is not int:raise ValueError()
        now=time.time()
        if payload['exp']<=now or payload['iat']>now+60 or not 0<payload['exp']-payload['iat']<=86400:raise ValueError()
        return Principal(UUID(payload['tenant']),UUID(payload['actor']))
    except (KeyError,ValueError,TypeError):
        with operation('memory.authentication.denied',request_id=request.state.request_id,outcome='denied'):
            try:_record_failure(request)
            except Exception:
                with operation('memory.authentication.audit_unavailable',outcome='unavailable'):pass
        raise HTTPException(401,detail={'code':'invalid_credentials'},headers={'WWW-Authenticate':'Bearer'}) from None

def not_found():return HTTPException(404,detail={'code':'resource_not_found'})
