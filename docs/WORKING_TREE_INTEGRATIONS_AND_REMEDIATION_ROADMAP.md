# Working-Tree Review, Focused Integration Status, and Regulated-Production Roadmap

**Assessment date:** 2026-08-25
**Branch:** `devin/1787593004-tax-stamp-platform`
**Scope:** Current local working tree; no commit, push, pull request, or merge was performed.

## 1. Exact working-tree diff and review boundary

The attached raw artifact was produced with:

```bash
git diff --no-ext-diff --binary
git ls-files --others --exclude-standard | sort
```

The first command is the exact standard Git diff for tracked modifications. Git does not include untracked paths in its diff, so the second command lists those paths separately. The raw inventory revealed generated dependency/build outputs under `apps/mobile/node_modules/` and `adapters/rust/ledger-boundary/target/`; they are not source changes and **must not be staged or committed**.

`git diff --check` passed, so the tracked patch had no whitespace errors at review time. The working tree nevertheless contains a large mix of tracked changes and new source, deployment, test, adapter, client, documentation and generated paths. Treat it as a release-sized change set, not a one-file CI edit.

### Recommended commit slicing

| Commit slice | Include | Exclude |
|---|---|---|
| 1. Core security and integration controls | FastAPI authorization fix, settings/capabilities, outbox/Kafka, reconciliation, tests. | Generated artifacts, PWA/mobile package installation directories. |
| 2. Non-production infrastructure | Compose overlays, APISIX, Keycloak, Permify, observability, validation scripts and simulation evidence. | Secrets, `.env` values other than non-secret examples. |
| 3. KMS/HSM evidence controls | Key-management settings, checker/converter, synthetic fixtures, local hook, CI job and tests. | Actual attestation files, real evidence URIs, credentials and raw key material. |
| 4. Clients and language boundaries | PWA, React Native source/configuration, TypeScript contracts, Go policy generator, Rust ledger-boundary source/tests. | `apps/mobile/node_modules/`, Rust `target/`, `.expo`, coverage and local build outputs. |
| 5. Documentation and roadmap | Architecture, assurance, compliance, security, evidence and handoff documents. | Downloaded/controlled evidence that contains sensitive operational data. |

## 2. Safe GitHub Actions commit and merge sequence

The new workflow job is defined in `.github/workflows/ci.yml` as `strict-storage-encryption-evidence`. It runs on pull requests through the existing CI workflow, creates a Python 3.12 virtual environment, installs pinned development dependencies and invokes `bash .githooks/pre-commit`. That hook validates only synthetic fixtures; it has no access to real KMS/HSM services, private evidence records or production credentials.

Before staging, use an explicit source allow-list rather than `git add -A`:

```bash
cd /home/ubuntu/taxstamp
git status --short
git diff --check

# Inspect generated paths; do not delete anything blindly.
git clean -ndX

# Stage only the intended CI/compliance slice first.
git add \
  .github/workflows/ci.yml \
  .githooks/pre-commit \
  requirements-dev.txt \
  scripts/check_storage_encryption.py \
  scripts/convert_storage_encryption_yaml.py \
  scripts/install_local_git_hooks.sh \
  deploy/nonprod/examples \
  tests/unit/test_storage_encryption_check.py \
  tests/unit/test_storage_attestation_yaml.py \
  docs/GITHUB_ACTIONS_COMPLIANCE_AND_URI_EDGE_CASES.md \
  docs/KMS_HSM_EVIDENCE_URI_AND_STRICT_PRODUCTION_REFERENCE.md \
  docs/MANUAL_HOOK_AND_CONVERTER_CODE_WALKTHROUGH.md \
  docs/YAML_CONVERSION_PRECOMMIT_AND_URI_AUDIT.md

git diff --cached --check
git diff --cached
git commit -m "ci: gate synthetic strict storage-encryption evidence"
git push origin devin/1787593004-tax-stamp-platform
```

After the branch is pushed, create and review a pull request using the GitHub CLI or the repository UI:

```bash
gh pr create \
  --repo munisp/taxstamp \
  --base main \
  --head devin/1787593004-tax-stamp-platform \
  --title "Regulated production controls and compliance CI" \
  --body-file /path/to/review-summary.md

gh pr checks --repo munisp/taxstamp --watch
```

An administrator should then add **strict storage-encryption evidence** to the main-branch ruleset/branch-protection required checks. Only after code review, all required checks, operational approval and an authorised decision should the maintainer merge using the UI or:

```bash
gh pr merge <number> --repo munisp/taxstamp --squash --delete-branch
```

No command above should be used to merge automatically without a reviewer and authorised release decision. The GitHub Action verifies synthetic source-controlled fixtures; it does not certify live storage encryption or a production release.

## 3. TigerBeetle, Mojaloop and OpenSearch: exact current maturity

