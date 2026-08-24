# Threat model

Assets: the authority to create valid stamps, the money flow, and the audit record.

| Threat | Control | Residual risk |
| --- | --- | --- |
| Forged stamp accepted in the field | Serial check character plus a per-serial keyed secure code; only the hash is stored; comparison is constant time; verification never defaults to authentic | A leaked `DEVICE_HMAC_SECRET` allows code derivation for every serial. Rotation requires re-deriving codes; not yet implemented |
| Stolen credential | Deny-by-default bearer auth, keyed hash storage, revocation and expiry checks on every request, role and tenant checks per route | No mTLS or device attestation; no automatic anomaly-driven revocation |
| Payment forgery or replay | HMAC signature over the exact bytes, bounded timestamp skew, Redis replay guard, unique external reference, exact amount matching | A leaked webhook secret allows forged settlements until rotated |
| Duplicate or lost work on retry | Durable idempotency committed with the effect; transactional outbox with leases and dead-lettering | Dead-lettered messages need manual requeue |
| Money drift or theft via ledger edits | Append-only ledger and audit tables enforced by database triggers; deferred balanced-journal constraint; reconciliation checks conservation | A superuser can disable triggers; detected after the fact by reconciliation and chain verification, not prevented |
| Insider issuing stamps without payment | Explicit state machine (issuance only from `paid`), maker-checker approval, segregation of duties, full audit trail | A database superuser can bypass application logic |
| Audit tampering | Keyed hash chain with genesis, append-only triggers, verification endpoint and CLI | Detection, not prevention; the chain secret must be held outside the database |
| Denial of service on verification | Per-principal rate limiting, bounded bulk sizes (1 000 serials), payload size limits, statement timeouts | Fail-closed design means a Redis outage stops verification |
| Secret exposure | Startup validation rejects short, placeholder or duplicated secrets and insecure production settings; no secret is logged; tokens are printed once by the CLI | Secrets are supplied by the environment; no KMS integration |
| Supply-chain compromise | Fully pinned requirements, `pip-audit --strict` in CI, no unused dependencies, non-root image | No hash-pinned lockfile and no image signing yet |
