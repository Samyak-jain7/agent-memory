# Operations and verification

[Back to README](../README.md) · [Local setup](local-setup.md)

Use a separate PostgreSQL17 pgvector container/database named `memory_test` for verification.
Set `TEST_DATABASE_URL` to it and `BACKUP_TEST_CONTAINER` to that container's exact ID/name.
The tests delete only that explicitly designated disposable schema. Never point them at real data.

```sh
pip install -e . 'pytest>=8,<9' uv
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

Production evidence must identify `deployment_id` and a 40-character `release_sha`; runtime `MEMORY_DEPLOYMENT_ID` and `MEMORY_RELEASE_SHA` must match. `evidence_receipts` must contain storage, backup, restore, provider, telemetry, retention, erasure, backup_expiry, deployment, and approval receipts. Each includes matching deployment/release, UTC `verified_at` within seven days, `artifact_path` relative to the readiness document directory, `artifact_sha256`, and named verifier `provenance`. Artifacts are limited to 1 MiB and must remain within that directory; hashes are checked before startup. Keep private evidence outside the source repository. Receipt validation is an integrity check; real platform verification remains required. See [proposed policy defaults](../deploy/policy-proposal.md) for unapproved recommendations.

Release packaging is checked with `python scripts/verify_distribution.py` (requires `uv`): it builds a wheel from temporary clean inputs, installs it in a new environment, and imports API, workers, SDK and packaged migrations outside the checkout. CI also scans publishable files and complete reachable Git history for credential patterns, builds the Docker image, and imports its installed package as UID 10001 with networking disabled and a read-only filesystem. These checks complement lifecycle and browser tests; they do not deploy the application.


## Suggestion maintenance

Keep each tenant’s formation worker running to clear expired suggestions and reconcile review jobs. List/decision requests also clean expired payloads. Seven days is the suggestion review window, not a raw-episode or backup retention policy. Approved/rejected payloads are cleared immediately; approved memories follow the version/suppression/erasure lifecycle. Sensitive-content filtering is conservative pattern matching, not complete detection. Production policies and measured platform evidence remain unresolved.
