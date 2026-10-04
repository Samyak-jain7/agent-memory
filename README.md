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

## Operator console

Run `npm --prefix apps/web ci`, then `npm --prefix apps/web run dev`.
The server uses `API_URL` (default `http://127.0.0.1:8000`); remote APIs require HTTPS.
Paste an expiring access token and a granted subject UUID. Tokens stay in React memory and
are cleared on reload; the console never stores credentials in browser storage.
Source evidence requires a separate inspection grant. Correction requires an existing supporting
episode for the same subject. The confirmation dialog describes immediate suppression,
asynchronous erasure, and shared-evidence retention. Audit detail is bounded to 100 latest events.
Run the API without Uvicorn access logs so search text is not logged in request URLs.
`npm --prefix apps/web test -- --run` builds Next.js and runs actual Chromium keyboard
lifecycle tests at 1280px and 390px. Browser fixtures isolate UI behavior; Python contract
and integration tests separately verify the database and public API lifecycle.


## Local setup and release verification

This repository is a verified development baseline. The offline extraction/embedding provider
is a fixture; live provider quality, approved numeric targets, and deployed encryption remain
production blockers. No production deployment or paid API usage is performed by these commands.

1. Create a Python 3.11+ virtual environment and run `pip install -e . pytest`.
2. Copy `.env.example` to `.env` and supply local passwords plus a random signing secret.
   The application does not load dotenv automatically: export these values in your shell.
3. Run `docker compose up -d postgres`, then `python -m scripts.database migrate` with
   the trusted `ADMIN_DATABASE_URL`. Migration checksums and an advisory lock prevent races.
4. Set a random hexadecimal `MEMORY_DB_PASSWORD` (at least 16 characters) and run
   `python -m scripts.database runtime-role`. It creates a restricted runtime LOGIN role
   with membership in `memory_app`,
   no schema ownership, no superuser/BYPASSRLS rights, and no direct table privileges.
   Set `DATABASE_URL` to that account (URL-encode non-hexadecimal passwords). Admin credentials are only for migrations/provisioning.
5. Run `python -m scripts.database bootstrap --development --consent --inspect-sources`
   to create an isolated synthetic development tenant/actor/subject/session. Omit the consent
   or source flag to retain default-deny behavior. Save the printed UUIDs locally.
6. Run `python -m scripts.issue_token --tenant UUID --actor UUID` for a one-hour token.
   Tokens are credentials: paste only into your local operator console or SDK session.
7. Start `uvicorn apps.api.main:app --no-access-log`, `python -m apps.worker.main --tenant UUID`,
   `python -m apps.worker.erase --tenant UUID`, and `npm --prefix apps/web run dev`.
   `MEMORY_TELEMETRY=console` enables the API SDK's local structured trace and metric export;
   production requires a reviewed exporter and verified collection. Set the same
   `MEMORY_TELEMETRY=console` for worker and erasure hosts.

Use a separate PostgreSQL17 pgvector container/database named `memory_test` for verification.
Set `TEST_DATABASE_URL` to it and `BACKUP_TEST_CONTAINER` to that container's exact ID/name.
The tests delete only that explicitly designated disposable schema. Never point them at real data.

```
python -m pytest tests/integration tests/contract tests/browser tests/security tests/system
npm --prefix apps/web ci
(cd apps/web && npx --no-install playwright install chromium)
npm --prefix apps/web test -- --run
python evals/run.py
python scripts/smoke.py
python -m deploy.readiness deploy/production.example.json
```

The last command intentionally exits nonzero: approvals and platform evidence are absent.
Do not fill its approval fields merely to bypass the gate. Development golden thresholds are
explicit regression baselines, not approved live-model quality targets. The versioned evaluation
runs real database extraction, hybrid retrieval and context citations, reporting precision,
relevance, citation correctness and observed correction need. The smoke flow uses a real local
HTTP server and the public SDK with synthetic subjects. CI repeats these checks on an empty
pgvector service. Backup testing performs real `pg_dump`/restore into a uniquely named disposable
database, reruns isolation/correction/suppression, then removes only that database.

Production launch requires approved values and measurements for latency, throughput, availability,
retention, erasure, backup expiry and quality, verified HTTPS and database `verify-full` TLS,
encrypted storage/backups with platform evidence, approved provider data policy and live evaluation,
and verified telemetry export. Environment examples and test fixture declarations do not prove
these platform properties. Backups must expire according to the approved erasure policy.

