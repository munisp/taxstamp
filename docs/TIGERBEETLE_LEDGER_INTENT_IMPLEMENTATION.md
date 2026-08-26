# TigerBeetle Ledger-Intent Implementation and Migration Guide

**Implementation date:** 2026-08-26
**Migration:** `4bf6b1f5f0ab` (`0002_tigerbeetle_ledger_intent.py`)
**Status:** Durable PostgreSQL intent/outbox control and client-agnostic retry boundary implemented and tested. **No live TigerBeetle cluster, account, client credential, or financial transfer was used.**

> The implementation is deliberately **disabled by default**. `Runtime.tigerbeetle_client` is `None` until a separately reviewed deployment adapter provides a version-compatible, authenticated client. A relay event then fails closed as `capability_not_configured`; it is not reported as delivered. This is not a live TigerBeetle integration certification.

## 1. Database migration

The exact migration is version-controlled at:

```text
migrations/versions/0002_tigerbeetle_ledger_intent.py
```

It adds the `tigerbeetle_ledger_intents` table. Its purpose is to persist an immutable transfer request before a relay attempts the external side effect.

| Column group | Columns | Control purpose |
|---|---|---|
| Identity and relationship | `id`, `payment_intent_id`, `tigerbeetle_transfer_id` | Primary key plus one local payment intent and one durable external transfer ID per intent. |
| Immutable transfer material | `debit_account_id`, `credit_account_id`, `ledger_code`, `transfer_code`, `transfer_flags`, `amount_minor`, `currency`, `payload_hash` | Supplies the exact material that must match an observed external transfer. |
| Lifecycle and operations | `state`, `attempt_count`, `last_error`, `external_timestamp`, `external_confirmed_at`, `posted_at`, `created_at`, `updated_at` | Records uncertainty, confirmation, local finalisation, rejection or quarantine without changing the transfer material. |

### Database-enforced constraints and indexes

| Constraint or index | Enforcement |
|---|---|
| `uq_tigerbeetle_ledger_intents_payment_intent_id` | A payment intent cannot create two TigerBeetle control intents. |
| `uq_tigerbeetle_ledger_intents_tigerbeetle_transfer_id` | The persisted 128-bit external transfer ID is unique. |
| Account/amount checks | Debit and credit identifiers differ; amount is positive. |
| Numeric range checks | Ledger code is unsigned 32-bit, transfer code is unsigned 16-bit, flags and attempts are non-negative. |
| Format checks | Account/transfer IDs are 32-character lower-hex values; payload hash is a 64-character lower-hex SHA-256 digest; currency is a 3-character uppercase code. |
| State check | Only `ready`, `submission_uncertain`, `external_confirmed`, `posted`, `rejected`, and `quarantined` are accepted. |
| `ix_tigerbeetle_ledger_intents_state_created` | Supports relay/reconciliation investigation of intents by state and age. |
| PostgreSQL trigger | Rejects updates to every financial identity/material field, while permitting only state and operational-evidence updates. |

### Apply and verify the migration

Use an approved non-production database and an operator-provided connection string. Do not run this against a production database until the migration has passed the organisation’s change-control and backup/rollback process.

```bash
cd /home/ubuntu/taxstamp

# Apply the immutable intent table after reviewing the target URL.
TAXSTAMP_DATABASE_URL="$NONPROD_DATABASE_URL" .venv/bin/alembic upgrade head

# Confirm the current migration revision.
TAXSTAMP_DATABASE_URL="$NONPROD_DATABASE_URL" .venv/bin/alembic current

# Inspect table, constraints, indexes and trigger in PostgreSQL.
psql "$NONPROD_DATABASE_URL" -c '\d+ tigerbeetle_ledger_intents'
psql "$NONPROD_DATABASE_URL" -c "
  SELECT tgname
  FROM pg_trigger
  WHERE tgrelid = 'tigerbeetle_ledger_intents'::regclass
    AND NOT tgisinternal;"
```

The integration test performs a real downgrade-to-base and upgrade-to-head cycle against disposable PostgreSQL, verifies a single Alembic head and checks the new table exists. A second real-database test executes an `UPDATE` against `amount_minor` and verifies PostgreSQL rejects it through the immutability trigger.

## 2. Durable local workflow

`create_ledger_intent()` in `src/taxstamp/services/tigerbeetle_ledger.py` locks the payment intent and refuses creation unless its status is exactly `settled`. It validates amount/currency equality, normalises and validates the identifiers, computes a canonical SHA-256 payload hash, inserts the intent, writes a hash-chained audit event, and enqueues `tigerbeetle.transfer_requested` in the existing transactional outbox. All three local changes commit or roll back together.

The transition boundary is:

```text
ready -> submission_uncertain -> external_confirmed -> posted
                     |                  |
                     v                  v
                 rejected           quarantined
```

