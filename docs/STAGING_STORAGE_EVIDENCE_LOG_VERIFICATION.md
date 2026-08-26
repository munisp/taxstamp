# Staging Storage Evidence-Log Verification

**Scope:** Map PostgreSQL and Redis backup-restore and retention/deletion records to the Taxstamp staging attestation schema.
**Evidence boundary:** The attestation stores **non-secret record references**, not raw logs, credentials, backup IDs containing customer data, query output, or signed URLs. The linked evidence record may be access-controlled and may contain the detailed operational logs.

> The standard checker validates attestation shape and internal consistency. It does **not** connect to PostgreSQL, Redis, the backup system, KMS/HSM, or cloud audit logs. A passing result is `evidence_attested_not_live_verified`, not proof that a restore occurred.

## 1. Verification method

For each store, the reviewer should open the evidence record referenced in the staging attestation and verify the required log attributes below. The reviewer then confirms that the non-secret attestation URI points to that evidence record, updates `verified_at` to the actual review time in UTC, and obtains the accountable owner’s approval in the change/GRC system.

| Verification step | Required output | Must not be copied into the attestation |
|---|---|---|
| Confirm storage configuration | Provider/host configuration proving encrypted primary storage, replicas and backups; associated non-secret key identifier. | Credentials, raw provider policy JSON containing identities, customer data, or encrypted material. |
| Confirm recovery test | Dated restore event from an encrypted source to an isolated target, outcome, owner, recovery objective results and cleanup. | Database password, connection string, table rows, application tokens, private endpoints. |
| Confirm retention/deletion | Retention schedule, lifecycle or deletion event, exception/legal-hold route and accountable owner. | Customer data, backup payload, signed deletion URLs or access tokens. |
| Bind evidence to attestation | Stable URI/record ID plus actual UTC review time. | The detailed log body itself. |

## 2. PostgreSQL: match logs to attestation fields

The reviewer should consider PostgreSQL primary storage, replicas, snapshots, backups and exports as one evidence scope. A successful `pg_isready`, SQL query or application health check does not prove encrypted backup recovery.

| Attestation field | Evidence record/log entries to verify | Pass condition |
|---|---|---|
| `assets.postgres.encryption_at_rest` | Storage encryption configuration, replica encryption status, non-secret KMS/HSM key reference, and storage service/volume identifier. | Primary and replicas are confirmed encrypted at rest with the approved key-management boundary. |
| `assets.postgres.backup_encryption` | Snapshot/backup/export encryption configuration and backup key reference. | Backups and exports are encrypted; a backup is not merely present. |
| `assets.postgres.evidence_uri` | Parent change/GRC record containing the storage, replication, backup and access-control evidence. | URI resolves to the approved non-secret record and key reference matches the staging environment record. |
| `assets.postgres.backup_restore_evidence_uri` | Restore test identifier; encrypted source snapshot/backup reference; isolated restore target; started/completed time; success/failure; owner; measured RPO/RTO; application/data-integrity verification; target cleanup. | A documented successful restore from an encrypted source occurred within the approved test interval and did not expose production data broadly. |
| `assets.postgres.retention_deletion_evidence_uri` | Retention policy; snapshot/backup/export lifecycle; automated expiry/deletion result; legal-hold exception path; crypto-erasure procedure where applicable; accountable owner. | Retention/deletion covers every copy class and has an auditable exception process. |
| `assets.postgres.verified_at` | The reviewer’s completed review timestamp. | Actual ISO-8601 UTC time, for example `2026-08-25T18:30:00Z`; not a planned test date. |

### Safe PostgreSQL evidence summary shape

This summary belongs in the protected evidence system, not in the attestation. Identifiers below are illustrative only.

```json
{
  "record": "CHG-2026-042/postgres-encrypted-restore",
  "event": "postgres.encrypted_backup_restore",
  "source_encryption_confirmed": true,
  "restore_target_isolated": true,
  "restore_completed": true,
  "rpo_observed": "within-approved-target",
  "rto_observed": "within-approved-target",
  "integrity_check_completed": true,
  "restore_target_cleanup_completed": true,
  "reviewed_at": "2026-08-25T18:30:00Z"
}
```

## 3. Redis: match logs to attestation fields

Start by documenting whether staging Redis has persistence, replicas, managed backups or exports. The local disposable Compose service deliberately has persistence disabled; that local decision does not prove the staging service is out of scope. If staging Redis persists data, every persistence and recovery path is in scope. TLS in a `rediss://` connection protects transport only.

