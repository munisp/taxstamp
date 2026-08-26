# Mission-Critical Code Assurance Report

**Author:** Manus AI. **Assessment timestamp:** 2026-08-26T11:47:15Z. **Method:** User-endorsed independent mission-critical assurance framework.

## A. Release Decision

**Status: BLOCKED.** **Assurance score: 43.00 / 100.** The target is `https://github.com/munisp/taxstamp.git`, branch `devin/1787593004-tax-stamp-platform`, baseline commit `09bd499532a0cf829d5bd35232443131c7fb31d6`, assessed as a dirty local working tree with 87 tracked modifications. The repository and separate PWA were built and tested locally, against real disposable PostgreSQL/Redis and the local non-production service overlay where available. The platform has substantive local control-plane, authorization, accounting, audit, migration, polyglot, container, mobile, and PWA evidence. It is nevertheless blocked because critical funds, regulatory, identity, encryption, provider, deployment, recovery, legal, and operational requirements have no real approved environment or authoritative evidence. This is an engineering evidence assessment, **not** legal advice, a compliance certification, or a production-release approval.

> A passing local test, local container, config attestation, or interface boundary does not prove a live provider, encrypted storage, regulated operating control, or deployable production system. The score communicates current evidence maturity only; it cannot waive a blocker.

## B. Mandatory-Gate Checklist

| Mandatory gate | Required evidence | Executed command/environment | Observed result | Status |
|---|---|---|---|---|
| Claim and checked-TODO traceability | Stable claims, implementation, evidence and residual risk | `docs/CLAIM_COVERAGE_INVENTORY.md`; local `todo.md` review | Material claims are catalogued; current working tree is not committed | Pass for inventory; release blocker remains |
| Python formatting/lint | All source, tests, scripts and migrations | `make lint` in pinned `.venv` | Passed, including formatted initial migration | Pass |
| Python typing/security/dependencies | Mypy, Bandit, pip-audit and package resolution | `make type`; `make security`; `make audit-deps`; `pip check` | 59 mypy source files clean; Bandit clean after explicit converter guard correction; no known Python advisories; no broken requirements | Pass |
| Python package build | Pinned backend and generated wheel | `make package` | Built `taxstamp-1.0.0-py3-none-any.whl` | Pass |
| Real local dependency integration | Disposable PostgreSQL and Redis, actual migrations | Loopback Compose services; `pytest -q`; Alembic cycle | 154 passed, one existing non-failing Starlette/httpx deprecation warning; migration cycle and 3 migration tests passed | Pass locally |
| Funds invariants/idempotency | Exact amounts, balanced journals, duplicate/replay/concurrency controls | Full Python suite against local services | Passed | Pass locally |
| Go verification | Immutable module, vet and race test | `go list -mod=readonly ./...`; `go vet ./...`; `go test -race ./...` | Passed | Pass |
| Rust verification | Format, warnings-as-errors lint and tests | `cargo fmt --check`; `cargo clippy --all-targets -- -D warnings`; `cargo test` | Passed; 5 unit tests and doc tests clean | Pass |
| Rust dependency advisory scan | Compatible RustSec scan | `cargo-audit 0.21.1 audit` | Scanner failed parsing CVSS 4 advisory metadata under Rust 1.75 | **Blocker** |
| Mobile foundation | Frozen install, typecheck, Expo config and production audit | `apps/mobile`: pnpm frozen install, `tsc`, `expo config --type public`, audit | Passed; no known production advisories | Pass locally |
| PWA channel | Frozen install, typecheck, tests, build, audit and CI workflow lint | Separate PWA project; 7 Vitest tests; actionlint | Passed; separate checkpoint `75b9913c` | Pass locally |
| Workflow/container syntax | CI syntax, Compose overlays, runtime image | actionlint; three `docker compose config --quiet`; `docker build --target runtime` | Passed; image user `10001:10001` | Pass locally |
| Gateway/non-production E2E | Local Keycloak/APISIX/Permify/Kafka/metrics health and anonymous denial | `sudo bash deploy/nonprod/validate-local.sh`; loopback curl | Health/Kafka topic passed; `/v1/readiness` returned 401 without bearer token | Pass locally only |
| Real provider integrations | Official FIRS/NAFDAC/SON/Customs, PSP/bank, Mojaloop, TigerBeetle evidence | No approved accounts/credentials/sandboxes supplied | Not executed | **Blocker** |
| Encryption, backup and restore | Real KMS/HSM, encrypted stores, snapshot/restore/rotation evidence | Synthetic configuration/attestation gate only | No live provider or restore proof | **Blocker** |
| Deployment/rollback/DR/performance | Production-shaped target deployment and exercised operations | No target environment or operator authority supplied | Not executed | **Blocker** |
| Compliance applicability | Legal entity/licence/control-owner decisions | Engineering documents only | No counsel/DPO/CBN/scheme approval | **Blocker** |

