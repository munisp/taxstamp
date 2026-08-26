# KMS/HSM Evidence URI and Strict Production Reference

## YAML staging attestation

`deploy/nonprod/examples/staging-storage-encryption-attestation.sample.yaml` is a populated **synthetic** human-review sample. Its KMS ARN, HSM evidence references and database record locations are intentionally non-operational. The current checker accepts **JSON attestations only**, so a real YAML record must be converted into the protected JSON attestation supplied by `--attestation`; the checker does not parse YAML.

The staging YAML intentionally contains only PostgreSQL and Redis. Until OpenSearch is deployed and its encryption/recovery/retention evidence exists, run the standard checker with `--assets postgres redis` rather than falsely attesting OpenSearch.

## Exact strict-production invocation

The checker reads the required values from a non-secret `KEY=value` file specified by `--env-file`; it does not use shell-exported variables. The completed protected production file must contain exactly the fields below, with real approved values substituted for every angle-bracket placeholder.

```dotenv
TAXSTAMP_ENV=production
TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED=true
TAXSTAMP_KMS_PROVIDER=<aws_kms|azure_key_vault|gcp_kms|pkcs11_hsm>
TAXSTAMP_KMS_KEY_REFERENCE=<approved-production-non-secret-key-identifier>
TAXSTAMP_KMS_HSM_BACKED=true
TAXSTAMP_KMS_KEY_ROTATION_DAYS=<integer-from-1-to-730>
TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI=<approved-non-secret-evidence-uri>
TAXSTAMP_POSTGRES_STORAGE_ENCRYPTION_ATTESTED=true
TAXSTAMP_REDIS_STORAGE_ENCRYPTION_ATTESTED=true
TAXSTAMP_OPENSEARCH_STORAGE_ENCRYPTION_ATTESTED=true
```

Run the strict gate locally from the repository root:

```bash
cd /home/ubuntu/taxstamp
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file /secure/deployment/taxstamp-production.config \
  --attestation /secure/evidence/taxstamp-production-storage-encryption.json \
  --assets postgres redis opensearch
```

The expected successful result is `overall_status: evidence_attested_not_live_verified` and exit status `0`. That status confirms only non-secret configuration and attestation consistency. It does not connect to the KMS/HSM, storage platform, PostgreSQL, Redis or OpenSearch.

For a safe command-shape test only, run:

```bash
cd /home/ubuntu/taxstamp
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file deploy/nonprod/examples/production-storage-encryption.sample.config \
  --attestation deploy/nonprod/examples/production-storage-encryption-attestation.sample.json \
  --assets postgres redis opensearch
```

The sample uses `.invalid` evidence locations and a synthetic ARN. Its success is not evidence of a production control.

## Required KMS/HSM evidence URI syntax

The checker accepts an evidence URI only if it is non-empty, has no leading/trailing whitespace, does not match its placeholder guard (`change-me`, `placeholder`, `example`, `replace`, `secret`/`secrets`, or `password`), uses an accepted scheme, includes an addressable path, and contains **no query string or fragment**. Query strings and fragments are prohibited to prevent presigned URLs, bearer/session tokens and accidental credential material from entering source control.

| Prefix accepted by the checker | Suitable use | Safe shape |
|---|---|---|
| `https://` | Access-controlled ticket, GRC, evidence portal or immutable report page. | `https://evidence.company.internal/records/CHG-2026-091/kms-hsm-access-review` |
| `s3://` | Private evidence bucket object reference. | `s3://regulated-evidence-prod/CHG-2026-091/kms-hsm-access-review.pdf` |
| `gs://` | Private Cloud Storage evidence object reference. | `gs://regulated-evidence-prod/CHG-2026-091/kms-hsm-access-review.pdf` |
| `az://` | Private Azure storage evidence locator. | `az://regulated-evidence-prod/records/CHG-2026-091/kms-hsm-access-review.pdf` |
| `file://` | Local controlled evidence path for a tightly managed self-hosted environment only. | `file:///var/lib/taxstamp-evidence/CHG-2026-091/kms-hsm-access-review.pdf` |

The accepted prefix is a **syntax** check only. The checker does not prove that the URI resolves, is access-controlled, is immutable, contains the claimed evidence, or is current. A reviewer must verify those properties separately.

## KMS/HSM access-review and recovery-exercise records

| Attestation field | URI must reference a record that demonstrates | Must not appear in the URI or attestation |
|---|---|---|
| `key_management.access_review_evidence_uri` | Completed review of human, workload, backup and break-glass roles; least privilege; separation of duties; reviewer, outcome and remediation tracking. | IAM access keys, session tokens, user e-mail exports, private policies with credentials, signed URLs. |
| `key_management.recovery_exercise_evidence_uri` | Completed authorised recovery exercise for encrypted data/key access; scope, owner, result, recovery target, exception/remediation and review date. | HSM PINs, recovery codes, key shares, plaintext data, key-export files, database passwords. |
| `key_management.rotation_evidence_uri` | Completed rotation or approved rotation-control review bound to the same production key reference and period. | Data keys, provider credentials, signer private keys or recovery material. |

An evidence URI is therefore an opaque pointer to a protected record, not an unauthenticated public link. Prefer stable, access-controlled records and short non-secret object paths. Do not use query-string credentials, presigned URLs, bearer tokens, fragments or embedded passwords.
