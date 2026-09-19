from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException

from contracts.models import AddMemoryRequest, CaptureEpisodeRequest, MemoryResource


@dataclass(frozen=True)
class CaptureResult:
    episode: object
    job: object
    replayed: bool


class IdempotencyConflict(Exception):
    pass


class MemoryService:
    def __init__(self, repository):
        self.repository = repository

    def capture_episode(self, principal, request: CaptureEpisodeRequest, request_id: str) -> CaptureResult:
        try:
            return self.repository.capture_episode(principal, request, request_id)
        except IdempotencyConflict:
            raise HTTPException(status_code=409, detail={"code": "idempotency_key_reused"}) from None
        except PermissionError:
            raise HTTPException(status_code=404, detail={"code": "resource_not_found"}) from None

    def add_memory(self, principal, request: AddMemoryRequest, request_id: str) -> MemoryResource:
        try:
            return self.repository.add_memory(principal, request, request_id)
        except PermissionError:
            raise HTTPException(status_code=404, detail={"code": "resource_not_found"}) from None

    def get_memory(self, principal, memory_id: UUID, request_id: str) -> MemoryResource:
        memory = self.repository.get_memory(principal, memory_id, request_id)
        if memory is None:
            raise HTTPException(status_code=404, detail={"code": "resource_not_found"})
        return memory
