# YAML Conversion, Local Pre-Commit, and Evidence URI Audit

## 1. Convert the staging YAML sample to checker-ready JSON

The staging YAML sample is a structured human-review source. The compliance checker consumes JSON, so use the repository conversion script to validate the YAML before writing a JSON attestation. The converter uses `yaml.safe_load`, accepts only the repository-defined fields, validates provider/key/evidence/asset requirements, and does **not** contact a KMS/HSM, database, Redis, OpenSearch, cloud storage, or evidence portal.

First perform a non-writing schema check:

```bash
cd /home/ubuntu/taxstamp
.venv/bin/python scripts/convert_storage_encryption_yaml.py \
  --input deploy/nonprod/examples/staging-storage-encryption-attestation.sample.yaml \
  --validate-only
```

For a completed, protected staging YAML record, convert it to a protected JSON path outside source control:

```bash
cd /home/ubuntu/taxstamp
.venv/bin/python scripts/convert_storage_encryption_yaml.py \
  --input /secure/evidence/taxstamp-staging-storage-encryption.yaml \
  --output /secure/evidence/taxstamp-staging-storage-encryption.json
```

The root object must contain `schema`, `environment`, `key_management`, and `assets`; `synthetic_example` is allowed only to label an example. The key-management object must contain the supported provider, matching non-secret key reference, `hsm_backed: true`, a 1–730 day rotation period, and rotation/access-review/recovery evidence URI fields. Each included asset must contain the full encryption, backup, scope, key-reference, evidence, restore, retention and quoted UTC timestamp field set. A YAML timestamp must be quoted, for example `"2026-08-25T18:30:00Z"`, so it remains a string in JSON.

> The staging sample uses `.invalid` locations and a synthetic ARN. Converting or validating it proves only the file shape, not a staging control.

## 2. Set up the deterministic local pre-commit gate

The repository includes an opt-in local hook. It validates the synthetic staging YAML and runs strict production mode against separate synthetic production fixtures. It never reads protected attestation files, real KMS/HSM configuration, or real evidence locations, so it is safe to run automatically before every local commit.

Install it once in each clone:

```bash
cd /home/ubuntu/taxstamp
bash scripts/install_local_git_hooks.sh
git config --local --get core.hooksPath
```

The expected output from the second command is `.githooks`. To exercise the exact hook without committing, run:

```bash
cd /home/ubuntu/taxstamp
.githooks/pre-commit
```

The hook performs these deterministic checks in order:

| Check | Fixture used | Expected result |
|---|---|---|
| YAML schema validation | `staging-storage-encryption-attestation.sample.yaml` | `YAML attestation schema validation passed.` |
| Strict compliance validation | `production-storage-encryption.sample.config` and `production-storage-encryption-attestation.sample.json` | `overall_status: evidence_attested_not_live_verified` with exit status `0`. |

The hook is intentionally **not** a substitute for the production release gate. It protects repository quality by detecting schema drift and validator regressions; the real protected production files must still be checked manually under authorised change control.

## 3. Audit production access-review and recovery-exercise URIs

Audit `key_management.access_review_evidence_uri` and `key_management.recovery_exercise_evidence_uri` before adding them to the protected JSON record. Each URI should be a stable, access-controlled pointer to an evidence record—not the evidence content itself.

| Audit dimension | Required standard | Common failure that the checker catches |
|---|---|---|
| Scheme | Exactly `https://`, `s3://`, `gs://`, `az://`, or absolute `file://`. | Unsupported scheme, missing scheme, or an empty location. |
| Addressability | For non-file URIs: host/bucket and a non-root path. For `file://`: an absolute path. | `https://evidence.company.internal/` or `file://relative/path`. |
| Clean syntax | No leading/trailing whitespace, query string (`?`) or fragment (`#`). | `...?ticket=CHG-91`, `...?token=...`, `#access-review`. |
| Safe pathname | No `change-me`, `placeholder`, `example`, `replace`, `secret`, `secrets`, or `password` text, case-insensitively. | `.../placeholder-access-review` or `...?token=secret`. |
| Access control | Protected record/bucket path with access controlled outside the URI. | Public link, presigned link, or a path carrying a credential. This needs human review; the checker does not resolve URIs. |
| Content binding | Access-review record identifies the approved production key reference, scope, reviewer/date/outcome and remediation tracking. Recovery-exercise record identifies scope, authorised owner, outcome, recovery target, cleanup and remediation. | A valid-looking URI that points to the wrong key/environment or an incomplete exercise; this requires evidence review. |

Use paths rather than query parameters to identify change records. For example, use `https://evidence.company.internal/records/CHG-2026-091/kms-hsm-access-review`, not `https://evidence.company.internal/review?ticket=CHG-2026-091`. For object stores, use the canonical object URI without an access token, for example `s3://regulated-evidence-prod/CHG-2026-091/kms-hsm-recovery-exercise.pdf`.

## 4. Final operator check

Before invoking strict production mode with a real protected record, confirm that the environment file and JSON attestation have the same provider, key reference and rotation period, every production store is attested, all UTC values are quoted strings in YAML/strings in JSON, and all evidence URIs pass the audit above. Then run the strict command recorded in `KMS_HSM_EVIDENCE_URI_AND_STRICT_PRODUCTION_REFERENCE.md` and attach the JSON result to the approved change record. A passing local result remains configuration-and-attestation evidence only; independent platform-security, storage and compliance review is still required.
