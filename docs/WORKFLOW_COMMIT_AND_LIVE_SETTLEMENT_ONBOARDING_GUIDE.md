# Workflow-Only Git Commit and Live Settlement Onboarding Guide

**Scope:** Safe staging of the GitHub Actions compliance workflow, plus the engineering and operational work needed to replace the current TigerBeetle/Mojaloop reconciliation-only boundaries with live integrations.
**Status:** Planning and source-control guidance. No financial transfer, provider onboarding, credential exchange, commit, push, pull request, or merge was performed.

> **Safety boundary:** The current repository deliberately has configuration and snapshot-reconciliation contracts, not live TigerBeetle or Mojaloop clients. Do not enable a live money-moving path until the approved operator, scheme/partner, security and finance functions have accepted the controls and the end-to-end conformance plan. [1] [2]

## 1. Commit only the GitHub Actions workflow

The repository currently has unrelated tracked changes and untracked generated outputs. The current `.gitignore` does not yet ignore `apps/mobile/node_modules/` or `adapters/rust/ledger-boundary/target/`, so **do not use `git add -A`, `git add .`, or a broad glob** for this narrow commit.

Run the following commands from the repository root. They stage and commit exactly one file, without altering or deleting any other working-tree changes:

```bash
cd /home/ubuntu/taxstamp

# Confirm the current working tree; this does not stage or delete anything.
git status --short
git diff --check
git diff -- .github/workflows/ci.yml

# Stage exactly the workflow. The explicit path leaves all other changes untracked/unstaged.
git add -- .github/workflows/ci.yml

# Confirm the index contains only the intended file before committing.
git diff --cached --check
git diff --cached --name-only
git diff --cached -- .github/workflows/ci.yml

# Commit only this path even if another file was staged by a separate local action.
git commit --only .github/workflows/ci.yml \
  -m "ci: enforce strict storage evidence checks on pull requests"

# Confirm the commit contains only the workflow.
git show --stat --name-only --oneline HEAD
git status --short
```

The configured local hook will run as part of `git commit` because the repository’s local `core.hooksPath` is `.githooks`. It only validates the repository’s synthetic fixtures, so it does not require provider credentials or query real evidence systems.

If `git diff --cached --name-only` shows any path other than `.github/workflows/ci.yml`, stop before committing. Review the index with `git diff --cached`; do **not** delete files or run a destructive clean command merely to make the output smaller.

After this narrow commit is independently reviewed, push and open a pull request:

```bash
git push origin devin/1787593004-tax-stamp-platform
gh pr create \
  --repo munisp/taxstamp \
  --base main \
  --head devin/1787593004-tax-stamp-platform \
  --title "CI: enforce strict storage-evidence validation" \
  --body "Adds the synthetic strict storage-encryption evidence job to pull-request CI."
gh pr checks --repo munisp/taxstamp --watch
```

The repository administrator must separately configure **strict storage-encryption evidence** as a required status check in the protected-branch ruleset. Merge only after code review and authorised release approval.

### Follow-up hygiene commit (separate from the workflow commit)

After review, add generated-path exclusions in a separate commit:

```gitignore
apps/mobile/node_modules/
apps/mobile/.expo/
adapters/rust/ledger-boundary/target/
```

This exclusion change is intentionally not included in the workflow-only staging command so reviewers can assess its scope independently.

## 2. TigerBeetle: live client and subledger implementation

### Decision and control design

Before writing the adapter, document whether TigerBeetle is Taxstamp’s **operational double-entry subledger**, the final internal accounting system of record, or only a secondary control ledger. Define one authoritative mapping for each currency, legal entity, tenant/product programme, asset/liability/revenue/expense account and payment reference. The application must not allow a user-supplied TigerBeetle account ID, ledger code, transfer flag or amount to bypass the domain service.

TigerBeetle models accounts and transfers as separate objects; creating objects with a stable ID is idempotent. It supports batched account/transfer requests, and its client behavior is designed for retrying requests until a reply is received. [3] [4] The Taxstamp adapter should therefore create and durably persist a 128-bit account or transfer identifier before the request is submitted, then reuse that same ID for retry/recovery. This identifier must be mapped to the Taxstamp payment/settlement and audit record.

### Concrete engineering sequence

