"""The edge and identity artifacts must stay consistent with the code.

A gateway that no longer protects the route the application exposes, or a policy schema
missing a permission the platform asks about, is a silent failure: everything looks
configured and nothing is enforced. These tests are cheap and catch exactly that drift.

They are not evidence that a production edge exists. That needs a real deployment, real
certificates and an external test, all of which are recorded as outstanding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from taxstamp.authz.policy import company_permissions, programme_permissions

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
APISIX = REPO_ROOT / "deploy" / "edge" / "apisix.yaml"
KEYCLOAK = REPO_ROOT / "deploy" / "identity" / "keycloak-realm.json"
PERMIFY_SCHEMA = REPO_ROOT / "deploy" / "identity" / "permify-schema.perm"
PUBLIC_VERIFY = "/v1/public/verify"


def _apisix() -> dict[str, object]:
    document = yaml.safe_load(APISIX.read_text())
    assert isinstance(document, dict)
    return document


def _routes() -> list[dict[str, object]]:
    routes = _apisix()["routes"]
    assert isinstance(routes, list)
    return [route for route in routes if isinstance(route, dict)]


def test_every_route_caps_the_request_body_and_the_request_rate() -> None:
    """An uncapped route at the edge is an uncapped route in application memory."""
    for route in _routes():
        plugins = route["plugins"]
        assert isinstance(plugins, dict)
        assert "client-control" in plugins, route["id"]
        assert "limit-count" in plugins, route["id"]


def test_the_public_route_is_the_most_tightly_limited() -> None:
    """The unauthenticated route is the one an attacker gets for free."""
    routes = {route["id"]: route for route in _routes()}
    public = routes["public-verify"]
    default = routes["api"]
    public_plugins = public["plugins"]
    default_plugins = default["plugins"]
    assert isinstance(public_plugins, dict)
    assert isinstance(default_plugins, dict)
    assert public["uri"] == PUBLIC_VERIFY
    assert (
        public_plugins["client-control"]["max_body_size"]
        < (  # type: ignore[index]
            default_plugins["client-control"]["max_body_size"]  # type: ignore[index]
        )
    )
    assert public_plugins["limit-count"]["count"] < default_plugins["limit-count"]["count"]  # type: ignore[index]
    assert "limit-req" in public_plugins, "the public route needs a burst limit, not only a quota"


def test_the_upstream_never_retries_a_request() -> None:
    """Idempotency is keyed, not URL-derived, so a blind edge retry can duplicate work."""
    upstreams = _apisix()["upstreams"]
    assert isinstance(upstreams, list)
    for upstream in upstreams:
        assert isinstance(upstream, dict)
        assert upstream["retries"] == 0


def test_the_edge_health_checks_readiness_not_liveness() -> None:
    """Routing to a process that cannot reach its database is a self-inflicted outage."""
    upstreams = _apisix()["upstreams"]
    assert isinstance(upstreams, list)
    checks = upstreams[0]["checks"]["active"]  # type: ignore[index]
    assert checks["http_path"] == "/readyz"


def test_the_realm_does_not_let_anyone_self_register_or_skip_the_browser_flow() -> None:
    realm = json.loads(KEYCLOAK.read_text())
    assert realm["registrationAllowed"] is False
    assert realm["bruteForceProtected"] is True
    assert realm["sslRequired"] in {"external", "all"}
    clients = {client["clientId"]: client for client in realm["clients"]}
    portal = clients["taxstamp-portal"]
    # Authorisation code with PKCE only: no implicit flow, no password grant, and no
    # client secret in a browser.
    assert portal["publicClient"] is True
    assert portal["standardFlowEnabled"] is True
    assert portal["implicitFlowEnabled"] is False
    assert portal["directAccessGrantsEnabled"] is False
    assert portal["serviceAccountsEnabled"] is False
    assert portal["attributes"]["pkce.code.challenge.method"] == "S256"


def test_the_realm_ships_no_users() -> None:
    """Principals are provisioned in the platform and linked deliberately, never seeded."""
    realm = json.loads(KEYCLOAK.read_text())
    assert realm.get("users", []) == []


def test_every_permission_the_platform_asks_for_exists_in_the_schema() -> None:
    """A question the schema cannot answer is an "unknown", which denies when enforcing."""
    schema = PERMIFY_SCHEMA.read_text()
    for entity, permissions in (
        ("company", set(company_permissions().values())),
        ("programme", set(programme_permissions().values())),
    ):
        body = _entity_body(schema, entity)
        declared = set(re.findall(r"permission\s+(\w+)\s*=", body))
        assert permissions <= declared, f"{entity} lacks {sorted(permissions - declared)}"


def test_delegated_reads_are_modelled_per_company_and_per_case() -> None:
    """Delegation must be revocable at the grain it was granted."""
    schema = PERMIFY_SCHEMA.read_text()
    for entity in ("company", "case"):
        assert "relation delegated_reader @user" in _entity_body(schema, entity)


def _entity_body(schema: str, entity: str) -> str:
    match = re.search(rf"entity {entity} \{{(.*?)\n\}}", schema, re.DOTALL)
    assert match is not None, f"schema declares no {entity} entity"
    return match.group(1)
