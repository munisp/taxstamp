# Data Residency and Encryption-at-Rest Specification

**Assessment date:** 2026-08-25
**Scope:** Taxstamp persistent non-production target and eventual regulated production deployment.
**Status:** Required design and evidence specification; no cloud provider, key manager, or production storage account was available to verify implementation.

> **Legal working analysis, not formal legal advice.** A Nigerian data-protection lawyer, DPO, regulated-entity compliance function, and—where relevant—CBN/scheme contacts must confirm the applicable licence, outsourcing, payment-data, data-transfer and record-retention requirements before a production decision.

## Verified routing and port-binding result

The updated **disposable** Compose profile published its validation ports on `127.0.0.1` only. Runtime listeners confirmed loopback bindings for the API, PostgreSQL, Redis, Kafka, Keycloak, Permify, APISIX, Prometheus and Grafana. The direct API deliberately returned HTTP 200 for the public capability declaration, while the equivalent `/v1/capabilities` request through APISIX returned HTTP 401 with a Bearer challenge. This verifies that the configured `/v1/*` route matched the gateway OIDC policy and denied an anonymous request.

The **persistent** Compose overlay resets direct host publishing for `api`, `postgres`, and `redis`; APISIX remains the intended ingress. The local APISIX route includes response-hardening headers for routed upstream responses. This check did not include an authenticated Keycloak token because the disposable realm deliberately contains no test user or direct-grant flow. The persistent deployment must complete the authenticated route/claims/tenant matrix before promotion.

## What NDPA/GAID requires for residency and transfers

The current operative NDPC GAID states that the NDPR 2019 is no longer applied as the legal instrument regulating privacy after GAID’s issuance, subject to transitional treatment. The applicable analysis should therefore be framed under the **Nigeria Data Protection Act 2023 and GAID 2025**, not as an unsupported “NDPR-only” certification. [1]

The GAID does **not** establish a simple blanket rule that every personal-data workload must be physically stored in Nigeria. Instead, it requires controllers/processors to consider the value, volume, variety, velocity and veracity of data flowing within and beyond Nigeria and put appropriate technical and organisational measures in place. For cross-border transfers, Article 45 defers to Part VIII of the Act and Schedule 5. The guidance identifies adequacy decisions, an NDPC-approved Cross-Border Data Transfer Instrument, and other lawful bases; a non-adequate destination requires documented legal and technical safeguards. [1]

| Requirement area | Required decision and evidence | Taxstamp status |
|---|---|---|
| Data-location register | Record primary processing, replicas, backups, logs, support access, analytics, disaster recovery and key-management locations by data class. | Not evidenced. |
| Cross-border transfer assessment | For each non-Nigerian location or remote-support path, identify legal basis, destination, recipient, onward transfer, safeguards, data-subject remedy, and transfer instrument/approval need. | Not evidenced. |
| Processor and hosting contract | Execute a data-processing agreement with confidentiality, security, subprocessor, incident, audit-right, deletion/return and assistance clauses. | Not evidenced. |
| DPIA and risk review | Cover payment/tax-stamp records, identity, field-device data, audit trails, location/telemetry, analytics and international transfers. | Not evidenced. |
| Security schedule | Maintain confidentiality, integrity and availability controls, monitoring, evaluation and maintenance evidence as called for in GAID Article 7. [1] | Partial technical foundation; no production evidence. |

## CBN payment-cloud position

The CBN Payments System Supervision page states a mandate to ensure payment-system soundness and safety, promote strong internal controls, transparency and accountability in fintechs, and operate monitoring with early-warning capability. [2] Those principles support a controlled cloud-risk, encryption, resilience and monitoring programme. However, this official page does **not**, by itself, prescribe a universal storage-country rule, a named cloud provider, or a particular encryption algorithm.

Accordingly, the following items are **conditional obligations to be confirmed** against the operator’s CBN licence, payment-service role, outsourcing arrangement, applicable risk-based cybersecurity framework/circular, payment-scheme rules, and contractual commitments. The platform must not claim “CBN cloud compliant” until that confirmation exists.

| Conditional payment-cloud control | Required evidence before relying on it |
|---|---|
| Cloud outsourcing approval/materiality decision | Legal/compliance assessment, approved vendor due diligence, accountable executive approval, contract, exit plan, and—if applicable—CBN communication/approval evidence. |
| Payment-data and settlement-record location | Authoritative classification of records, primary/replica/backup locations, retention schedule, regulator-access obligations, and cross-border analysis. |
| Security, early warning and resilience | SIEM/monitoring architecture, actionable alerts, security incident process, independent testing, backup/restore and disaster-recovery exercise evidence. |
| Third-party control | Contractual audit rights, subprocessor governance, service-level objectives, notification obligations, data-return/deletion proof, and annual reassessment. |

