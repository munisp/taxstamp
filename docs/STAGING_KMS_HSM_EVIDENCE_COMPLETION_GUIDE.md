# Staging KMS/HSM Evidence Completion Guide

**Audience:** The staging deployment owner, platform-security owner, DPO/compliance owner, and database/search administrators.
**Purpose:** Complete Taxstamp’s **non-secret** KMS/HSM and storage-encryption evidence contract for staging.
**Boundary:** This guide never asks for KMS credentials, plaintext keys, HSM PINs, database passwords, access tokens, signed URLs, or certificate private keys. Those remain in the approved secret manager and provider IAM system.

> A completed checker result is `evidence_attested_not_live_verified`. It validates that records are complete and consistent; it does not connect to the provider or certify a regulated deployment.

## 1. Prepare the two non-secret records

Create a protected staging configuration file from `deploy/nonprod/.env.nonprod.example` and a protected, non-secret evidence record from `deploy/nonprod/storage-encryption-attestation.example.json`. Do not commit either completed file. Keep the actual evidence artefacts in the approved change-management, GRC, ticketing, or security-evidence repository; this JSON stores only stable identifiers or non-secret references to those artefacts.

| File | Contains | Must never contain |
|---|---|---|
| `.env.nonprod` | KMS provider code, non-secret key identifier, environment, boolean control assertions, non-secret evidence reference. | KMS API credentials, HSM PINs, plaintext encryption keys, database passwords, client secrets. |
| `storage-encryption-attestation.json` | Matching key reference, operational evidence URIs, per-store assertions, UTC verification time. | Secrets, key material, customer data, backup data, signed/download URLs with embedded credentials. |

For staging, retain `TAXSTAMP_ENV=staging`. The `--strict-production` option is intentionally expected to reject that value; it is a production-cutover gate, not a staging success criterion.

## 2. Complete the environment contract

The following values are **identifiers or control assertions**, not credentials. Use the provider value that corresponds to the approved staging key-management service. All key references must point to the actual staging key and must not include any secret material.

| Environment variable | What to enter for staging | Acceptance rule |
|---|---|---|
| `TAXSTAMP_ENV` | `staging` | Must remain staging until the production cutover. |
| `TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED` | `true` | Required by staging startup validation. |
| `TAXSTAMP_KMS_PROVIDER` | One of `aws_kms`, `azure_key_vault`, `gcp_kms`, or `pkcs11_hsm`. | Must match the `key_management.provider` value. |
| `TAXSTAMP_KMS_KEY_REFERENCE` | The approved **staging** KMS key ARN/resource ID/PKCS#11 URI, without a secret. | Must be non-placeholder and match every asset key reference. |
| `TAXSTAMP_KMS_HSM_BACKED` | `true` only after the platform-security owner has obtained the provider/HSM control attestation. | Never set it true based solely on a design intention. |
| `TAXSTAMP_KMS_KEY_ROTATION_DAYS` | The approved numeric rotation/review period, e.g. `365` when that is the approved policy. | Must match `key_management.rotation_days`; production strict mode accepts 1–730. |
| `TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI` | A non-secret, access-controlled change/GRC/evidence record URI. | Must use `https://`, `s3://`, `gs://`, `az://`, or `file://` and contain no placeholders. |
| `TAXSTAMP_POSTGRES_STORAGE_ENCRYPTION_ATTESTED` | `true` only after the PostgreSQL evidence below is complete. | Staging startup rejects false. |
| `TAXSTAMP_REDIS_STORAGE_ENCRYPTION_ATTESTED` | `true` only after Redis persistence/backup evidence is complete, or an approved record confirms no persistence/data-at-rest scope. | Staging startup rejects false. |
| `TAXSTAMP_OPENSEARCH_STORAGE_ENCRYPTION_ATTESTED` | `true` only when OpenSearch is actually configured and its evidence exists. | Required once `TAXSTAMP_OPENSEARCH_URL` is configured. |

### Safe key-reference shapes

The values below illustrate **shape only**. Replace all angle-bracket values with approved staging identifiers. Do not copy credentials into the reference.

