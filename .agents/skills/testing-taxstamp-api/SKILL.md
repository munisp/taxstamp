---
name: testing-taxstamp-api
description: How to stand up and adversarially test the taxstamp FastAPI service end-to-end over HTTP (live uvicorn + worker, real PostgreSQL/Redis), including HMAC-signed remittances/verification, tenant isolation, reconciliation gauges, migrations and concurrency races.
---

# Runtime testing of the taxstamp platform

There is no frontend: everything is exercised over the HTTP API (bearer tokens) plus `python -m taxstamp.cli`.
Do NOT record a screen video for this repo's testing — capture terminal transcripts and render them to PNG instead.

## Bring up an isolated environment

1. Deps: PostgreSQL on `55432` and Redis on `56379` (see repo blueprint). Use a **dedicated database per
   revision under test** so left-over rows never taint reconciliation:
   `psql .../postgres -c "CREATE DATABASE taxstamp_<tag>"`, then
   `TAXSTAMP_DATABASE_URL=postgresql+psycopg://taxstamp:taxstamp@127.0.0.1:55432/taxstamp_<tag> .venv/bin/alembic upgrade head`.
2. Env: prefix `TAXSTAMP_`; the four secrets (`API_TOKEN_SECRET`, `DEVICE_HMAC_SECRET`,
   `PAYMENT_WEBHOOK_SECRET`, `AUDIT_CHAIN_SECRET`) must each be >= 48 chars **and distinct**.
   Keep them in one sourced `env.sh` so the API, the worker and the test scripts sign with the same values.
3. External registries: order submission calls FIRS/NAFDAC/SON/CUSTOMS. Run a tiny local HTTP stub and point
   `TAXSTAMP_{FIRS,NAFDAC,SON,CUSTOMS}_BASE_URL` + `TAXSTAMP_LEDGER_ANCHOR_BASE_URL` at it; return
   `{"compliant": true, "reference": "...", "checked_at": "<iso>"}` and, for `/anchors`,
   `{"root", "reference", "anchored_at"}`. Without it order creation fails **503 "FIRS is unreachable"** —
   easy to mistake for a product bug. If the stub takes its port as `argv[1]`, remember to pass it: a crashed
   stub looks exactly like an unreachable registry.
4. Run **two** API instances: one fully configured, one with the registry URLs unset — the latter is how you
   prove the unconfigured-dependency refusal (503 `capability_not_configured`) and `requires_configuration`
   capability states without touching the configured instance.
5. Bootstrap: `create-company`, `add-tariff`, `create-principal` (prints the bearer token exactly once —
   capture it to a `tokens.json`). Create **two companies** and a requester in each: cross-tenant tests
   (orders, batches) need a second tenant.

## Signing requests

`v1|<unix-seconds>|<canonical JSON>` HMAC-SHA256, headers `x-signature` + `x-timestamp`; canonical JSON is
sorted-keys/compact/ASCII and **rejects floats**. Verification also needs a fresh `nonce` (Redis-backed,
single-use) and `derive_secure_code(serial, secret=<device secret>)`.
Send the *exact same bytes* you signed (`content=` raw string, not `json=`), otherwise you get 401
"signature is invalid". Never reuse signed bodies/headers persisted by an earlier run against a new
database — the reference no longer exists and you will misread it as a replay-protection failure.

## Gotchas that cause false failures

- Duplicate remittance delivery responds `{"status": "duplicate_delivery", "external_reference": ...}` —
  there is no `duplicate: true` field in that shape; assert on `status` plus an unchanged receipt row count.
- Table is `journals` (+ `ledger_entries`), not `ledger_journals`.
- Prometheus gauges are **per process**: if you reconcile via one uvicorn worker and scrape `/metrics` from
  another you can read a stale value. Pin gauge tests to a single-worker instance.
- `/metrics` is admin/auditor only (403 for operator/requester); `/readyz` and `/v1/verify` fail closed
  (503) whenever Redis is down — stop the Redis container to prove it, then restart it.

## Concurrency / lock-ordering tests

To exercise cancel-vs-settle style races over the real API, run uvicorn with `--workers 4` (verify the
children exist via `ps --ppid <master>`; they appear as `multiprocessing.spawn` processes) and fire the two
requests from two threads. Two useful variants: a `threading.Barrier(2)` for true simultaneity, and a
deliberate 20-100 ms head start for the slower path — **a barrier cancels out any head start**, so use
`Barrier(1)` (or drop it) when you want a specific winner. In practice settlement wins an even race, so
without a head start you will never exercise the cancellation branch.
Prove absence of deadlock with data, not just status codes: snapshot
`select deadlocks from pg_stat_database where datname='<db>'` before and after, and grep the API log for
`unhandled_exception`. Also assert one receipt per remittance (`count = count(distinct external_reference)`)
and `sum(debit) == sum(credit)` over `ledger_entries`.

## Migrations

Test up/down/up on a scratch database and assert the actual schema, e.g.
`select pg_get_constraintdef(oid) from pg_constraint where conname='ck_payment_receipts_status_valid'`
(note the `ck_<table>_` prefix — `conname='status_valid'` matches nothing). Guarded downgrades are expected
to fail: run them against a database that holds the guarded rows and assert the alembic exit code is
non-zero, the message, and that the revision + rows are unchanged.

## Gates

`make lint`, `make type`, `make security`, `make audit-deps`, `make test`, `bash scripts/run_assurance.sh`
(writes `evidence/*.log`). `make test` creates its own `taxstamp_test` database and uses Redis DB 9, so it
is safe to run while your manual environment is up.

## Devin Secrets Needed

None — all secrets are locally generated test values; no external accounts are involved.
