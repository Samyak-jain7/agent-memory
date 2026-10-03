# Proposed defaults for a personal developer platform

These are recommendations for the owner to approve, not measured performance or production readiness evidence.

| Target | Proposal | Rationale |
|---|---:|---|
| API p95 latency | 1000 ms | Interactive inspection at modest load |
| Sustained throughput | 5 requests/second | Initial personal and demonstration use |
| Monthly availability | 99% | Small deployment without enterprise redundancy |
| Extraction precision | 0.95 | Avoid recording unsupported personal claims |
| Retrieval relevance | 0.80 | Initial labeled query set with manual review |
| Citation correctness | 0.99 | Provenance should reliably support answers |
| Correction rate | at most 0.05 | Track incorrect derived memories |
| Delay before erasure | 0 seconds | Suppression is immediate; enqueue erasure immediately |
| Primary erasure completion | 24 hours | Allows recovery from transient worker failures |
| Backup retention | 7 days | Short recovery window limits deleted-data exposure |

Active memories and necessary provenance remain only while consent and the selected retention policy allow them. Raw evidence cleanup must preserve surviving provenance dependencies. Backups require encryption, restricted access, enforced expiry, and quarantine during restore so deletions after the backup can be reapplied before serving reads.

The owner still needs to select hosting, provider/models and credentials, approve policies and targets, and provide measured deployment evidence. The readiness document binds attestations to a deployment and release, checks artifact hashes, and rejects receipts older than seven days. Those checks establish document integrity and freshness; they do not establish that a platform encrypts storage or that an attestation is true. The deployment's reviewed verifier must produce the evidence.
