# Product specification — memory-system

> Status: draft. A coding agent must not implement product code until this specification is approved through Genesis.

## Objective

Build a production-grade, tenant-isolated full-stack memory platform for AI agents that durably captures episodes, asynchronously forms provenance-linked versioned memories, exposes API and Python SDK retrieval and bounded context composition, and provides operator inspection, correction, and forgetting controls.

## Problem

Agent developers need durable memory without putting extraction latency or failures on the response path. They need a small integration surface for capture, explicit memory management, retrieval, and context composition. Operators and memory subjects need to understand what is retained, where it came from, how it changed, and whether a correction or deletion took effect. The system must make tenant isolation, provenance, deletion, graceful degradation, and evaluation part of the first release rather than later hardening.

## Users

- Agent developer: integrates the Python SDK or HTTP API into an agent application.
- Agent runtime: captures episodes, adds explicit memories, searches memory, and requests bounded context.
- Tenant operator: inspects, searches, corrects, and forgets memories through the web console and reviews audit status.
- Memory subject: the person or entity described by memories and protected by tenant, consent, retention, and deletion policy.
- Service actor: an authenticated user or service account performing an operation for a tenant and, where allowed, a subject.
- Platform operator: deploys and observes the API, worker, database, and console without gaining an application path that bypasses tenant authorization.
- Formation worker: asynchronously extracts, evaluates, and writes memories from durable episodes.

## Desired outcomes

- Agent applications gain useful, cited, bounded memory context without depending on memory availability to answer a request.
- Captured interactions become searchable structured memories through observable, retryable asynchronous work.
- Operators can inspect and control retained memory, including provenance, history, correction, suppression, and erasure state.
- Every durable and observable boundary carries tenant, subject, actor, source, and request identity as applicable.
- Retrieval and extraction quality can be measured against versioned evaluation sets before release.

## Functional requirements

- FR-1: The system shall authenticate each API and console request, derive `tenant_id` from trusted credentials, and validate the requested `subject_id` and `actor_id` within that tenant before data access.
- FR-2: The system shall accept an episode for a session and atomically persist both the immutable episode and its initial formation job under an idempotency key.
- FR-3: Episode capture shall return durable episode and job identifiers plus an explicit processing state without waiting for extraction or evaluation.
- FR-4: A formation worker shall lease pending jobs, extract structured candidates, evaluate utility, confidence, sensitivity, and evidence, persist accepted candidates, record rejected candidates as job outcomes, and retry or dead-letter failures without duplicate memory versions.
- FR-5: An authenticated caller shall be able to add a structured explicit memory synchronously with kind, content, subject, and source evidence.
- FR-6: An authenticated caller shall be able to get one memory, list memories with cursor pagination and filters, and search active memories using tenant-scoped PostgreSQL full-text and pgvector candidates.
- FR-7: Retrieval shall apply a versioned deterministic ranking policy using relevance, recency, importance, confidence, and reinforcement, and shall return memory identifiers, source citations, and pagination or limit metadata.
- FR-8: An authenticated caller shall be able to request a bounded context package containing core profile state and ranked active memories within a caller-supplied or server-default token budget.
- FR-9: Context composition shall return a valid degraded result without retrieved memory when retrieval is unavailable or misses its deadline.
- FR-10: An authenticated caller shall be able to correct a memory by appending a new version, closing the prior validity interval, linking the superseded version and correction evidence, and retaining history.
- FR-11: An authenticated caller shall be able to forget a memory; the operation shall immediately suppress it from all reads and start an auditable policy-driven physical-erasure workflow.
- FR-12: An authenticated caller shall be able to query episode-formation job state and distinguish pending, leased, succeeded, rejected, retryable failure, and dead-letter outcomes.
- FR-13: The first-party Python SDK shall expose typed methods for episode creation, memory add/get/list/search/update/forget, context build, and job status while preserving API request IDs and machine-readable errors.
- FR-14: The web console shall provide tenant-scoped search and filters, memory detail, source evidence, version history, correction, forgetting with confirmation, job or deletion status, and relevant audit events.
- FR-15: The system shall record append-only audit events for memory writes, reads, corrections, suppression, erasure transitions, authorization failures, and administrative actions.

## Non-functional requirements

