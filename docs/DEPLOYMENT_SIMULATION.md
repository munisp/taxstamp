# Disposable Non-Production Deployment Simulation

**Simulation date:** 2026-08-25
**Scope:** A disposable, isolated Docker Compose deployment. This is a validation exercise, not a persistent environment or production release.

## Result: Passed after two configuration remediations

| Integration surface | Evidence exercised | Result |
|---|---|---|
| Keycloak | Container health endpoint at port 9000 | Passed |
| Permify | HTTP health endpoint | Passed |
| Taxstamp API | `/healthz` on the application container | Passed |
| APISIX and OIDC | Unauthenticated request to `/v1/capabilities` through APISIX | Passed: APISIX returned HTTP 401 after the local OIDC route loaded |
| Kafka | `taxstamp.events.v1` created with three partitions; a real outbox event consumed from the topic | Passed |
| Worker | Background worker health command checked its database and Redis dependencies | Passed after dedicated worker health check was added |
| Prometheus | Prometheus readiness endpoint and configured Keycloak/APISIX target discovery | Passed |
| Grafana | Container started with Prometheus datasource provisioning | Started; dashboard content was not in scope |

## End-to-end event evidence

An `order.awaiting_payment` outbox record was inserted into the running disposable Taxstamp application. The worker processed it and Kafka emitted a JSON envelope containing a unique event ID, order aggregate ID, dedupe key, event type, timestamp, and payload. This demonstrates the intended ordering: local transactional-outbox work succeeds first, Kafka projection follows, and a Kafka delivery failure would leave the message retryable.

## Remediations discovered by the simulation

The initial stack revealed three deployment defects. The Kafka Python client rejected an unsupported `enable_idempotence` option; it was removed while retaining the source-of-truth transactional outbox and `acks=all` / retry policy. APISIX did not load its standalone route until the required `#END` delimiter was added, and its OIDC plugin required a client-secret field even in bearer-only/JWKS mode; a plainly local-only placeholder was added to the disposable configuration. Finally, the worker inherited an HTTP health check despite not serving HTTP; a worker-specific dependency health command now verifies database and Redis availability.

## Scope limitations

The stack was destroyed with `down -v` after evidence capture to avoid retaining credentials or consuming local resources. Local Keycloak/APISIX/Kafka settings intentionally use non-production HTTP/plaintext patterns inside an isolated network. The persistent template requires private DNS, TLS and certificate management, secret-manager injection, persistent data stores and backups, secure Kafka listener material, Keycloak realm/client configuration, a Permify authorization model, and an audited metrics service identity before it can be used as a non-production environment.
