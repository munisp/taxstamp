# Local and Persistent Stack Deployment Notes

The disposable validation stack must remain isolated from production credentials and must not claim production availability. The persistent packaging will use the same service boundaries but moves all sensitive values to an ignored secret file or a managed secret store.

| Component | Deployment finding | Stack decision |
|---|---|---|
| Keycloak | Official container guidance supports PostgreSQL backing, health and metrics endpoints, and a production-mode startup path with HTTPS. [1] | Use development mode with throwaway credentials locally; use PostgreSQL, explicit hostname, health/metrics, and injected secrets for the persistent package. |
| APISIX | Official documentation describes traditional, decoupled, and standalone deployment modes. [2] | Use standalone declarative configuration for disposable validation; use a controlled configuration path in persistent non-production, never expose the admin plane publicly. |
| Permify | Official Docker guidance uses `ghcr.io/permify/permify`, with HTTP on 3476, gRPC on 3478, an in-memory default, and `/healthz` for health validation. [3] | Use the documented in-memory default only for disposable validation; bind Permify to PostgreSQL with persisted authorization data in the persistent package. |
| Gateway identity | APISIX has a supported OIDC plugin path for Keycloak integration. [3] | Validate OIDC discovery and bearer-only route behavior before routing Taxstamp traffic through the gateway. |
| Financial ledger | TigerBeetle is an OLTP transaction data plane that works beside—not instead of—a general-purpose database. [4] | Keep PostgreSQL as business metadata authority and reconcile any TigerBeetle transfer state explicitly. |

## References

[1]: https://www.keycloak.org/server/containers "Running Keycloak in a container"
[2]: https://apisix.apache.org/docs/apisix/deployment-modes/ "Apache APISIX deployment modes"
[3]: https://fusionauth.io/permify-docs/setting-up/installation/container "Permify Docker deployment"
[4]: https://apisix.apache.org/docs/apisix/plugins/openid-connect/ "APISIX OpenID Connect plugin"
[5]: https://docs.tigerbeetle.com/coding/system-architecture/ "TigerBeetle in Your System Architecture"
