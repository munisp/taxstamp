# APISIX OIDC Gateway Policy

This Go package emits a safe, declarative APISIX OIDC route fragment. It validates HTTPS discovery, requires a client identifier and route URI, then emits bearer-only validation with TLS verification, JWKS verification, PKCE support, denied unauthenticated requests, and explicit scopes.

It intentionally does not call the APISIX Admin API or carry client secrets. Delivery requires an approved secret manager, declarative route promotion, Keycloak realm and client configuration, gateway integration tests, mTLS/TLS policy, and change control.
