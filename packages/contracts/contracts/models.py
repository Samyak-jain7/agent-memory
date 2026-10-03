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
    indexing_state: Literal["pending", "ready"] = "pending"
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


class MemoryPage(BaseModel):
    items: list[MemoryResource]
    next_cursor: str | None = None
    limit: int
    ranking_version: str | None = None
    request_id: str

class ContextRequest(BaseModel):
    subject_id: UUID
    query: str = Field(default='',max_length=512)
    token_budget: int = Field(default=2048,ge=32,le=32000)
    deadline_ms: int = Field(default=250,ge=50,le=2000)

class ContextEnvelope(BaseModel):
    text: str
    core_profile: list[UUID]
    memory_ids: list[UUID]
    citations: dict[str,list[UUID]]
    token_budget: int
    budget_used: int
    budget_unit: Literal['utf8-byte-upper-bound']='utf8-byte-upper-bound'
    degraded: bool
    truncated: bool
    ranking_version: str='hybrid-v1'
    request_id: str

class CursorError(Exception):
    pass


class CorrectMemoryRequest(BaseModel):
    expected_version_id: UUID
    content: str = Field(min_length=1,max_length=8000)
    source: SourceInput
    confidence: float = Field(ge=0,le=1)
    importance: float = Field(default=.5,ge=0,le=1)

class ForgetMemoryRequest(BaseModel):
    confirm: Literal[True]
    expected_version_id: UUID

class VersionResource(BaseModel):
    id: UUID
    content: str | None
    origin: str
    actor_id: UUID
    valid_from: datetime
    valid_to: datetime | None
    supersedes_version_id: UUID | None
    source_episode_ids: list[UUID]

class HistoryEnvelope(BaseModel):
    memory_id: UUID
    lifecycle_state: str
    versions: list[VersionResource]
    request_id: str

class ErasureResource(BaseModel):
    id: UUID
    memory_id: UUID
    state: Literal['pending','completed','blocked']
    available_at: datetime
    completed_at: datetime | None
    retained_shared_episodes: int
    error_code: str | None
    erasure_scope: str='memory_and_unshared_episode_content'

class ErasureEnvelope(BaseModel):
    erasure: ErasureResource
    request_id: str

class SourceEpisode(BaseModel):
    id: UUID
    created_at: datetime
    messages: list[Message]

class SourcesEnvelope(BaseModel):
    episodes: list[SourceEpisode]
    request_id: str

class VersionConflict(Exception):
    pass


class APIError(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    request_id: UUID

class APIErrorEnvelope(BaseModel):
    error: APIError


class AuditEvent(BaseModel):
    id: UUID
    actor_id: UUID
    subject_id: UUID | None
    action: str
    resource_id: UUID | None
    request_id: str
    outcome: str
    created_at: datetime

class AuditEnvelope(BaseModel):
    events: list[AuditEvent]
    request_id: str
