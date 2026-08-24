# taxstamp

Excise tax-stamp issuance, settlement and field verification: licensing and product master
data, order intake, maker-checker approval, exact-amount payment matching, double-entry
posting, resumable serial issuance, acceptance sampling, activation, signed field
verification and stamp accountability, with an append-only audit chain and reconciliation.

Procurement is licence-controlled: a company needs an effective manufacturer or importer
licence covering the ordered product category before it can order stamps
(`POST /v1/licences`, `POST /v1/products`). Held funds are resolved by treasury
(`/v1/treasury/unapplied-receipts`), and spoiled, damaged, destroyed or returned stamps are
declared per serial (`POST /v1/batches/{id}/dispositions`).

Issued marks are then traceable through the supply chain: stamps are aggregated into cases,
pallets and containers (`POST /v1/units`), movements are recorded against the outermost unit
(`POST /v1/trace-events`), imported and duty-suspended goods are declared as consignments
(`POST /v1/consignments`) and released only when their stamps account for the declared
quantity. Contradictions between records surface as deterministic findings
(`GET /v1/anomalies`), the audit chain is committed to publicly
(`/v1/transparency/checkpoints`, with inclusion proofs), and disclosure is served by signed
exports (`/v1/exports/regulator`, `/v1/exports/portability`) under a published retention
policy (`GET /v1/retention-policy`).

Downstream of issuance, the public checks a stamp at `POST /v1/public/verify` — unauthenticated,
rate limited, and returning only the outcome, brand, category and intended market. Officers
work findings as cases (`POST /v1/cases`), take goods into custody with a hash-chained
handover log (`POST /v1/cases/{ref}/seizures`, `POST /v1/seizures/{ref}/custody`), and
supervisors read the programme at `GET /v1/reports/kpis`,
`GET /v1/reports/revenue-at-risk` and `GET /v1/reports/risk/{company_id}`. Inspectors
without connectivity carry a signed revocation bundle (`GET /v1/offline/bundles/latest`)
and hand their scans back at `POST /v1/offline/scans`, where every scan is decided again
server-side.

## What is real and what is not

Every externally dependent flow is declared in `GET /v1/capabilities` and in
[docs/FEATURE_CLAIMS.md](docs/FEATURE_CLAIMS.md). Regulatory checks, ledger anchoring and
similar integrations call a configured external service over HTTP; when an integration is
not configured or is unreachable, the request is **rejected** (HTTP 503) rather than
silently treated as a success. There are no simulated approvals, no default-authentic
verification and no hardcoded confidence scores.

Three limits are worth stating plainly. An offline bundle answers one-sidedly: a hit means
"possibly revoked", while a miss proves only that the mark is not on that revocation list —
never that it is genuine. Revenue at risk reports observed exposure from stored records,
itemised by source and not extrapolated to unobserved trade, so it is not an assessed
liability. A prosecution referral is an internal record only; nothing is filed with any
court or prosecuting authority.

The release decision for this revision is recorded in
[docs/ASSURANCE_REPORT.md](docs/ASSURANCE_REPORT.md). It is **BLOCKED** for production:
several mandatory gates depend on evidence that can only be produced with real sandbox
credentials and an authoritative rate/rule source.

## Layout

```
src/taxstamp/            application: config, money, domain services, API, worker, CLI
migrations/              Alembic revisions (append-only triggers, balanced-journal check)
tests/unit               pure logic
tests/integration        real PostgreSQL, Redis and a real HTTP registry sandbox
tests/e2e                the public HTTP interface, end to end
tests/concurrency        parallel-execution invariants (real threads, real row locks)
tests/fault              dependency outages, interrupted issuance, injected corruption
docs/                    assurance report, feature claims, remediation ledger, runbooks
```

## Local setup

```bash
docker compose up -d postgres redis
make install
cp .env.example .env      # then set real local values
make migrate
make run                  # API on :8080
make worker               # outbox relay, expiry, reconciliation
```

## Quality gates

```bash
make lint type security audit-deps test    # all must pass
make evidence                              # writes evidence/*.log
```

Tests require PostgreSQL and Redis; they create their own database (default
`taxstamp_test`) and use Redis database 9. Override with `TAXSTAMP_TEST_PG_PORT`,
`TAXSTAMP_TEST_REDIS_URL` and friends.

## Money

Amounts are integer minor units throughout (`taxstamp.money.Money`). Binary floating
point is rejected at the boundary, and every journal is balanced by a deferred database
constraint, not by application convention alone.

## Operations

- Health: `GET /healthz`, readiness (PostgreSQL + Redis): `GET /readyz`
- Metrics: `GET /metrics` (admin/auditor only)
- Reconciliation: `POST /v1/ops/reconciliation`
- Audit chain verification: `GET /v1/ops/audit-chain`
- Runbooks, rollback and restore: [docs/RUNBOOK.md](docs/RUNBOOK.md)
- Threat model: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)