## Required encryption-at-rest architecture

The following is the recommended **engineering baseline**, not a claim that the GAID or the cited CBN page mandates a particular cipher name. It operationalises GAID’s risk-based confidentiality, integrity and availability expectation and payment-system internal-control principles. [1] [2]

| Asset | Required protection | Key-management requirement | Evidence gate |
|---|---|---|---|
| PostgreSQL business and audit data | Managed-storage encryption at rest using a modern industry-standard authenticated encryption service; encrypted snapshots and replicas; no unencrypted export. | Separate production key per environment; KMS/HSM-backed master key; least-privilege encrypt/decrypt policy; rotation and break-glass record. | Storage/KMS configuration export, restore test, access-policy review, key-rotation record. |
| Redis replay/rate-limit data | Encrypt persistent files and backups if persistence is enabled; assess whether sensitive values should be stored at all. | Separate key policy from database keys; do not store long-lived credentials or raw payment secrets. | Configuration review and data-classification approval. |
| Kafka events, DLQ and snapshots | Encrypt broker disks, replicas and backups; minimise personal data in messages; encrypt/retain DLQ content under the same classification. | Broker/storage key separation; topic ACLs; key access and replay authorization audit. | Broker configuration, storage encryption, ACL export, replay and retention test. |
| Keycloak/Permify databases | Encrypt database volumes, backups and exported realm/authorization models; protect identity metadata as sensitive. | Dedicated keys and restricted administrator access; rotate secrets separately from data-encryption keys. | Configuration export, backup restore and admin-access review. |
| Object storage, documents and evidence | Default encryption at rest; tenant/purpose labels; immutable or retention-lock policy where legally justified; encrypted versioning. | Envelope encryption with managed or customer-controlled keys according to risk; separate key access from object-read access. | Bucket policy, encryption default, version/retention config, access log and recovery test. |
| Logs, metrics and traces | Redact tokens, secrets and sensitive identifiers before export; encrypt the storage backend and backups; limit query access. | Distinct observability key/role; short, approved retention; access review. | Redaction tests, storage encryption record, retention policy, access audit. |
| Endpoints and mobile devices | Use OS/hardware-backed secure storage for access tokens/keys; avoid embedded master secrets; require remote revocation and lost-device procedure. | Device-bound asymmetric keys where approved; server verification and rotation; no shared device HMAC secret in application binary. | Mobile security review, device-enrollment/revocation exercise, penetration-test results. |

## Key-management rules

1. Generate and store production data-encryption keys in an approved KMS or HSM; do not place master keys in Docker environment files, source control, CI logs, container images or application configuration.
2. Separate duties: database operators, application operators, cloud administrators and key administrators should not receive unrestricted combined access without a documented break-glass process.
3. Separate environments and data classes with distinct keys. A development or local key must never decrypt non-production or production data.
4. Enable key-use audit logging, define rotation/retirement schedules, test restoration after rotation, and preserve recovery procedures that do not require a single administrator’s personal credential.
5. Treat backups, replicas, exports, dead-letter events, search indexes, analytical projections and support captures as the same data-residency and encryption scope as the primary record.

## Data-residency implementation sequence

| Step | Action | Completion evidence |
|---|---|---|
| 1 | Classify Taxstamp data into payment/settlement, tax-stamp issuance, identity, audit, operational telemetry, public reference, and secrets. | Approved data-classification matrix mapped to databases, Kafka topics, buckets, logs and vendors. |
| 2 | Select and document the primary Nigerian or other approved processing region, backup/DR region, support locations, and any cross-border vendor path. | Location register with legal basis and owner for every processing and access path. |
| 3 | Complete DPIA, transfer impact/risk assessment, DPA/vendor assessment and relevant cross-border instrument decision. | DPO/counsel approval and contractual records. |
| 4 | Configure cloud storage, database, broker, backup and observability encryption with environment-separated KMS/HSM keys. | Technical configuration exports and independent security review. |
| 5 | Configure private networking so only APISIX exposes approved ingress; keep state, management, broker and monitoring services private. | Network scan from external/internal segments and change record. |
| 6 | Exercise restore, access revocation, key rotation, compromise response and cross-border support escalation. | Measured exercise reports and remediated findings. |
| 7 | Review at least annually and before any material subprocessor, region, schema, payment flow or retention change. | DPO/compliance review record and updated inventory. |

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"
