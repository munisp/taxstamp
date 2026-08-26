# Codebase Defect Discovery Report

**Framework:** User-supplied *Master Prompt — Encompassing Codebase Defect Discovery*.
**Target:** Dirty working tree on branch `devin/1787593004-tax-stamp-platform`, baseline `09bd499532a0cf829d5bd35232443131c7fb31d6`.
**Discovery rule:** No production-code remediation was made during this discovery pass. Findings below are based on traced executable paths, not comments, UI copy, or configuration labels.

## Executive summary

The core order, settlement, accounting, audit, issuance, outbox, and authorization paths have substantial local controls: server pricing, tenant checks, database constraints, transactions, idempotency records, unique external references, HMAC signatures, and append-only audit events. The discovery pass confirmed four local defects with code-only remediation paths and three external/incomplete capability blocks. It also confirmed that the earlier local provider, KMS/HSM, regulated-rule, deployment, and real-counterparty limitations remain release blocks rather than hidden simulated success paths.

| Classification | Count | Summary |
|---|---:|---|
| `CONFIRMED` and locally actionable | 5 | Device identity binding, TLS forwarded-header trust, unbounded metrics labels, missing production trusted-host validation, and cross-principal idempotency response replay. |
| `SUSPECTED` / needs product decision | 1 | `PaymentIntentStatus.MISMATCHED` exists but mismatch receipts intentionally leave an intent open for later correct settlement; no safe semantic change is inferred. |
| `EXTERNAL_BLOCKED` / intentionally unavailable | 3 | Per-device signing/provisioning, external notification delivery, and real provider/regulated-production evidence. |
| Negative results | 12 F-family dispositions | No confirmed fabricated settlement, direct money float arithmetic, raw-SQL injection, HMAC timing comparison, silent registry approval, or unregistered outbox event was found. |

## Ground-truth maps

| Map | Evidence from executable/configuration paths | Result |
|---|---|---|
| Service map | `src/taxstamp/api/app.py:29-84` registers API routers; `src/taxstamp/worker/main.py` runs the relay; `docker-compose.yml` and `deploy/nonprod/docker-compose*.yml` declare API, worker, PostgreSQL, Redis, Kafka, Keycloak, APISIX, Permify and observability containers. Local publications are loopback-bound. | Local topology is traceable. Production topology remains external. |
| Money map | `models.py:164-397` defines orders, payment intents/receipts, journals and ledger entries; `services/payments.py:51-197` locks and posts settlement journals; migrations enforce unique references and balance trigger. | Local value state is constrained and transactionally updated. |
| Trust-boundary map | `api/routers/payments.py:29-93` handles signed remittances; `api/routers/verification.py:30-103` handles signed device verification; `providers/base.py:55-87` performs provider calls; `worker/relay.py` dispatches durable outbox messages. | Provider boundaries fail closed when unavailable; real counterparties remain unverified. |
| Gate map | `services/orders.py:112-218` enforces role, company, KYB, tariff and compliance gates; `api/deps.py:62-130` authenticates credentials and validates idempotency keys; `services/context.py` supplies role/company checks. | Gates are present in core paths. Four ingress/configuration gaps are recorded below. |
| Config map | `config.py:41-247` validates secrets, TLS, CORS, endpoints and storage evidence; `runtime.py:79-115` wires providers and intentionally leaves the TigerBeetle client unset. | Non-production defaults are visible; production host/proxy validation needs remediation. |

## Findings by defect family

| ID | Family | Finding | Evidence | Classification / severity | User-, operator-, or regulator-facing discrepancy |
|---|---|---|---|---|---|
| DD-001 | F3, F6, F8, F15 | A valid device credential may report any body `device_id`; that untrusted identifier drives nonce/velocity policy and durable verification/audit evidence. | `api/routers/verification.py:38-40,72-91`; `services/verification.py:88-122,134-143`; `models.py:517-541`. | `CONFIRMED` / High | “Device-signed” verification is true for a shared request signature but the reported device identity is not bound to the authenticated principal. |
| DD-002 | F6, F10, F12, F16 | TLS enforcement accepts a client-supplied `X-Forwarded-Proto: https` header without a configured trusted-proxy boundary. | `api/middleware.py:35-43,61-66`; `config.py:63-65,187-223`. | `CONFIRMED` / High | A direct HTTP caller can be told that TLS is required but bypass the check by sending a header normally set by a proxy. |
| DD-003 | F15 | Metrics labels use `request.url.path` before routing is complete, enabling arbitrary path values to create unbounded Prometheus time series. | `api/middleware.py:45-54`. | `CONFIRMED` / Medium | Operators may see memory/cardinality exhaustion caused by hostile paths rather than bounded route telemetry. |
| DD-004 | F10, F16 | Staging/production configuration permits an empty `trusted_hosts`, so `TrustedHostMiddleware` is not installed. | `config.py:64-65,187-223`; `api/app.py:66-67`. | `CONFIRMED` / Medium | Deployment configuration appears hardened but accepts arbitrary Host headers unless an operator independently configures the optional list. |
| DD-005 | F5, F8 | Durable idempotency reads a record by `(scope, key)` without checking the stored principal before replaying its response. A different principal reusing the same key and identical payload can receive the first caller’s durable response. | `idempotency.py:47-77`; `models.py:544-566`; `api/idempotent.py:33-49`. | `CONFIRMED` / Medium | An idempotency key is described as a client retry token, but it is globally replayable within a scope instead of being bound to the authenticated principal. |
| DD-006 | F11, F14 | `PaymentIntentStatus.MISMATCHED` is declared but no traced write sets it on a mismatch receipt. The current implementation keeps the intent open while quarantining the received value. | `enums.py:74-79`; `services/payments.py:76-175`. | `SUSPECTED` / Low | The enum suggests a state that may not be a real lifecycle state; changing it could incorrectly prevent a valid later settlement. Requires payment-operations decision. |
| DD-007 | F1, F2, F15 | Notification outbox handling records `notification.recorded` in the audit trail but does not deliver to an external channel. | `worker/handlers.py:77-117`. | `EXTERNAL_BLOCKED` / Medium | Notification delivery must not be inferred from an audit record. A channel, recipient policy, retries and escalation are absent. |
| DD-008 | F6, F7 | Direct mobile field verification is intentionally not shipped because a per-device signing/gateway provisioning design is absent; server-side verification still relies on a global HMAC signing secret. | `apps/mobile/App.tsx:47-52,84-92`; `api/routers/verification.py:59-65`. | `EXTERNAL_BLOCKED` / High | A real device enrollment/key lifecycle/gateway signing solution is required before direct field verification is a production channel. |
| DD-009 | F1, F2, F4, F5, F9, F10, F12, F13, F14 | Negative result: settlement ingest is not fabricated; it is HMAC-verified, replay-guarded, locks the payment intent, writes a unique receipt, posts balanced integer-minor-unit journals and uses a transaction. Provider failures are rejected. | `api/routers/payments.py:29-93`; `services/payments.py:51-197`; `models.py:256-397`; `providers/base.py:47-87`. | `CLEAN` | No confirmed local phantom settlement, float-money, raw-SQL injection or fail-open compliance path. Real PSP/bank evidence remains external. |
| DD-010 | F2, F16 | Negative result: local service URLs are restricted to declared Compose service names or explicit configuration endpoints; local exposure is loopback-only. | `docker-compose.yml`; `deploy/nonprod/docker-compose.local.yml`; `deploy/nonprod/apisix/apisix.local.yaml`; `runtime.py:79-115`. | `CLEAN` locally | Local topology is not proof of production DNS, TLS, certificates, or provider reachability. |