`quarantined` is terminal for the original transfer. A correction must be an independently approved, linked compensating transfer; the original record is never edited or deleted.

## 3. Lookup-before-retry algorithm

The relay handler invokes `submit_intent_lookup_before_retry()`. It transitions the local record to `submission_uncertain` **before** any client call. It then looks up the exact persisted transfer ID. Only when no external transfer is found does it submit `create_transfer` using that same ID. For a `created` or `exists` result, it performs a second lookup and verifies every material field before local finalisation.

```text
persist intent + outbox + audit in PostgreSQL
lease one outbox event
mark intent submission_uncertain and commit
lookup external transfer by the persisted ID
if found: verify every material field
if absent: create using the same persisted ID
if create says created or exists: lookup again and verify
if rejected: mark local intent rejected
if field mismatch: quarantine and emit audit evidence
if verified: mark external_confirmed, then mark posted locally
```

A timeout or lost response is never treated as “not sent.” The worker performs lookup before a retry. A duplicate `exists` result is not enough by itself: the observed external debit account, credit account, ledger, code, flags, amount and ID must exactly match the immutable local request.

The Rust ledger-boundary crate now carries a client-agnostic reference implementation at:

```text
adapters/rust/ledger-boundary/src/lib.rs
```

Its concrete `submit_lookup_before_retry()` function has the following contract:

```rust
pub fn submit_lookup_before_retry<C: TransferClient>(
    client: &mut C,
    intent: &TransferIntent,
) -> Result<SubmitOutcome, SubmitError<C::Error>>
```

The function validates the intent, calls `lookup_transfer(id)`, calls `create_transfer(intent)` only when lookup is empty, looks up again after `Created` or `Exists`, and returns `ExistingTransferMismatch` when the observed transfer differs. A deployment adapter must implement `TransferClient` with the approved TigerBeetle client; this crate intentionally has no network or credential dependency. [1] [2]

## 4. Reconciliation and failure handling

The normal platform reconciliation now emits `tigerbeetle_intent_control_failure` for either a quarantined intent or an intent that remains `ready`, `submission_uncertain`, or `external_confirmed` longer than the configured stale threshold. It preserves the intent IDs and state counts for operators; it does not auto-correct money movement.

| Failure condition | Implemented response |
|---|---|
| TigerBeetle adapter is not configured | Relay raises `capability_not_configured`; outbox remains retryable rather than being marked delivered. |
| Local worker restarts before submission | Existing leased/outbox retry semantics reclaim the event; same external ID is retained. |
| Network outcome is unknown | `submission_uncertain`; lookup by the same ID precedes any create retry. |
| Existing external transfer conflicts | Intent becomes `quarantined`, audit evidence is written and reconciliation surfaces a control finding. |
| Provider rejects a validly formed request | Intent becomes `rejected`; no local posted confirmation is created. |
| External confirmation succeeds but final local transaction fails | Intent remains externally confirmed and is finalised locally on retry without another external create. |

## 5. Validation evidence

The final isolated quality run used loopback-only disposable PostgreSQL and Redis dependencies. It passed `git diff --check`, Ruff format/lint on the production and changed paths, mypy over 59 source files, Bandit, pip-audit, the complete Python suite (**139 passed**; one existing non-failing Starlette/httpx deprecation warning), Rust format and **5 Rust tests**, plus the synthetic strict-compliance hook.

The Python TigerBeetle tests use real PostgreSQL migrations, constraints, transactions, trigger enforcement, outbox leasing, and reconciliation. Their in-process deterministic TigerBeetle client is explicitly a branch-level unit driver only; it is **not** evidence of actual TigerBeetle protocol, cluster, account, security or settlement interoperability.

The full repository-wide formatter remains blocked by a pre-existing formatting finding in `migrations/versions/0001_initial_schema.py`. The implemented and changed paths were all formatter-clean; the untouched initial migration is not included in the scoped quality command. This should be addressed in a separate non-functional maintenance commit.

## 6. Remaining live-integration gates

1. Approve the financial account/ledger mapping and whether TigerBeetle is a control or authoritative subledger.
2. Provision a non-production TigerBeetle cluster and accounts with approved network/credential/KMS controls.
3. Implement the actual version-pinned `TigerBeetleClient` adapter and inject it only after settings, health checks, secret management and readiness probes exist.
4. Run client/cluster conformance: timeout, lost response, `exists`, rejection, account/ledger mismatch, restart, failover, restore and reconciliation cases.
5. Obtain finance, risk, security and operations sign-off before enabling any money-affecting workflow.

## References

[1]: https://docs.tigerbeetle.com/coding/reliable-transaction-submission/ "TigerBeetle reliable transaction submission"

[2]: https://docs.tigerbeetle.com/coding/requests/ "TigerBeetle requests and idempotency"
