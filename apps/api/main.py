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


def create_app(database_url: str | None = None) -> FastAPI:
    engine = create_engine(database_url or os.environ["DATABASE_URL"])
    repository = Repository(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="Memory System", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_ids(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"error": {"code": "service_unavailable", "request_id": request_id}},
                headers={"X-Request-ID": request_id},
            )
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

    @app.get("/v1/memories/{memory_id}", response_model=MemoryCreated)
    def get_memory(
        memory_id: UUID,
        request: Request,
        principal: Principal = Depends(principal_from_headers),
    ) -> MemoryCreated:
        memory = MemoryService(repository).get_memory(principal, memory_id, request.state.request_id)
        return MemoryCreated(memory=memory, request_id=request.state.request_id)

    return app


app = create_app() if os.getenv("DATABASE_URL") else FastAPI(title="Memory System", version="1.0.0")