## C. Claims and Traceability Coverage

The complete claim map is version-controlled in [`CLAIM_COVERAGE_INVENTORY.md`](./CLAIM_COVERAGE_INVENTORY.md). The most material verified-local chain is: authorized order intake → server pricing → maker-checker → signed settlement ingestion → integer-minor-unit balanced journal → durable idempotency/outbox → resumable issuance → append-only audit/reconciliation. The most material unverified chain is the one that crosses any actual counterparty: provider credentials, real status lookup, settlement finality, regulated tax data, encrypted backup/restore, deployment, and operator response. [1] [2]

| Coverage class | Local evidence conclusion | Release implication |
|---|---|---|
| Core API/database controls | Strong repository and real local PostgreSQL/Redis evidence | Does not certify external funds movement or regulated pricing. |
| Local platform overlay | Health, Kafka topic and anonymous APISIX denial passed under loopback-only Compose | Does not prove authenticated claims, TLS/mTLS, WAF/rate controls or internet exposure. |
| Mobile/PWA | Independent build/type/test/audit evidence exists | No device trust/offline or production API/browser journey proof. |
| Compliance/KMS | Fail-closed configuration and synthetic evidence check exist | Not a live encryption, backup or compliance attestation. |

## D. Evidence Log

All commands below were actually executed in the local sandbox on the stated dirty working tree. Secrets were not printed; disposable services used documented local-only development values and were torn down with volumes after validation.

| Work directory | Command group | Environment | Exit/result |
|---|---|---|---|
| `/home/ubuntu/taxstamp` | `make lint`, `make type`, `make security`, `make audit-deps`, `pip check`, `make package` | Python 3.12.3, pinned development dependencies | Exit 0; wheel built; 59 mypy files clean; no known Python advisories. |
| `/home/ubuntu/taxstamp` | `pytest -q` after the attached-framework remediation | Real loopback PostgreSQL 16.6 and Redis 7.4 | Exit 0; 154 passed in 14.48s; one non-failing dependency deprecation warning. |
| `/tmp/taxstamp-auth-mutation-src` | Targeted `tests/e2e/test_authorization.py -k batch` after removing only requester company-scope enforcement from a temporary source copy | Fresh disposable loopback PostgreSQL/Redis; production repository unchanged | Expected exit 1: `test_requester_cannot_read_another_tenants_batch` failed, proving the regression rejects the reintroduced cross-tenant read defect. Temporary source, containers, network, and volume were removed. |
| `/home/ubuntu/taxstamp` | Alembic upgrade → base downgrade → upgrade; migration test | Same disposable PostgreSQL | Exit 0; head `4bf6b1f5f0ab`; 3 migration tests passed. |
| `adapters/go/gatewaypolicy` | `go list -mod=readonly`, `go vet`, `go test -race` | Go 1.22.2 | Exit 0. |
| `adapters/rust/ledger-boundary` | Rust fmt, Clippy `-D warnings`, tests | Rust/Cargo 1.75.0 | Exit 0; 5 tests passed. |
| `adapters/rust/ledger-boundary` | `cargo-audit 0.21.1 audit` | RustSec advisory DB | Exit 1; CVSS 4 parsing unsupported by compatible local scanner. Recorded as B-008, not treated as pass. |
| `/home/ubuntu/taxstamp` | Compose syntax, runtime image build, actionlint | Docker 29.1.3/Compose 2.40.3; actionlint 1.7.7 | Exit 0; all base/local/persistent configs valid; runtime image non-root `10001:10001`. |
| `/home/ubuntu/taxstamp` | `deploy/nonprod/validate-local.sh`; anonymous gateway curl | Disposable local Keycloak, APISIX, Permify, Kafka, Prometheus, Grafana, PostgreSQL, Redis | Exit 0; readiness endpoints and Kafka topic passed; anonymous gateway response was HTTP 401; all containers/volume removed afterward. |
| `/home/ubuntu/taxstamp/apps/mobile` | Frozen install, TypeScript, Expo public config, production audit | pnpm 11.21.0; Expo SDK 57 dependency graph | Exit 0; no known production advisories. |
| `/home/ubuntu/taxstamp-pwa` | Frozen install, typecheck, tests, build, audit, actionlint | pnpm 10.4.1, Node 22.13.0 | Exit 0; 7 Vitest tests passed; no known production advisories; PWA checkpoint `75b9913c`. |

