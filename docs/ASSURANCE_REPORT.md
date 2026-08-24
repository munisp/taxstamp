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
   holographic verification and prosecution/court filing are not implemented. They are
   refused at runtime rather than simulated, which is safe but means those business
   requirements are unmet.
6. **No enforcement-authority evidence.** A prosecution referral is recorded internally
   only. Nothing has been filed with, or acknowledged by, any prosecuting authority, and
   the platform never claims otherwise.
7. **No deployed edge or operated identity provider.** The gateway configuration, the realm
   and the policy schema are all verified to load and behave against real products started
   locally, but there is no TLS certificate, no client certificate authority for the device
   fleet, no WAF in prevention mode and no provisioned staff directory. The edge and
   identity tier exists as verified configuration, not as an operated control.
8. **No field-device evidence.** Offline bundles and scan synchronisation are verified over
   HTTP against this service; no handheld scanner fleet has been exercised, so the
   distribution and staleness behaviour is untested on real devices.

## Gates and evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Formatting | pass | `evidence/format.log` |
| Lint (ruff, security rules enabled) | pass | `evidence/lint.log` |
| Strict typing (mypy strict, no explicit `Any`) | pass | `evidence/type.log` |
| Static security scan (bandit) | pass, no findings | `evidence/security.log` |
| Dependency advisories (pip-audit, strict) | pass, none known | `evidence/audit-deps.log` |
| Tests | pass | `evidence/tests.log` |
| Coverage | branch coverage on, threshold enforced | `evidence/tests.log` |
| Import / entry points | pass | `evidence/import-check.log` |
| Migration upgrade → downgrade → upgrade | pass | `tests/integration/test_migrations.py`, CI job `quality` |
| Image build | pass | CI job `image` |
| Deployment / restore / rollback drill | **not run** | no target environment |
| Real external integrations | **not run** | no credentials |
| Edge configuration loads and refuses | pass | `scripts/verify_edge.sh` |
| Shipped realm imports into Keycloak, token verification | pass | `tests/integration/test_keycloak_realm.py` |
| Shipped policy schema accepted, delegation decided | pass | `tests/integration/test_permify_engine.py` |
| Edge TLS / mutual TLS / WAF in prevention mode | **not run** | no certificates, no deployment |
| Identity provider operated for real staff | **not run** | no provisioned directory |

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

Phase 3 adds a public consumer verification channel, enforcement case management with
seizures and chain of custody, programme KPI reporting, observed revenue-at-risk analytics,
signed offline revocation bundles, replay-protected offline scan synchronisation and
deterministic explainable risk scoring. Prosecution/court filing remains unimplemented and
is declared as such in `docs/FEATURE_CLAIMS.md`; it is not simulated.

### Phase 4 evidence and deliberate limits

Phase 4 adds the edge, the federated identity boundary and an external authorisation
engine: an APISIX gateway in front of the API, a Keycloak realm the platform validates
tokens *from*, a Permify policy engine that may narrow decisions and grant delegated
cross-tenant reads, and a documented WAF integration point.

- **The edge really loads and really refuses.** `scripts/verify_edge.sh` starts APISIX
  against the declarative configuration in `deploy/edge/apisix.yaml` and proves, over HTTP,
  that an oversized public verification body is rejected with 413 before it reaches the
  application and that the public quota returns 429 once exhausted. The gateway does not
  retry, because idempotency here is keyed rather than URL-derived, so a blind edge retry
  would duplicate work.
- **The edge is additional, never a substitute.** The application keeps its own
  authentication and its own rate limits: a request that bypasses the gateway - an operator
  port-forwarding the service - is still authenticated and still limited.
- **The realm imports and is a real provider.** `tests/integration/test_keycloak_realm.py`
  runs against a live Keycloak with the shipped realm and asserts the discovery document
  advertises authorisation code with PKCE (`S256`), signs with an algorithm the verifier
  accepts, and publishes a usable key set. A token minted by a different provider for the
  same issuer and audience is refused, so the realm's key set is the only key set that
  opens the API.
- **The provider says who, never what.** A verified subject grants access only when an
  administrator has linked it to an active principal; role, tenant and audit identity come
  from the platform's own record. The database refuses to link one subject to two
  principals, and refuses to federate a device at all - a handheld must keep verifying a
  stamp while the identity provider is unreachable.
- **Token verification is strict by construction.** Asymmetric algorithms only (`alg=none`
  and symmetric algorithms are refused), issuer and audience enforced, `exp`/`iat`/`nbf`
  validated with bounded leeway, required claims mandatory, unknown signing keys refused,
  and a provider outage classified as "unavailable" rather than as a bad token. Supervisory
  roles cannot hold a federated session without a multi-factor assertion in `amr` or `acr`.
