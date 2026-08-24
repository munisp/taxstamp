# Identity and delegated authorisation

Two artifacts live here. Both are development wiring, verified to load; neither is
evidence that a production identity programme exists.

## `keycloak-realm.json`

A realm the platform can validate tokens *from*. The platform never issues tokens, and a
realm role in a token grants nothing on its own: access comes from a `principals` row
whose `oidc_subject` an administrator has deliberately linked. That is why the realm
ships no users.

Properties the realm asserts, each covered by a test in
`tests/unit/test_deployment_artifacts.py`:

* self-registration disabled, brute-force protection on, TLS required for external
  clients;
* the browser client `taxstamp-portal` is public and uses authorisation code with PKCE
  (`S256`) only - no implicit flow, no direct access grants, no service account, no
  client secret in a browser;
* a five-minute access token, a 30-minute idle session and an eight-hour maximum
  session, so a stolen token has a short life and a forgotten session ends;
* a TOTP policy, because supervisory roles cannot hold a federated session without a
  multi-factor assertion (`TAXSTAMP_OIDC_MFA_REQUIRED_ROLES`).

The file contains no comments: Keycloak's realm importer rejects unknown fields, so an
annotated realm fails to import at startup. Document intent here instead.

Import it in development with the `edge` Compose profile, then point the API at it:

```
TAXSTAMP_OIDC_ISSUER=http://localhost:8081/realms/taxstamp
TAXSTAMP_OIDC_AUDIENCE=taxstamp-api
```

Production needs its own realm with real clients, real secrets, an enforced MFA policy
and an operated lifecycle for joiners, movers and leavers. Linking a provider subject to
a privileged principal is an administrative act with an audit trail, never automatic.

## `permify-schema.perm`

Delegated reads across a tenant boundary - an auditor engaged by one manufacturer, or
counsel granted sight of one case - are relationships, not roles, and change too often to
live in a deployment. The local role table (`src/taxstamp/authz/actions.py`) remains the
policy of record for every write, and this schema can never widen it: the engine is
consulted only after a local check has already permitted the action.

Load the schema against a running engine:

```
curl -s -X POST "http://localhost:3476/v1/tenants/t1/schemas/write" \
  -H 'content-type: application/json' \
  --data "$(python3 -c 'import json,sys;print(json.dumps({"schema":open("deploy/identity/permify-schema.perm").read()}))')"
```

Run the engine in `shadow` mode first (`TAXSTAMP_AUTHZ_EXTERNAL_MODE=shadow`) and watch
`taxstamp_authz_shadow_disagreements_total`. `enforcing` refuses any request the engine
cannot answer, so it must not be selected before the engine is highly available.
