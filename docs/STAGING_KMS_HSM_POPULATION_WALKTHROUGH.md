# Staging KMS/HSM Population Walkthrough

**Scope:** Populate the Taxstamp staging evidence contract with real, non-secret identifiers and evidence references.
**Do not place in either record:** KMS credentials, access keys, KMS plaintext key material, HSM PINs, database passwords, client secrets, signed download links, or customer data.

> The files in `deploy/nonprod/examples/` are **synthetic shape-validation examples only**. Their account ID, key UUID and `.invalid` evidence locations are non-operational. They demonstrate the checker’s required shape; they are not valid staging evidence.

## Step 1: Identify the approved staging KMS/HSM key

Use the approved cloud or HSM administration process to locate the **staging** encryption key assigned to Taxstamp storage. Copy only the provider’s durable, non-secret key identifier. For AWS KMS, this is the key ARN—not an API key and not a data key. For an HSM-backed service, the supporting evidence must identify the approved HSM boundary and access-control review, but must not include a PIN or secret partition credential.

| Provider | Populate `TAXSTAMP_KMS_PROVIDER` | Populate `TAXSTAMP_KMS_KEY_REFERENCE` with |
|---|---|---|
| AWS KMS | `aws_kms` | The actual staging KMS key ARN: `arn:aws:kms:<region>:<account-id>:key/<key-id>`. |
| Azure Key Vault | `azure_key_vault` | The approved key URI: `https://<vault>.vault.azure.net/keys/<key-name>/<version>`. |
| Google Cloud KMS | `gcp_kms` | The approved CryptoKey resource ID: `projects/<project>/locations/<location>/keyRings/<ring>/cryptoKeys/<key>`. |
| PKCS#11 HSM | `pkcs11_hsm` | The non-secret PKCS#11 object locator: `pkcs11:token=<label>;object=<key-label>;type=secret-key`. |

For example, an **illustrative shape only** AWS value is:

```dotenv
TAXSTAMP_KMS_PROVIDER=aws_kms
TAXSTAMP_KMS_KEY_REFERENCE=arn:aws:kms:af-south-1:111122223333:key/4f3e2d1c-0b9a-4876-8c5d-4e3f2a1b0c9d
```

Replace the entire ARN with the approved staging ARN. Do not edit only the account ID or key UUID, and never reuse the production ARN in staging.

## Step 2: Populate the staging environment record

Copy the template to an access-controlled location outside source control. The following entries are the relevant **shape**; substitute the actual staging key ARN and real evidence-record URI.

```dotenv
TAXSTAMP_ENV=staging
TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED=true
TAXSTAMP_KMS_PROVIDER=aws_kms
TAXSTAMP_KMS_KEY_REFERENCE=<approved-staging-kms-key-arn>
TAXSTAMP_KMS_HSM_BACKED=true
TAXSTAMP_KMS_KEY_ROTATION_DAYS=365
TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI=<approved-non-secret-change-or-grc-record-uri>
TAXSTAMP_POSTGRES_STORAGE_ENCRYPTION_ATTESTED=true
TAXSTAMP_REDIS_STORAGE_ENCRYPTION_ATTESTED=true
TAXSTAMP_OPENSEARCH_STORAGE_ENCRYPTION_ATTESTED=false
```

Set `TAXSTAMP_KMS_HSM_BACKED=true` only after the platform-security owner has recorded provider/HSM assurance in the evidence system. The main evidence URI should point to the parent change or GRC record; it must not be a signed URL, password-protected secret URL, or a location that exposes infrastructure configuration to unauthorised parties.

## Step 3: Populate the matching attestation

Set `key_management.provider` and `key_management.key_reference` to exactly the same values as the environment file. The three KMS/HSM evidence references have distinct purposes:

| Field | What the evidence record must show |
|---|---|
| `rotation_evidence_uri` | Current rotation configuration or a completed rotation/review; include owner, date and the staging key identifier. |
| `access_review_evidence_uri` | Completed least-privilege review of human, workload, backup and break-glass access to the key/HSM boundary. |
| `recovery_exercise_evidence_uri` | Exercise of the authorised recovery procedure for encrypted data/key access, including outcome and accountable owner, without exposing recovery secrets. |

