# TigerBeetle Worker Trace, Test Coverage, and Migration Verification

**Assessment date:** 2026-08-26
**Scope:** The durable `tigerbeetle_ledger_intents` implementation added in migration `4bf6b1f5f0ab`.
**Critical status:** The outbox path is implemented and tested against real local PostgreSQL and Redis. It does **not** currently dispatch to the Rust crate at runtime, and it does **not** connect to a live TigerBeetle cluster.

> The Python service and Rust crate are deliberately separate bounded components today. There is no FFI, subprocess, RPC, or shared-library bridge between them. Claiming that the worker currently “dispatches to Rust” would be incorrect.

## 1. Actual durable outbox worker path

### Creation boundary

`create_ledger_intent()` in `src/taxstamp/services/tigerbeetle_ledger.py` is the required application-service entry point. It first locks the local `payment_intents` row and requires `status = settled`. It then validates the account/transfer identifiers and monetary material, computes a canonical SHA-256 payload hash, and writes three local records in one PostgreSQL transaction:

| Local write | Reason |
|---|---|
| `tigerbeetle_ledger_intents` row in `ready` state | Persists the stable 128-bit transfer ID and immutable material before any remote call. |
| Hash-chained `audit_events` row | Preserves an immutable record of intent creation. |
| `outbox_messages` event, `tigerbeetle.transfer_requested` | Makes external delivery retryable after the local transaction commits. |

Duplicate creation attempts lock and compare the immutable canonical payload. The same material returns the existing durable intent; differing material under the same payment or transfer ID raises a conflict. A real PostgreSQL concurrency test runs two simultaneous application sessions and verifies that exactly one intent and one outbox message result.

### Relay and handler path

```text
tigerbeetle_ledger_intents (ready)
        + matching outbox_messages row
                      |
                      v
worker.relay.relay_once()
  └─ outbox.claim_batch(... FOR UPDATE SKIP LOCKED ...)
      └─ worker.handlers.handler_for()
          └─ handle_tigerbeetle_transfer()
              └─ submit_intent_lookup_before_retry()
                  ├─ runtime.tigerbeetle_client.lookup_transfer(stable_id)
                  ├─ create_transfer(same stable_id), only if lookup is empty
                  ├─ lookup again after Created or Exists
                  ├─ exact material comparison
                  └─ local POSTED / REJECTED / QUARANTINED finalisation
```

The outbox relay leases due messages via PostgreSQL `FOR UPDATE SKIP LOCKED`. A worker owns a short lease, not the transfer permanently. If an exception escapes the handler, the generic outbox retry/backoff/dead-letter policy applies and the message is not marked processed. If `runtime.tigerbeetle_client` is `None`, the handler raises `CapabilityNotConfigured`; the message remains visible and retryable rather than returning a fabricated success.

The relay does **not** hold an open PostgreSQL transaction across a remote call. It first changes the intent to `submission_uncertain` in a local transaction. A lost request/response is therefore an unknown outcome, never proof that no transfer exists.

## 2. Rust ledger-boundary: current integration point

`adapters/rust/ledger-boundary/src/lib.rs` now contains a concrete, client-agnostic implementation:

```rust
pub fn submit_lookup_before_retry<C: TransferClient>(
    client: &mut C,
    intent: &TransferIntent,
) -> Result<SubmitOutcome, SubmitError<C::Error>>
```

The crate validates an intent, looks up its stable ID, creates only after an empty lookup, looks up again after `Created` or `Exists`, and rejects a mismatched observed transfer. Its `TransferClient` trait is the intended semantic contract for a future deployment adapter.

| Current fact | Consequence |
|---|---|
| Python worker calls `Runtime.tigerbeetle_client`, not Rust. | The Python path cannot currently benefit from the Rust function at runtime. |
| `Runtime.tigerbeetle_client` defaults to `None`. | Live transfer submission is disabled until an approved adapter is implemented and injected. |
| Rust crate has no network or credential dependency. | It is safe to compile/test locally, but it is not TigerBeetle protocol/cluster conformance evidence. |

