## Cover

Taxstamp Non-Production Infrastructure

Deployment Simulation, Control Assessment, and Readiness Path

## Slide 1

### The control foundation is now demonstrable

- Disposable Docker simulation passed for identity, authorization, gateway denial, Kafka projection, metrics discovery, API, and worker health.
- A real `order.awaiting_payment` outbox event was observed on the three-partition `taxstamp.events.v1` topic.
- The simulation surfaced and corrected Kafka client, APISIX standalone-route, OIDC schema, and worker-health configuration defects.

## Slide 2

### Validated stack versus production prerequisites

- Validated locally: Keycloak, APISIX, Permify, Kafka, Prometheus, Grafana, PostgreSQL, Redis, FastAPI, and worker.
- Configuration-ready only: openAppSec, TigerBeetle, Mojaloop, OpenSearch, Dapr, Fluvio, and lakehouse.
- Persistent use requires controlled secrets, private DNS, TLS, backup/restore, production identity configuration, and operational ownership.

## Slide 3

### Architecture separates trust, state, and evidence

- APISIX is the public policy boundary; Keycloak supplies OIDC metadata and Permify is the fine-grained authorization boundary.
- PostgreSQL remains the application system of record; Redis supports replay prevention, rate limits, and leases.
- The worker projects durable outbox events to Kafka only after local handler success.

## Slide 4

### Gateway policy denies anonymous protected traffic

- APISIX returned HTTP 401 to an unauthenticated request for a protected API route in the simulation.
- The local policy uses bearer-only OIDC/JWKS validation; the production template requires TLS, client-secret handling, and audited claims mapping.
- Tenant-scoped authorization is enforced in the FastAPI domain layer and protected by regression tests.

## Slide 5

### The transactional outbox protects event integrity

- Events are committed with business state before Kafka projection is attempted.
- The projection envelope carries event ID, aggregate identity, dedupe key, type, timestamp, and payload.
- Kafka metrics distinguish publication success, failure type, and delivery duration; failed projection remains retryable through the outbox path.

## Slide 6

### Settlement reconciliation is intentionally fail-closed

- TigerBeetle and Mojaloop snapshots are parsed as reviewed evidence, not live payment instructions.
- Missing, unknown, duplicate, amount/currency, and settlement-state discrepancies become recorded findings.
- No auto-correction and no transfer creation occurs in the reconciliation workflow.

## Slide 7

### Privacy controls are technical foundations, not compliance proof

- NDPC GAID 2025 emphasises appropriate technical and organisational measures, security monitoring, audits, DPOs where required, DPIAs, notices, rights, and breach handling.
- Existing evidence supports TLS configuration validation, secrets, roles, audit trail, readiness checks, and privacy-relevant security controls.
- Missing evidence includes DPO/governance, ROPA, lawful-basis register, rights workflow, DPIA, breach exercise, vendor agreements, and transfer governance.

## Slide 8

### CBN-related readiness remains conditional

- CBN payments supervision emphasises safety, internal controls, accountability, and early-warning monitoring.
- Technical building blocks exist for access control, monitoring, transaction evidence, and reconciliation.
- Direct CBN obligations depend on licence, payment role, participant status, and outsourcing structure; those must be determined outside the codebase.

## Slide 9

### The path to controlled non-production is clear

- First, establish applicability, accountable owners, DPO/privacy governance, and payment/regulatory role decisions.
- Next, deploy the persistent stack with secret-manager injection, TLS, Keycloak realm/clients, Permify model, Kafka ACLs, dashboards, and alert ownership.
- Then complete TigerBeetle/Mojaloop sandbox cycles, restore drills, incident exercises, and independent security/privacy assurance.

## Slide 10

### Decision requested: fund evidence, not only software

Proceed with a controlled persistent non-production environment and named owners for privacy, security, finance control, payments integration, and operations.