Every `assets.<store>.key_reference` must exactly equal `TAXSTAMP_KMS_KEY_REFERENCE`. The checker rejects mismatched values because an unexpected per-store key is a material deployment decision.

## Step 4: PostgreSQL backup-restore and retention evidence

| Attestation field | Minimum evidence content | Reviewer should confirm |
|---|---|---|
| `assets.postgres.backup_restore_evidence_uri` | Immutable change/test record identifying encrypted backup or snapshot, restoration into an isolated target, restore completion time, owner, test result, and cleanup of the test target. | Primary, replica, snapshot, backup and export paths use approved encryption; restored data is readable only by authorised roles; measured RPO/RTO is recorded. |
| `assets.postgres.retention_deletion_evidence_uri` | Approved retention schedule, backup expiry/deletion configuration, legal-hold exception path, and deletion or crypto-erasure procedure. | Database, snapshots, exports and backup copies follow the same retention schedule; delete/expire actions are auditable. |
| `assets.postgres.evidence_uri` | Storage encryption configuration plus key reference, replica and backup policy, and access-control evidence. | It maps to the same staging KMS/HSM key and approved data location. |

The restore exercise must use an encrypted source and must not substitute a simple connectivity check. Do not put backup IDs, database credentials, table contents, or customer records into the attestation JSON; those stay in the secure evidence record.

## Step 5: Redis backup-restore and retention evidence

First, record whether staging Redis persists data. The local disposable profile has persistence disabled; a regulated staging implementation must make an explicit scope decision. If AOF/RDB persistence, replicas, managed backups or exports are enabled, they are in scope for storage encryption and restore evidence.

| Attestation field | Minimum evidence content | Reviewer should confirm |
|---|---|---|
| `assets.redis.backup_restore_evidence_uri` | Configuration proving encrypted persistence/backup, a restore or replica-recovery test, test result, owner and cleanup. | AOF/RDB files, replicas, managed backups and exports are protected; no long-lived token, password or payment secret is stored unnecessarily. |
| `assets.redis.retention_deletion_evidence_uri` | TTL policy, persistence/backup retention, eviction/deletion process, and scope decision for disabled persistence. | Key TTL, backup retention and deletion behaviour are intentional; any exception/hold is governed and auditable. |
| `assets.redis.evidence_uri` | Redis service/storage encryption configuration, key reference, persistence mode and access-control evidence. | Transport TLS (`rediss://`) is not presented as encryption-at-rest evidence. |

If the approved staging Redis design has no persistence and no backups, record that approved scope decision in the actual evidence system. Do not fabricate `backup_encryption: true`; change the implementation contract only after the DPO/security owner approves an alternative control model.

## Step 6: Run the staging compliance checker

After the completed non-secret files are in their protected locations, run the standard staging check. This command validates PostgreSQL and Redis because OpenSearch is not yet staged:

```bash
cd /home/ubuntu/taxstamp
chmod 600 /secure/deployment/taxstamp-staging.env
./scripts/check_storage_encryption.py \
  --env-file /secure/deployment/taxstamp-staging.env \
  --attestation /secure/evidence/taxstamp-staging-storage-encryption.json \
  --assets postgres redis
```

Expected success output is `overall_status: evidence_attested_not_live_verified` with exit status `0`. That means only that the **non-secret configuration and attestation fields are complete and internally consistent**. It does not prove the live provider configuration. Preserve the output with the staging change record and have the platform-security, database and compliance owners review the linked evidence.

To validate the command shape without a real provider, run the synthetic sample:

```bash
cd /home/ubuntu/taxstamp
./scripts/check_storage_encryption.py \
  --env-file deploy/nonprod/examples/staging-storage-encryption.sample.config \
  --attestation deploy/nonprod/examples/staging-storage-encryption.sample.json \
  --assets postgres redis
```

The synthetic sample’s success is not a staging compliance result. It merely verifies that the file structure and command are correct.

For an exact log-to-attestation review checklist and a field-by-field comparison of standard staging validation with the strict production gate, see `STAGING_STORAGE_EVIDENCE_LOG_VERIFICATION.md`.
