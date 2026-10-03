import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine

from contracts.models import (
    AddMemoryRequest,
    CaptureEpisodeRequest,
    EpisodeAccepted,
    MemoryCreated,
)
from memory_domain.service import MemoryService
from persistence.repositories import Repository

from .auth import Principal, principal_from_headers
from observability import operation, extract_context
from opentelemetry import context, propagate


def create_app(database_url: str | None = None) -> FastAPI:
    engine = create_engine(database_url or os.environ["DATABASE_URL"])
    repository = Repository(engine)
    from memory_domain.context import ContextComposer
    composer=ContextComposer(repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        composer.close()
        engine.dispose()

    from contracts.models import APIErrorEnvelope
    app = FastAPI(title="Memory System", version="1.0.0", lifespan=lifespan,
        responses={status:{"model":APIErrorEnvelope} for status in [401,404,409,422,503]})
    app.state.repository=repository
    app.state.database_engine=engine
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(HTTPException)
    async def http_error(request:Request,error:HTTPException):
        code=error.detail.get('code','request_failed') if isinstance(error.detail,dict) else 'request_failed'
        return JSONResponse(status_code=error.status_code,content={'error':{'code':code,'request_id':request.state.request_id}},headers=error.headers)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request:Request,error:RequestValidationError):
        return JSONResponse(status_code=422,content={'error':{'code':'invalid_request','request_id':request.state.request_id}})

    from contracts.models import PolicyRejected
    @app.exception_handler(PolicyRejected)
    async def policy_rejected(request: Request,error: PolicyRejected):
        return JSONResponse(status_code=422,content={"error":{"code":str(error),"request_id":request.state.request_id}})


    @app.middleware("http")
    async def request_ids(request: Request, call_next):
        try:
            request_id = str(UUID(request.headers.get("X-Request-ID", "")))
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        token=context.attach(extract_context(dict(request.headers)))
        try:
            with operation("memory.api",request_id=request_id):
                response = await call_next(request)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"error": {"code": "service_unavailable", "request_id": request_id}},
                headers={"X-Request-ID": request_id},
            )
        finally:
            context.detach(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.post(
        "/v1/episodes",
        response_model=EpisodeAccepted,
        status_code=202,
        summary="Durably capture an episode for asynchronous formation",
    )
    def capture_episode(
        body: CaptureEpisodeRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(principal_from_headers),
    ) -> EpisodeAccepted:
        result = MemoryService(repository).capture_episode(principal, body, request.state.request_id)
        response.status_code = 200 if result.replayed else 202
        return EpisodeAccepted(episode=result.episode, job=result.job, request_id=request.state.request_id)

    @app.post(
        "/v1/memories",
        response_model=MemoryCreated,
        status_code=201,
        summary="Synchronously persist an explicit memory",
    )
    def add_memory(
        body: AddMemoryRequest,
        request: Request,
        principal: Principal = Depends(principal_from_headers),
    ) -> MemoryCreated:
        memory = MemoryService(repository).add_memory(principal, body, request.state.request_id)
        return MemoryCreated(memory=memory, request_id=request.state.request_id)

    from fastapi import Query, HTTPException
    from contracts.models import MemoryPage,ContextRequest,ContextEnvelope,CursorError
    @app.exception_handler(CursorError)
    async def invalid_cursor(request:Request,error:CursorError):
        return JSONResponse(status_code=400,content={'error':{'code':'invalid_cursor','request_id':request.state.request_id}})

    @app.get('/v1/memories',response_model=MemoryPage)
    def list_memories(request:Request,subject_id:UUID,limit:int=Query(20,ge=1,le=100),cursor:str|None=Query(None,max_length=2048),kind:str|None=None,principal:Principal=Depends(principal_from_headers)):
        try:items,next_cursor=repository.list_memories(principal,subject_id,request.state.request_id,limit,cursor,kind)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return MemoryPage(items=items,next_cursor=next_cursor,limit=limit,request_id=request.state.request_id)

    @app.get('/v1/search',response_model=MemoryPage)
    def search(request:Request,subject_id:UUID,q:str=Query(...,min_length=1,max_length=512),limit:int=Query(20,ge=1,le=100),principal:Principal=Depends(principal_from_headers)):
        try:items=repository.search(principal,subject_id,q,request.state.request_id,limit)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return MemoryPage(items=items,limit=limit,ranking_version='hybrid-v1',request_id=request.state.request_id)

    @app.post('/v1/context',response_model=ContextEnvelope)
    def context_build(body:ContextRequest,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:return composer.build(principal,body,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None

    @app.get("/v1/memories/{memory_id}", response_model=MemoryCreated)
    def get_memory(
        memory_id: UUID,
        request: Request,
        principal: Principal = Depends(principal_from_headers),
    ) -> MemoryCreated:
        memory = MemoryService(repository).get_memory(principal, memory_id, request.state.request_id)
        return MemoryCreated(memory=memory, request_id=request.state.request_id)

    from contracts.models import JobEnvelope
    @app.get('/v1/jobs/{job_id}',response_model=JobEnvelope)
    def job_status(job_id:UUID,request:Request,principal:Principal=Depends(principal_from_headers)):
        result=repository.job_status(principal,job_id,request.state.request_id)
        if result is None:
            from fastapi import HTTPException
            raise HTTPException(404,detail={'code':'resource_not_found'})
        return JobEnvelope(job=result,request_id=request.state.request_id)

    from contracts.models import CorrectMemoryRequest,ForgetMemoryRequest,HistoryEnvelope,ErasureEnvelope,SourcesEnvelope,VersionConflict
    @app.exception_handler(VersionConflict)
    async def version_conflict(request:Request,error:VersionConflict):
        return JSONResponse(status_code=409,content={'error':{'code':'version_conflict','request_id':request.state.request_id}})

    @app.patch('/v1/memories/{memory_id}',response_model=MemoryCreated)
    def correct(memory_id:UUID,body:CorrectMemoryRequest,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:result=repository.correct(principal,memory_id,body,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return MemoryCreated(memory=result,request_id=request.state.request_id)

    @app.post('/v1/memories/{memory_id}/forget',response_model=ErasureEnvelope,status_code=202)
    def forget(memory_id:UUID,body:ForgetMemoryRequest,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:result=repository.forget(principal,memory_id,body,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return ErasureEnvelope(erasure=result,request_id=request.state.request_id)

    @app.get('/v1/memories/{memory_id}/history',response_model=HistoryEnvelope)
    def history(memory_id:UUID,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:return repository.history(principal,memory_id,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None

    @app.get('/v1/memories/{memory_id}/sources',response_model=SourcesEnvelope)
    def sources(memory_id:UUID,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:return repository.sources(principal,memory_id,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None

    @app.get('/v1/memories/{memory_id}/erasure',response_model=ErasureEnvelope)
    def erasure_status(memory_id:UUID,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:result=repository.erasure_status(principal,memory_id,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return ErasureEnvelope(erasure=result,request_id=request.state.request_id)

    @app.post('/v1/memories/{memory_id}/erasure/retry',response_model=ErasureEnvelope,status_code=202)
    def retry_erasure(memory_id:UUID,body:ForgetMemoryRequest,request:Request,principal:Principal=Depends(principal_from_headers)):
        try:result=repository.retry_erasure(principal,memory_id,body,request.state.request_id)
        except PermissionError:raise HTTPException(404,detail={'code':'resource_not_found'}) from None
        return ErasureEnvelope(erasure=result,request_id=request.state.request_id)

    return app


app = create_app() if os.getenv("DATABASE_URL") else FastAPI(title="Memory System", version="1.0.0")