## E. Findings and Fixes

The complete lifecycle ledger is [`ASSURANCE_REMEDIATION_LEDGER.md`](./ASSURANCE_REMEDIATION_LEDGER.md). Fourteen in-scope local findings were remediated and re-tested. The six latest corrections arise from the user-supplied defect-discovery framework: authenticated device-identity binding; source-CIDR-bound proxy-header trust; bounded unmatched-route metric labels; mandatory non-wildcard staging/production host policy; cross-principal idempotency replay rejection; and an explicit fail-closed converter guard in place of a runtime assertion. The discovery map and source evidence are recorded in [`CODEBASE_DEFECT_DISCOVERY_REPORT.md`](./CODEBASE_DEFECT_DISCOVERY_REPORT.md). [3]

Repository-wide placeholder/mock review produced 42 textual hits. They were classified as fail-closed placeholder validation, explicit local-only/documented configuration, marker exception classes, bounded worker error handling, intentionally unavailable capabilities, or unit-test-only doubles. One material product gap was confirmed: `handle_notify` records an audit event but has no external delivery adapter; it is B-011, not a delivered-notification claim. The only test doubles are narrowly scoped deterministic TigerBeetle branch behavior and a unit-level `Mock`; neither is counted as live integration or release evidence. The strict no-test-doubles policy was not applied to unit tests, but no test double was used as evidence for a provider, funds-transfer, encryption, or production-security outcome.

## F. Funds-Flow Assurance

Local verified invariants include integer minor-unit amounts, exact payment matching, deferred balanced journals, append-only/hashing controls, idempotency payload binding, database uniqueness/concurrency protections, outbox persistence, and reconciliation findings. Python integration/concurrency/fault coverage exercised local duplicate, mismatch, replay, retry, issuance-resume and integrity-detection behavior against real local PostgreSQL/Redis. [1]

| Scenario class | Expected durable local result | Local result | Limitation |
|---|---|---|---|
| Same idempotency key / same payload | One business effect and replayed durable response | Covered by repository tests; full suite passed | Provider-side idempotency remains unverified. |
| Same key / divergent payload | Reject conflicting reuse | Covered by local tests | No external provider operation identity proof. |
| Settlement amount mismatch or unknown reference | No paid order/incorrect ledger effect; receipt is controlled | Covered by local tests | No bank/PSP callback contract. |
| Journal imbalance/tampering | Database trigger/reconciliation detects violation | Covered by local tests | Superuser/operational access model and external subledger proof are missing. |
| Transfer retry/unknown result | Durable TigerBeetle intent requires lookup-before-retry path | Local deterministic branch and intent persistence evidence | No real TigerBeetle request, timeout-after-acceptance, compensation, or cluster reconciliation. |

This is **not** an end-to-end funds-release decision. No real money, provider sandbox settlement, bank/PSP feed, TigerBeetle cluster, Mojaloop scheme, or official regulator was contacted.

## G. Security and Operational Assurance

