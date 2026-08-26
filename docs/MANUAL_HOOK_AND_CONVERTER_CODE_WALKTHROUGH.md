# Manual Hook Demonstration and YAML Converter Walkthrough

## 1. Run the installed pre-commit hook manually

The hook is installed for this clone through the local Git setting `core.hooksPath=.githooks`. It uses only repository-tracked synthetic fixtures and has no access to real KMS/HSM services, protected evidence records, credentials or deployment infrastructure.

Run the normal happy-path hook manually:

```bash
cd /home/ubuntu/taxstamp
.githooks/pre-commit
```

The expected result is first `YAML attestation schema validation passed.`, then a strict checker result containing `overall_status: evidence_attested_not_live_verified`. This means the synthetic fixture is internally consistent; it is not live-environment proof.

Run the negative demonstration without overwriting any fixture:

```bash
cd /home/ubuntu/taxstamp
TAXSTAMP_PRECOMMIT_YAML_INPUT=deploy/nonprod/examples/staging-storage-encryption-attestation.invalid-missing-backup.sample.yaml \
  .githooks/pre-commit
echo "hook exit status: $?"
```

The deliberate fixture omits `assets.redis.backup_encryption`. The hook must stop before strict synthetic production validation and return exit status `1` with:

```text
YAML attestation validation failed:
- assets.redis is missing fields: backup_encryption.
```

`TAXSTAMP_PRECOMMIT_YAML_INPUT` is intentionally limited to an existing repository-relative file. The hook rejects absolute paths and path traversal (`..`), so a local commit cannot accidentally point the pre-commit gate at an arbitrary protected file.

## 2. Converter control flow

`scripts/convert_storage_encryption_yaml.py` converts only validated, non-secret YAML attestation records into JSON. Its implementation is intentionally narrow rather than a general configuration transformer.

| Implementation stage | Function(s) | Exact behaviour and failure result |
|---|---|---|
| Parse safely | `load_yaml` | Reads UTF-8 and calls `yaml.safe_load`; YAML syntax/read failures are printed to stderr and return exit status `1`. It never evaluates YAML objects. |
| Require mappings | `mapping` | Root, `key_management`, `assets` and each asset must be mappings. A scalar/list produces a deterministic `<field> must be a mapping.` error. |
| Check root shape | `validate_attestation` | Allows only `schema`, `synthetic_example`, `environment`, `key_management` and `assets`; rejects unknown keys, invalid schema and non-`staging`/`production` environments. |
| Check key management | `validate_key_management` | Requires every provider/key/HSM/rotation/evidence field; allows only `aws_kms`, `azure_key_vault`, `gcp_kms` or `pkcs11_hsm`; requires `hsm_backed: true` and integer rotation days 1–730. |
| Check each store | `validate_asset_record` | Requires the full encryption, backup, scope, matching key, evidence, recovery, retention and UTC timestamp field set; rejects unknown/missing fields and inconsistent key references. |
| Validate evidence references | `is_evidence_uri` | Applies the placeholder regex and parsed-URI policy described below. Invalid values are reported against their exact field. |
| Emit JSON | `main` | With `--validate-only`, emits no file. With `--output`, writes stable, pretty JSON only after zero validation errors. Any error returns exit status `1`; success returns `0`. |

Run validation only while reviewing a YAML document:

```bash
cd /home/ubuntu/taxstamp
.venv/bin/python scripts/convert_storage_encryption_yaml.py \
  --input deploy/nonprod/examples/staging-storage-encryption-attestation.sample.yaml \
  --validate-only
```

Convert a completed protected record only after validation succeeds:

```bash
cd /home/ubuntu/taxstamp
.venv/bin/python scripts/convert_storage_encryption_yaml.py \
  --input /secure/evidence/taxstamp-staging-storage-encryption.yaml \
  --output /secure/evidence/taxstamp-staging-storage-encryption.json
```

The converter expects JSON-compatible scalars. In particular, quote `verified_at` in YAML so it remains a string: `"2026-08-25T18:30:00Z"`.

## 3. Production access-review URI security rules

The converter and checker apply the same two-layer rule to every evidence URI, including `key_management.access_review_evidence_uri` and `key_management.recovery_exercise_evidence_uri`.

### Layer A: placeholder and credential-word guard

The case-insensitive regular expression is:

```text
change[_-]?me|placeholder|example|replace|secrets?|password
```

Any matching text makes the URI invalid. This rejects both `secret` and `secrets`; it also rejects common copied-template values such as `change-me` or `placeholder`.

### Layer B: parsed URI policy

The validator parses the URI and requires all of the following:

| Constraint | Accepted | Rejected example |
|---|---|---|
| Scheme | `https`, `s3`, `gs`, `az`, or `file` | `http://`, `ftp://`, no scheme. |
| Non-file address | A non-empty host/bucket and a non-root path. | `https://evidence.company.internal/` or `s3://bucket`. |
| File address | An absolute path after `file://`. | `file://relative/path`. |
| Whitespace | None before or after the URI. | ` https://evidence.company.internal/records/CHG-91 `. |
| Query string | None. | `https://evidence.company.internal/review?ticket=CHG-91`. |
| Fragment | None. | `https://evidence.company.internal/records/CHG-91#review`. |

Use a stable path that includes the change/GRC record identifier instead of a query parameter. For example:

```text
https://evidence.company.internal/records/CHG-2026-091/kms-hsm-access-review
s3://regulated-evidence-prod/CHG-2026-091/kms-hsm-recovery-exercise.pdf
```

The validator does not fetch evidence URIs. Before release, the reviewer must separately confirm that each URI resolves through authorised access, identifies the intended production key and environment, records owner/date/outcome/remediation, and does not expose customer data or credential material.
