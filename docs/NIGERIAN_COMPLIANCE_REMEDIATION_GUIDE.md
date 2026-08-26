# Nigerian Regulatory Compliance Remediation Guide

**Status:** Technical and operational working plan.
**Audience:** Executive sponsor, DPO, Nigerian counsel, compliance, platform security, SRE, finance control, and payments integration owners.

> **Not legal advice or a regulatory certification.** This guide translates identified technical and operating gaps into evidence-gated work. Nigerian counsel and the designated DPO must determine legal applicability, licensing, registration, filing, and notification duties before the organisation relies on it.

## How to use this guide

Run the steps in order. A task is not complete when a document is drafted or a service is installed; it is complete only when the listed owner accepts the stated **evidence gate**. The NDPC’s GAID sets expectations around accountability, technical and organisational safeguards, privacy governance, audits, breach handling, rights, DPIAs and processor/transfer controls. [1] The CBN payment-system mandate emphasises soundness, safety, strong internal controls, transparency, accountability and early-warning monitoring; exact licence-specific duties depend on the organisation’s role. [2]

| Phase | Target outcome | Primary owners | Evidence gate |
|---|---|---|---|
| 0 | Establish legal and regulatory applicability | Nigerian counsel, compliance lead, executive sponsor | Signed obligations register and decision log |
| 1 | Establish privacy accountability and processing governance | DPO, legal, product, security | Approved privacy programme and processing inventory |
| 2 | Build operational privacy workflows | DPO, customer operations, engineering | Tested rights and breach exercises |
| 3 | Harden persistent technical controls | Platform security, SRE, IAM | Secure non-production validation report |
| 4 | Prove payment and reconciliation controls | Finance control, treasury, payments | Signed sandbox reconciliation and exception records |
| 5 | Demonstrate resilience and independent assurance | SRE, risk, security assurance | Restore/incident evidence and independent review |

## Phase 0 — Determine applicability before designing controls

### Step 0.1: Create the regulated-activity map

Identify the legal entity operating Taxstamp, each data-processing role, payment or settlement role, outsourced function, regulated counterparty, and every data location. Include the tax-stamp issuer, customer/merchant, inspector, payer, payment participant, host, identity provider, security service, analytics provider, and support provider. Record which entity determines processing purposes, which acts only on instructions, and whether any regulated payment or financial-service activity is being performed.

**Owner:** Nigerian counsel and compliance lead.
**Evidence gate:** A signed map lists legal entity, role, licence/registration relevance, contract relationship, data category, system, location, and accountable executive.

### Step 0.2: Build the obligations register

Convert the legal assessment into dated obligations. The register should cover NDPA/GAID classification and registration analysis, DPO requirement, audit-return requirement where applicable, data-subject notices, security and breach duties, vendor and transfer requirements, CBN/payment-scheme role, outsourcing/materiality treatment, financial reporting and reconciliation duties, and notification/record-retention rules. The GAID also states that the NDPR 2019 is no longer the operating privacy instrument after GAID’s issuance, subject to transitional application; use the current NDPA/GAID framework rather than an unsupported NDPR-only claim. [1]

**Owner:** Counsel, DPO, compliance.
**Evidence gate:** Every obligation has a source, applicability decision, deadline, owner, risk rating, system scope, and evidence requirement.

## Phase 1 — Establish privacy accountability and governance

### Step 1.1: Appoint accountable privacy ownership

Formally appoint the DPO or record the reason a DPO is not required, then create a privacy steering group with security, product, legal, operations, and procurement representation. Publish a reporting route for privacy issues and establish an independent escalation path to executive management.

**Evidence gate:** Appointment record, role description, reporting line, meeting cadence, escalation matrix, and policy approval history.

### Step 1.2: Produce a data inventory and ROPA

For each data flow, record personal-data categories, data subjects, lawful basis, purposes, recipient categories, systems, storage locations, retention period, deletion method, cross-border path, processor status, security measures, and access roles. Trace the field-verification PWA/mobile path, payment and settlement records, identity events, audit data, and analytics projections separately.

**Evidence gate:** Reviewed register of processing activities linked to architecture diagrams, database tables, API endpoints, vendor inventory, and retention controls.

### Step 1.3: Complete a DPIA and vendor/transfer assessment

Assess high-risk processing before persistent deployment, including financial/payment information, device and field-inspection data, identity data, audit trails, profiling/automated decision use, and external-provider transfers. Review data-processing agreements, confidentiality/security clauses, audit rights, subprocessor controls, exit support, and transfer safeguards for every relevant provider.

**Evidence gate:** DPIA approval, treatment plan, signed vendor assessments/DPAs, transfer analysis, and open-risk acceptance signed by accountable owners.

## Phase 2 — Make privacy rights and breach response executable

### Step 2.1: Implement a verified rights-request process

Build or operationalise a protected intake channel for access, correction, objection, restriction, portability, erasure, complaint, and automated-decision requests where applicable. Verify requester identity, establish service-level targets, preserve an immutable request ledger, provide export and correction workflows, and document lawful retention or legal-hold exceptions.

**Evidence gate:** End-to-end exercise for representative rights requests, with identity verification, approval, fulfilment, exception handling, closure evidence, and DPO review.