| Step | Implementation work | Required acceptance evidence |
|---|---|---|
| 1. Architecture decision | Approve TigerBeetle role, account chart, ledger codes, currencies, account flags, zero-balance/close policy, transfer types and compensating-entry policy. | Finance, risk and platform sign-off on a versioned account-mapping specification. |
| 2. Deploy or procure the cluster | Use an approved managed offering or run a dedicated cluster. For self-hosting, model replica addresses, cluster ID, durable disks, supervisor, backups and operational ownership. TigerBeetle recommends six dedicated replicas for production fault tolerance; cluster geometry and endpoint order are fixed at creation. [5] [6] | Production-shaped cluster acceptance, independent failure-domain evidence, monitoring, backup/restore and incident runbook. |
| 3. Add a pinned client boundary | Add a maintained TigerBeetle client library compatible with the approved server version. Implement a narrow `TigerBeetleLedgerClient` interface in the server, lifecycle-managed shared client, timeouts/health indicators and a disabled-by-default feature gate. | Client/server compatibility test and secret/network architecture review. |
| 4. Provision accounts safely | Implement a migration/admin-only account bootstrap flow, deterministic Taxstamp-to-TigerBeetle 128-bit account IDs and duplicate handling. Never lazily create financial accounts in a customer request without governance. | Account inventory reconciles to the approved chart and can be reproduced in an isolated environment. |
| 5. Submit transfer intents | Translate only approved internal `LedgerIntent` objects into balanced transfers. Persist local intent, TigerBeetle transfer ID, request payload digest and state before submission. Treat `created` and `exists` as idempotent outcomes only after verifying the original mapping. | Duplicate, timeout, restart, partial-result and concurrent-submit tests with no unaccounted balance movement. |
| 6. Resolve cross-store consistency | Do not pretend PostgreSQL and TigerBeetle have a distributed transaction. Use a durable intent/outbox state machine, explicit retries, immutable audit events and daily/intraday reconciliation; quarantine discrepancies for review. | Failure injection proves every local intent reaches a terminal posted/quarantined/reversed state and reconciliation detects any divergence. |
| 7. Operate and recover | Implement read-only balance/transfer lookup, reconciliation export, alerting, durable audit evidence and role-separated break-glass process. | Restore and re-lookup drill; records reconcile before and after failover/restore. |

### Minimum tests before live value is enabled

Test duplicate transfer IDs, API retry after lost response, worker restart, duplicate outbox delivery, insufficient funds/limits, partial batch result, unavailable client endpoint, replica failure, misconfigured address order, reconciliation mismatch, reverse/compensating process and access-control denial. The existing Rust crate validates basic positive/cross-account intent shape, but it is not yet a live client or financial operations implementation. [1]

## 3. Mojaloop: participant onboarding and settlement integration

### Operating model first

Determine the role before coding: Taxstamp may be a participant/DFSP, a technology provider to an approved participant, or an upstream tax-stamp merchant/biller—not automatically a Mojaloop scheme participant. The scheme/operator and regulated entity must determine participation agreement, permitted use case, currencies, customer/party identifiers, AML/CFT responsibilities, dispute process, settlement bank relationship, liquidity cover and settlement model. This is a business, legal and scheme decision, not a software toggle.

Mojaloop’s technical onboarding separates party discovery, quote agreement and transfer execution. A participant can implement asynchronous FSPIOP interfaces directly or integrate through Mojaloop-SDK/Payment Manager OSS; the official onboarding documentation recommends using an integration component rather than a direct connection where appropriate. [7]

### Concrete onboarding and engineering sequence

