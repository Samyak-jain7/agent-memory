# Run Agent Memory locally

[Back to README](../README.md) · [First-use walkthrough](usage.md)

Start with the disposable demo. It uses synthetic preferences and the offline provider, so you can explore without API keys or real personal data.

## 1. Install

You need Python 3.11+, Node.js 24, and Docker running. Clone the repository using your authorized GitHub access, open its directory, then run:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
npm --prefix apps/web ci
```

Run later commands from the repository root with this environment active. If your `python3` is older than 3.11, use an installed newer interpreter, such as `python3.13`.

## 2. Start a fresh demo

```sh
python docs/demo.py
```

Wait for the local console build and the printed console URL to respond. The command creates its own PostgreSQL 17/pgvector container with no persistent volume, chooses local ports, migrates it, creates a restricted runtime role and provisions one synthetic subject. It starts the API, operator console, formation and erasure workers, and queues two example suggestions. No Gemini, Jev or OpenAI requests are made, even if provider variables existed in your shell.

The printed **private connection file** holds an expiring demo token and the subject ID. Open that file locally, copy the token into **Access token**, and copy the subject ID into **Subject ID**. Keep the file private; do not paste it into issues or screenshots. Reloading the console clears its token. The demo’s token lasts one hour; restart the demo for a new one.

## 3. Use it

Select **Load suggestions**, then open a suggestion to inspect its source. Follow the [review, correction and forgetting walkthrough](usage.md). The API URL printed by the demo also exposes `/docs` for authenticated API exploration.

The demo’s formation worker also handles new captured episodes and expired-suggestion cleanup. Its erasure worker completes confirmed forgetting with a zero development retention delay. Use persistent setup below when you want data to survive restarts.

## 4. Stop

Press **Ctrl+C** in the demo terminal. It stops its own API/console and removes only its uniquely named disposable container. All demo data is lost. It does not use or stop an existing database.

## Persistent development setup

Use this when you want data to survive restarts. Choose a new development database; never point migrations, bootstrap or tests at an existing user database without a separate migration plan.

1. Copy [`.env.example`](../.env.example) to a private local file and fill the database passwords and signing secret. Use long random values; hexadecimal passwords avoid URL-escaping mistakes. Keep files mode 0600. The application does **not** load dotenv automatically: export the variables into each API/worker shell explicitly. Docker Compose reads `.env` for its own substitutions only.
2. Run `docker compose up -d postgres`. This creates the named development volume and listens locally on port 54329. Set `ADMIN_DATABASE_URL` to this new `memory_dev` database, then run `python -m scripts.database migrate`.
3. Set `MEMORY_DB_PASSWORD`, then run `python -m scripts.database runtime-role`. The command creates `memory_service` and intentionally refuses to overwrite an existing role. Set `DATABASE_URL` to this restricted role; the API and workers must not use the admin URL.
4. Run `python -m scripts.database bootstrap --development --consent --inspect-sources --review-memory`. Save the printed tenant, actor, subject and session UUIDs privately. These options grant access only to the newly created development identities. Existing subjects retain default-deny permissions.
5. Run `python -m scripts.issue_token --tenant TENANT_UUID --actor ACTOR_UUID` in your trusted local terminal. The output is a credential; use it only in the local console/SDK.
6. Set `MODEL_PROVIDER=offline`, `EXTRACTION_PROVIDER=offline`, `MEMORY_VERIFIER=none` and `MEMORY_FORMATION_MODE=supervised` in each worker shell. The last variable is required: the code’s fallback mode is automatic; `.env.example` recommends supervised mode.
7. Start these in separate terminals with the same exported environment:

```sh
uvicorn apps.api.main:app --host 127.0.0.1 --no-access-log
python -m apps.worker.main --tenant TENANT_UUID
python -m apps.worker.erase --tenant TENANT_UUID
npm --prefix apps/web run dev -- --hostname 127.0.0.1
```

`API_URL` defaults to `http://127.0.0.1:8000`; set it for the console server if your API uses a different port. Keep the formation worker running for new episodes and expired-suggestion cleanup. The erasure worker handles confirmed forgetting after the configured retention delay. See [operations](operations.md) before configuring telemetry, backups or production.

## If something fails

| Symptom | What to check |
|---|---|
| Docker connection fails | Start Docker, then retry. |
| Demo server stops | Confirm Node.js and Python versions and finish both installation commands. Rerun `python docs/demo.py --verbose` in your private terminal to see server startup diagnostics; keep that output private. |
| No suggestions after approval | Approved suggestions have left the queue; select **Search memories**. |
| `resource_not_found` during review | Check the token’s subject grant, review/source permissions, consent and expiry. |
| `invalid_credentials` | The token may have expired; create a new local token or restart the demo. |
| `version_conflict` | Refresh the item; another decision, expiry or correction made the displayed state stale. |

Tests use a different, explicitly disposable `memory_test...` database and delete its public schema. See [verification instructions](operations.md); do not run them against the demo or persistent development database.

## Reproduce the screenshots

In a fresh demo, install the local Chromium binary with `(cd apps/web && npx --no-install playwright install chromium)`. Set `MEMORY_DEMO_FILE` to the printed private connection-file path and run `node docs/capture.cjs`. This drives the actual console and API, captures only regions below the credential inputs, and exercises approve/reject/correct/forget on its synthetic subject. It consumes the sample queue; restart the demo to explore from the beginning. The script expects a fresh demo with its two original suggestions.
