# Use Agent Memory

[Back to README](../README.md) · [Start the offline demo](local-setup.md)

## Your first memory

1. Start the demo and connect the console using its private token and subject ID.
2. Select **Load suggestions**, then choose **I prefer jasmine tea.**
3. Read the original user message shown beneath the suggestion. Model scores are uncalibrated; the evidence and your judgment decide whether to save it.
4. Select **Approve suggestion**, review the confirmation, then **Confirm approval**. To discard it instead, choose **Reject suggestion** and confirm. Cancel or Escape closes the dialog without sending a decision.
5. Select **Search memories** to see the saved memory. Open it to inspect its version history, source evidence and audit events.

![The actual offline demo shows a suggested tea preference beside user evidence and approve/reject controls.](screenshots/review.png)

Suggestions never appear in search/context before approval. They expire after seven days. Repeating the same decision is safe; an opposing decision conflicts. Expired or invalidated suggestions cannot be approved. Source and review access require explicit grants; tokens cannot grant their own permissions.

## Correct a fact

Open a saved memory, edit **Corrected content**, and keep or replace **Supporting episode ID** with an existing, unerased episode for the same subject. Select **Save correction**. This appends a version and preserves history; stale version IDs produce a conflict. For the demo, a wording correction such as “I prefer jasmine tea” keeps the original evidence; a new preference needs a new supporting episode.

![A saved synthetic tea preference with version history, source evidence and the correction form.](screenshots/memory.png)

## Forget a memory

Select **Forget memory**, read the confirmation and choose **Confirm forget**. It disappears from retrieval immediately. An erasure worker later removes structured payloads and unshared source content; shared evidence may remain. The disposable demo runs an erasure worker with zero retention delay. **Refresh erasure status** shows progress; a blocked erasure requires an explicit authorized retry.

Forgetting also invalidates pending suggestions from the same evidence. Seven-day suggestion expiry does not erase the raw episode or expire backups; those have separate policies described in [operations](operations.md).

## Python SDK

Use the API URL, token and UUIDs from your own local setup. In the demo, read the connection file privately; never commit it. This example captures a synthetic episode through the real HTTP API:

```python
import json, os
from pathlib import Path
from uuid import UUID, uuid4
from memory_sdk import MemoryClient, CaptureEpisodeRequest, Message, ContextRequest

# Set MEMORY_DEMO_FILE to the private connection-file path printed by the demo.
connection = json.loads(Path(os.environ["MEMORY_DEMO_FILE"]).read_text())
token, api_url = connection["token"], connection["api_url"]
subject_id, session_id = connection["subject_id"], connection["session_id"]

with MemoryClient(token, base_url=api_url) as memory:
    captured = memory.create_episode(CaptureEpisodeRequest(
        session_id=UUID(session_id), subject_id=UUID(subject_id),
        messages=[Message(role="user", content="I prefer jasmine tea.")],
        idempotency_key=str(uuid4()),
    ))
    print(memory.job_status(captured.job.id).job.state)
    # The worker forms suggestions asynchronously; approve in the console/API.
    print(memory.search(UUID(subject_id), "tea").items)
    context = memory.build_context(ContextRequest(
        subject_id=UUID(subject_id), query="tea", token_budget=512,
    ))
    print(context.text, context.citations)
```

Set `MEMORY_DEMO_FILE` to the printed private path before running the example. For persistent setup, use your own local token, API URL and bootstrap IDs. The demo connection file includes subject, session, tenant and actor IDs. Use those values or the IDs from persistent bootstrap for new captures. The demo’s sample queue is already formed. The SDK supports explicit add, read, list, search, context, correction, history, sources, forgetting and erasure status/retry. Review actions currently use the console or API rather than SDK methods.

Reuse the **same** idempotency key when retrying an identical episode request after an interrupted response. Reusing it with different content conflicts. SDK requests use canonical UUID request IDs, typed responses and safe error codes; no hidden retries occur. Remote API URLs require HTTPS.

## Review through the API

Open the local API’s `/docs`, authorize with your own bearer token, then follow this sequence:

| Action | Request |
|---|---|
| Capture evidence | `POST /v1/episodes` with subject/session UUIDs, messages and an idempotency key |
| Check formation | `GET /v1/jobs/{job_id}` |
| List pending suggestions | `GET /v1/suggestions?subject_id={subject_id}` |
| Inspect filtered evidence | `GET /v1/suggestions/{id}/sources` |
| Save after human review | `POST /v1/suggestions/{id}/approve` with `{"confirm":true}` |
| Discard | `POST /v1/suggestions/{id}/reject` with `{"confirm":true}` |
| Correct an approved memory | `PATCH /v1/memories/{memory_id}` with current version ID, content and supporting source |
| Forget | `POST /v1/memories/{memory_id}/forget` with `confirm:true` and current version ID |

The approve response returns `memory_id`. Retrieve it with `GET /v1/memories/{memory_id}` for its current `version_id` and `source_episode_ids`. The [OpenAPI contract](../packages/contracts/openapi.json) defines complete bodies; `/docs` provides editable examples. Unauthorized resources return 404 without disclosing their contents.

## Screenshot provenance

Both images are Chromium screenshots of the actual Next.js console backed by the local API, a restricted database runtime role and a fresh synthetic pgvector database. Extraction/embeddings use the offline fixture; no provider dashboards or live model requests are involved. Connection inputs are outside the captured viewport region. See [capture script](capture.cjs) for reproducibility.
