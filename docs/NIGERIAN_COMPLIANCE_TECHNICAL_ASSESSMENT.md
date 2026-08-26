# Nigerian Privacy and Financial-Services Technical Compliance Assessment

**Assessment date:** 2026-08-25
**Author:** Manus AI
**Scope:** Repository controls, the disposable Docker simulation, and deployment documentation.

> **Working technical assessment — not formal legal advice or a regulatory certification.** A Nigerian data-protection lawyer, designated Data Protection Officer, regulated financial institution, and relevant regulator must validate legal applicability, licence conditions, outsourcing status, filing thresholds, and any production compliance conclusion.

## Executive conclusion

The platform now demonstrates a meaningful **technical control foundation**: tenant-scoped access checks, durable PostgreSQL state, Redis replay/rate-limit support, append-only audit features, an authorization-ready gateway pattern, Kafka transactional-outbox projection, secure configuration validation, and fail-closed sandbox reconciliation. The local simulation verified Keycloak, Permify, APISIX OIDC denial, Kafka publication, Prometheus discovery, the API, and the worker.

The evidence does **not** justify an assertion of NDPA/NDPR or CBN compliance. It supports a **partially implemented technical posture** only. The highest remaining gaps are the organisational privacy programme, rights-handling, breach process, data-processing agreements and transfer governance, security operations, resilience testing, regulatory/payment-scheme onboarding, and production evidence. The term “NDPR compliance” should not be used as the operative legal claim: the official GAID states that the earlier NDPR 2019 is no longer the legal instrument used to regulate privacy after GAID’s issuance, subject to the NDP Act’s transitional treatment. [1]

| Assessment dimension | Evidence-based status | Reasoned conclusion |
|---|---|---|
| Privacy technical safeguards | **Partial** | TLS-required staging/production configuration, secret validation, access roles, audit trail, and evidence logging exist; privacy governance processes are not evidenced. |
| Data-subject and controller obligations | **Gap / unverified** | No verified rights portal, request workflow, privacy notice inventory, ROPA, DPO appointment, DPIA record, registration analysis, or audit-return evidence is present. |
| Payment-system technical controls | **Partial** | Transactional outbox, reconciliation checks, gateway model, and sandbox snapshot comparison exist; live payment-scheme onboarding and settlement evidence do not. |
| CBN-regulated entity obligations | **Conditional and unverified** | Direct applicability depends on whether the operator is a CBN-regulated institution, payment-service provider, or material outsourced provider. This cannot be determined from code. |
| Production launch readiness | **Not demonstrated** | The simulated environment is disposable and intentionally uses development-only trust and credential patterns. |

## Regulatory applicability and audit method

The NDPC GAID applies to processing relating to personal data of people in Nigeria and requires controllers/processors to consider the context of processing and adopt appropriate technical and organisational risk measures. [1] The CBN’s payments-supervision mandate emphasises soundness and safety, strong internal controls, transparency, accountability, supervision, and monitoring with early-warning capability. [2]

For this review, these official expectations were translated into auditable technical control themes. A result of **partial** means repository or simulation evidence exists but operational proof is absent. A result of **gap / unverified** means the repository contains no sufficient evidence; it does not prove that the organisation lacks the control outside the repository.

## NDPA and GAID technical control matrix

| Control theme | Relevant official expectation | Repository and simulation evidence | Status | Required closure evidence |
|---|---|---|---|---|
| Classification, registration and audit returns | GAID Article 7 calls for registration where classified as of major importance, annual compliance audits, and compliance-audit returns for specified major-importance classifications. [1] | No classification analysis, NDPC registration record, DPCO engagement, audit-return record, or governance decision is included. | Gap / unverified | Data-map and classification memorandum; counsel/DPO determination; registration and audit-return evidence where required. |
| DPO, policies and training | GAID calls for a DPO for relevant major-importance entities, policy publication, internal policy, and training schedules. [1] | PWA/mobile foundations do not evidence a privacy notice, cookie choice, training programme, DPO, or privacy policy publication workflow. | Gap / unverified | DPO appointment; public privacy/cookie notices; employee/vendor training; policy approval and review records. |
| Lawful basis, minimisation and purpose limitation | GAID addresses principles, lawful bases, consent, legitimate interest, and information to data subjects. [1] | Data models and API controls do not encode a processing register, purpose tag, lawful-basis record, consent evidence, retention schedule, or deletion process. | Gap / unverified | ROPA; purpose and lawful-basis register; retention/deletion design; consent or other basis evidence per processing purpose. |
| Privacy impact assessment | GAID includes DPIA requirements and guidance. [1] | Threat model and secure configuration documents exist, but no data-protection impact assessment or approval record is present. | Partial | DPIA covering payment, tax-stamp, device, identity, location and cross-border processing; mitigation sign-off. |
| Confidentiality, integrity and availability | GAID Article 7 requires monitoring, evaluation and maintenance schedules for a CIA security system. [1] | Staging/production settings require TLS; secrets are validated; roles, audit-chain checks, database/Redis readiness, gateway architecture, metrics, and local health simulation exist. | Partial | Key management, encryption-at-rest evidence, vulnerability management, security monitoring runbooks, alert tests, restoration drill, and production evidence. |
| Breach response | GAID Article 7 identifies notification to the Commission within 72 hours of awareness. [1] | The NDPC breach-reporting path is documented in sources, but no breach playbook, detection-to-triage workflow, evidence template, notification decision matrix, or tested exercise is present. | Gap / unverified | Incident/breach response plan; 72-hour clock process; exercise record; regulator/data-subject notification templates; ownership matrix. |
| Data-subject rights | NDPC lists rights to information, access, rectification, objection, restriction, portability, erasure, complaint and automated-decision safeguards. [3] | No authenticated rights request workflow, identity verification procedure, export, rectification, erasure exception, or response-SLA control is demonstrated. | Gap / unverified | Rights-request portal/process; identity verification; fulfilment log; exceptions register; SLA and escalation evidence. |
| Processor, vendor and cross-border controls | GAID covers data-processing agreements and cross-border transfer guidance. [1] | External services are configuration-ready only; no executed DPA, vendor assessment, transfer impact assessment, data residency decision, or subprocessor register is included. | Gap / unverified | Vendor register; DPAs; transfer assessment and contractual safeguards; subprocessor change process. |

