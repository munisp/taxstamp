# Local Non-Production Security Assessment Scope

**Target:** Disposable Taxstamp Docker Compose environment bound to the local sandbox only.
**Authorisation:** The user requested this assessment for the Taxstamp non-production environment.

## Permitted checks

The assessment will use non-destructive techniques only: loopback TCP service discovery and version fingerprinting, HTTP header and endpoint checks, passive web-proxy analysis, container/image and filesystem vulnerability scanning, dependency-audit verification, and review of exposed Compose configuration. It will not attack internet targets, attempt credential guessing, exploit vulnerabilities, alter application data, create payment instructions, or run denial-of-service workloads.

## Success criteria

The report will distinguish observed findings from design risks, record command evidence, validate any safe configuration corrections, and identify findings that require a persistent environment or provider configuration to resolve. The disposable stack will be removed after testing.