- **Authorisation cannot be escalated from outside.** The local role table is the policy of
  record: the engine is consulted only after a local check has already permitted the
  action, so no engine configuration can widen a role. `tests/integration/test_permify_engine.py`
  proves against a live Permify that the shipped schema is accepted, that a delegated
  reader may read the company it was granted on and not another, that delegation never
  confers a write, and that enforcing mode refuses a subject with a permitted role but no
  relationship.
- **The engine fails closed, and is proven before it is trusted.** In `shadow` mode
  disagreements are counted and the local decision stands; in `enforcing` mode an answer
  the engine cannot give refuses the request. An outage never admits.
- **Configuration cannot be half-applied.** An audience without an issuer, an unknown
  authorisation mode, an engine mode without an engine, or a plaintext identity endpoint in
  production are all refused at startup.

Deliberate limits, each recorded as an outstanding condition below:

- No TLS, no mutual TLS for the device fleet and no WAF are proven. The gateway
  configuration carries the TLS and client-CA stanzas commented out because certificates do
  not exist here; `deploy/edge/openappsec.md` is an integration point and a rollout order,
  not a deployed WAF.
- Nothing here is evidence of a production deployment. A Compose profile that starts three
  containers on one host proves the code paths and the configuration; it does not prove
  availability, and `enforcing` authorisation must not be selected until the engine is
  operated highly available.
- Provisioning, joiner/mover/leaver lifecycle and MFA enrolment for real staff are
  programme activities that have not happened.

### Phase 3 evidence and deliberate limits

- **Consumer channel discloses nothing confidential.** The public answer carries only the
  outcome, brand, category and intended market; company, order, licence and movement data
  are absent by construction. Attempts are recorded against a keyed pseudonymous
  fingerprint rather than the caller's address, and the endpoint is rate limited per
  client. Authenticity is never asserted from an image or a confidence score.
- **Separation of enforcement powers.** An investigator may open a case and attach
  evidence; only a supervisor or admin may refer or close one, and never the officer who
  opened it. Evidence must reference a record that exists (witness statements excepted),
  is append-only, and a case cannot close while goods remain in custody.
- **Custody is chained.** Each handover is sequence-numbered and hash-chained to its
  predecessor; a forced edit made with the append-only trigger disabled is detected and the
  first broken sequence is identified. Handovers must start from the current custodian, run
  forward in time, and stop once goods leave custody.
- **Seizure duty is priced at the tariff effective when the goods were taken**, in exact
  minor units, not at today's rate.
- **Reporting cannot double count.** Windows are half-open, verified by two adjacent
  windows over the same instant. Every figure states its basis.
- **Revenue at risk is exposure, not a receivable.** It is itemised by evidence source,
  never presented as a single deduplicated liability, states that components may overlap,
  and contains no extrapolation to unobserved trade.
- **Offline answers are one-sided.** The revocation filter can produce a false "possibly
  revoked" but never a false "clean"; a negative answer is documented in the bundle itself
  as proving only "not on this revocation list". Bundles are signed, sequenced and bounded
  by `valid_until`; the signing key and the filter key are separate, so possession of a
  distributed bundle does not permit minting one.
- **Device verdicts are never trusted.** Synchronisation re-decides every scan server-side;
  a batch replayed identically is idempotent, the same sequence with different contents is
  a conflict, reused nonces are counted as duplicates, and stale or future captures are
  refused.
- **Risk scores are reproducible.** Deterministic weighted counts of stored records with a
  per-factor cap, a versioned rule set, a stated observation window and an explanation per
  contribution. No learned parameters exist anywhere in the codebase.

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
7. Agree with the prosecuting authority how a referral is transmitted and acknowledged, and
   implement it against that contract; today a referral is an internal record only.
8. Exercise offline bundles and scan synchronisation on the real handheld fleet, including
   bundle distribution, expiry and reconnection after a long outage.
9. Deploy the edge with real certificates, issue client certificates to the device fleet,
   turn open-appsec from detection to prevention after a false-positive review, and record
   an external penetration test against the deployed edge.
10. Provision the identity provider for real staff with an operated joiner/mover/leaver
    lifecycle and enforced MFA enrolment, and record the linkage of each provider subject
    to its platform principal as an auditable administrative act.
11. Operate the policy engine highly available, run it in `shadow` mode until
    `taxstamp_authz_shadow_disagreements_total` is understood and stable, and only then
    select `enforcing`.
