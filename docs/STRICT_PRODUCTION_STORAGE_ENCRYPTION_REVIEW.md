# Strict Production Storage-Encryption Review

**Run date:** 2026-08-25
**Input environment:** `deploy/nonprod/.env.nonprod.example`
**Input attestation:** `deploy/nonprod/storage-encryption-attestation.example.json`
**Command:**

```bash
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file deploy/nonprod/.env.nonprod.example \
  --attestation deploy/nonprod/storage-encryption-attestation.example.json \
  --assets postgres redis opensearch
```

## Result

The checker exited with status **1** and produced `overall_status: not_verified`. This is the expected result: the supplied files are explicitly a **staging template** with placeholder values, not a completed production attestation. `live_provider_verified` remained `false` because the checker is intentionally configuration-and-attestation-only; it did not contact a provider KMS/HSM, PostgreSQL, Redis, or OpenSearch service.

| Output property | Observed value |
|---|---|
| `mode` | `strict_production` |
| `overall_status` | `not_verified` |
| `verification_scope` | `configuration-and-attestation-only` |
| `live_provider_verified` | `false` |
| Exit status | `1` |

## Global validation log

| Field/log item | Exact checker result | Resolution |
|---|---|---|
| `TAXSTAMP_KMS_KEY_REFERENCE` | `TAXSTAMP_KMS_KEY_REFERENCE is missing or placeholder text.` | Enter the approved non-secret staging key identifier, then use the production key identifier only in the production file. |
| `TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI` | `TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI is missing, unsafe, or placeholder text.` | Point to the access-controlled change/GRC/evidence record. |
| `key_management.rotation_evidence_uri` | `Missing valid key-rotation evidence URI.` | Reference the completed rotation exercise or approved rotation-control review. |
| `TAXSTAMP_ENV` | `must be exactly production` | Expected failure for this staging template. Retain `staging` in staging; set `production` only at production cutover. |
| `key_management.access_review_evidence_uri` | `must reference a completed KMS/HSM access review` | Add a non-secret URI for the least-privilege, break-glass and key-custodian access review. |
| `key_management.recovery_exercise_evidence_uri` | `must reference a completed KMS/HSM recovery exercise` | Add a non-secret URI for the documented recovery exercise. |

## Per-store validation log

| Store | Exact missing fields from strict validation | Resolution |
|---|---|---|
| PostgreSQL | `assets.postgres.evidence_uri`; `assets.postgres.backup_restore_evidence_uri`; `assets.postgres.retention_deletion_evidence_uri` | Record encrypted primary/replica/storage configuration, successful restore from encrypted backup/snapshot, and retention/deletion or crypto-erasure procedure. |
| Redis | `assets.redis.evidence_uri`; `assets.redis.backup_restore_evidence_uri`; `assets.redis.retention_deletion_evidence_uri` | Record persistence/backup scope and encryption. If Redis persistence is intentionally disabled, retain an approved scope decision; if enabled, supply restore and deletion evidence. |
| OpenSearch | `assets.opensearch.evidence_uri`; `assets.opensearch.backup_restore_evidence_uri`; `assets.opensearch.retention_deletion_evidence_uri` | Do not attest OpenSearch until a staging service is provisioned. Once deployed, record encrypted node/index storage, snapshots, restore, retention/deletion, and access controls. |

## Readiness conclusion

The review found **no contradictory evidence**; it found expected placeholder and staging-state gaps. The correct next action is to complete the non-secret staging evidence records according to `STAGING_KMS_HSM_EVIDENCE_COMPLETION_GUIDE.md`, run the standard checker for deployed staging assets, and reserve `--strict-production` for the production release gate.
