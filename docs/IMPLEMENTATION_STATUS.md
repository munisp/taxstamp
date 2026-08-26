# Implementation Status and Readiness Roadmap

**Assessment date:** 2026-08-25
**Scope:** Current repository changes, PWA foundation, React Native foundation, and integration-ready adapter contracts.
**Assessment posture:** The score measures demonstrable repository and validation evidence. It does not treat a configured URL, generated interface, or unvalidated adapter as a live regulated-service integration.

## Current production-readiness score: 63 / 100

The score rises from the prior **52 / 100** assessment because the identified cross-tenant batch-read vulnerability is fixed and protected by an end-to-end regression test; configuration is now explicit and fail-closed for the requested services; integration state is disclosed without claiming false success; Python, Go, Rust, TypeScript/PWA, and React Native foundations are validated; and all repository quality gates now pass. The score remains below a regulated-production threshold because no external platform is provisioned, authenticated, conformance-tested, reconciled, or operated in a production-like Nigerian environment.

| Area | Weight | Evidence now available | Score |
|---|---:|---|---:|
| Core application security and tenancy | 20 | Requester batch reads are company-scoped; authorization regression coverage and static checks pass | 15 |
| State, audit, and local resilience | 15 | PostgreSQL/Redis-backed application controls and existing reconciliation logic are tested | 10 |
| Integration architecture | 15 | Honest configuration boundaries, capability states, Python manifest, Go gateway policy, and Rust ledger boundary | 7 |
| Identity, ingress, and authorization operations | 15 | Keycloak/APISIX/openAppSec/Permify are represented as integration-ready only | 4 |
| Eventing, search, and analytics operations | 10 | Kafka/OpenSearch/Dapr/Fluvio/lakehouse are declared but not deployed or replay-tested | 3 |
| Payment and financial settlement | 10 | TigerBeetle/Mojaloop boundaries exist; no scheme onboarding, settlement, or reconciliation proof | 2 |
| PWA and mobile delivery | 10 | Responsive PWA, React Native foundation, secure local storage posture, and contract tests | 7 |
| **Total** | **100** | **Evidence-based current state** | **63** |

## Implemented repository work

| Domain | Delivered artifact | Validation |
|---|---|---|
| Authorization | `GET /v1/batches/{batch_id}` now joins the owning order and applies a requester company check before returning a batch | End-to-end cross-tenant denial plus owner success coverage |
| Python | Integration settings require HTTPS in non-development environments and secure Kafka protocols; capabilities distinguish `configured_not_verified` from implementation | Ruff, mypy, Bandit, pip-audit, and 114 pytest tests pass |
| Integration manifest | `taxstamp.integrations` reports configuration and required evidence for all requested external boundaries without making remote calls | Unit coverage passes |
| Go | `adapters/go/gatewaypolicy` emits a safe APISIX OIDC route fragment with HTTPS discovery, JWKS, TLS verification, PKCE, scopes, and bearer-only denial | `go test ./...` and `gofmt` verification pass |
| Rust | `adapters/rust/ledger-boundary` validates non-empty, cross-account, positive double-entry intents before a ledger client is called | `cargo test` and `rustfmt --check` pass |
| PWA | Ledger Workshop operations interface provides manual HTTPS endpoint configuration, API-reported capabilities, session-only bearer-token retention, and shell-only offline caching | Desktop and 390px mobile screenshots; 4 Vitest tests; TypeScript check pass |
| Native mobile | Expo/React Native foundation stores the endpoint with encrypted device storage and intentionally blocks field signing until device identity is approved | `pnpm typecheck` passes |

## What “integration-ready” means in this codebase

> An integration-ready boundary has typed configuration, a declared purpose, fail-closed deployment semantics, and tests for its local contract. It is **not** a substitute for a provider account, secret, network path, live conformance test, or production acceptance decision.