The assessed threat surface includes tenant authorization, forged/replayed settlement, duplicate issuance, ledger/audit tampering, provider outage, gateway access, secret exposure, and supply-chain dependency risk. Local evidence supports the cross-tenant batch-read fix, server-side authorization tests, signed settlement controls, loopback-only published disposable ports, APISIX anonymous denial, non-root runtime image, Python dependency audit, mobile/PWA production audits, and PWA CI audit enforcement. [1] [4]

Operational evidence is materially incomplete. No real secrets manager, certificate authority, production identity administration, WAF, rate-limit policy, log/alert ownership, encrypted backup, restore rehearsal, incident exercise, rollback, capacity budget, load test, or external penetration test was authorized or available. The local stack is disposable and was torn down; it is not an operated non-production or production environment.

## H. Scorecard

| Domain | Weight | Score / 5 | Weighted points | Evidence-based rationale |
|---|---:|---:|---:|---|
| Requirements, correctness and completeness | 15 | 2.0 | 6.00 | Core flow evidence exists, but authoritative tax/business rules and several promised capabilities are absent. |
| Reproducible build, code quality and static verification | 10 | 4.0 | 8.00 | Local Python/Go/Rust/mobile/PWA/build gates passed; Rust advisory scan remains incomplete and no provenance/SBOM evidence exists. |
| Functional, contract, integration and E2E testing | 15 | 2.0 | 6.00 | Real local database/cache and local overlay evidence exist; external providers and authenticated production-like E2E do not. |
| Funds integrity, atomicity, idempotency and reconciliation | 20 | 2.5 | 10.00 | Strong local controls and tests; no live payment/subledger/provider finality/recovery proof. |
| Security, privacy and abuse resistance | 15 | 2.0 | 6.00 | Local authorization and supply-chain fixes pass; production identity, secrets, TLS, WAF, privacy operations and penetration evidence are missing. |
| Reliability, recovery and operational readiness | 10 | 1.0 | 2.00 | Local health/outbox controls exist; no restore, DR, incident or operated-environment proof. |
| Performance and capacity | 5 | 0.0 | 0.00 | No approved budgets or realistic load/soak results. |
| Deployment, configuration and supply chain | 5 | 3.0 | 3.00 | Image, Compose, CI and package evidence pass locally; no signed artifact, target deployment, rollback or provenance. |
| Compliance, documentation, auditability and lifecycle | 5 | 2.0 | 2.00 | Good engineering documentation/audit design; no applicability approval, owner evidence, retention/rights/recovery proof. |
| **Total** | **100** |  | **43.00** | **A mandatory blocker overrides this score.** |

## I. Compliance and Audit-Trail Evidence

| Control profile | Applicability decision owner | Engineering evidence | Result | Outstanding evidence |
|---|---|---|---|---|
| Nigerian NDPA/GAID | Legal/DPO not supplied | Technical assessments and data-residency/encryption analysis | Engineering-only, not certified | Applicability, lawful basis, retention, rights workflows, transfer decisions and DPO sign-off. |
| CBN/payment/scheme controls | Legal entity/licence/counsel not supplied | Maker-checker, ledger, reconciliation, audit and KMS evidence boundaries | Engineering controls partially implemented | Licence/scheme applicability, limits, KYC/AML/sanctions scope, provider oversight and approved control matrix. |
| Storage encryption/KMS/HSM | Security/cloud owner not supplied | Strict synthetic attestation checker and CI fixture gate | `evidence_attested_not_live_verified` only | Real KMS/HSM policy, keys, encrypted stores/backups, access review, rotation and restore. |
| Audit trail | System/control owner not supplied | Append-only tables/triggers, hash chain, audit events and tamper tests | Local design/test evidence | Retention schedule, access model review, export/retrieval, restore, independent immutable store/WORM decision and production separation-of-duties proof. |

Audit events include stable IDs, timestamps, actor/role/tenant context, action, target, outcome, request/idempotency references and chained integrity material in the local design. Application-level ordinary mutation is constrained by append-only triggers and test coverage, but privileged database access, long-term retention, clock governance, export/restore, and production segregation-of-duties have not been independently exercised. [1] [5]

