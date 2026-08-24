"""Federated sessions end to end: a real provider token against the real application.

The contract under test is that the identity provider establishes *who* is calling and
nothing else. The platform's own principal record supplies the role, the tenant and the
audit identity, and a locally disabled principal loses access immediately even though the
provider keeps issuing perfectly valid tokens for them.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.api.app import create_app
from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.enums import Role
from taxstamp.runtime import build_runtime
from tests.support.factories import create_company, create_identity
from tests.support.identity_server import IdentitySandbox

pytestmark = pytest.mark.integration

AUDIENCE = "taxstamp-api"
AUDIT_CHAIN = "/v1/ops/audit-chain"


@pytest.fixture
def provider() -> Iterator[IdentitySandbox]:
    sandbox = IdentitySandbox()
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()


@pytest.fixture
def federated_client(
    settings: Settings,
    clock: FixedClock,
    provider: IdentitySandbox,
) -> Iterator[TestClient]:
    federated = settings.model_copy(
        update={
            "oidc_issuer": provider.issuer,
            "oidc_audience": AUDIENCE,
            "oidc_jwks_url": provider.jwks_url,
        }
    )
    runtime = build_runtime(federated, clock=clock)
    try:
        with TestClient(
            create_app(runtime.settings, runtime=runtime), raise_server_exceptions=False
        ) as client:
            yield client
    finally:
        runtime.close()


def _token(provider: IdentitySandbox, subject: str, *, amr: list[str] | None = None) -> str:
    now = dt.datetime.now(dt.UTC)
    return provider.token(
        subject=subject,
        audience=AUDIENCE,
        issued_at=now - dt.timedelta(minutes=1),
        expires_at=now + dt.timedelta(minutes=5),
        amr=amr,
    )


def _link(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    role: Role,
    oidc_subject: str,
    active: bool = True,
    with_company: bool = False,
) -> None:
    with session_factory() as session:
        company = create_company(session) if with_company else None
        create_identity(
            session,
            role=role,
            api_token_secret=settings.api_token_secret,
            company_id=None if company is None else company.id,
            oidc_subject=oidc_subject,
            active=active,
        )
        session.commit()


def test_linked_multi_factor_session_is_accepted(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: IdentitySandbox,
) -> None:
    _link(session_factory, settings, role=Role.ADMIN, oidc_subject="keycloak-admin-1")
    response = federated_client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'keycloak-admin-1', amr=['pwd', 'otp'])}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["intact"] is True


def test_unlinked_subject_is_refused(federated_client: TestClient, provider: IdentitySandbox) -> None:
    """A valid provider token for someone with no principal must not mint access.

    This is the auto-provisioning hazard: anyone who can register at the identity
    provider would otherwise obtain a platform session.
    """
    response = federated_client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'stranger', amr=['pwd', 'otp'])}"},
    )
    assert response.status_code == 401


def test_locally_disabled_principal_loses_access(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: IdentitySandbox,
) -> None:
    """Local revocation must be sufficient, without waiting on the provider."""
    _link(
        session_factory,
        settings,
        role=Role.ADMIN,
        oidc_subject="keycloak-disabled",
        active=False,
    )
    response = federated_client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'keycloak-disabled', amr=['pwd', 'otp'])}"},
    )
    assert response.status_code == 401


def test_supervisory_role_requires_a_multi_factor_session(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: IdentitySandbox,
) -> None:
    _link(session_factory, settings, role=Role.ADMIN, oidc_subject="keycloak-single-factor")
    response = federated_client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'keycloak-single-factor', amr=['pwd'])}"},
    )
    assert response.status_code == 401


def test_role_comes_from_the_platform_not_the_provider(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: IdentitySandbox,
) -> None:
    """A principal linked as a requester cannot read audit evidence, token regardless."""
    _link(
        session_factory,
        settings,
        role=Role.REQUESTER,
        oidc_subject="keycloak-requester",
        with_company=True,
    )
    response = federated_client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'keycloak-requester', amr=['pwd', 'otp'])}"},
    )
    assert response.status_code == 403


def test_forged_token_is_refused(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
    provider: IdentitySandbox,
) -> None:
    """A token signed by a key the provider does not publish must not authenticate."""
    _link(session_factory, settings, role=Role.ADMIN, oidc_subject="keycloak-admin-2")
    now = dt.datetime.now(dt.UTC)
    forged = provider.token(
        subject="keycloak-admin-2",
        audience=AUDIENCE,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=5),
        amr=["pwd", "otp"],
        kid="rsa-unpublished",
    )
    response = federated_client.get(AUDIT_CHAIN, headers={"authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_platform_credentials_still_work_when_federation_is_configured(
    federated_client: TestClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    """Devices and services keep their own credentials; federation must not disturb them."""
    with session_factory() as session:
        identity = create_identity(session, role=Role.ADMIN, api_token_secret=settings.api_token_secret)
        session.commit()
    response = federated_client.get(AUDIT_CHAIN, headers={"authorization": f"Bearer {identity.token}"})
    assert response.status_code == 200, response.text


def test_provider_token_is_refused_when_federation_is_not_configured(
    client: TestClient, provider: IdentitySandbox
) -> None:
    """With no issuer configured a provider token is refused, never trusted unverified."""
    response = client.get(
        AUDIT_CHAIN,
        headers={"authorization": f"Bearer {_token(provider, 'keycloak-admin-1', amr=['pwd', 'otp'])}"},
    )
    assert response.status_code == 401
