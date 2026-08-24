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

## Market-parity scope

Phase 1 closed licensing, product master data, statutory tariff versioning, stamp
accountability and treasury resolution.

Phase 2 adds supply-chain traceability with aggregation, a queryable repository, import and
duty-suspension regimes, retention and portability, deterministic clone/diversion findings,
a Merkle transparency log with inclusion proofs, and EPCIS-shaped export documents. Three
limits are deliberate and visible at runtime: the EPCIS envelope declares that it has not
been validated against the GS1 conformance suite (no GS1 company prefix is configured), a
regulator export states that no delivery occurred when no repository endpoint is
configured, and a checkpoint states that it is not externally anchored when no anchor
endpoint is configured.

Consumer verification, enforcement cases, KPI reporting, offline verification bundles and
risk analytics remain unimplemented and are declared as such in `docs/FEATURE_CLAIMS.md`;
none of them is simulated.

### Phase 2 evidence

- **Aggregation integrity.** A stamp can belong to only one open unit (partial unique
  index), a packed unit cannot be moved independently of its parent, and a movement whose
  declared count differs from the unit's contents is refused.
- **Destruction.** A destruction event voids the covered serials in the same transaction,
  so a destroyed stamp no longer verifies as authentic.
- **Customs control.** Domestic release requires a duty-paid regime, an operator-entered
  customs evidence reference and linked stamps equal to the declared quantity; free-zone,
  transit and duty-free consignments are refused release. One stamp cannot cover two
  consignments (unique constraint), and a shortfall is reported by reconciliation.
- **Detection.** Findings are produced only by named deterministic rules from stored
  evidence, carry the rule version, and are deduplicated so re-running detection cannot
  inflate the queue.
- **Transparency.** Each checkpoint chains onto its predecessor's root, and an inclusion
  proof recomputes the published root from the leaf alone, verified independently in
  `tests/unit/test_merkle.py` and over HTTP.
- **Disclosure integrity.** Every export is hashed over canonical JSON and signed with a
  purpose-separated key; a duplicate export reference is refused, oversized exports are
  refused rather than silently truncated, and reconciliation re-verifies stored export and
  checkpoint signatures.

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
