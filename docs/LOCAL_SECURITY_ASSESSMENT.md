# Local Non-Production Security Assessment

**Assessment date:** 2026-08-25
**Target:** The disposable Taxstamp Docker Compose deployment bound to the local sandbox.
**Method:** Non-destructive loopback-only security checks, Nmap service fingerprinting, HTTP method/header tests, Compose-configuration review, Bandit, and `pip-audit`.

## Scope and limits

The assessment was authorised by the user for the disposable local stack. It did not target the public internet, perform credential guessing, exploit a vulnerability, create payment instructions, mutate application data, or run denial-of-service activity. It is therefore a useful **configuration and exposure assessment**, not a substitute for a credentialed application penetration test, cloud/network review, mobile assessment, payment-scheme conformance test, or independent production security assessment.

## Automated evidence

| Check | Result | Evidence |
|---|---|---|
| Loopback security scanner | Completed | `local_security_scan.py` checked host-published ports, HTTP status/headers, TRACE response, direct API access, and APISIX access. |
| TCP service fingerprinting | Completed | Nmap scanned the approved eleven local ports and saved `local_nmap_scan.txt`. |
| Application dependency audit | Passed | `pip-audit --strict` reported no known vulnerabilities in pinned Python requirements. |
| Static security analysis | Passed | Bandit passed over `src`; Ruff and mypy passed. |
| Regression suite | Passed | **121 tests** passed; the only warning is the pre-existing Starlette/httpx deprecation notice. |

## Findings and disposition

| ID | Severity at discovery | Observation | Status after remediation | Required persistent-environment verification |
|---|---|---|---|---|
| LS-01 | High if publicly reachable | PostgreSQL, Redis, Kafka, Permify, Keycloak management, Prometheus, and the direct API were reachable on the local host because the disposable profile publishes ports for validation. | **Template remediated.** Local bindings now use `127.0.0.1`; the persistent overlay resets direct `api`, `postgres`, and `redis` port publishing and retains only APISIX on 443. | Confirm only the approved APISIX listener is reachable from the intended network segment; verify state, broker, authorization, and management ports have no external route. |
| LS-02 | High if direct API port is routable | The direct API returned HTTP 200 for the intentionally public capability declaration while APISIX returned HTTP 401 for the same path. A published API port would bypass the gateway identity policy. | **Template remediated.** The persistent overlay removes direct API host publishing. The capability endpoint’s public contract was not changed. | Test from an external non-production network: direct API port must be unreachable, APISIX must reject anonymous protected requests, and only documented public health/capability policy may be exposed. |
| LS-03 | Medium | Interactive FastAPI documentation returned HTTP 200 in the development stack. | **Code remediated and tested.** Interactive docs now exist only in `development`; staging and production have no `docs_url`. | Confirm `/docs` is unavailable in a staging deployment and approved contract documentation is identity-aware if published. |
| LS-04 | Low to medium depending on exposure | The disposable scan found missing browser-hardening headers at listener roots and Grafana returned HTTP 302 to a TRACE request. This did not demonstrate TRACE reflection or an exploit. | **Partially remediated.** APISIX local route policy adds `X-Content-Type-Options`, `Referrer-Policy`, and `X-Frame-Options`; persistent renderer requirements document the same. Grafana is not host-published in the persistent overlay. | Check headers and deny-method policy through the actual TLS gateway route; ensure Grafana, Prometheus, Keycloak management, and Permify are private or protected by identity-aware access. |
| LS-05 | Informational | Nmap identified expected local services, including OpenResty/APISIX and Redis, confirming the disposable profile has broad loopback exposure by design. | **Accepted for disposable local validation.** | Do not promote the disposable profile or its local-only credentials into a persistent environment. |

## Remediation implemented in this revision

The local Compose profile now binds all published validation ports to `127.0.0.1`, reducing accidental LAN exposure. The persistent overlay explicitly resets inherited host ports for the API and state services, leaving the gateway as the intended external ingress. Interactive documentation is restricted to development and protected by a unit regression test. The APISIX route template includes response-hardening headers and its persistent renderer has a mandatory validation note.

The direct-capability result is not classified as an application-authentication defect in isolation because the capability declaration is an intentional public API contract. It becomes a material bypass only if the direct application listener is reachable outside the private deployment network; the persistent template therefore removes that listener’s host port.

## Remaining technical work before persistent promotion

The next assessment must include authenticated and role-specific tests through the TLS gateway, Keycloak token/claim checks, Permify authorization-model tests, a controlled container-image CVE scan, cloud/network security-group review, secrets-manager review, API fuzzing under an approved rate limit, mobile-device review, and independent penetration testing. No production or regulated-release decision should rely solely on this local assessment.
