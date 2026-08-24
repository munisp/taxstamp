# Assurance report

**Revision:** this branch. **Decision: BLOCKED for production.**

## Basis of the decision

The mandatory release rules require every material claim to rest on recorded evidence and
forbid any production-reachable mock, stub or default-success path. The implementation in
this repository satisfies the source-quality, correctness, durability and security gates
that can be exercised here, but the following mandatory gates cannot be closed in this
environment, and each is individually sufficient for a BLOCKED decision:

1. **No authoritative rate/rule source.** Excise rates, VAT treatment and product-category
   rules are operator-entered. A wrong rate is a funds-affecting defect, so the pricing
   path cannot be certified without an authoritative published schedule.
2. **No regulatory sandbox evidence.** FIRS, NAFDAC, SON and Customs integrations are
   implemented against an explicit HTTP contract and tested against a real local sandbox,
   but never against the real registries.
3. **No payment provider sandbox evidence.** Settlement ingestion is verified with signed
   requests against this service; no real bank or PSP feed has been exercised.
4. **No deployment, restore or rollback drill.** Migrations are verified
   upgrade/downgrade/upgrade against a real database, and the image builds, but no target
   environment was available for a deployment, backup-restore or rollback drill.
5. **Capabilities declared unavailable.** Image/ML authenticity, printer control,
   holographic verification and offline sync are not implemented. They are refused at
   runtime rather than simulated, which is safe but means those business requirements are
   unmet.

## Gates and evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Formatting | pass | `evidence/format.log` |
| Lint (ruff, security rules enabled) | pass | `evidence/lint.log` |
| Strict typing (mypy strict, no explicit `Any`) | pass, 57 files | `evidence/type.log` |
| Static security scan (bandit) | pass, no findings | `evidence/security.log` |
| Dependency advisories (pip-audit, strict) | pass, none known | `evidence/audit-deps.log` |
| Tests | pass, 133 tests | `evidence/tests.log` |
| Coverage | 89% overall, branch coverage on | `evidence/tests.log` |
| Import / entry points | pass | `evidence/import-check.log` |
| Migration upgrade → downgrade → upgrade | pass | `tests/integration/test_migrations.py`, CI job `quality` |
| Image build | pass | CI job `image` |
| Deployment / restore / rollback drill | **not run** | no target environment |
| Real external integrations | **not run** | no credentials |

## What the tests actually prove

- **Funds flow.** Every settlement posts a balanced journal; the balance is enforced by a
  deferred database constraint, and an injected ledger defect (created by disabling the
  append-only trigger as a privileged operator would) is reported by reconciliation rather
  than absorbed.
- **Exactness.** Amounts are integer minor units; underpayment by one minor unit is
  quarantined to an unapplied-receipts account and the order stays unpaid.
- **Idempotency and concurrency.** Eight parallel submissions with one idempotency key
  create exactly one order; eight parallel serial-block allocations never overlap; six
  parallel activations of the same serials change each stamp exactly once.
- **Crash recovery.** Issuance interrupted after the first committed chunk resumes and
  produces exactly the ordered quantity with no duplicate serials.
- **Dependency failure.** A registry outage blocks order creation with 503; a malformed
  anchoring response keeps the outbox message pending and it is delivered after recovery.
- **Tamper evidence.** Deleting an audit row with triggers disabled is detected by chain
  verification.
- **Authorization.** Wrong role, wrong tenant and self-approval are all refused, and
  cross-tenant listing returns nothing.
- **Legal entitlement.** Procurement requires an effective manufacturer or importer licence
  covering the ordered category; missing, expired, suspended, revoked, distributor-only and
  non-covering licences all refuse the order, and a licence that lapses behind an in-flight
  order is reported by reconciliation.
- **Stamp accountability.** A spoilage, damage, destruction or return declaration voids the
  named serials in the same transaction; a declaration whose stamps are still live is
  reported by reconciliation, so a paper-only declaration cannot hide circulating stamps.
- **Held funds have an exit.** A quarantined receipt can be applied to a payable order only
  at the exact amount and currency, or refunded to a named beneficiary, exactly once, and
  the unapplied-receipts balance returns to zero either way.

## Phase 1 market-parity scope

Licensing, product master data, statutory tariff versioning, stamp accountability and
treasury resolution close the locally remediable market gaps. Traceability and aggregation,
regulator repository exports, consumer verification, enforcement cases, KPI reporting and
offline verification remain unimplemented and are declared as such in
`docs/FEATURE_CLAIMS.md`; none of them is simulated.

## Conditions to reach a releaseable state

1. Supply the authoritative rate/rule source and add rule-level tests against it.
2. Supply regulatory and payment sandbox credentials; re-run the integration suites against
   them and record the evidence.
3. Run a deployment, backup-restore and rollback drill in a production-shaped environment.
4. Either implement the unavailable capabilities with real hardware/vendor evidence or have
   the business owner accept their absence in writing.
5. Obtain legal/compliance sign-off; nothing in this repository constitutes a compliance
   attestation.
6. Reconcile the local licence register against the issuing authority's register; licences
   are currently operator-entered with a statutory reference and are not externally
   confirmed.
