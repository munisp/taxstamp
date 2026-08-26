# Non-Production Security, Eventing, and Observability Stack

The local overlay is a **disposable validation environment**. Start it from the repository root with:

```bash
docker compose -f docker-compose.yml -f deploy/nonprod/docker-compose.local.yml up --build -d
./deploy/nonprod/validate-local.sh
```

It starts development-only Keycloak, APISIX, Permify, Kafka, Prometheus, and Grafana alongside the Taxstamp API, worker, PostgreSQL, and Redis services. The local Keycloak realm contains a public PKCE client for `http://localhost:3000`; it is not a production configuration. The Kafka broker is plaintext only because it is limited to a disposable internal Compose network.

For the persistent non-production package, copy `.env.nonprod.example` to `.env.nonprod`, set permissions to `0600`, inject all values from a secret manager, then combine `docker-compose.yml` with `docker-compose.persistent.yml`. Before exposing any service, the deployment owner must provide TLS certificates, private DNS, a persistent Redis configuration, a backed-up database plan, Kafka SASL/TLS material, Keycloak realm/client exports, a Permify database and authorization model, and a dedicated audited identity for Taxstamp metrics scraping.

## Storage encryption and KMS/HSM gate

Neither Docker Compose named volumes nor the `rediss://` scheme proves encryption at rest. The disposable overlay is intentionally **not** an encryption-at-rest compliant deployment: its PostgreSQL named volume is local host storage, Redis persistence is disabled, and it contains no OpenSearch service. Do not promote that profile or treat its health checks as encryption evidence.

For persistent non-production and production, the database, Redis persistence/backup service, OpenSearch domain, replicas, snapshots, exports and observability stores must use an approved encrypted managed service or host-encrypted volume. Copy `storage-encryption-attestation.example.json` to an access-controlled non-secret evidence location, replace every placeholder with an approved KMS/HSM reference and evidence URI, and execute:

```bash
./.venv/bin/python scripts/check_storage_encryption.py \
  --env-file deploy/nonprod/.env.nonprod \
  --attestation /secure/evidence/taxstamp-storage-encryption.json \
  --output evidence/storage-encryption.json
```

The command exits successfully only for a complete, internally consistent attestation. Its success is intentionally labelled `evidence_attested_not_live_verified`; it does not contact the provider and therefore cannot prove a disk, snapshot, backup, KMS policy or HSM configuration. Attach provider configuration exports, a restore test, a key-rotation record and an independent access review to the referenced change evidence before release.

Neither overlay provisions Mojaloop credentials or a real settlement sandbox. Mojaloop onboarding, client certificates, scheme approval, and participant endpoints are external prerequisites. TigerBeetle is likewise intentionally excluded from the default local stack because its cluster and account mapping require a separate reconciliation acceptance plan.