## Cross-family composition

The main confirmed composition chain is **transport-control bypass × operator-visible telemetry degradation**: a direct caller can forge `X-Forwarded-Proto` to reach an HTTP listener that should have rejected the request (DD-002); distinct adversarial path values can then create unbounded route-label series (DD-003). The first defect is a transport policy bypass and the second is an availability/observability concern. No evidence currently shows this chain can directly create or move value because money routes retain independent authentication, signature, authorization, and database controls.

The potential device-audit composition is **stolen device credential × arbitrary payload device identifier × velocity/replay evidence pollution** (DD-001). This is high severity for audit integrity and field-abuse detection. It is not classified as direct money loss because `/v1/verify` does not mutate a ledger or payment state.

## Discovery completeness checklist

| Required question | Result |
|---|---|
| Internal service URLs cross-checked against the service map | Yes for executable/local configuration references; external configured endpoints remain external blocks. |
| Money mutations traced for atomicity, idempotency and gates | Yes for order submission, approval, remittance ingestion, issuance/outbox, and TigerBeetle intent boundary. |
| Control-plane failures assessed for fail-open polarity | Yes; provider, Redis replay/rate limits and readiness paths fail closed or honestly report unavailable. |
| Displayed numbers traced | Core API metrics/audit/readiness paths sampled; DD-003 records the route-label defect. |
| Declared gates checked in execution paths | Yes for core role/company/KYB/tariff/compliance/idempotency gates; DD-001 and DD-004 record missing enforcement. |
| Composition chain attempted | Yes; TLS/metrics and device-audit chains are documented above. |
| Independent adversarial verification | Pending remediation phase; every local fix will receive a focused regression test and an independent review/sweep. |

## Remediation boundary

The next phase may safely correct DD-001 through DD-005 without a schema migration. DD-006 is held pending a payment-operations decision; DD-007 and DD-008 require an external product/security architecture decision and approved provider infrastructure. None may be represented as resolved by this report alone.

## Remediation and verification update

The local corrections identified above have now been implemented without introducing a schema migration or weakening any existing control. DD-001 now requires `device_id` to equal the authenticated actor subject; DD-002 accepts forwarded transport headers only from configuration-validated proxy CIDRs; DD-003 uses post-routing labels with the constant `<unmatched>` fallback; DD-004 requires non-wildcard production/staging host policy; and DD-005 rejects cross-principal idempotency replay before returning a durable response. A Bandit-discovered runtime `assert` in the attestation converter was also replaced with an explicit fail-closed branch.

| Verification layer | Executed evidence after remediation | Result |
|---|---|---|
| Focused control regressions | Configuration, TLS/proxy CIDR, metrics-label, device-binding, idempotency, attestation converter tests. | Passed; the device-binding negative case and unlisted-proxy rejection are explicit. |
| Core Python | Ruff format/check, Mypy for 59 source files, Bandit over `src`/`scripts`, pip-audit, 154-test suite, migration round-trip. | Passed; one pre-existing Starlette/httpx deprecation warning remains non-failing. |
| Multi-language and mobile | Go module list/vet/race tests, Rust fmt/Clippy/test, Expo frozen install/typecheck/configuration/audit. | Passed; mobile audit reported no known production vulnerabilities. |
| Deployment simulation | Base/local/persistent Compose validation, non-root runtime image build, actionlint, disposable local stack health/Kafka check, anonymous APISIX protected-route denial. | Passed locally; anonymous `/v1/capabilities` returned 401 through APISIX. |

The `SUSPECTED` payment-state question and all `EXTERNAL_BLOCKED` items remain deliberately unresolved. They require approved business, counterparty, device-provisioning, legal, operational, or production-environment evidence and are not closed by local code or simulation.