### Step 2.2: Implement a 72-hour breach decision workflow

Create detection, triage, containment, impact assessment, decision, notification, and post-incident processes. The GAID describes notification to the Commission within 72 hours of awareness for relevant breaches. [1] Maintain a time-stamped incident record that makes it possible to show when awareness arose, what was known, who decided, and what notices were issued.

**Evidence gate:** Tabletop and technical exercise results; notification decision record; regulator/data-subject communication templates; corrective-action tracker.

## Phase 3 — Harden the persistent non-production environment

### Step 3.1: Deploy only the persistent profile

Do not promote the disposable local Compose profile. Deploy the persistent overlay with private network segmentation, APISIX as the only approved external ingress, managed TLS, private DNS, secret-manager injection, Keycloak database persistence, Permify persistence, Kafka TLS/SASL and ACLs, protected monitoring access, encrypted backups, and explicit resource limits.

**Evidence gate:** Infrastructure-as-code review, Compose/Kubernetes configuration record, port-access scan from external and internal segments, TLS test, secrets-manager attestation, and approved change record.

### Step 3.2: Complete identity, gateway and authorization tests

Configure Keycloak realm/client policies, PKCE, claims mapping, client credential rotation, administrator MFA, session policies, and device revocation. Publish the Permify model and tuple lifecycle. Test APISIX routes with anonymous, valid, expired, wrong-audience, wrong-scope, cross-tenant, and privileged-role tokens. Preserve gateway and authorization decision logs.

**Evidence gate:** Token/claim test report, access review, authorization-model test report, gateway header/method test, certificates/keys rotation record, and remediation of all critical/ high findings.

### Step 3.3: Complete security operations and observability

Connect structured application, gateway, identity, Kafka, database, and reconciliation logs to controlled monitoring/SIEM retention. Configure actionable alerts for authentication failures, authorization denials, Kafka publish failures/lag, outbox retry accumulation, reconciliation findings, backup failure, secret/certificate expiry, and health degradation. Set named on-call owners and escalation windows.

**Evidence gate:** Alert tests, dashboard screenshots, alert routing evidence, log-retention decision, runbooks, and on-call exercise report.

## Phase 4 — Prove payment and reconciliation safeguards

### Step 4.1: Complete TigerBeetle sandbox controls

Provision the sandbox through approved vendor procedures. Define immutable account and ledger-code mapping, transfer ID/idempotency convention, money precision/currency policy, reconciliation extract format, exception ownership, and a change-control process. Treat PostgreSQL business state and TigerBeetle transaction state as separate systems that require documented reconciliation rather than implicit consistency.

**Evidence gate:** Approved account map, sandbox transfer test evidence, repeated reconciliation reports, exception log, and finance-controller sign-off.

### Step 4.2: Complete Mojaloop sandbox participation

Finish participant/onboarding requirements, protected credentials and certificates, callback authenticity checks, response/retry semantics, settlement-report process, dispute/exception procedure, and reconciliation timing. Do not send live funds from the current snapshot-based reconciliation process.

**Evidence gate:** Provider/sandbox conformance evidence, signed callback verification tests, settlement export evidence, reconciled exception reports, and payment owner sign-off.

### Step 4.3: Govern Kafka and downstream projections

Define topic ownership, ACLs, schema compatibility, data classification, retention, dead-letter handling, replay authorization, consumer ownership, and lineage to OpenSearch/lakehouse. Any personal data in an event must map back to the ROPA, purpose, retention and access rules.

**Evidence gate:** Kafka ACL/configuration export, schema registry or compatibility policy, replay test, DLQ exercise, projection data map, and approved retention controls.

## Phase 5 — Prove resilience, assurance, and release authority

### Step 5.1: Exercise recovery and continuity

Set and approve RTO/RPO by business process. Run encrypted backup restoration, database recovery, Kafka replay, Keycloak/Permify recovery, loss-of-worker and loss-of-broker exercises, and document observed recovery times. Validate that the audit chain and reconciliation reports remain usable after restoration.

**Evidence gate:** Signed restoration and continuity reports, measured recovery results, remedial actions, and approved risk acceptance for any gap.

### Step 5.2: Obtain independent assurance

Commission independent penetration testing of the actual persistent environment, a mobile assessment, cloud/network review, privacy/compliance review, and payment-scheme/provider conformance work. Remediate critical and high findings before any regulated release and have accountable executives accept remaining risk.

**Evidence gate:** Independent reports, closure verification, updated risk register, DPO/counsel/compliance approval, security release approval, and executive production-go/no-go decision.

## Immediate 30-day operating plan

| Week | Action | Deliverable |
|---|---|---|
| 1 | Run Phase 0 applicability workshop and assign named owners. | Signed obligations register and ownership matrix. |
| 2 | Create ROPA/data map; freeze persistent deployment inputs; select secrets and TLS owners. | Data inventory, vendor inventory, persistent deployment change plan. |
| 3 | Complete Keycloak/APISIX/Permify persistent configuration and test gateway/tenant/role matrices. | Security test report and change evidence. |
| 4 | Run DPIA, breach tabletop, first TigerBeetle/Mojaloop snapshot reconciliation cycle, and backup/restore design review. | DPIA, exercise reports, reconciliation evidence, remediation backlog. |

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"