| Platform | Implemented now | What is absent | Release closure evidence |
|---|---|---|---|
| TigerBeetle | `SettlementProvider.TIGERBEETLE`; reviewed-snapshot parser and fail-closed comparison of expected local settlements with provider reference, external ID, amount, currency and state; Rust ledger-boundary validates non-empty, cross-account, positive transfer intents before a client call. | No TigerBeetle client dependency, authenticated cluster client, account/ledger bootstrap, live transfer submission, account query, idempotency mapping, cluster operations or migration/rollback integration. | Cluster/account/ledger topology, signed credentials, live sandbox/prod-shaped transfer conformance, account balance reconciliation, replay/idempotency proof, fault/rollback tests and operations runbook. |
| Mojaloop | `SettlementProvider.MOJALOOP`; reviewed-snapshot parser and fail-closed mismatch reporting for missing, duplicate, unknown, money/currency and state errors. | No Mojaloop SDK/client, OAuth/mTLS/callback verification, participant/DFSP configuration, quote/transfer lifecycle, settlement-report pull or operational exception workflow. | Participant onboarding, approved credentials/certificates, callback signature verification, sandbox conformance, settlement export process, exceptions/reversals and scheme acceptance evidence. |
| OpenSearch | HTTPS endpoint setting, capability/integration manifest, storage-encryption attestation requirement when configured, and Kafka projection envelope available as an upstream event boundary. | No OpenSearch client dependency, index template/mapping, projection consumer/indexer, tenant/document access policy, redaction pipeline, snapshot/restore, retention or replay implementation. | Managed/self-hosted domain, encrypted nodes/snapshots, index/data model, Kafka consumer checkpointing/idempotency, field redaction, RBAC/tenant search tests, retention/deletion and replay/restore evidence. |

The repository search found only configuration/capability/manifest references for these platforms in the Python application source, apart from the explicit snapshot reconciliation code. There is no TigerBeetle, Mojaloop or OpenSearch client library in the declared Python dependencies. Therefore these are **not live integrations**; TigerBeetle and Mojaloop have a useful reconciliation control boundary, and OpenSearch remains a projection architecture target.

## 4. Prioritized remediation roadmap

The roadmap assumes a cross-functional delivery team with engineering, platform/security, payment operations, tax/compliance and business ownership. Time ranges are planning bands, not regulatory or vendor commitments.

| Priority and dependency | Work package | Main deliverables | Exit criterion |
|---|---|---|---|
| P0.1 — immediately | Repository hygiene and release baseline | Update ignore rules for mobile/Rust build artifacts; split the large working tree into reviewable commits; review raw diff; run complete quality/migration/image gate on final tree. | All intended source is committed, generated files excluded, CI is green and PR checks are required. |
| P0.2 — immediately | Authoritative fiscal-rule governance | Approved rate/product-rule source, versioned ingestion, effective-date test cases, ownership and change-control procedure. | Every production price derives from an approved source with traceable version and business/legal sign-off. |
| P0.3 — immediate prerequisite | Production-shaped trust foundation | Private network, APISIX TLS/mTLS, Keycloak realm/client/claims, Permify model, openAppSec baseline, secrets manager and environment promotion process. | Authenticated/negative tenant-role matrix passes through the gateway and all service credentials are externalised. |
| P0.4 — immediate prerequisite | Data protection, KMS/HSM and recoverability | Actual KMS/HSM key policy, encrypted PostgreSQL/Redis/OpenSearch where used, backup/snapshot encryption, access review, key rotation/recovery exercise and data-location/transfer register. | Provider-side evidence plus successful restore and access-revocation/rotation exercises, not just syntax-attested files. |
| P0.5 — immediate prerequisite | Deploy/DR/incident evidence | Immutable build promotion, migrations, rollback, restore, RTO/RPO measurement, monitoring/alert ownership, incident and tabletop exercises. | Rehearsed deployment, restore and rollback in production-shaped non-production with recorded remediation. |
| P1.1 — external dependency | Regulatory and payment conformance | FIRS/NAFDAC/SON/Customs sandbox tests; bank/PSP/switch signed settlement feed; contract/error/retry evidence. | Approved sandbox/partner acceptance and controlled error/reconciliation workflows. |
| P1.2 — financial integration decision | TigerBeetle and/or Mojaloop delivery | Selected architecture, client adapter, credential/certificate handling, lifecycle/settlement/reversal support and daily reconciliation. | End-to-end financial flow conformance with no unhandled exception path and operator-approved exception handling. |
| P1.3 — event/search/data operations | Kafka governance and OpenSearch delivery; decide Dapr/Fluvio/lakehouse need. | Topic/schema/ACL/DLQ/replay/lag controls; OpenSearch index/redaction/RBAC/retention; approved scope for optional platforms. | Operational replay, restore, access and retention tests pass; no unused production service is deployed. |
| P2 — field and physical features | Mobile device trust, offline workflow decision, printer/hologram/ML vendor integration or formal scope exclusion. | Enrollment/revocation/signing, privacy/replay/lost-device tests; vendor hardware acceptance or approved deferral. | Field and physical-tax-stamp scope is demonstrably supported or explicitly excluded by accountable owners. |
| P2 — release governance | Legal, DPO, CBN/payment-scheme and business release authority. | DPIA/transfer assessment, contracts, audit evidence, training and decision records. | Written authority to release for the actual licence, service and data-processing scope. |

## Bottom line

The correct next milestone is **not** a broad feature merge. It is a clean, reviewable P0 release-baseline pull request, followed by a production-shaped trust/data/DR environment. TigerBeetle, Mojaloop and OpenSearch should each remain explicitly non-live until their client, security, data and operational acceptance criteria are met. This preserves the current fail-closed posture instead of converting configuration declarations into unverified financial or search operations.

## References

[1]: ./CODE_FEATURE_COMPLETENESS_ASSESSMENT.md "Code feature completeness and outstanding gaps"

[2]: ./SANDBOX_RECONCILIATION.md "TigerBeetle and Mojaloop sandbox reconciliation"

[3]: ../src/taxstamp/services/external_settlement_reconciliation.py "Snapshot-driven settlement reconciliation"

[4]: ../src/taxstamp/projection.py "Kafka-compatible transactional outbox projection"

[5]: ../src/taxstamp/capabilities.py "Runtime capability registry"
