# Generated-Artifact Cleanup and TigerBeetle Intent/Outbox Design

**Scope:** Safe source-control hygiene for local generated artifacts and a proposed durable PostgreSQL-to-TigerBeetle integration state machine.
**Status:** Design and operator guidance only. No local files were deleted, no Git paths were staged, and no TigerBeetle client was added or called.

> **Financial-control boundary:** PostgreSQL and TigerBeetle cannot be committed in one distributed ACID transaction by this application. The design below uses a durable local intent and replay-safe external identifier, then reconciles independently. It must not be represented as atomic two-phase commit. [1] [2]

## 1. Non-destructive generated-artifact cleanup

### Current source-control issue

The current `.gitignore` excludes Python caches and generic build directories but does not exclude the generated mobile dependency directory or the Rust target directory. Consequently, these local build outputs appear as untracked files:

| Generated path | Keep in source control? | Recreate command |
|---|---|---|
| `apps/mobile/node_modules/` | No | `pnpm install` from `apps/mobile/` |
| `apps/mobile/.expo/` | No | Expo recreates it locally. |
| `adapters/rust/ledger-boundary/target/` | No | `cargo build` or `cargo test` from the crate directory. |

Keep `apps/mobile/package.json`, `apps/mobile/pnpm-lock.yaml`, `apps/mobile/App.tsx`, the Rust `Cargo.toml`, `Cargo.lock`, source and tests. Those are reproducibility inputs, not generated outputs.

### Step A — inspect only; do not delete

```bash
cd /home/ubuntu/taxstamp

# Show only the generated paths currently visible to Git.
git status --short -- \
  apps/mobile/node_modules \
  apps/mobile/.expo \
  adapters/rust/ledger-boundary/target

# Preview exactly what Git would remove if asked; -n means no deletion.
git clean -nd -- \
  apps/mobile/node_modules \
  apps/mobile/.expo \
  adapters/rust/ledger-boundary/target
```

If the preview contains a hand-created file that needs retaining, move it outside the generated directory before cleanup. Do not use `git clean -fd` against the whole repository.

### Step B — add source-control exclusions

Append the following **exact lines** to `.gitignore` in a separately reviewed hygiene commit:

```gitignore
# Generated mobile dependencies and Expo metadata.
apps/mobile/node_modules/
apps/mobile/.expo/

# Rust compiler and test outputs.
adapters/rust/ledger-boundary/target/
```

Verify the rules before staging them:

```bash
git check-ignore -v apps/mobile/node_modules/.modules.yaml
git check-ignore -v adapters/rust/ledger-boundary/target/debug
git status --short -- \
  apps/mobile/node_modules \
  apps/mobile/.expo \
  adapters/rust/ledger-boundary/target
```

`.gitignore` affects untracked paths only; it does not delete local files and does not remove a path that is already tracked. If any generated path was previously committed, remove it from the Git index only after review with `git rm -r --cached <path>`; retain the local file by checking the resulting `git status` before committing.

### Step C — clean generated outputs only after the preview is accepted

Use the tool-native cleanup commands where possible:

```bash
cd /home/ubuntu/taxstamp

# Removes only local mobile dependencies; lockfile and source remain untouched.
rm -rf apps/mobile/node_modules apps/mobile/.expo

# Removes only compiled Rust output for this crate.
cargo clean --manifest-path adapters/rust/ledger-boundary/Cargo.toml

# Confirm only intended outputs disappeared and source files remain.
git status --short
```

Run these cleanup commands only after inspecting the Step A preview. They are local destructive operations, so they are shown here for the operator to run—not executed by this assessment. Restore dependencies later with `pnpm install` and regenerate Rust outputs with `cargo test` or `cargo build`.

### Step D — stage the hygiene rule alone

```bash
git add -- .gitignore
git diff --cached --check
git diff --cached -- .gitignore
git commit --only .gitignore -m "chore: ignore generated mobile and Rust build outputs"
```

Do not combine this hygiene commit with the GitHub Actions workflow commit or financial-integration implementation. It makes review, rollback and merge conflict resolution safer.

## 2. Durable PostgreSQL-to-TigerBeetle intent/outbox state machine

### Recommended ownership model

Choose one of the following explicitly before implementation:

