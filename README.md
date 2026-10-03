# Agent Memory

Tenant-isolated episode capture and provenance-linked memory for AI agents.

Development uses Python 3.11+, PostgreSQL 17 with pgvector, and the deterministic offline model fixture. The fixture is for local verification, not a production-quality semantic model. Live provider calls are not exercised by offline tests.

Worker: `python -m apps.worker.main --tenant TENANT_UUID` (or `--once`). API: `uvicorn apps.api.main:app` with `DATABASE_URL` and `MEMORY_AUTH_SECRET` configured. Provision tenants, actors, subjects, sessions and actor-subject grants through trusted administration; callers cannot grant their own access.

The worker uses tenant-scoped PostgreSQL leases, fencing tokens, atomic result writes, retries and dead-letter states. Capture does not wait for formation. Job state is available at `/v1/jobs/{id}`. Candidate policy rejects likely secrets and low-confidence facts; rejected outcomes contain codes, never candidate payloads.

Development tests require a dedicated disposable database explicitly named `memory_test...`: `TEST_DATABASE_URL=... PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/integration`. Tests delete its public schema. Never target an existing application database.

Model configuration: default `MODEL_PROVIDER=offline`. Optional `openai` requires operator-approved `MODEL_API_KEY`, `EXTRACTION_MODEL`, and `EMBEDDING_MODEL`; configure these only after approving provider costs and data-use/retention terms. No live model calls or deployment costs are part of local verification.

Consent is separate from actor access: `subjects.memory_consent` defaults false and trusted administration must enable it before explicit or observed memory persistence. Revocation during formation rejects candidates. Public request IDs accept canonical UUIDs; arbitrary caller text is replaced with a generated UUID before telemetry or audit storage. Traces and duration/call metrics exclude payloads. Live model usage tokens are recorded when returned; monetary cost remains explicitly unknown until approved prices are configured.
