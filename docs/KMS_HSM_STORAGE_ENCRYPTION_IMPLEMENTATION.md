# KMS/HSM and Storage-Encryption Implementation Record

**Implemented:** 2026-08-25
**Scope:** Configuration and evidence controls for Taxstamp PostgreSQL, Redis and OpenSearch storage.
**Verification level:** Repository validation plus a fail-closed evidence checker; no live KMS/HSM, managed-database, Redis, or OpenSearch account was provided for provider-side verification.

> This record describes technical controls designed to support the risk-based confidentiality, integrity and availability duties in the Nigeria Data Protection Act/GAID framework and payment-system internal-control expectations. It is **not** a certification or legal conclusion. Production applicability must be confirmed by the DPO, legal counsel, the regulated entity, and the applicable CBN/payment-scheme requirements. [1] [2]

## Implemented control boundary

Taxstamp now exposes a provider-neutral, fail-closed storage-encryption contract through `Settings` and `taxstamp.key_management`. Staging and production startup require an external KMS/HSM provider, a non-placeholder key reference, an HSM-backed attestation, a rotation period, a non-secret evidence URI, and PostgreSQL/Redis storage-encryption attestations. If OpenSearch is configured, an OpenSearch attestation is also mandatory.

| Control | Implementation | What it proves | What it does not prove |
|---|---|---|---|
| KMS/HSM provider boundary | `TAXSTAMP_KMS_PROVIDER` accepts `aws_kms`, `azure_key_vault`, `gcp_kms`, or `pkcs11_hsm`; `TAXSTAMP_KMS_KEY_REFERENCE` holds an identifier only. | A deployment cannot start in staging/production without naming a supported external key-management boundary. | A usable provider credential, key policy, HSM tenancy, or key accessibility. |
| Encrypted-store contract | `TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED=true` and per-store attestation flags are required outside development/test. | The application fails closed when the deployment has not asserted encrypted PostgreSQL/Redis storage; OpenSearch is required when configured. | Disk, snapshot, replica, export or backup encryption. |
| Evidence consistency check | `scripts/check_storage_encryption.py` reads a non-secret environment file plus an attestation JSON. | Key references, HSM assertions, encryption/backup assertions and evidence URIs are complete and internally consistent. | A live cloud/HSM check. Its successful status is explicitly `evidence_attested_not_live_verified`. |
| CI/assurance hook | `scripts/run_assurance.sh` runs the checker when both evidence paths are supplied. | A release pipeline can make storage-encryption evidence a hard gate. | Production compliance when variables/evidence are absent. |

## Required deployment configuration

The repository includes `deploy/nonprod/.env.nonprod.example` and `deploy/nonprod/storage-encryption-attestation.example.json`. Both deliberately contain placeholders and are expected to fail the check until an authorised operator supplies actual non-secret identifiers and evidence references. Credentials, raw key material and provider client secrets must remain in an approved secret manager; they must never appear in the attestation, repository, Compose file, environment example or application logs.

| Store | Production configuration that must be evidenced | Current local disposable state |
|---|---|---|
| PostgreSQL | Encrypt primary storage, replicas, snapshots, backups and exports with an approved KMS/HSM-controlled service or host volume; restrict key use and test restore after key rotation. | Local named volume only; **not evidence of encryption at rest**. |
| Redis | If persistence is enabled, encrypt persistent files, replicas and backups; minimise stored sensitive values and protect backup/export paths. | Persistence intentionally disabled; `rediss://` protects transport only and does not evidence data-at-rest protection. |
| OpenSearch | Use encrypted node storage and encrypted snapshots, apply node/index access controls, redact sensitive events, and retain a matching KMS key/evidence reference. | No OpenSearch service or provider account is provisioned; **not verified**. |

## Operational evidence required before promotion

The deployment owner must provide a location/transfer register, KMS/HSM key policy export, provider attestation of HSM-backed key protection, least-privilege access policy, key rotation/retirement record, restore exercise, backup encryption configuration, deletion/crypto-erasure procedure, and separate administrator/key-custodian access review. The same assessment must cover Keycloak, Permify, Kafka, logs, metrics, object storage, and analytical stores because a regulated record can be copied outside the primary PostgreSQL database.

The GAID calls for schedules to monitor, evaluate and maintain a data security system that guarantees confidentiality, integrity and availability, and separately governs cross-border transfer conditions. It does not name a universal cipher, KMS vendor or single data-residency region. [1] The cited CBN payment-system supervision material emphasises safety, strong internal controls and monitoring; it should be supplemented with the licence-, outsourcing-, scheme- and cloud-specific primary instruments that apply to the regulated operator. [2]

## Executed compliance check

On 2026-08-25, the checker was executed against the repository’s intentionally incomplete templates for **PostgreSQL, Redis and OpenSearch**:

```bash
./scripts/check_storage_encryption.py \
  --env-file deploy/nonprod/.env.nonprod.example \
  --attestation deploy/nonprod/storage-encryption-attestation.example.json \
  --assets postgres redis opensearch
```

The result was correctly fail-closed: exit status `1`, overall status `not_verified`, and `live_provider_verified: false`. PostgreSQL, Redis and OpenSearch each failed because their asset evidence URI still contained placeholder text. Global findings also identified the placeholder KMS key reference, placeholder deployment-evidence URI, and missing valid key-rotation evidence URI. This is the expected result for templates and must not be overridden.

The checker now also has a `--strict-production` mode. It requires `TAXSTAMP_ENV=production`, a defined rotation period, a complete key-management access/recovery/rotation record, a recognised attestation schema, and per-store backup-restore and retention/deletion evidence. A strict run against the staging template correctly returned `not_verified`; its exact field-level findings are recorded in `STAGING_KMS_HSM_EVIDENCE_COMPLETION_GUIDE.md`.

| Checked asset | Check result | Reason | Required closure evidence |
|---|---|---|---|
| PostgreSQL | `not_verified` | No completed KMS key/evidence attestation; local named volume has no provider-side proof. | Managed-service or host-volume encryption configuration, encrypted backups/snapshots, KMS policy/export, and restore exercise. |
| Redis | `not_verified` | No completed persistence/backup encryption attestation; local Redis disables persistence. | If persistence is enabled: encrypted persistence, replicas and backup/export evidence with the approved key reference. |
| OpenSearch | `not_verified` | No OpenSearch account/service or completed evidence record is available. | Encrypted domain/node storage and snapshots, encryption key mapping, access review, and retention/redaction evidence. |

Because no authorised KMS/HSM connector, cloud account, key policy, database account, Redis persistence service, or OpenSearch domain was available in this session, live provider verification was deliberately not attempted. The next release gate is to run the same command against completed, access-controlled non-secret evidence after the deployment owner has provisioned the actual services.

## Repository validation

The focused configuration, key-management and checker tests passed: **16 passed**. The full repository quality gate was then executed with disposable, loopback-only PostgreSQL and Redis dependencies temporarily started for database-backed tests. The result was **126 passed** with the existing non-failing Starlette/httpx deprecation warning. Ruff format/lint, mypy across 57 source files, Bandit, pip-audit, and import checks also passed. The temporary test containers and their volume were removed after the run.

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"