| Model | TigerBeetle role | Local posting rule |
|---|---|---|
| **Control subledger** | Independent double-entry control record that mirrors an already-authorized local movement. | Local business posting may occur first; the TigerBeetle intent must reconcile or quarantine before release/settlement closure. |
| **Authoritative operational subledger** | The approved internal source of posting truth for the relevant money movement. | A Taxstamp payment becomes locally posted only after the TigerBeetle transfer is confirmed and the local finalisation transaction commits. |

For either model, write the exact account/ledger mapping before coding: legal entity, programme/tenant, currency, asset/liability/revenue/expense account, debit/credit direction, transfer flags, settlement reference, reversal method and migration owner. The existing Rust ledger boundary helps reject non-positive or same-account intents but does not replace this mapping or provide a client. [3]

### Persistent records

Create two PostgreSQL tables and reuse the existing immutable audit/outbox conventions.

| Record | Essential fields | Invariants |
|---|---|---|
| `tigerbeetle_ledger_intent` | Internal UUID; payment/order reference; stable 128-bit `tigerbeetle_transfer_id`; debit/credit account IDs; ledger code; amount minor units; currency; transfer flags; canonical payload digest; `state`; `attempt_count`; `last_error_code`; `external_timestamp`; `created_at`/`updated_at`. | Unique `tigerbeetle_transfer_id`; unique business idempotency key; immutable financial fields after `READY`; amount is positive integer minor units; debit and credit differ. |
| `outbox_message` (existing, extended event) | Event type `tigerbeetle.transfer_requested`; aggregate/intent ID; dedupe key equal to stable transfer ID; payload contains no secret; lease/retry metadata. | Inserted in the **same PostgreSQL transaction** as the intent and audit event; never marked delivered before external outcome is confirmed. |
| `tigerbeetle_reconciliation_finding` | Intent ID, provider transfer ID, check time, finding kind, expected/observed digests, severity, resolution owner/state. | A material discrepancy is append-only and cannot be silently overwritten or automatically corrected. |

The external transfer ID must be generated by the Taxstamp system layer before the outbox event is emitted and persisted permanently. TigerBeetle uses object IDs for idempotent creation; retrying the same transfer ID should result in the original creation or an `exists` outcome, not a second money movement. [1] [2]

### State model and allowed transitions

```text
NEW
  -> READY                  (local transaction creates intent + audit + outbox)
READY
  -> LEASED                 (worker obtains a bounded processing lease)
LEASED
  -> SUBMISSION_UNCERTAIN   (network call begins or timeout/connection loss occurs)
  -> REJECTED               (deterministic provider validation failure)
SUBMISSION_UNCERTAIN
  -> EXTERNAL_CONFIRMED     (create result or lookup proves exact transfer)
  -> READY                  (safe retry only after lookup proves transfer absent)
  -> QUARANTINED            (conflicting transfer/detail, unresolved timeout or control breach)
EXTERNAL_CONFIRMED
  -> LOCAL_FINALISATION     (transaction begins to link local posting/audit)
LOCAL_FINALISATION
  -> POSTED                 (local transaction commits)
  -> EXTERNAL_CONFIRMED     (local transaction failure; retry finalisation, never re-create transfer)
POSTED
  -> RECONCILED             (independent reconciliation confirms expected external state)
REJECTED | QUARANTINED | RECONCILED
  -> REVERSAL_PENDING       (only through an approved compensating-transfer workflow)
REVERSAL_PENDING
  -> RECONCILED             (a separate, linked reversal transfer is confirmed and reconciled)
```

`SUBMISSION_UNCERTAIN` is a crucial state. A timeout cannot be interpreted as “not sent”: the request may have committed externally while the response was lost. The worker must `lookup_transfers` by the persisted ID before issuing another create. TigerBeetle documents idempotent object creation, immutable committed requests and retry behavior; it also notes that client requests may be retried until a reply. [1] [2]

### Transaction and worker flow

#### A. Local domain transaction — no network call inside the transaction

```text
BEGIN PostgreSQL transaction
  validate domain payment/settlement preconditions and approved account mapping
  generate stable TigerBeetle transfer ID
  calculate canonical transfer payload + SHA-256 digest
  INSERT ledger_intent(state=READY, ...)
  INSERT audit event "tigerbeetle_intent_created"
  INSERT outbox event(tigerbeetle.transfer_requested, dedupe_key=transfer_id)
COMMIT
```

The transaction either persists the intent, audit event and outbox event together or persists none of them. It must not call TigerBeetle before the local commit because a database rollback after a successful remote transfer would create an untracked external movement.

#### B. Relay worker — create or verify, never guess

