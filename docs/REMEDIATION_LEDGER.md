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

## Not remediated (external dependency required)

| Area | Why it remains open |
| --- | --- |
| Authoritative excise rates and statutory rules | No authoritative source supplied; tariffs are operator-entered with a statutory reference field |
| FIRS / NAFDAC / SON / Customs sandbox evidence | No credentials or sandbox endpoints available |
| Payment provider sandbox evidence | No provider credentials available |
| Printer / holographic / offline-scanner integration | No hardware, drivers or vendor contracts available |
| Production deployment, restore and rollback drills | No target environment available in this session |