- NFR-1: Tenant isolation shall be enforced at the API authorization layer and PostgreSQL access layer; every repository read and write shall require tenant scope.
- NFR-2: Deleted or suppressed memories shall be excluded from every get, list, search, ranking, profile, and context path immediately after the forget transaction commits.
- NFR-3: Memory retrieval and formation failures shall not block the host agent's response path; degraded behavior shall be explicit and observable.
- NFR-4: Every structured memory version shall retain at least one source-evidence link, origin classification, confidence, creation time, and actor identity.
- NFR-5: Derived inference, when added in a future release, shall remain distinguishable from explicit or observed memory and shall never be silently promoted to fact.
- NFR-6: Conflicts and corrections shall create append-only versions and supersession links rather than destructive overwrites.
- NFR-7: Episode persistence and initial job scheduling shall be atomic. A future external workflow engine shall preserve this property through a transactional outbox or an equivalent proven boundary.
- NFR-8: API operations shall use versioned routes, stable resource envelopes, cursor pagination where lists can grow, machine-readable error codes, request IDs, and documented consistency semantics.
- NFR-9: Network traffic and persistent storage shall be encrypted using deployment-platform capabilities; secrets shall not be written to source, logs, traces, or audit payloads.
- NFR-10: Sensitive-data filtering hooks and consent-policy checks shall run before structured memory persistence, with policy outcomes observable without exposing filtered values.
- NFR-11: API and worker operations shall emit correlated OpenTelemetry traces, structured logs, and metrics for latency, errors, retries, degraded retrieval, corrections, suppression, erasure, model cost, and evaluation version.
- NFR-12: Database migrations shall be forward-tested, and backup plus restore shall be verified before a production release.
- NFR-13: Extraction and retrieval changes shall be evaluated against versioned golden sets and shall not pass release gates when an approved quality threshold regresses.
- NFR-14: The web console shall support keyboard operation, visible focus, semantic labels, error recovery, and responsive layouts for supported desktop and mobile widths.
- NFR-15: Numeric latency, throughput, availability, quality, retention, and erasure targets shall be approved before production launch; absence of approved values blocks a production-readiness claim.

## Trust and security boundaries

- Credentials are trusted only after API authentication. Tenant identity supplied in request bodies or query parameters is never authoritative.
- Tenant, subject, and actor are separate identities. A caller may act only within the credential tenant and only for authorized subjects.
- All repository operations are deny-by-default without tenant scope; PostgreSQL row-level or equivalent database policy is defense in depth, not a replacement for application authorization.
- Model providers receive only the minimum episode or candidate content needed for the configured operation. Provider retention and data-use terms must be approved before production use.
- Raw episodes are evidence and may contain sensitive content. Access is narrower than ordinary memory search and is audited.
- The operator console uses the same public authorization and domain rules as SDK clients; it does not access tables directly.
- Platform telemetry may carry identifiers and policy outcomes but must not carry raw secrets or unredacted filtered content.
- Suppression is immediate logical non-retrievability; physical erasure is a separate auditable workflow governed by retention and legal constraints.

## Constraints

- The first release is a modular monolith with independently runnable API, worker, and web-console processes.
- PostgreSQL is the system of record; pgvector and PostgreSQL full-text search provide initial retrieval.
- Initial asynchronous work uses PostgreSQL-backed leased jobs with retry metadata and dead-letter state behind a narrow queue boundary.
- The service and first SDK use Python with FastAPI, SQLAlchemy, Alembic, and Pydantic; the console uses Next.js; OpenAPI defines the transport contract.
- Memory identity and history use stable memory records plus append-only versions, validity intervals, supersession links, and many-to-many source evidence.
- The response path and memory-formation path remain independent.
- Genesis specification and plan approval are required before implementation; HumanLayer artifacts remain source evidence and are not reorganized or replaced.

## Non-goals

- MCP transport.
- Public, cross-tenant, or link-based memory sharing.
- A TypeScript SDK.
- A dedicated vector database or graph database.
- Entity-graph traversal or a general knowledge graph.
- Autonomous reflection, derived beliefs, consolidation, or sophisticated decay.
- A complete document-ingestion or RAG platform.
- Kubernetes, multi-region active-active operation, or independent microservices.
- Customer-managed keys, legal holds, configurable data residency, compliance certification, or a complete relationship-based access-control system.
- Guaranteeing model-independent extraction quality across every model provider.

## Failure cases and required behavior

- Duplicate episode request: return the original durable result for the same tenant and idempotency key; do not create a second episode or job.
- Database transaction failure during capture: persist neither episode nor job and return a retryable error with a request ID.
- Worker crash or lease expiry: make the job safely reclaimable without duplicating accepted memory versions.
- Extractor or evaluator failure: record retry state and eventual dead-letter state; leave the immutable episode available for authorized replay.
- Sensitive candidate: reject or redact it according to policy, record the policy outcome, and never expose the sensitive value in telemetry.
- Cross-tenant or unauthorized-subject request: deny without revealing whether the target resource exists and emit an audit event.
- Retrieval timeout or dependency failure: return degraded context without memory and emit degraded-mode telemetry.
- Embedding unavailable during explicit add: return a documented partial or retryable state; do not report a memory as searchable until its retrieval indexes are ready.
- Concurrent corrections: serialize against the current version or return a conflict; never create two undisclosed current versions.
- Forget request: suppress in the committing transaction, exclude from all retrieval immediately, and expose erasure progress or policy restriction.
- Backup restore: restored data must preserve tenant boundaries, suppression state, version lineage, source links, and audit integrity.
- SDK/API schema mismatch: contract tests fail the release; clients receive a versioned error rather than silently misinterpreting data.

