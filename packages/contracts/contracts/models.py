from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1)


class CaptureEpisodeRequest(BaseModel):
    session_id: UUID
    subject_id: UUID
    messages: list[Message] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class SourceInput(BaseModel):
    episode_id: UUID


class AddMemoryRequest(BaseModel):
    subject_id: UUID
    kind: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    source: SourceInput
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)


class Resource(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    subject_id: UUID
    created_at: datetime


class EpisodeResource(Resource):
    session_id: UUID
    actor_id: UUID


class JobResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    episode_id: UUID
    state: Literal["pending", "leased", "succeeded", "rejected", "retryable_failure", "dead_letter"]
    created_at: datetime


class MemoryResource(Resource):
    kind: str
    lifecycle_state: Literal["active"]
    version_id: UUID
    content: str
    origin: Literal["explicit", "observed", "derived"]
    confidence: float
    importance: float
    actor_id: UUID
    source_episode_ids: list[UUID]


class EpisodeAccepted(BaseModel):
    episode: EpisodeResource
    job: JobResource
    request_id: str


class MemoryCreated(BaseModel):
    memory: MemoryResource
    request_id: str


class JobStatus(JobResource):
    attempts: int
    completed_at: datetime | None
    error_code: str | None
    outcomes: list[dict]

class JobEnvelope(BaseModel):
    job: JobStatus
    request_id: str


class PolicyRejected(Exception):
    pass
