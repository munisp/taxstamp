# Runbook

## Deploy

1. Build and push the image; deploy by digest, never by a floating tag.
2. Run `alembic upgrade head` as a separate job that must complete before the API and
   worker roll out (`docker-compose.yml` models this ordering).
3. Roll out the API, then the worker. Readiness (`/readyz`) checks PostgreSQL and Redis;
   an instance that cannot reach either stays out of the load balancer.
4. After rollout, run `POST /v1/ops/reconciliation` and `GET /v1/ops/audit-chain` and
   confirm both are clean.

## Rollback

- **Application only:** redeploy the previous image digest. All migrations in this revision
  are additive, so the previous application version runs against the current schema.
- **Schema:** `alembic downgrade -1`. Verified upgrade → downgrade → upgrade against a real
  database in CI. Downgrading the initial revision drops all tables and therefore all data:
  restore from backup instead if any production data exists.
- Never roll back while the outbox has unprocessed messages whose handler no longer exists
  in the target revision; drain or dead-letter them first.

## Backup and restore

- Take `pg_dump --format=custom` snapshots plus WAL archiving; Redis holds only nonces,
  rate-limit counters and leases and needs no backup.
- Restore drill: `pg_restore` into an empty database, run `alembic upgrade head` (expect no
  change), then run reconciliation and audit-chain verification. Both must be clean before
  the restored database is accepted. **This drill has not been executed** — see the
  assurance report.

## Incident playbooks

| Symptom | Action |
| --- | --- |
| `/readyz` failing | Check PostgreSQL and Redis reachability. Verification and rate limiting fail closed by design: no Redis means no verification. |
| Outbox backlog rising (`reconciliation_findings{kind="outbox_backlog"}`) | Inspect `outbox_messages.last_error`; a `CapabilityNotConfigured` error means an external dependency is unset — configure it and the message is retried. |
| Dead-lettered messages | Fix the cause, then requeue by resetting `dead_lettered_at` and `attempts` for the affected rows in a reviewed migration or maintenance script. |
| `paid_order_without_receipt` finding | A payment was matched without a receipt row, or vice versa: stop issuance for that order and reconcile against the bank statement before intervening. |
| `audit_chain_broken` finding | Treat as a security incident: the append-only triggers must have been bypassed. Preserve the database, take a snapshot, and escalate. |
| Amount mismatch queue growing | Receipts are quarantined in `liability:unapplied_receipts`. List them with `GET /v1/treasury/unapplied-receipts`, then either apply one to its payable order (exact amount and currency) with `POST /v1/treasury/unapplied-receipts/{id}/application` or return it with `POST .../refund` naming the beneficiary. Each receipt resolves once. |
| `order_without_effective_licence` finding | An in-flight procurement now sits behind a licence that is missing, expired, suspended, revoked or not an ordering type. Decide per order: reinstate the licence, or cancel the order. Issuance is not blocked automatically once the order is already paid. |
| `disposition_not_voided` finding | Stamps declared spoiled, damaged, destroyed or returned are still live. Treat as potential diversion: identify the serials from `stamp_dispositions.serials` and investigate before voiding. |
| `overlapping_tariff` finding | Two rates cover the same category and date, so pricing is ambiguous. Close the earlier rate's effective period; new overlapping rates are refused at entry. |

## Observability

- Structured JSON logs with request IDs on every request.
- Prometheus metrics at `/metrics` (admin/auditor only): request counts and latency,
  stamps issued, reconciliation findings by kind.
- Alert on: `reconciliation_findings` non-zero, readiness failures, 5xx rate, outbox
  backlog, and verification failure ratio.
