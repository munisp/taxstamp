# GitHub Actions Compliance Gate and URI Edge Cases

## Pull-request compliance gate

The existing `.github/workflows/ci.yml` already runs on `pull_request`. It now includes a separate `strict-storage-encryption-evidence` job, which checks out the pull-request commit, installs the pinned development tooling in a Python 3.12 virtual environment, and runs `bash .githooks/pre-commit`.

That command performs two deterministic checks: schema validation of the synthetic staging YAML and strict production validation of synthetic configuration/JSON fixtures. The job has no service containers, secrets, cloud credentials or network calls to an evidence platform. A pull request fails when the schema, URI policy or strict checker behavior regresses.

```yaml
strict-storage-encryption-evidence:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - run: |
        python -m venv .venv
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install -r requirements-dev.txt
    - run: bash .githooks/pre-commit
```

To enforce the job organisationally, configure the repository’s branch-protection/ruleset requirements in GitHub to require the **strict storage-encryption evidence** status check before merging to the protected branch. This repository file creates the check; selecting it as a required check is an administrative GitHub setting and is not changed by the workflow itself.

> The pull-request gate protects source-controlled synthetic fixtures. It must not be supplied with real attestation files, KMS/HSM credentials, customer information or access-controlled evidence URLs. The real production gate remains an authorised change-control activity.

## Exact URI validator implementation

Both `scripts/check_storage_encryption.py` and `scripts/convert_storage_encryption_yaml.py` use the same two-stage validator:

```python
PLACEHOLDER = re.compile(
    r"change[_-]?me|placeholder|example|replace|secrets?|password",
    re.IGNORECASE,
)

def is_evidence_uri(value: object) -> bool:
    if not is_non_placeholder(value):
        return False
    uri = str(value)
    if uri != uri.strip():
        return False
    parsed = urlparse(uri)
    if parsed.scheme not in {"https", "s3", "gs", "az", "file"} or parsed.query or parsed.fragment:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme == "file":
        return parsed.netloc == "" and parsed.path.startswith("/")
    if parsed.scheme != "https" and port is not None:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    return bool(parsed.netloc and parsed.path and parsed.path != "/")
```

| URI case | Result | Reason |
|---|---|---|
| `https://evidence.example/records/CHG-91/review` | Accepted | HTTPS, host, non-root path, no credentials/query/fragment. |
| `https://evidence.example:8443/records/CHG-91/review/` | Accepted | A valid explicit HTTPS port and trailing slash after a non-root record path are allowed. |
| `https://evidence.example/` | Rejected | Root-only path is not an addressable evidence record. |
| `https://evidence.example:0/records/CHG-91` | Rejected | Port must be in the inclusive 1–65535 range. |
| `https://evidence.example:not-a-port/records/CHG-91` | Rejected | Parsing `.port` raises `ValueError`. |
| `https://token@evidence.example/records/CHG-91` | Rejected | User-info is credential-like and prohibited. |
| `s3://bucket/CHG-91/access-review.pdf` | Accepted | Canonical non-HTTPS object-store form with bucket and path. |
| `s3://bucket:9000/CHG-91/access-review.pdf` | Rejected | Explicit ports are accepted only for HTTPS. |
| `file:///var/lib/evidence/CHG-91/review.pdf` | Accepted | Absolute local path with no hostname. |
| `file://server/share/review.pdf` | Rejected | Remote file authority is prohibited. |
| `https://evidence.example/record?ticket=CHG-91` | Rejected | Queries could carry presigned URLs/tokens and are prohibited. |

The placeholder regular expression is deliberately case-insensitive and rejects `change-me`, `change_me`, `placeholder`, `example`, `replace`, `secret`, `secrets` and `password` anywhere in the URI. It is a conservative source-control control; a human reviewer must also verify record ownership, production/environment binding, access control, freshness and evidence content.

## Complete synthetic production attestation

`deploy/nonprod/examples/production-storage-encryption-attestation.complete.synthetic.json` contains all strict-required key-management, PostgreSQL, Redis and OpenSearch fields. It uses a synthetic ARN and `.invalid` evidence host, so it is safe for source control and demonstrably passes the strict parser; it is not a real production attestation.

Validate the full synthetic file against the existing synthetic production configuration:

```bash
cd /home/ubuntu/taxstamp
./scripts/check_storage_encryption.py \
  --strict-production \
  --env-file deploy/nonprod/examples/production-storage-encryption.sample.config \
  --attestation deploy/nonprod/examples/production-storage-encryption-attestation.complete.synthetic.json \
  --assets postgres redis opensearch
```