```text
claim one due outbox message with a short lease
load ledger_intent FOR UPDATE
if state is POSTED, RECONCILED, REJECTED or QUARANTINED: acknowledge/no-op appropriately
mark state SUBMISSION_UNCERTAIN and increment attempt count; commit local state

lookup TigerBeetle transfer by persisted transfer ID
if found:
    verify debit account, credit account, ledger, amount, code and flags match payload digest
    if mismatch: record immutable finding; transition QUARANTINED; alert; do not overwrite
    else: transition EXTERNAL_CONFIRMED
if not found:
    submit create_transfer using the same stable transfer ID
    if created or exists: lookup/verify exact transfer then transition EXTERNAL_CONFIRMED
    if deterministic business validation error: transition REJECTED and retain provider result
    if network/availability error: retain SUBMISSION_UNCERTAIN, release with bounded backoff

BEGIN PostgreSQL transaction
  reload intent in EXTERNAL_CONFIRMED
  post/link local settlement according to selected ownership model
  write audit event "tigerbeetle_transfer_confirmed"
  mark intent POSTED and outbox delivery complete
COMMIT
```

The implementation must never regard `exists` alone as proof of a correct duplicate. It should verify the stored external transfer’s material fields against the immutable local payload digest. A different transfer using the same ID is a critical control failure, not an idempotent success.

### Failure matrix

| Failure point | Safe state and action | Prohibited shortcut |
|---|---|---|
| PostgreSQL transaction rolls back before outbox insert | No intent exists; no external call occurred. | Generating a transfer remotely before local durability. |
| Worker crashes before remote call | `READY` or expired lease; safely retry. | Marking event permanently delivered based on lease acquisition. |
| Request or response is lost | `SUBMISSION_UNCERTAIN`; lookup by stable ID, then verify. | Creating a new ID and sending a second transfer. |
| TigerBeetle create returns `exists` | Lookup and verify exact material fields. | Assuming any `exists` result means equivalent money movement. |
| External success, local finalisation transaction fails | Preserve `EXTERNAL_CONFIRMED`; retry local finalisation only. | Reissuing the external transfer or deleting evidence. |
| External deterministic rejection | `REJECTED`; retain result and resolve business cause. | Retrying indefinitely or silently marking settlement paid. |
| External/local mismatch found in reconciliation | `QUARANTINED`; append finding, alert owner and use governed compensating process if approved. | Auto-adjusting balances, deleting transfer or mutating the original record. |

### Reconciliation and operations

Use both event-driven confirmation and an independent periodic reconciliation. The periodic process should query or obtain reviewed TigerBeetle transfer data by stable ID/time window, compare reference, debit/credit account, ledger, amount, code/flags and posting state, then persist findings. The current snapshot reconciliation implementation already demonstrates fail-closed missing, duplicate, unknown, amount/currency and state findings; extend it with TigerBeetle account/transfer field comparison rather than replacing it. [4]

Metrics should include intents by state, age of oldest `READY`/`SUBMISSION_UNCERTAIN`, worker lease expiry, create/lookup success/failure, `exists` verification failures, finalisation retry count, reconciliation exception count and time to quarantine. Alerts must route to finance operations and platform/security owners, not only application engineering.

### Minimum implementation and acceptance tests

1. One local payment intent creates exactly one durable intent/outbox pair under concurrent requests.
2. Timeout before and after external commit resolves via transfer lookup using the same ID.
3. A duplicate external ID with different payload quarantines the intent and does not post locally.
4. External confirmation followed by local database failure finalises locally on retry without a second external transfer.
5. Deterministic rejection, unavailable cluster, lease expiry, worker restart and partial-batch behavior remain observable and replay-safe.
6. Reconciliation detects missing, duplicate, wrong-account, wrong-ledger, amount, code/flag and state discrepancies.
7. A controlled reversal creates a linked compensating transfer and preserves both audit trails.
8. Restore/failover and role-denial tests prove the account map, transfer history and reconciliation records survive and remain protected.

## References

[1]: https://docs.tigerbeetle.com/coding/reliable-transaction-submission/ "TigerBeetle reliable transaction submission"

[2]: https://docs.tigerbeetle.com/coding/requests/ "TigerBeetle requests and idempotency"

[3]: ../adapters/rust/ledger-boundary/ "Taxstamp ledger boundary crate"

[4]: ../src/taxstamp/services/external_settlement_reconciliation.py "Taxstamp external settlement reconciliation"