## Success criteria

- A reference agent can capture an episode, observe asynchronous completion, search the resulting memory, and build cited bounded context through the Python SDK.
- A tenant operator can inspect provenance and history, correct a memory, forget it, and observe immediate suppression plus erasure status in the console.
- Automated adversarial tests cannot read or mutate another tenant's episodes, memories, versions, jobs, or audit events.
- Retrieval outages do not prevent the reference agent from producing a response without memory.
- The approved extraction and retrieval golden sets meet approved quality thresholds and retain their dataset and policy versions in evidence.
- A clean environment can migrate, start, exercise the core flow, back up, restore, and rerun isolation and suppression checks from documented commands.

## Acceptance criteria

- AC-1: [FR-1, NFR-1] Automated API and repository tests attempt cross-tenant get, list, search, update, forget, job, source, and audit access and prove every attempt is denied without resource-existence disclosure.
- AC-2: [FR-2, FR-3, NFR-7] An integration test forces failure between episode and job writes and proves the database contains either both records or neither; replay with the same idempotency key yields one episode and one job.
- AC-3: [FR-4, FR-12] A worker integration test proves lease recovery, bounded retries, dead-letter transition, queryable job state, and no duplicate memory version after crash-and-replay.
- AC-4: [FR-5, FR-13] A Python SDK contract test adds an explicit memory and retrieves the same typed resource with request ID, tenant-derived scope, and source citation.
- AC-5: [FR-6, FR-7] A retrieval test fixture proves full-text-only and vector-only candidates can both enter the result set, ranking is deterministic for a fixed policy version, and every result includes a memory ID and source citation.
- AC-6: [FR-8] Context tests prove the composed package never exceeds the configured token budget and contains the bounded core profile before lower-ranked retrieved items.
- AC-7: [FR-9, NFR-3] An end-to-end test makes retrieval unavailable and proves context build returns an explicit degraded result within its deadline while emitting correlated degraded-mode telemetry.
- AC-8: [FR-10, NFR-6] A correction integration test proves the prior version remains readable in authorized history, has a closed validity interval, and is linked from exactly one new current version.
- AC-9: [FR-11, NFR-2] In the same transaction-visible state after forget succeeds, automated tests prove the memory is absent from get, list, search, profile, rank, and context paths while erasure status and audit evidence remain available to authorized actors.
- AC-10: [FR-15] Audit tests prove every specified operation emits an append-only event carrying tenant, actor, subject where applicable, action, resource ID, request ID, timestamp, and outcome without raw secrets.
- AC-11: [FR-13, NFR-8] OpenAPI and Python SDK contract checks prove all public methods use stable envelopes, cursor semantics, machine-readable errors, request IDs, and documented asynchronous states.
- AC-12: [FR-14, NFR-14] Browser tests prove an operator can search, inspect provenance and versions, correct, confirm forget, and inspect status using keyboard-only interaction at supported desktop and mobile widths.
- AC-13: [NFR-4] A database constraint or repository integration test proves an active structured memory version cannot be committed without origin, confidence, actor, timestamp, and at least one source-evidence link.
- AC-14: [NFR-9, NFR-10] Security tests prove filtered secrets are absent from persisted structured memory, logs, traces, and audit payloads, and deployment checks prove encryption is enabled for network and storage boundaries.
- AC-15: [NFR-11] An end-to-end trace test proves one capture request correlates API, database, job, worker, model, and resulting-memory telemetry through non-secret identifiers.
- AC-16: [NFR-12] Release automation applies all migrations to an empty database and an approved prior schema, performs backup and restore, and reruns AC-1, AC-8, and AC-9 against restored data.
- AC-17: [NFR-13] A versioned evaluation command reports extraction precision, retrieval relevance, citation correctness, and correction rate against approved thresholds and exits non-zero on regression.
- AC-18: [NFR-15] Production release validation fails when approved numeric latency, throughput, availability, retention, erasure, or quality targets are absent or unmet.
- AC-19: [all first-release FRs] A documented clean-environment smoke command exercises capture, job completion, search, context, correction, forget, and status through the public Python SDK and exits non-zero on any failed assertion.

## Risks