| Technology | Current state | Production evidence still required |
|---|---|---|
| PostgreSQL | Core application source of truth | HA design, encrypted backup/restore drill, RTO/RPO proof |
| Redis | Existing replay/rate-limit/lease component | TLS, Sentinel/cluster policy, failover, eviction, and recovery evidence |
| TigerBeetle | Rust transfer boundary and configured-address declaration | Cluster, immutable account/ledger mapping, dual-write/reconciliation, migration and rollback proof |
| Mojaloop | Typed endpoint declaration and architecture boundary | Scheme/PSP sponsorship, sandbox conformance, signed credentials, settlement reconciliation |
| Kafka | Secure configuration declaration and outbox target architecture | Cluster, ACLs, schemas, relay, DLQ, replay, and consumer lag evidence |
| APISIX | Go declarative OIDC route policy | Gateway deployment, secret injection, TLS/mTLS, route policy tests, controlled promotion |
| Keycloak | HTTPS issuer configuration declaration | Realm, clients, PKCE, claims, FAPI/OIDC policy decision, rotation, device revocation |
| openAppSec | Configuration declaration | Detection baseline, policy review, attack tests, enforcement observability |
| Permify | Configuration declaration | Authorization model, tuple migration, decision tracing, negative authorization tests |
| OpenSearch | Configuration declaration | Kafka projection, index permissions, retention, replay, and search access control |
| Fluvio | Optional edge-streaming declaration | Approved differentiated edge use case; avoid a duplicate primary event bus |
| Dapr | Sidecar endpoint declaration | Components, mTLS, resiliency policies, workload topology, and workflow evidence |
| Lakehouse | Catalog endpoint declaration | Governed immutable datasets, lineage, retention, data-classification, reporting validation |

## Evidence-gated path above 85 / 100

The estimated work is **20–30 engineering weeks of effort**, or approximately **14–20 calendar weeks** with a staffed cross-functional team working in parallel. These are delivery-planning estimates, not a guarantee; regulator, payment-scheme, and provider onboarding lead times are external dependencies and can extend the calendar plan.

| Phase | Estimated effort | Target readiness impact | Completion condition |
|---|---:|---:|---|
| 1. Secure ingress and identity | 3–5 engineering weeks | 63 → 72 | APISIX, Keycloak, openAppSec, and Permify deployed in a non-production environment; HTTPS/mTLS, PKCE, scoped clients, authorization-model tests, and negative access tests pass |
| 2. Event and projection platform | 3–5 engineering weeks | 72 → 78 | Kafka outbox relay, schema governance, DLQ/replay, OpenSearch projection, Dapr components, dashboards, and alert ownership are operating |
| 3. Financial and payment conformance | 5–8 engineering weeks | 78 → 83 | TigerBeetle account mapping and reconciliation are proven; Mojaloop or selected payment-switch sandbox certification and settlement exception workflow are complete |
| 4. Mobile field-security completion | 3–4 engineering weeks | 83 → 86 | Device enrollment/revocation, approved hardware-backed signing or gateway signing path, camera capture privacy review, replay testing, and lost-device exercises complete |
| 5. Regulated operational evidence | 6–8 engineering weeks | 86 → 90 | Nigeria-specific legal/compliance approval, authoritative tax-rule governance, penetration test remediation, DR/restore drills, incident exercises, and release authority sign-off complete |

## Explicit blockers to a regulated Nigerian production launch

The remaining work cannot be completed solely inside this repository. It requires organisation-owned environments, provider credentials, data-protection and legal review, tax-rule authority, payment-scheme engagement, security approvals, and live operational exercises. The PWA and mobile foundations should connect only to a gateway-approved HTTPS API after these controls are established; they must not be given embedded service credentials or a device HMAC secret.

## Validation record

The final local validation completed successfully with **114 Python tests passing** in 12.61 seconds, one non-failing Starlette/httpx deprecation warning, Ruff format and lint checks, mypy, Bandit, pip-audit, Go tests and format checks, Rust tests and format checks, PWA Vitest tests, PWA TypeScript checks, and mobile TypeScript checks. The PWA was visually reviewed at desktop and 390×844 mobile viewports.

For target architecture boundaries and source references, see [PRODUCTION_TARGET_ARCHITECTURE.md](./PRODUCTION_TARGET_ARCHITECTURE.md).
