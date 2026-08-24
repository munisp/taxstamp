# Feature claim manifest

Status values: **implemented** (code plus automated evidence in this repository),
**configuration-dependent** (implemented against a real external contract; refuses to run
when the dependency is absent), **not implemented** (declared unavailable at runtime; no
code path pretends otherwise).

| Capability | Status | Evidence / enforcement |
| --- | --- | --- |
| Excise licensing (manufacturer / importer / distributor) as a procurement precondition | implemented | `tests/e2e/test_registry_api.py` (no licence, expired, suspended, revoked, wrong category, distributor-only all refuse procurement) |
| Product master data (SKU, brand, pack size, intended market) | implemented | `tests/e2e/test_registry_api.py` (company-scoped, withdrawn and foreign products cannot be ordered) |
| Statutory tariff versioning with overlap refusal | implemented | `tests/integration/test_registry_governance.py`; API and CLI both refuse an overlapping effective period, and existing overlaps are reported by reconciliation |
| Stamp accountability (spoiled / damaged / destroyed / returned) | implemented | `tests/e2e/test_accountability_api.py` (serial-level evidence, batch population account, live-stamp declarations reported by reconciliation) |
| Treasury resolution of held funds (application / refund) | implemented | `tests/e2e/test_treasury_api.py` (exact amount and currency, balanced journals, one resolution per receipt) |
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
| Supply-chain traceability events (dispatch, arrival, transload, export, destruction) | implemented | `tests/e2e/test_traceability_api.py` (state machine, declared-quantity conservation, destruction voids the stamps it covers) |
| Aggregation and disaggregation (stamp → case → pallet → container) | implemented | `tests/e2e/test_traceability_api.py` (a stamp belongs to one open unit; a packed unit cannot move independently of its parent) |
| Repository queries by stamp, unit, movement and time range | implemented | `services/repository.py`, `tests/e2e/test_traceability_api.py`; every sensitive query is recorded in the audit chain and scoped to the reader's tenant |
| Import, free-zone, transit and duty-free consignments | implemented | `tests/e2e/test_customs_disclosure_api.py` (domestic release only under a duty-paid regime, only with an operator-entered customs evidence reference, and only once linked stamps equal the declared quantity) |
| Clone and diversion detection | implemented | `services/anomaly.py`, `tests/e2e/test_traceability_api.py`; deterministic rules only (impossible travel, quantity divergence, intended-market divergence, duplicate-scan divergence), each finding carrying its rule version and evidence |
| Public transparency log (Merkle checkpoints and inclusion proofs) | implemented | `services/transparency.py`, `tests/unit/test_merkle.py`, `tests/e2e/test_customs_disclosure_api.py`; a proof verifies against the published root without database access |
| EPCIS-shaped interoperability | implemented (shape only) | `services/epcis.py`; the envelope declares that it has not been validated against the GS1 conformance suite and that identifiers are platform URNs, because no GS1 company prefix is configured |
| Regulator export delivery | configuration-dependent | `api/routers/disclosure.py`; with no repository endpoint configured the response states that no delivery occurred, and a configured endpoint is delivered by the outbox relay rather than claimed synchronously |
| Retention and data portability | implemented | `retention.py`, `GET /v1/retention-policy`, `POST /v1/exports/portability`; expiry is archive-only, statutory records are never destructively erased, and every export carries a canonical hash and signature |
| Image / ML stamp authenticity | not implemented | declared unavailable in `capabilities.py`; no confidence score is produced anywhere |
| Printer production control (HP / Zebra / Atlantic Zeiser / Kurz) | not implemented | declared unavailable; no device driver is present |
| Holographic / taggant verification | not implemented | declared unavailable |
| Offline verification sync | not implemented | declared unavailable |
| Blockchain registration as a system of record | not implemented | only external anchoring of a Merkle root, and only when configured |

## Market-parity capabilities not yet built

Benchmarked against SICPA SICPATRACE Evo, Authentix and De La Rue, and against WHO FCTC
Art. 8 and EU Implementing Regulation 2018/574. These are scheduled, not claimed.

| Capability | Status |
| --- | --- |
| Consumer verification channel | not implemented |
| Enforcement case management | not implemented |
| Revenue and compliance KPI reporting | not implemented |
| Offline verification bundles | not implemented |
| Explainable risk scoring and revenue-at-risk analytics | not implemented |