To use Rust in the production worker, an explicit design decision is required. The preferred options are either: (a) implement the exact same lookup-before-retry contract in the approved Python client adapter and keep Rust as a separately tested boundary crate; or (b) expose the Rust crate as a versioned service/library with a defined IPC/FFI contract, request/response schema, timeouts, process supervision, telemetry, rollout/rollback and integration tests. Do not introduce an ad hoc subprocess call in the outbox worker: it complicates durability, availability and auditability without proving TigerBeetle correctness.

## 3. Integration-test suite walkthrough

`tests/integration/test_tigerbeetle_ledger.py` uses the real Alembic-migrated PostgreSQL and real Redis fixture. It contains a deterministic local client only to force the external-branch outcomes; that double is explicitly **not** a live TigerBeetle integration test.

| Test | Real dependency evidence | Assertion |
|---|---|---|
| `test_migration_rejects_mutation_of_durable_financial_fields` | PostgreSQL trigger | Updating `amount_minor` raises the database error declaring financial fields immutable. |
| `test_unsettled_payment_cannot_create_an_external_ledger_intent` | PostgreSQL payment row and domain service | Intent creation fails unless the payment is exactly settled. |
| `test_concurrent_duplicate_intent_requests_return_the_same_durable_record` | Two concurrent PostgreSQL sessions, unique constraints and savepoint recovery | Same payment/material creates one intent and one outbox event. |
| `test_relay_looks_up_before_creating_and_posts_matching_transfer` | Real outbox lease, durable state transitions and database finalisation | Empty first lookup leads to one create, second lookup and posted state. |
| `test_existing_matching_transfer_is_confirmed_without_a_create_call` | Real relay/database path | Existing exact transfer is posted with zero create calls. |
| `test_exists_result_requires_a_second_matching_lookup_before_posting` | Real relay/database path | `Exists` is followed by a required second lookup before posting. |
| `test_existing_mismatched_transfer_is_quarantined_without_create` | Real relay/database path plus reconciliation | Conflicting observed material never creates again, is quarantined and becomes an operator finding. |
| `test_unconfigured_client_leaves_the_outbox_retryable` | Real outbox failure/backoff persistence | The intent stays `ready`; the event remains unprocessed and not dead-lettered on its first failed attempt. |

### Race and duplicate coverage limits

The suite verifies database-level duplicate creation and generic `SKIP LOCKED` outbox behavior. It does **not** yet verify these live conditions, which remain release gates:

1. Two or more worker processes using an authenticated TigerBeetle client against the same transfer ID.
2. A network timeout after TigerBeetle commits but before the caller receives a response.
3. TigerBeetle cluster failover, read-after-write visibility, account/ledger provisioning, clock/timestamp behavior and partial batch results.
4. A failure after external confirmation but before local finalisation, followed by process restart and exact finalisation recovery.
5. Independent reconciliation against a real TigerBeetle query/export and an approved accounting/settlement report.

## 4. Comprehensive migration and rollback verification checklist

### Before forward migration

| Check | Evidence required |
|---|---|
| Revision and change approval | Capture `git rev-parse HEAD`, `git status --short`, target revision, reviewed migration SHA and change-ticket approval. |
| Compatibility | Verify deployed application code understands migration head `4bf6b1f5f0ab`; do not run a new worker against an old schema or an old worker against newly emitted TigerBeetle outbox events. |
| Backup and recovery | Confirm a tested, encrypted database backup/snapshot and access to its restoration procedure before the migration window. |
| Traffic and worker plan | Pause only the TigerBeetle producer/consumer path or deploy with its feature disabled. Do not drain or delete existing outbox records merely to simplify the change. |
| Capacity and locks | Inspect table size/lock policy and schedule the DDL window. The new table does not rewrite existing tables, but deployment ownership and rollback plans still require approval. |

