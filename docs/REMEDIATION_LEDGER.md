# Remediation ledger

Findings are from the review of the supplied `NACTP_COMPLETE_WITH_ORCHESTRATION_v2.0`
bundle (see `TAX_STAMP_CODE_REVIEW.md` in the session artefacts). The repository at
`github.com/munisp/taxstamp` was empty, so remediation is a canonical implementation of
the flows the bundle claimed, not a patch series against it.

| # | Severity | Finding in the reviewed bundle | Remediation | Regression test |
| --- | --- | --- | --- | --- |
| 1 | Critical | ML detector returned a hardcoded 95% "authentic" verdict for any image | Image authenticity is declared unavailable; verification uses a keyed secure code compared in constant time | `tests/integration/test_verification_service.py` |
| 2 | Critical | Regulatory checks (FIRS/NAFDAC/SON/Customs) returned success without calling anything | Real HTTP provider contract; unconfigured or unreachable ⇒ 503; unknown never passes | `tests/integration/test_providers.py`, `tests/fault/test_failure_paths.py` |
| 3 | Critical | Payments accepted without signature or amount check | HMAC-signed, timestamped, replay-guarded ingestion with exact minor-unit matching; mismatches quarantined | `tests/e2e/test_payments_api.py` |
| 4 | Critical | Monetary values held as `float64` | Integer minor units end to end; binary floats rejected at the boundary | `tests/unit/test_money.py` |
| 5 | Critical | Order/stamp state held in memory; restart lost state | PostgreSQL-backed state machines with explicit legal transitions | `tests/unit/test_transitions.py`, `tests/e2e/test_lifecycle.py` |
| 6 | Critical | No double-entry accounting; totals could drift | Balanced journals enforced by a deferred database trigger, plus reconciliation | `tests/integration/test_constraints.py`, `tests/fault/test_failure_paths.py` |
| 7 | Critical | Secrets regenerated at startup; tokens stored in clear | Startup-validated secrets; credentials stored only as keyed hashes | `tests/unit/test_config.py`, `tests/integration/test_cli.py` |
| 8 | High | Missing authorization and tenant checks | Deny-by-default authentication, explicit role and company checks on every route | `tests/e2e/test_authorization.py` |
| 9 | High | Weak/absent replay protection | Redis single-use nonces and body-hash replay guard, failing closed | `tests/integration/test_gates.py`, `tests/e2e/test_lifecycle.py` |
| 10 | High | Retries could duplicate orders and payments | Durable `(scope, key)` idempotency committed with the effect; unique external payment reference | `tests/integration/test_idempotency.py`, `tests/concurrency/test_concurrent_operations.py` |
| 11 | High | Audit trail mutable and unverifiable | Append-only tables with database triggers and a keyed hash chain with verification endpoint | `tests/integration/test_audit_chain.py` |
| 12 | High | Side effects fired inside request handlers, lost on failure | Transactional outbox with leases, backoff and dead-lettering; unconfigured dependency keeps work pending | `tests/integration/test_outbox.py`, `tests/fault/test_failure_paths.py` |
| 13 | High | Issuance not resumable; interruption duplicated or lost serials | Chunked issuance with locked counters; resumes from the persisted count | `tests/fault/test_failure_paths.py`, `tests/concurrency/test_concurrent_operations.py` |
| 14 | High | Quality inspection auto-passed | Tabulated single-sampling plan; failed lots block activation | `tests/unit/test_quality.py`, `tests/fault/test_failure_paths.py` |
| 15 | High | No migrations; schema drifted from code | Alembic revision with constraints; upgrade/downgrade/upgrade verified | `tests/integration/test_migrations.py` |
| 16 | High | Nothing compiled; no tests; no CI | 104 tests across unit, integration, e2e, concurrency and fault suites, plus lint, strict typing, bandit, pip-audit in CI | `.github/workflows/ci.yml`, `scripts/run_assurance.sh` |
| 17 | Medium | Vulnerable/unused dependencies | Removed unused PyJWT; upgraded FastAPI/Starlette/uvicorn to versions with no known advisories | `evidence/audit-deps.log` |
| 18 | Medium | Signature covered server-normalised payloads | Signatures now cover the exact bytes the client sent | `tests/e2e/test_lifecycle.py` |
| 19 | High | Settlement of a cancelled order raised on an illegal transition and rolled the receipt back, losing evidence of received funds | Cancellation closes open intents; settlement locks the order and records an `order_not_payable` receipt posted to `liability:unapplied_receipts` for manual application | `tests/e2e/test_payments_api.py` |
| 20 | Medium | Reconciliation gauges kept the last non-zero value after a finding resolved | Every declared finding kind is published each run, zero included | `tests/unit/test_reconciliation_report.py` |
| 21 | High | Requesters could read any batch by id, across tenants | Batch reads resolve the owning order's company and enforce tenant isolation | `tests/e2e/test_authorization.py` |
| 22 | High | Any KYB-verified company could procure stamps for any category; no legal entitlement was checked | An effective manufacturer or importer licence covering the category is required to submit an order, and the licence is recorded on the order | `tests/e2e/test_registry_api.py` |
| 23 | Medium | Orders named a free-text category with no product identity, so stamps could not be tied to a real SKU | Company-scoped product master data; a product-backed order is the primary path and withdrawn or foreign products are refused | `tests/e2e/test_registry_api.py` |
| 24 | High | Two tariffs could cover the same category and date, making the price non-deterministic | Overlapping effective periods are refused on both the API and CLI paths; pre-existing overlaps are reported by reconciliation | `tests/integration/test_registry_governance.py` |
| 25 | High | Stamps that were spoiled, damaged, destroyed or returned had no disposition record, so a batch could not be accounted for | Serial-level declarations void the stamps in the same transaction, with a reason and an evidence reference, plus a batch population account | `tests/e2e/test_accountability_api.py` |
| 26 | High | Funds held for a mismatched, unknown-reference or non-payable remittance had no exit: money sat in `liability:unapplied_receipts` forever | Treasury can apply a held receipt to a payable order (exact amount and currency) or refund it to a named beneficiary, exactly once per receipt, with balanced journals | `tests/e2e/test_treasury_api.py` |
| 27 | Medium | A remittance quoting an unknown reference produced a review event the worker could never deliver, retrying to the dead-letter queue | The review handler accepts an absent expected amount, which is the only case where no intent exists, and still validates the received amount | `tests/e2e/test_treasury_api.py` |
| 28 | High | Registry master data could be enumerated across tenants by any low-privilege reader | Register listings are scoped to the reader's own company; only analyst, supervisor and admin credentials read across tenants | `tests/e2e/test_registry_api.py` |
| 29 | High | Nothing tied an issued mark to a physical package or its movements, so goods could be diverted with no record to contradict | Facilities, aggregation units and append-only trace events, with declared-quantity conservation and idempotent event references | `tests/e2e/test_traceability_api.py` |
| 30 | High | A destruction declaration left the covered stamps verifiable as authentic | A destruction event voids its stamps in the same transaction and records a `destroyed` stamp event | `tests/e2e/test_traceability_api.py` |
| 31 | High | Imported and duty-suspended goods had no representation, so untaxed stock could enter the domestic market unrecorded | Consignments per customs regime; domestic release requires a duty-paid regime, a customs evidence reference and linked stamps equal to the declared quantity | `tests/e2e/test_customs_disclosure_api.py` |
| 32 | Medium | Customs decisions were blocked for the staff who take them: the consignment lookup required the actor to belong to the importing company | Cross-tenant supervisory roles may act on any consignment; company-scoped credentials remain restricted to their own | `tests/e2e/test_customs_disclosure_api.py` |
| 33 | Medium | Contradictions between records (impossible travel, quantity divergence, market divergence, duplicate scans) were never surfaced | Deterministic versioned rules writing deduplicated findings with the evidence needed to reproduce them | `services/anomaly.py`, `tests/e2e/test_traceability_api.py` |
| 34 | Medium | A regulator could not confirm that a record existed at a point in time without database access | Signed Merkle checkpoints over the audit chain, chained to their predecessor, with inclusion proofs verifiable from the published root alone | `tests/unit/test_merkle.py`, `tests/e2e/test_customs_disclosure_api.py` |
| 35 | Medium | Exports had no integrity evidence and no defined retention or portability behaviour | Canonical-JSON hash plus purpose-separated signature per export, duplicate references refused, oversized exports refused, and a published archive-only retention policy | `retention.py`, `tests/e2e/test_customs_disclosure_api.py` |

## Not remediated (external dependency required)

| Area | Why it remains open |
| --- | --- |
| Authoritative excise rates and statutory rules | No authoritative source supplied; tariffs are operator-entered with a statutory reference field, and overlapping periods are now refused |
| Licence register reconciliation against the issuing authority | Licences are recorded locally with a statutory reference; no register feed is available to confirm them |
| FIRS / NAFDAC / SON / Customs sandbox evidence | No credentials or sandbox endpoints available |
| Payment provider sandbox evidence | No provider credentials available |
| Printer / holographic / offline-scanner integration | No hardware, drivers or vendor contracts available |
| Production deployment, restore and rollback drills | No target environment available in this session |
| GS1 EPCIS conformance | No GS1 company prefix issued, so identifiers are platform URNs and the document is EPCIS-shaped rather than conformance-validated; the envelope says so |
| Regulator repository delivery | No repository endpoint or credentials available; an export is created, hashed and signed but never reported as delivered |
| External anchoring of transparency checkpoints | No anchor endpoint configured; checkpoints state that they are not externally anchored |