- Cross-tenant leakage: require explicit tenant scope at every domain and persistence boundary, database defense in depth, and adversarial release-blocking tests.
- Hallucinated or over-personal memory: retain provenance and origin, evaluate candidates, expose confidence, support correction, and gate extraction quality with golden sets.
- Sensitive-data retention: minimize provider payloads, run policy hooks before persistence, audit outcomes, and keep secrets out of telemetry.
- Eventual-consistency confusion: expose job and indexing states, document visibility semantics, and return resource IDs and request IDs.
- Queue correctness under crashes: keep capture and scheduling in one transaction, use leases and idempotent writes, and test crash recovery.
- Irrecoverable deletion mistakes versus incomplete erasure: separate immediate suppression from policy-driven erasure, require confirmation, expose status, and preserve only legally permitted audit evidence.
- PostgreSQL retrieval or queue scale ceiling: instrument candidate counts, query latency, lease contention, and database load; adopt dedicated systems only after measured thresholds are exceeded.
- Model/provider lock-in and policy exposure: use narrow extraction, embedding, and evaluation boundaries; approve provider retention and data-use terms before production.
- Evaluation overfitting: version datasets and ranking policies, retain holdout cases, and require review of threshold changes.
- Two-language repository complexity: keep OpenAPI authoritative and minimize shared hand-written transport types.

## Assumptions requiring validation

- AS-1: PostgreSQL with pgvector and full-text search can meet the initial workload and relevance needs without a dedicated vector or graph database.
- AS-2: PostgreSQL-backed leased jobs can meet initial throughput and recovery needs without Temporal or another external workflow engine.
- AS-3: Tenant operators are authorized to inspect raw source evidence for their subjects; narrower evidence permissions may be required.
- AS-4: A useful initial core profile can be bounded and always included without exceeding practical context budgets.
- AS-5: The selected model and embedding provider can satisfy data-use, retention, regional, cost, latency, and quality constraints.
- AS-6: Immediate logical suppression followed by asynchronous physical erasure is acceptable to users and applicable policy.

## Open questions

- OQ-1: Which authentication and identity provider, credential format, tenant-membership model, and service-account lifecycle will be used?
- OQ-2: Which extraction, evaluation, and embedding models/providers are approved for development and production?
- OQ-3: What are the numeric launch targets for API and retrieval latency, formation completion, throughput, availability, extraction quality, retrieval quality, and maximum context budget?
- OQ-4: What default retention periods apply to episodes, memory versions, jobs, audit events, and backups, and what is the physical-erasure service-level target?
- OQ-5: Which sensitive-data categories are always rejected, which may be stored with consent, and who configures those policies?
- OQ-6: May tenant operators inspect raw episode evidence by default, or is a separate privileged role required?
- OQ-7: What concurrency contract should correction expose: mandatory version precondition, last-write rejection, or another explicit mechanism?
- OQ-8: When embedding is temporarily unavailable, should explicit memory addition fail atomically or create a durable non-searchable indexing state?
- OQ-9: Which deployment platform and managed PostgreSQL offering define encryption, backup, restore, and regional capabilities?
- OQ-10: Which browsers, Python versions, operating environments, and accessibility conformance target are supported at launch?

## Evidence

- `.humanlayer/tasks/full-stack-memory-layer-for-ai-agents-7ix3z6/task.md` — original product intent, architecture diagram, SDK/API aspiration, MCP deferral, and request to scope the first implementation.
- `.humanlayer/tasks/full-stack-memory-layer-for-ai-agents-7ix3z6/02-research-current-memory-architecture.md` — repository inventory, current conceptual contracts, documented stack statuses, data semantics, operational concerns, testing concepts, and frontend evidence.
- `.humanlayer/tasks/full-stack-memory-layer-for-ai-agents-7ix3z6/03-design-discussion-first-implementation.md` — reviewed and resolved first-release product boundary, stack, identity, API semantics, persistence, workflow, retrieval, console, production baseline, and verification approach.
- `memory-architecture.mmd` — canonical behavioral flows and invariants for online retrieval, durable capture, formation, temporal conflict handling, controls, and cross-cutting planes.
- `memory-system.html` — architecture study supplying candidate technologies, lifetime taxonomy, deployment and verification concepts, and the existing editorial visual language; it is evidence, not implementation.

## Evidence reconciliation

- The original task mentions add, show, share, and search. The later reviewed design intentionally defers sharing until grant, revocation, redaction, and audit semantics exist; this specification follows the later decision.
- The architecture study labels Temporal as part of a canonical MVP stack. The later reviewed design chooses PostgreSQL-backed jobs first and retains Temporal as a future migration target; this specification follows the later decision and preserves atomic capture-to-enqueue as the invariant.
- The Mermaid artifact describes semantic stores (Core, Session, Temporal, Episodes, Knowledge), while the HTML study also describes lifetime tiers (working, session, long-term, knowledge). The reviewed design resolves the first persistence slice around episodes, stable memories, versions, and sources; exact future mapping for knowledge and derived memories remains out of scope.
- The illustrative SQL in the HTML is narrower than the target identity and temporal model. It is not adopted as the implementation schema; the reviewed design's tenant, subject, actor, version, validity, supersession, and source requirements govern.