| Attestation field | Evidence record/log entries to verify | Pass condition |
|---|---|---|
| `assets.redis.encryption_at_rest` | Managed service or host-volume encryption setting; AOF/RDB persistence path; replica storage settings; non-secret key reference. | All enabled persistent copies are encrypted at rest. |
| `assets.redis.backup_encryption` | Managed backup/snapshot configuration, backup encryption status, backup key reference and export controls. | Every enabled Redis backup/export is encrypted. |
| `assets.redis.evidence_uri` | Parent record documenting persistence mode, encryption configuration, access policy and data-classification/scope decision. | URI resolves to the approved non-secret record and uses the exact staging key reference. |
| `assets.redis.backup_restore_evidence_uri` | Restore or replica-recovery test; encrypted persistence/backup source; isolated test target; completion result; owner; application/TTL validation; cleanup. | An authorised recovery exercise succeeds using an encrypted source, and sensitive values are not captured in the evidence. |
| `assets.redis.retention_deletion_evidence_uri` | Key TTL policy; persistence/backup retention; lifecycle/deletion event; scope decision if persistence is disabled; exception process. | TTL and backup retention are intentional, documented and auditable; there is no unexplained persistent copy. |
| `assets.redis.verified_at` | Completed review timestamp. | Actual ISO-8601 UTC time. |

If persistence is intentionally disabled, the evidence record should state that explicitly, identify whether managed backups/replicas/exports also remain disabled, and record the DPO/security owner’s approved scope decision. It must not falsely assert an encrypted backup restore that did not occur.

## 4. Standard staging gate versus production strict mode

| Validation area | Standard staging gate | `--strict-production` mode |
|---|---|---|
| Intended use | Validate deployed staging evidence shape for the selected assets. | Production-cutover gate; rejects staging configuration. |
| Environment requirement | Does not require `TAXSTAMP_ENV=staging` or `production`. | Requires `TAXSTAMP_ENV=production`. |
| Core environment controls | Requires `TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED=true`, supported KMS/HSM provider, non-placeholder key reference, `TAXSTAMP_KMS_HSM_BACKED=true`, and a valid main evidence URI. | Requires all standard controls plus `TAXSTAMP_KMS_KEY_ROTATION_DAYS` as an integer from 1–730. |
| Attestation schema | Does not require `schema`. | Requires `taxstamp.storage-encryption-attestation.v1`. |
| Key-management checks | Provider/key-reference consistency, HSM assertion, and valid `rotation_evidence_uri`. | All standard checks plus matching `rotation_days`, `access_review_evidence_uri`, and `recovery_exercise_evidence_uri`. |
| Per-store checks | Requires record, `encryption_at_rest=true`, `backup_encryption=true`, approved `storage_scope`, matching key reference, valid `evidence_uri`, and non-placeholder `verified_at`. | All standard checks plus `backup_restore_evidence_uri`, `retention_deletion_evidence_uri`, and syntactically valid ISO-8601 UTC `verified_at`. |
| Asset selection | Can validate only live staging assets, for example `--assets postgres redis` while OpenSearch remains unprovisioned. | Typically validates the complete production scope, including OpenSearch when deployed; omitted assets are not evaluated. |
| Success status | `evidence_attested_not_live_verified` and exit `0`. | Same status and exit `0`, but only after the additional production assertions pass. |
| Live provider verification | Never performed. | Never performed; provider configuration, KMS/HSM policy and actual logs still require independent review. |

The platform uses the difference deliberately: staging validates that the deployment evidence record is internally coherent without mislabelling a staging environment as production, while strict mode demands the additional operational assurance expected at production release. The NDPC GAID’s risk-based confidentiality, integrity and availability expectations and CBN payment-system internal-control principles support maintaining this evidence trail, but the technical gate itself is not a legal certification. [1] [2]

## 5. Commands

Run the staging gate after the PostgreSQL and Redis evidence records are complete:

```bash
cd /home/ubuntu/taxstamp
./scripts/check_storage_encryption.py \
  --env-file /secure/deployment/taxstamp-staging.env \
  --attestation /secure/evidence/taxstamp-staging-storage-encryption.json \
  --assets postgres redis
```

At production cutover, after OpenSearch has been provisioned and its evidence completed, run:

```bash
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file /secure/deployment/taxstamp-production.env \
  --attestation /secure/evidence/taxstamp-production-storage-encryption.json \
  --assets postgres redis opensearch
```

## References

[1]: https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf "Nigeria Data Protection Act General Application and Implementation Directive 2025"

[2]: https://www.cbn.gov.ng/PaymentsSystem/ "Central Bank of Nigeria Payments System Supervision"