| Provider code | Safe identifier shape |
|---|---|
| `aws_kms` | `arn:aws:kms:<region>:<account-id>:key/<key-id>` |
| `azure_key_vault` | `https://<vault>.vault.azure.net/keys/<key-name>/<version>` |
| `gcp_kms` | `projects/<project>/locations/<location>/keyRings/<ring>/cryptoKeys/<key>` |
| `pkcs11_hsm` | `pkcs11:token=<token-label>;object=<key-label>;type=secret-key` |

## 3. Complete the `key_management` attestation

The `key_management` object is a cross-check against the environment configuration. Copy the exact provider and exact key reference from `.env.nonprod`; differences are a failure, because a single deployment must not silently encrypt different stores with an unintended key.

| Attestation field | Evidence it must reference |
|---|---|
| `provider` | Exact `TAXSTAMP_KMS_PROVIDER` value. |
| `key_reference` | Exact `TAXSTAMP_KMS_KEY_REFERENCE` value. |
| `hsm_backed` | `true` only after provider or HSM service control evidence is recorded. |
| `rotation_days` | Exact numeric `TAXSTAMP_KMS_KEY_ROTATION_DAYS` value. |
| `rotation_evidence_uri` | Completed key-rotation or approved rotation-policy/review record, with owner and date. |
| `access_review_evidence_uri` | Completed KMS/HSM IAM or partition-access review, including least-privilege and break-glass review. |
| `recovery_exercise_evidence_uri` | Completed recovery exercise showing the authorised recovery path for encrypted data/keys, without exposing recovery material. |

## 4. Complete each storage asset

The same process applies to `assets.postgres`, `assets.redis`, and `assets.opensearch`. Set an assertion to `true` only after the referenced provider configuration and test record exist.

| Asset field | Required staging proof |
|---|---|
| `encryption_at_rest` | Provider setting or host-volume encryption configuration confirming primary storage and replicas are encrypted at rest. |
| `backup_encryption` | Backup/snapshot/export encryption setting, retention configuration, and access-policy evidence. |
| `storage_scope` | `managed-encrypted-service` for an approved managed service, or `host-encrypted-volume` for approved infrastructure-managed disks. |
| `key_reference` | Exact shared staging KMS/HSM key reference. If a distinct per-store key is authorised, change the application contract and evidence model first rather than silently diverging. |
| `evidence_uri` | Change record/configuration export or control report for this specific store. |
| `backup_restore_evidence_uri` | Record of a successful restore from encrypted backup/snapshot, including test date and accountable owner. |
| `retention_deletion_evidence_uri` | Approved retention and deletion/crypto-erasure process record for the store. |
| `verified_at` | Actual verification time in UTC, for example `2026-08-25T18:30:00Z`. |

PostgreSQL evidence must cover its primary volume, replicas, snapshots, backups and exports. Redis evidence must cover all persistence mechanisms and backups if persistence is enabled; transport encryption alone is not evidence of encryption at rest. OpenSearch is currently not provisioned in the local stack. Until a staging OpenSearch domain is deployed, do not set an OpenSearch attestation to true or configure `TAXSTAMP_OPENSEARCH_URL`; document it as a deferred service. Once it is deployed, cover node/index storage, snapshots, access controls and log redaction/retention.

## 5. Run the correct gate for staging

After the non-secret staging files are complete, run the **standard** checker:

```bash
./scripts/check_storage_encryption.py \
  --env-file deploy/nonprod/.env.nonprod \
  --attestation /secure/evidence/taxstamp-staging-storage-encryption.json \
  --assets postgres redis
```

Add `opensearch` only when the staging OpenSearch service and its evidence have been completed. A zero exit status means the result is `evidence_attested_not_live_verified`; attach the JSON result to the change record. At production cutover, change the environment to `production`, complete every listed asset, and run the strict gate:

```bash
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file /secure/deployment/taxstamp-production.env \
  --attestation /secure/evidence/taxstamp-production-storage-encryption.json \
  --assets postgres redis opensearch
```

## 6. Required approval handoff

Before release, the platform-security owner should validate KMS/HSM policy and access separation; the database/search owners should validate encrypted storage, backups and recovery; the DPO/compliance owner should validate the data inventory, location/transfer implications and evidence retention; and the regulated operator should confirm any licence-, outsourcing- or scheme-specific obligations. The Nigerian privacy and payment-system sources described in the implementation record support risk-based security and strong internal controls, but the records above are technical evidence—not a legal certification. [1] [2]

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"