| Step | Implementation and onboarding work | Required acceptance evidence |
|---|---|---|
| 1. Obtain onboarding decision | Identify hub/operator, authorised participant/DFSP, scheme participation agreement, supported use case, currency and settlement model. | Written operator/participant approval and named accountable owners. |
| 2. Choose integration model | Select direct FSPIOP, Mojaloop-SDK or Payment Manager OSS based on internal ownership, asynchronous callback capability and operating model. Keep Taxstamp’s domain API separate from hub protocol translation. [7] | Architecture decision record, support model and software supply-chain review. |
| 3. Map state machines | Implement durable mapping from Taxstamp payment intent to party lookup, quote, transfer/fulfilment, callback and settlement states. Use immutable provider IDs and a inbox/outbox pattern for callbacks and retries. | State-transition specification and replay/concurrency test suite. |
| 4. Implement connection security | Exchange/validate mTLS certificates, configure OAuth 2.0 client access, IP allowlists, JWS signing/verification and ILP condition/fulfilment controls. Store private material in the approved secret/KMS boundary, never in source or attestation files. [7] [9] | Mutual authentication, certificate rotation, OAuth scope, JWS negative/positive and allowlist tests in hub pre-production. |
| 5. Build callback receiver | Verify transport/client identity and message signature before parsing; enforce idempotency; persist the event and correlation record durably before returning the scheme-required acknowledgement. Do not acknowledge a financial event based only on in-memory processing. [9] | Lost-response, duplicate callback, malformed signature, expired condition and restart/replay tests. |
| 6. Test transfer path | Exercise party lookup, quote, transfer, fulfilment, abort/timeout, duplicate request, asynchronous notifications and scheme error codes against hub simulations then friendly real participants. | Hub-provided technical, security, end-to-end, SLA and performance acceptance evidence. [7] |
| 7. Complete settlement onboarding | Configure participant, currency, callback URLs, liquidity cover, net debit cap, initial position and notification contacts with the hub. Select settlement window/report and exception workflow. [7] [8] | Settlement-window run, report ingestion, bank/partner confirmation and daily reconciliation sign-off. |
| 8. Operate exceptions | Build reconciled ingestion of settlement reports, discrepancy queue, manual investigation workflow, no-auto-repair policy, disputes/reversals and incident escalation. | Controlled exception drill; no balance mismatch remains silently accepted. |

### Settlement-specific controls

Mojaloop’s published settlement model requires the debtor participant to demonstrate adequate liquidity cover; it tracks participant position and supports settlement models such as multilateral or bilateral deferred net settlement and immediate gross settlement. Settlement reports support participant and settlement-bank reconciliation. [8] The Taxstamp implementation must not derive liquidity or settlement finality from a local status alone: ingest signed/authoritative hub results, link them to the payment and ledger intent, and run the existing fail-closed snapshot reconciliation against controlled settlement exports.

The current repository’s Mojaloop work is intentionally limited to a reviewed-snapshot parser and mismatch reporting. It has no FSPIOP client, credential/certificate exchange, participant provisioning, callback receiver, quote/transfer lifecycle or settlement-report API integration. [1] [2]

## 4. Joint go-live gates

1. **No live money path until independent reconciliation is green.** Compare Taxstamp records, TigerBeetle records (if used), Mojaloop transfer/settlement reports and bank/partner evidence at an approved cadence.
2. **No real credentials in the repository.** Use the existing external-secret and KMS/HSM evidence boundaries; record only non-secret identifiers and controlled evidence paths.
3. **No customer-facing automatic correction.** Quarantine discrepancies, preserve immutable audit evidence and apply a governed compensating/reversal process.
4. **No production promotion on syntactic evidence alone.** The current checker and CI job validate source-controlled synthetic fixtures, not live KMS/HSM, payment or settlement reality.

## References

[1]: ../src/taxstamp/services/external_settlement_reconciliation.py "Snapshot-driven TigerBeetle and Mojaloop reconciliation"

[2]: ./SANDBOX_RECONCILIATION.md "TigerBeetle and Mojaloop sandbox reconciliation runbook"

[3]: https://docs.tigerbeetle.com/coding/reliable-transaction-submission/ "TigerBeetle reliable transaction submission"

[4]: https://docs.tigerbeetle.com/coding/requests/ "TigerBeetle requests and idempotency"

[5]: https://docs.tigerbeetle.com/operating/deploying/ "TigerBeetle deployment"

[6]: https://docs.tigerbeetle.com/operating/cluster/ "TigerBeetle cluster recommendations"

[7]: https://docs.mojaloop.io/adoption/HubOperations/Onboarding/technical-onboarding.html "Mojaloop technical onboarding of DFSPs"

[8]: https://docs.mojaloop.io/adoption/HubOperations/Settlement/settlement-basic-concepts.html "Mojaloop settlement basic concepts"

[9]: https://docs.mojaloop.io/product/features/invariants.html "Mojaloop invariants and security"