For production, start API and tenant workers only through `python -m scripts.serve api|worker|erase ...`
with `MEMORY_ENV=production` and `PRODUCTION_READINESS_FILE` pointing to reviewed platform evidence.
The startup guard also requires an actual `verify-full` database connection with active TLS,
a runtime role without superuser/BYPASSRLS rights, a strong signing secret, the evaluated live
provider, telemetry configuration, and retention matching the reviewed measurements.
The API factory enforces this guard even when started directly.
Use `python -m scripts.reindex --tenant UUID` to reindex pending legacy current versions; it
locks active rows, scopes to one tenant, and commits index updates atomically.

`python -m scripts.privacy_scan` checks publishable files and reachable Git history for
credential formats and tracked environment secrets without printing matched values. It is
a bounded format check, not proof that arbitrary prose contains no confidential information.

## Authenticated backup artifacts

`python -m scripts.encrypted_backup --output /secure/backup.ambak --deployment-id ID
--release-sha COMMIT --retention-seconds APPROVED_VALUE` runs the installed PostgreSQL
`pg_dump` client using `ADMIN_DATABASE_URL` and writes only AES-256-GCM authenticated
ciphertext with mode 0600. The operator must provide `BACKUP_ENCRYPTION_KEY` as a
base64-encoded 32-byte key through an approved secret manager; this project never creates
or persists a production backup key automatically. Keep the key out of command arguments.
Production database connections require `verify-full` TLS. The artifact authenticates its
manifest, release/deployment identity and plaintext digest; fresh random nonces protect
independent artifacts. Tampering, truncation and wrong keys fail closed.

The utility deliberately caps in-memory dumps at 256 MiB; larger databases require an
approved streaming backup service. A manifest expiry is policy metadata, not deletion:
the selected platform must enforce and verify storage encryption, access control, expiry,
key rotation and recovery. Never restore an old backup into a serving database without
reapplying every suppression/erasure that occurred after the backup; restore into quarantine
until that reconciliation is verified. The synthetic restore test now decrypts an
authenticated artifact in memory before real PostgreSQL restore and isolation checks.
This proves the artifact encryption/restore mechanism, not a deployed backup policy.

Production evidence must identify `deployment_id` and a 40-character `release_sha`; runtime `MEMORY_DEPLOYMENT_ID` and `MEMORY_RELEASE_SHA` must match. `evidence_receipts` must contain storage, backup, restore, provider, telemetry, retention, erasure, backup_expiry, deployment, and approval receipts. Each includes matching deployment/release, UTC `verified_at` within seven days, `artifact_path` relative to the readiness document directory, `artifact_sha256`, and named verifier `provenance`. Artifacts are limited to 1 MiB and must remain within that directory; hashes are checked before startup. Keep private evidence outside the source repository. Receipt validation is an integrity check; real platform verification remains required. See [proposed policy defaults](deploy/policy-proposal.md) for unapproved recommendations.

Release packaging is checked with `python scripts/verify_distribution.py` (requires `uv`): it builds a wheel from temporary clean inputs, installs it in a new environment, and imports API, workers, SDK and packaged migrations outside the checkout. CI also scans publishable files and complete reachable Git history for credential patterns, builds the Docker image, and imports its installed package as UID 10001 with networking disabled and a read-only filesystem. These checks complement lifecycle and browser tests; they do not deploy the application.

### Gemini Flash extraction and Jev verification

Set `EXTRACTION_PROVIDER=gemini` to select Google AI Studio's Gemini Developer API extractor. The pinned default is `EXTRACTION_MODEL=gemini-3.5-flash-lite`, a stable model intended for simple extraction and supporting structured output. `EXTRACTION_MODEL` may select another explicitly reviewed stable Flash model; moving `latest` and preview aliases are rejected. Requests use the stateless REST `generateContent` endpoint with a JSON Schema, no tools, and a 4096-token output bound. Local validation rejects malformed, truncated, blocked, oversized, or unknown-field output. The source messages remain untrusted data.