### Apply forward migration in non-production first

```bash
cd /home/ubuntu/taxstamp
export TAXSTAMP_DATABASE_URL="$NONPROD_DATABASE_URL"

.venv/bin/alembic current
.venv/bin/alembic heads
.venv/bin/alembic upgrade head
.venv/bin/alembic current

psql "$TAXSTAMP_DATABASE_URL" -c '\d+ tigerbeetle_ledger_intents'
psql "$TAXSTAMP_DATABASE_URL" -c "
  SELECT indexname, indexdef
  FROM pg_indexes
  WHERE tablename = 'tigerbeetle_ledger_intents'
  ORDER BY indexname;"
psql "$TAXSTAMP_DATABASE_URL" -c "
  SELECT tgname
  FROM pg_trigger
  WHERE tgrelid = 'tigerbeetle_ledger_intents'::regclass
    AND NOT tgisinternal;"
```

Verify the table, both unique constraints, state/format/check constraints, both indexes and `tigerbeetle_ledger_intents_immutable_fields` trigger. Then run the migration round-trip and ledger-intent integration suite against a disposable database. The repository test command is:

```bash
.venv/bin/pytest -q \
  tests/integration/test_migrations.py \
  tests/integration/test_tigerbeetle_ledger.py
```

### Controlled post-migration checks

1. Create one settled test payment and one immutable durable intent in a disposable or approved non-production environment.
2. Verify the matching outbox event has the exact stable transfer-ID dedupe key.
3. Run the worker with the client disabled and confirm the message remains retryable; do not approve a false delivery.
4. Run the approved non-production client adapter only after its separate security and TigerBeetle conformance approval; exercise empty lookup, existing exact, `Exists`, mismatch, rejection and timeout recovery.
5. Verify reconciliation reports a quarantined or stale intent and that no job silently changes the transfer material.
6. Record the migration version, SQL object inspection, test output, evidence owner and rollback decision in the change record.

### Rollback decision gate

**The provided Alembic downgrade drops `tigerbeetle_ledger_intents` and its data. It is not a normal production rollback once an intent has been created.** Prefer a forward-only corrective migration after production use begins.

Only consider a schema downgrade when all conditions are true:

| Gate | Required proof |
|---|---|
| Quiescence | TigerBeetle producer/worker is stopped and no deployment can enqueue `tigerbeetle.transfer_requested`. |
| No durable intent data | `SELECT count(*) FROM tigerbeetle_ledger_intents;` returns `0`. |
| No related unresolved outbox event | Query for unprocessed/dead-lettered `tigerbeetle.transfer_requested` records returns `0`, or approved preservation/remediation exists. |
| Backup recovery | A valid backup exists and a restore owner is ready if the database state needs recovery. |
| Release compatibility | The rollback application release has no code path expecting the table or event handler. |

For a disposable/non-production rollback only:

```bash
export TAXSTAMP_DATABASE_URL="$NONPROD_DATABASE_URL"
.venv/bin/alembic downgrade 273aefd6e9e8
.venv/bin/alembic current
.venv/bin/alembic upgrade 4bf6b1f5f0ab
.venv/bin/alembic current
```

After a data-bearing production migration, preserve the table and evidence; issue a reviewed forward migration to disable, repair or supersede the feature instead of dropping financial control records.

## References

[1]: ../src/taxstamp/worker/relay.py "Transactional outbox relay"

[2]: ../src/taxstamp/worker/handlers.py "Outbox handler registry"

[3]: ../src/taxstamp/services/tigerbeetle_ledger.py "Durable TigerBeetle intent service"

[4]: ../adapters/rust/ledger-boundary/src/lib.rs "Client-agnostic Rust lookup-before-retry boundary"

[5]: https://docs.tigerbeetle.com/coding/reliable-transaction-submission/ "TigerBeetle reliable transaction submission"