## J. Lifecycle Test Plan and Release Follow-Through

| Priority | Trigger/cadence and environment | Owner role | Pass condition and escalation |
|---|---|---|---|
| P0 | Every pull request: Python/Go/Rust/mobile/PWA static, unit, dependency and workflow gates | Engineering owner | All gates clean; any vulnerability or lint/test failure blocks merge. |
| P0 | Every merge and nightly: disposable PostgreSQL/Redis/Kafka/Keycloak/APISIX/Permify integration matrix | Platform/SRE owner | Actual migrations, authenticated tenant/role matrix, outbox/retry/reconciliation checks pass; page owner on failure. |
| P0 | Before sandbox and production promotion: real provider test suite | Payments/integration owner with compliance approval | Official sandbox signed positive, negative, timeout, replay, reconciliation and status-lookup evidence passes; block promotion otherwise. |
| P0 | Before production: KMS/HSM, encrypted storage, backup/restore, rollback, access-review and DR rehearsal | Security/SRE owner | Recorded RTO/RPO, restore integrity, key recovery/rotation and rollback evidence; block release on failure. |
| P1 | Quarterly and after material change: load, soak, chaos and concurrency test | SRE/engineering owner | Approved capacity/error/queue/latency budgets met; create incident/remediation for breach. |
| P1 | Continuous: dependency, secret, SBOM/provenance and image scanning | Security/release owner | No unaccepted material issue; rotate/revoke exposed credentials and prevent artifact promotion. |
| P1 | Quarterly: legal/DPO/payment/control-owner review | Compliance owner | Approved applicability and retention/rights/third-party record remains current; suspend scoped processing if invalid. |

## K. Explicit Open Blocks

1. **Authoritative money/tax rules:** supply approved source, owners, effective-date governance, exception policy, and test corpus.
2. **Real counterparties:** provide approved TigerBeetle, Mojaloop, PSP/bank, FIRS/NAFDAC/SON/Customs sandbox credentials and operating agreements; implement/verify real clients rather than treating local contracts as provider evidence.
3. **Identity/gateway authorization:** create production realm/client/claim/tenant model, Permify decision integration, TLS/mTLS/cert lifecycle, WAF/rate limits, and authenticated negative E2E matrix.
4. **Encryption/resilience:** provide real KMS/HSM, encrypted primary/replica/cache/search/backup scope, key rotation/recovery/access review, backup restore and retention evidence.
5. **Deployment operations:** run a production-shaped deployment, canary/rollback, migration compatibility, incident, DR, performance/capacity, alerting and access-revocation exercises.
6. **Compliance governance:** obtain written Nigerian legal/DPO/CBN/payment-scheme applicability and control-owner approval; engineering documentation alone cannot satisfy this block.
7. **Unsupported product capabilities:** decide and record deferral or deliver/accept field-device trust/offline sync, printer/press, optical/taggant and ML authenticity capabilities where required.
8. **External notifications:** decide whether external operational notification is required; if so, implement provider delivery, recipient/escalation policy, retries, auditable delivery outcomes and failure handling.
9. **Rust advisory scan:** run an approved compatible Rust vulnerability scanner after resolving the Rust 1.75/CVSS-4 tool compatibility mismatch.

The remediation ledger totals are **14 `VERIFIED_FIXED`** local findings and **11 `EXTERNAL_BLOCKED`** findings. The added local verification does not change the external evidence position or the mandatory-blocker override. This report remains a **BLOCKED progress report**, not completed production assurance.

## References

[1]: ./FEATURE_CLAIMS.md "Feature claim manifest"

[2]: ./CLAIM_COVERAGE_INVENTORY.md "Mission-Critical Claim and Coverage Inventory"

[3]: ./ASSURANCE_REMEDIATION_LEDGER.md "Mission-Critical Assurance Remediation Ledger"

[4]: ./CODE_FEATURE_COMPLETENESS_ASSESSMENT.md "Code Feature Completeness and Outstanding Gaps"

[5]: ./KMS_HSM_STORAGE_ENCRYPTION_IMPLEMENTATION.md "KMS/HSM and storage-encryption implementation record"