Set `MEMORY_VERIFIER=jev` and supply `TYPESAFE_API_KEY` securely to have TypeSafe Jev evaluate candidates before persistence. The pinned default `JEV_MODEL=jev-1.13.0` can be changed to another reviewed exact version. Three parallel Noul questions per candidate check source support, usefulness, and sensitive content. Provisional development acceptance thresholds are 0.95, 0.80, and 0.99 respectively; these require live golden-set evaluation and approval before production. Rejected or uncertain candidates are excluded; a verifier failure prevents persistence and enters the existing bounded job-failure flow. Consent is checked again before verification, and sensitive outputs are filtered locally before transmission to Jev.

Extraction and verification are independent of `MODEL_PROVIDER`, which continues to select embeddings. The default offline embeddings remain a development fixture. Selecting Gemini extraction does not silently switch embedding providers or introduce another live service. A production embedding model and evaluation remain pending.

Free tier only: do not enable billing, add payment methods, buy credits, or use a billed project/account. No paid fallback or automatic HTTP retry is implemented. Both providers require a separate, private account/model review file before their first request (`GEMINI_FREE_TIER_FILE`, `TYPESAFE_FREE_TIER_FILE`). The review must be obtained from current account evidence and remaining free quota; a key alone is insufficient. Each JSON file contains exactly:

```json
{
  "provider": "gemini",
  "model": "gemini-3.5-flash-lite",
  "account_id": "YOUR_VERIFIED_FREE_PROJECT_ID",
  "billing_enabled": false,
  "remaining_requests": 1,
  "verified_at": "ACTUAL_UTC_TIMESTAMP",
  "expires_at": "UTC_TIMESTAMP_WITHIN_24_HOURS",
  "evidence": "Reference to the actual account/model/quota verification"
}
```

For Jev use `provider: "typesafe"`, the verified TypeSafe account and exact Jev version. Do not create these files from guesses or set `billing_enabled` false for a paid account. Files are bounded, strictly parsed, matched to provider/model and expire within 24 hours. The reviewed request budget is per process, not a provider billing cap; it cannot establish that an arbitrary credential belongs to a free account or coordinate quotas across processes. Actual provider-side billing must remain disabled and account/key binding must be verified by the operator. HTTP 402/429 stops that adapter instance, and the affected job is not automatically retried; restart only after reviewing quota. Missing/stale evidence fails before transmission.

Google's free tier may use prompts and responses to improve its products. Use synthetic evaluation data until the owner has approved real-data transmission and provider data policies. TypeSafe's public model page lists input-token charges; free account eligibility and remaining grants must be checked in the console before any request. No live account/model quality or free-quota verification is claimed by the mocked tests. Keep keys and private review files outside source control and supply secrets only through the operator's secure local environment.

Official references checked 2026-10-04: [Gemini Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [REST generation contract](https://ai.google.dev/api/generate-content), [TypeSafe API](https://docs.typesafe.ai/api), [Jev versions](https://docs.typesafe.ai/models).


## Supervised formation

Set `MEMORY_FORMATION_MODE=supervised` for a supervised development pilot. Extraction produces suggestions only; Jev is not called and no suggestion enters retrieval or context until a human approves it. Model confidence is uncalibrated, not a measured correctness probability. Automatic mode remains available explicitly with unchanged provisional Jev thresholds; calibration is unresolved.

Trusted administration must grant `can_review_memory` and `can_inspect_sources` for the actor and subject, with active identities and memory consent. Development bootstrap accepts `--review-memory --inspect-sources --consent`; all grants default false. The operator console lists pending suggestions, shows filtered source evidence, and requires explicit approve/reject confirmation. API routes are `GET /v1/suggestions?subject_id=...`, `GET /v1/suggestions/{id}/sources`, and `POST /v1/suggestions/{id}/approve` or `/reject` with `{"confirm":true}`. Repeating the same decision returns the same result; an opposing decision conflicts. Approval records the human actor as explicit origin and preserves the source episode.

Unreviewed suggestions become inaccessible and unapprovable after seven days. Worker passes and review-list/decision requests clear expired suggestion payloads; run the worker continuously for scheduled cleanup. Rejected and approved suggestion payloads are cleared immediately; approved content lives in the existing versioned memory lifecycle. Sensitive inputs/candidates are conservatively excluded before extraction/review, and source display is filtered. Pattern filtering cannot guarantee detection of every sensitive statement; use synthetic data until a real-data policy is approved. Forgetting invalidates pending suggestions sharing its evidence immediately, and source erasure invalidates them as well. Expiry concerns suggestion content, not the separately governed immutable episode or backup retention policy.
