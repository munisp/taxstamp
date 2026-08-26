# Regulated Production Target Architecture

**Status:** Integration-ready blueprint. No provider credential, production certificate, regulator connection, or third-party platform is implied by this document.

## Design principle

The current Taxstamp service remains a Python control-plane application backed by PostgreSQL for business metadata, regulated workflow state, audit evidence, tariffs, and tenant configuration. New infrastructure is introduced only behind bounded adapters and never becomes a substitute for the source of truth without a migration, reconciliation, rollback, and external-validation plan.

| Technology | Target role | Authority boundary | Present implementation status |
|---|---|---|---|
| PostgreSQL | Control-plane database and audited business state | Source of truth for order, stamp, audit, tenant, and configuration metadata | Implemented in current platform |
| TigerBeetle | Optional high-integrity transaction-plane subledger | Transfer/account balances only; PostgreSQL retains business metadata and mapping | Adapter boundary to be added; no runtime service yet |
| Redis | Ephemeral replay, rate-limit, lease, and cache state | Never the system of record | Implemented; HA/restore evidence pending |
| Mojaloop | Optional regulated payment-switch settlement connector | External settlement messages only; no implicit payment success | Adapter boundary to be added; credentials and scheme onboarding pending |
| Kafka | Durable integration-event transport from transactional outbox | Event distribution and projections; not ledger authority | Adapter boundary to be added; cluster, schema registry, and credentials pending |
| APISIX | Public API gateway | TLS, routing, request policy, OIDC enforcement, rate limits, observability | Deployment configuration pending |
| Keycloak | OIDC identity provider | Human, service, device, and mobile client identities | Adapter boundary and claim mapping pending |
| openAppSec | Web application and API protection at ingress | Threat detection and enforcement before APISIX upstreams | Deployment policy pending |
| Permify | Externalized fine-grained authorization decisions | Policy decision point; Taxstamp retains local fallback only during migration | Adapter boundary and authorization model pending |
| OpenSearch | Search and operational evidence projection | Read model only; never a financial or audit source of truth | Event projection pending |
| Fluvio | Optional edge/field streaming evaluation | Edge/offline transport only; not deployed alongside Kafka as a duplicate primary bus | Deferred decision; no runtime service |
| Dapr | Cross-language invocation, pub/sub, secrets, resiliency, and workflow abstraction | Service-to-service integration layer; does not own domain data | Components and sidecar deployment pending |
| Lakehouse | Immutable analytical projections and regulatory reporting datasets | Analytics/read-only derivatives from governed events | Storage, catalog, retention, and governance pending |

## Target service topology

```text
PWA / React Native clients
        |
   Keycloak OIDC + PKCE
        |
openAppSec --> APISIX gateway --> Python Taxstamp control plane
                                  |        |          |
                                  |        |          +--> Permify PDP
                                  |        +--> PostgreSQL + Redis
                                  |
                                  +--> Dapr sidecar --> Kafka event topics --> OpenSearch / Lakehouse projections
                                  |                         |
                                  |                         +--> Mojaloop settlement adapter
                                  |
                                  +--> TigerBeetle subledger adapter

Go gateway and integration services own protocol translation and ingress policy integrations.
Rust components own deterministic secure-code and financial-transfer boundary libraries.
TypeScript owns shared client API contracts, PWA, and React Native user experiences.
Python owns the existing regulated workflow control plane and its authoritative business rules.
```

## Non-negotiable integration controls

Every external call must carry a correlation identifier, be authenticated with a secret managed outside source control, be time-bounded, and expose a typed failure state. Payment, ledger, and regulatory flows must fail closed; an unavailable adapter must never silently produce a success, settle a payment, grant a permission, or mark a stamp as authentic.

Every change to financial authority must use explicit dual-write/migration design, reconciliation between old and new stores, idempotency keys, dead-letter handling, and tested rollback. TigerBeetle is not a replacement for PostgreSQL metadata: its official architecture guidance positions it as a transaction-processing data plane beside a general-purpose control-plane database. [1]

Kafka is the proposed authoritative event-distribution path. Fluvio is retained only as an explicitly optional edge-streaming evaluation because running both as undifferentiated primary buses would create duplicate ordering, replay, and operational responsibilities. OpenSearch and the lakehouse consume versioned, non-authoritative projections only.

## Security and access model

The target identity flow uses Keycloak Authorization Code with PKCE for the PWA and native app, client credentials for machine adapters, short-lived access tokens, and gateway-side OIDC validation. The Keycloak documentation identifies Authorization Code as the recommended flow for web and native applications and cautions against the resource-owner-password grant. [2]

APISIX validates OIDC at the ingress edge, enforces TLS, request limits, request validation, correlation IDs, and routing. Its OIDC plugin supports provider discovery, required scopes, PKCE, and bearer-only protected-resource operation. [3] openAppSec is positioned before or alongside gateway ingress according to the deployed topology; its policy must be tested in detection mode before enforcement. Permify evaluates resource and relationship policy decisions; policy rollouts must be versioned and traceable to a release.

## Event, workflow, and observability model

The existing PostgreSQL transactional outbox remains the point at which business state is atomically committed. A Kafka relay publishes versioned events after commit; consumers independently build OpenSearch and lakehouse projections. Dapr may standardize cross-language service invocation, pub/sub, resiliency, secrets, and workflow lifecycle, but it does not replace application-level idempotency or financial reconciliation. Dapr documents portable building blocks for service invocation, pub/sub, workflow, state, secrets, resiliency, and observability. [4]

OpenSearch receives operational/audit search projections, never mutable ledger authority. Kafka-to-OpenSearch ingestion is a supported Data Prepper pattern and must use consumer groups, schema validation, TLS, authentication, and end-to-end acknowledgments where available. [5]

## Regulated Nigerian production evidence gates

The following are deployment gates, not placeholders that code can satisfy alone.

| Gate | Required evidence |
|---|---|
| Tax and product rules | Authoritative source, provenance owner, effective-dating process, approval workflow, and regression suite |
| Regulatory integrations | Written agreements, sandbox credentials, conformance results, timeouts/retries, operational contacts, and production certification |
| Payment settlement | Scheme/PSP onboarding, Mojaloop or provider sandbox certification, reconciliation results, exception workflow, and settlement cutover approval |
| Identity and authorization | Keycloak realm export, client registration, PKCE/device policy, key rotation, Permify authorization model, and negative access tests |
| Data protection and security | Data-classification record, retention/deletion policy, incident plan, penetration test, WAF results, KMS/secret-management evidence, and legal/compliance approval |
| Operational resilience | Multi-zone topology, backup/restore drill, disaster-recovery test, RTO/RPO approval, monitoring/alerting evidence, and on-call ownership |
| Financial migration | Reconciled migration plan, dual-write proof, immutable audit evidence, TigerBeetle and PostgreSQL balance agreement, and signed release decision |

## Reference architecture sources

[1]: https://docs.tigerbeetle.com/coding/system-architecture/ "TigerBeetle in Your System Architecture"
[2]: https://www.keycloak.org/securing-apps/oidc-layers "Securing applications and services with OpenID Connect"
[3]: https://apisix.apache.org/docs/apisix/plugins/openid-connect/ "APISIX openid-connect plugin"
[4]: https://docs.dapr.io/overview/ "Dapr overview"
[5]: https://docs.opensearch.org/latest/data-prepper/pipelines/configuration/sources/kafka/ "OpenSearch Data Prepper Kafka source"
[6]: https://docs.mojaloop.io/product/features/workstreams/evolution.html "Mojaloop Evolution Workstream"
