# Feature claim manifest

Status values: **implemented** (code plus automated evidence in this repository),
**configuration-dependent** (implemented against a real external contract; refuses to run
when the dependency is absent), **not implemented** (declared unavailable at runtime; no
code path pretends otherwise).

| Capability | Status | Evidence / enforcement |
| --- | --- | --- |
| Order intake with server-side pricing | implemented | `tests/e2e/test_lifecycle.py`, `services/orders.py`; client-supplied totals are rejected |
| Effective-dated tariff lookup | implemented | `tests/integration/test_cli.py`, `services/orders.py` |
| Maker-checker approval | implemented | `tests/e2e/test_authorization.py` (submitter cannot approve; one decision per level) |
| Regulatory compliance check (FIRS / NAFDAC / SON / Customs) | configuration-dependent | `providers/compliance.py`; `tests/integration/test_providers.py`, `tests/fault/test_failure_paths.py` (outage → 503, non-compliant → rejected) |
| Payment settlement ingestion (signed) | implemented | `tests/e2e/test_payments_api.py` (signature, exact amount, replay, mismatch, unknown reference) |
| Double-entry posting and funds conservation | implemented | balanced-journal DB trigger; `tests/integration/test_constraints.py`, `tests/fault` injected-defect test |
| Resumable serial issuance | implemented | `tests/fault/test_failure_paths.py` (crash after first chunk), `tests/concurrency` (no duplicate blocks) |
| Acceptance sampling (Z1.4-style single sampling) | implemented | `quality.py`, `tests/unit/test_quality.py`, `tests/fault` failed-lot test |
| Stamp activation / void / expiry | implemented | `tests/e2e/test_lifecycle.py`, `tests/concurrency` |
| Field verification (serial + keyed secure code) | implemented | `tests/integration/test_verification_service.py`; never defaults to authentic |
| Append-only audit chain | implemented | append-only triggers; `tests/integration/test_audit_chain.py` (tamper detection) |
| Durable idempotency | implemented | `tests/integration/test_idempotency.py`, `tests/concurrency` (one order per key under 8-way race) |
| Transactional outbox with dead-lettering | implemented | `tests/integration/test_outbox.py`, `tests/fault` anchor-outage test |
| Reconciliation | implemented | `services/reconciliation.py`, `tests/fault` injected-defect test |
| Batch anchoring / notarisation | configuration-dependent | `providers/anchor.py`; unconfigured anchor keeps the outbox message pending, never marks it delivered |
| Image / ML stamp authenticity | not implemented | declared unavailable in `capabilities.py`; no confidence score is produced anywhere |
| Printer production control (HP / Zebra / Atlantic Zeiser / Kurz) | not implemented | declared unavailable; no device driver is present |
| Holographic / taggant verification | not implemented | declared unavailable |
| Offline verification sync | not implemented | declared unavailable |
| Blockchain registration as a system of record | not implemented | only external anchoring of a Merkle root, and only when configured |
