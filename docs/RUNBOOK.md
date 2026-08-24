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
| `unit_quantity_not_conserved` finding | A trade unit's recorded stamp count no longer matches its members or its children. Do not reconcile by editing the count: identify the unit from the finding, re-scan it, and record a correcting aggregation or disaggregation so the history explains the change. |
| `consignment_short_of_stamps` finding | A consignment carries fewer linked stamps than it declared. Release is already blocked; investigate the shortfall with customs before linking further stamps. |
| `export_integrity_broken` finding | A stored export's signature no longer matches its content hash. Treat as a security incident: preserve the database, snapshot it, and escalate — the disclosure evidence can no longer be relied on. |
| `checkpoint_root_broken` finding | A published transparency checkpoint no longer matches the audit chain it commits to. Treat as a security incident and escalate; do not publish further checkpoints until the cause is known. |
| Anomaly queue growing (`GET /v1/anomalies`) | Findings are deterministic contradictions, not scores. Work each one from its stored evidence and rule version; an impossible-travel finding usually means either a mis-keyed facility or a cloned mark. |
| `overlapping_tariff` finding | Two rates cover the same category and date, so pricing is ambiguous. Close the earlier rate's effective period; new overlapping rates are refused at entry. |

## Offline verification

- Publish a bundle with `POST /v1/offline/bundles` and distribute the latest
  (`GET /v1/offline/bundles/latest`) to field devices. Each bundle is signed, carries a
  monotonic sequence and expires at `valid_until`; a device holding an expired bundle must
  refuse to rely on it rather than fall back to trusting the mark.
- A filter hit means "possibly revoked" and must be escalated to an online check. A miss
  means only "not on this revocation list" — never treat it as proof of authenticity.
- The signing key and the filter key are separate secrets
  (`TAXSTAMP_OFFLINE_SIGNING_SECRET`, `TAXSTAMP_OFFLINE_FILTER_SECRET`). Rotate them
  independently; a distributed bundle discloses neither. After rotating the signing key,
  publish a new bundle immediately, because devices reject signatures they cannot verify.
- Scans returned by a device are advisory: the server re-decides each one. A batch replayed
  with identical contents is idempotent; the same `(device, sequence)` with different
  contents is refused as a conflict and must be investigated rather than renumbered.

## Enforcement

| Symptom | Action |
| --- | --- |
| A case cannot be closed | Goods are still held under it. Settle each seizure (release, destruction or forfeiture, with a reason) first; closure while goods remain in custody is refused by design. |
| A custody chain fails verification | Treat as a security incident: the append-only trigger must have been bypassed. The verification response names the first broken sequence number — preserve the database, snapshot it, and escalate. |
| An officer cannot refer or close their own case | Intended: the officer who opened a case may not decide it. Route the decision to another supervisor. |
| Consumer verification failures rising for one serial | Look for a velocity finding: many distinct clients checking one serial usually means a cloned mark, not a device fault. |

## Retention, legal hold and portability

- The published policy is served from `GET /v1/retention-policy`; expiry is archive-only and
  statutory records are never destructively erased.
- A legal hold is an operator declaration recorded in the audit chain. No automated purge
  exists, so a hold suspends nothing: it is evidence that the records must not be archived
  out of the live database.
- A company's own data is exported with `POST /v1/exports/portability`, and a regulator
  disclosure with `POST /v1/exports/regulator`. Both carry a canonical-JSON hash and a
  purpose-separated signature; verify them with the export signing secret before relying on
  a copy that has left the platform.
- Regulator delivery is never claimed by the request that creates the export. With no
  repository endpoint configured, nothing is delivered; with one configured, the outbox
  relay delivers it and the outbox is the record of whether it arrived.

## Observability

- Structured JSON logs with request IDs on every request.
- Prometheus metrics at `/metrics` (admin/auditor only): request counts and latency,
  stamps issued, reconciliation findings by kind.
- Alert on: `reconciliation_findings` non-zero, readiness failures, 5xx rate, outbox
  backlog, and verification failure ratio.
