# Agent Memory

Tenant-isolated episode capture and provenance-linked memory for AI agents.

Development uses Python 3.11+, PostgreSQL 17 with pgvector, and the deterministic offline model fixture. The fixture is for local verification, not a production-quality semantic model. Live provider calls are not exercised by offline tests.

Worker: `python -m apps.worker.main --tenant TENANT_UUID` (or `--once`). API: `uvicorn apps.api.main:app` with `DATABASE_URL` and `MEMORY_AUTH_SECRET` configured. Provision tenants, actors, subjects, sessions and actor-subject grants through trusted administration; callers cannot grant their own access.

The worker uses tenant-scoped PostgreSQL leases, fencing tokens, atomic result writes, retries and dead-letter states. Capture does not wait for formation. Job state is available at `/v1/jobs/{id}`. Candidate policy rejects likely secrets and low-confidence facts; rejected outcomes contain codes, never candidate payloads.

Development tests require a dedicated disposable database explicitly named `memory_test...`: `TEST_DATABASE_URL=... PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/integration`. Tests delete its public schema. Never target an existing application database.

Model configuration: default `MODEL_PROVIDER=offline`. Optional `openai` requires operator-approved `MODEL_API_KEY`, `EXTRACTION_MODEL`, and `EMBEDDING_MODEL`; configure these only after approving provider costs and data-use/retention terms. No live model calls or deployment costs are part of local verification.

Consent is separate from actor access: `subjects.memory_consent` defaults false and trusted administration must enable it before explicit or observed memory persistence. Revocation during formation rejects candidates. Public request IDs accept canonical UUIDs; arbitrary caller text is replaced with a generated UUID before telemetry or audit storage. Traces and duration/call metrics exclude payloads. Live model usage tokens are recorded when returned; monetary cost remains explicitly unknown until approved prices are configured.

Retrieval: `/v1/memories?subject_id=...` uses signed keyset cursors bound to subject and filter. `/v1/search?subject_id=...&q=...` unions English full-text and vector candidates with deterministic `hybrid-v1` ranking (relevance, age, importance, confidence and source reinforcement). Index model identity is retained; default embeddings are a development fixture. Explicit writes fail atomically if embedding fails; migrated legacy versions remain `pending` until reindexed.

`POST /v1/context` returns cited text, profile IDs before retrieved IDs, budget usage, truncation and degraded state. The token budget is enforced using a conservative UTF-8 byte upper bound for the supplied text; metadata is not prompt text. Retrieval runs in a bounded executor and a deadline returns degraded context without waiting for a failed provider/database.

Corrections require the current version ID and source episode evidence, append a version, close the prior interval, and reject concurrent stale edits with `version_conflict`. History remains available to authorized subjects. Raw source inspection requires a separate `can_inspect_sources` grant, default false.

Forget requires explicit confirmation and the current version ID. Suppression commits immediately and excludes content from get, list, search, profile and context; suppressed history exposes metadata without payloads. A separate `python -m apps.worker.erase --tenant TENANT_UUID` process executes a narrowly scoped, audited erasure function. It removes structured payloads/indexes/source links and unshared raw episode content; shared evidence is retained and explicitly counted. Minimal tombstone/version/audit identifiers remain. Development retention delay defaults to zero; production must configure an approved retention and erasure policy before claiming readiness. Backups require their own approved expiry policy.

Erasure access revoked after forget transitions the job to `blocked` with a safe reason and audit event; the worker continues other eligible jobs. An authorized actor must explicitly confirm `/v1/memories/{id}/erasure/retry` with the unchanged version to reauthorize the blocked job. The initiating forget audit stays immutable; no automatic actor impersonation occurs.

Python SDK: `from memory_sdk import MemoryClient, CaptureEpisodeRequest, Message`. The SDK uses canonical Pydantic transport models shared with FastAPI; the published OpenAPI snapshot is derived from those models and contract-tested against the running app. It exposes capture, add/get/list/search/update/forget, context, job state, history, sources and erasure status/retry. Requests generate or preserve UUID request IDs; errors carry machine codes and request IDs without payloads. No hidden retries occur. HTTPS is required for non-loopback SDK endpoints.

Credentials are signed, tenant/actor-scoped, expiring bearer tokens (maximum one day), issued by trusted administration with `apps.api.auth.issue_token`. Tokens are not self-service grants; active actor and subject grants are checked by repositories. Invalid credential attempts are durably recorded without credential payloads.