## CBN and payment-system technical control matrix

The CBN source confirms that it supervises payment-system soundness and safety and promotes strong internal controls, transparency, accountability and early-warning monitoring in fintechs. [2] Direct legal obligations must be confirmed against the operator’s licence and role; this table therefore evaluates technical preparedness rather than declaring a licence-specific result.

| Control theme | Technical evidence | Status | Required closure evidence |
|---|---|---|---|
| Governance and accountable security ownership | Architecture and implementation documents assign technical boundaries but do not establish board/management governance, risk acceptance or policy ownership. | Partial | Security governance charter, risk register, accountable executives, policy approval, periodic reporting. |
| Identity, authentication and authorization | APISIX OIDC route, Keycloak deployment template, Permify boundary, custom bearer authentication, tenant-scoped batch fix and gateway simulation HTTP 401 are evidenced. | Partial | Production realm/client design, MFA/step-up decision, claims mapping, access-review process, privileged-access records, and integration tests. |
| Gateway and application protection | APISIX route and openAppSec configuration boundary exist. Local APISIX denial was tested. | Partial | TLS/mTLS, WAF policy, DDoS/rate-limit settings, gateway audit logs, certificate rotation, external attack-test evidence. |
| Event integrity and operational monitoring | Kafka outbox projection publishes keyed envelopes after local handler success; Kafka delivery metrics and Prometheus discovery were verified. | Partial | Production Kafka SASL/TLS, ACLs, schema governance, DLQ/replay runbook, dashboards, alert thresholds, SIEM integration and on-call response evidence. |
| Financial integrity and reconciliation | Existing double-entry controls, Rust ledger boundary, TigerBeetle/Mojaloop snapshot parser, mismatch findings and fail-closed exit state are implemented. | Partial | TigerBeetle account mapping, Mojaloop participant onboarding, signed callbacks, settlement reports, live sandbox reconciliation cycles, exception ownership and independent reconciliation sign-off. |
| Resilience, continuity and recoverability | Docker persistent template identifies volumes and prerequisite backups. | Gap / unverified | RTO/RPO approved by accountable owners; encrypted backup policy; restore drill; failover test; business-continuity and disaster-recovery exercises. |
| Third-party and outsourcing risk | Deployment template and adapter boundaries make vendors explicit. | Gap / unverified | Materiality assessment, due diligence, contracts, audit rights, data/security clauses, exit plan, periodic reassessment. |
| Security incident management | Audit and metrics primitives exist. | Gap / unverified | CBN-applicability determination; incident classification and notification plan; tabletop/technical exercise; forensic retention and regulator engagement procedures. |

## Prioritised remediation plan

| Priority | Action | Accountable owners | Exit evidence |
|---|---|---|---|
| P0 | Determine entity licensing, payment role, controller/processor classification, registration thresholds and outsourcing status. | Nigerian counsel, compliance lead, DPO, executive sponsor | Signed applicability memorandum and obligations register. |
| P0 | Establish privacy programme: DPO, data map/ROPA, privacy notices, lawful-basis matrix, DPIA, breach playbook and rights process. | DPO, legal, product, security | Approved artefacts; request and breach tabletop exercise. |
| P0 | Move gateway, identity, authorization and Kafka from disposable templates to a controlled persistent non-production environment with secrets, TLS and access reviews. | Platform security, SRE, IAM | Change records, secure configuration review, gateway and identity test report. |
| P1 | Run TigerBeetle/Mojaloop sandbox cycles with controlled exports and exception resolution; do not enable live movement of funds from this code path. | Treasury, finance control, payments integration | Signed reconciliation reports and unresolved-finding tracker. |
| P1 | Implement operational resilience: backup/restore, alerting, central logs/SIEM, capacity tests, incident response and disaster recovery exercises. | SRE, security operations, risk | Exercise reports and remediated findings. |
| P2 | Obtain independent technical penetration testing and privacy/compliance review before any regulated production release. | Security assurance, DPO, compliance | Independent reports, remediation attestations, release approval. |

## Evidence reviewed

The assessment used the system configuration, the deployment package, test results, the local simulation record, and official NDPC/CBN materials. The completed repository quality gate recorded 120 passing tests, format/lint checks, mypy, Bandit and dependency-audit success. See [Deployment Simulation](./DEPLOYMENT_SIMULATION.md), [Production Target Architecture](./PRODUCTION_TARGET_ARCHITECTURE.md), [Implementation Status](./IMPLEMENTATION_STATUS.md), and [Compliance Sources](./COMPLIANCE_SOURCES.md).

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"

[3]: https://ndpc.gov.ng/ "Nigeria Data Protection Commission"
