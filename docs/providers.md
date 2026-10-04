# Optional providers

[Back to README](../README.md) · [Local setup](local-setup.md)

Local setup and screenshots use offline fixtures. This guide describes optional configuration only; running it can transmit data and consume quota. Obtain separate approval before live requests. Never put keys in commands, screenshots or Git.

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

Google's free tier may use prompts and responses to improve its products. Use synthetic evaluation data until the owner has approved real-data transmission and provider data policies. TypeSafe's public model page lists input-token charges; free account eligibility and remaining grants must be checked in the console before any request. Mocked tests do not verify accounts, quotas or quality. Separate synthetic live checks established adapter compatibility; the frozen Jev comparison accepted 0/10 supported holdout candidates for both prompts. Automatic acceptance is not calibrated or approved. Keep keys and private review files outside source control and supply secrets only through the operator's secure local environment.

Official references checked 2026-10-04: [Gemini Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [REST generation contract](https://ai.google.dev/api/generate-content), [TypeSafe API](https://docs.typesafe.ai/api), [Jev versions](https://docs.typesafe.ai/models).



## Private configuration

Supply `GEMINI_API_KEY`, `TYPESAFE_API_KEY` and the private review-file paths through your local environment or an approved secret manager. Keep local files mode 0600 and Git-ignored; `.env.*` files are ignored except `.env.example`. The application does not load dotenv automatically. Restart the affected worker after changing its environment. Do not reuse an expired review attestation or guess its fields.

For supervised review, set `MEMORY_FORMATION_MODE=supervised`: extraction may be Gemini, but Jev is skipped and a human must approve every suggestion. For a separately approved automatic experiment, set `MEMORY_FORMATION_MODE=automatic` and `MEMORY_VERIFIER=jev`; this does not establish production quality. Leaving verification as `none` in automatic mode bypasses Jev entirely. `MODEL_PROVIDER=offline` keeps embedding calls local. Optional OpenAI extraction/embeddings require `MODEL_API_KEY`, explicit `EXTRACTION_MODEL`/`EMBEDDING_MODEL`, and separate cost/data-policy approval; they are not covered by the Gemini/Jev free-only guards.
