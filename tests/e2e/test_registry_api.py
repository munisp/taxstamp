"""Licensing and product master data as procurement controls, over HTTP."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.config import Settings
from taxstamp.enums import LicenceStatus, LicenceType, ProductStatus, Role
from tests.support.api import auth, new_key
from tests.support.factories import (
    create_company,
    create_identity,
    create_licence,
    create_product,
    create_tariff,
)
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e

ORDER_BODY = {
    "quantity": 10,
    "delivery_state": "Lagos",
    "delivery_address": "12 Marina Road, Lagos Island, Lagos",
}


def _order(client: TestClient, tenant: Tenant, **selector: object) -> object:
    return client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY, **selector},
        headers=auth(tenant.requester.token, new_key("order")),
    )


def test_order_against_a_registered_product_records_product_and_licence(
    client: TestClient, tenant: Tenant
) -> None:
    response = _order(client, tenant, product_id=str(tenant.product.id))
    assert response.status_code == 201, response.text
    document = response.json()
    assert document["product_id"] == str(tenant.product.id)
    assert document["licence_id"] == str(tenant.licence.id)
    assert document["product_category"] == tenant.product.product_category


def test_order_requires_exactly_one_product_selector(client: TestClient, tenant: Tenant) -> None:
    both = _order(client, tenant, product_id=str(tenant.product.id), product_category="alcohol")
    neither = _order(client, tenant)
    assert both.status_code == 422, both.text
    assert neither.status_code == 422, neither.text


def test_a_company_without_a_licence_cannot_procure_stamps(
    client: TestClient, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        company = create_company(session)
        requester = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=company.id,
        )
        session.commit()
    response = client.post(
        "/v1/orders",
        json={"company_id": str(company.id), "product_category": "alcohol", **ORDER_BODY},
        headers=auth(requester.token, new_key("order")),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_suspended_and_revoked_licences_stop_new_procurement(client: TestClient, tenant: Tenant) -> None:
    suspend = client.post(
        f"/v1/licences/{tenant.licence.id}/status",
        json={"status": "suspended", "reason": "excise arrears under review"},
        headers=auth(tenant.admin.token, new_key("suspend")),
    )
    assert suspend.status_code == 200, suspend.text
    blocked = _order(client, tenant, product_id=str(tenant.product.id))
    assert blocked.status_code == 403, blocked.text

    reinstate = client.post(
        f"/v1/licences/{tenant.licence.id}/status",
        json={"status": "active", "reason": "arrears settled"},
        headers=auth(tenant.admin.token, new_key("reinstate")),
    )
    assert reinstate.status_code == 200, reinstate.text
    assert _order(client, tenant, product_id=str(tenant.product.id)).status_code == 201

    revoke = client.post(
        f"/v1/licences/{tenant.licence.id}/status",
        json={"status": "revoked", "reason": "licence withdrawn"},
        headers=auth(tenant.admin.token, new_key("revoke")),
    )
    assert revoke.status_code == 200, revoke.text
    assert _order(client, tenant, product_id=str(tenant.product.id)).status_code == 403
    # A revoked licence is terminal: history stays, but it cannot be resurrected.
    again = client.post(
        f"/v1/licences/{tenant.licence.id}/status",
        json={"status": "active", "reason": "attempted reinstatement"},
        headers=auth(tenant.admin.token, new_key("reinstate-2")),
    )
    assert again.status_code == 409, again.text


def test_expired_licence_is_not_effective(
    client: TestClient,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        company = create_company(session)
        create_licence(
            session,
            company_id=company.id,
            valid_from=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            valid_to=dt.datetime(2021, 1, 1, tzinfo=dt.UTC),
        )
        requester = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=company.id,
        )
        session.commit()
    response = client.post(
        "/v1/orders",
        json={"company_id": str(company.id), "product_category": "alcohol", **ORDER_BODY},
        headers=auth(requester.token, new_key("order")),
    )
    assert response.status_code == 403, response.text


def test_distributor_licence_does_not_entitle_procurement(
    client: TestClient, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        company = create_company(session)
        create_licence(session, company_id=company.id, licence_type=LicenceType.DISTRIBUTOR)
        requester = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=company.id,
        )
        session.commit()
    response = client.post(
        "/v1/orders",
        json={"company_id": str(company.id), "product_category": "alcohol", **ORDER_BODY},
        headers=auth(requester.token, new_key("order")),
    )
    assert response.status_code == 403, response.text


def test_licence_does_not_cover_another_category(client: TestClient, tenant: Tenant) -> None:
    response = _order(client, tenant, product_category="tobacco")
    assert response.status_code == 403, response.text


def test_withdrawn_product_cannot_be_ordered_against(client: TestClient, tenant: Tenant) -> None:
    withdrawal = client.post(
        f"/v1/products/{tenant.product.id}/withdrawal",
        headers=auth(tenant.requester.token, new_key("withdraw")),
    )
    assert withdrawal.status_code == 200, withdrawal.text
    assert withdrawal.json()["status"] == ProductStatus.WITHDRAWN.value
    blocked = _order(client, tenant, product_id=str(tenant.product.id))
    assert blocked.status_code == 422, blocked.text
    repeat = client.post(
        f"/v1/products/{tenant.product.id}/withdrawal",
        headers=auth(tenant.requester.token, new_key("withdraw-2")),
    )
    assert repeat.status_code == 409, repeat.text


def test_a_product_of_another_company_cannot_be_ordered_against(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        other = create_company(session)
        foreign = create_product(session, company_id=other.id)
        session.commit()
        foreign_id = foreign.id
    response = _order(client, tenant, product_id=str(foreign_id))
    assert response.status_code == 403, response.text


def test_product_registration_is_scoped_to_the_callers_company(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        other = create_company(session)
        session.commit()
        other_id = other.id
    body = {
        "company_id": str(other_id),
        "sku": "SKU-CROSS-1",
        "brand": "Acme Reserve",
        "product_category": "alcohol",
        "pack_size": 12,
        "unit_of_measure": "bottle",
        "intended_market": "NG",
    }
    response = client.post(
        "/v1/products", json=body, headers=auth(tenant.requester.token, new_key("product"))
    )
    assert response.status_code == 403, response.text

    own = {**body, "company_id": str(tenant.company.id)}
    created = client.post(
        "/v1/products", json=own, headers=auth(tenant.requester.token, new_key("product-2"))
    )
    assert created.status_code == 201, created.text
    duplicate = client.post(
        "/v1/products", json=own, headers=auth(tenant.requester.token, new_key("product-3"))
    )
    assert duplicate.status_code == 409, duplicate.text


def test_licence_issuance_requires_admin_and_rejects_duplicates(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        create_tariff(session, product_category="tobacco")
        session.commit()
    body = {
        "company_id": str(tenant.company.id),
        "licence_number": "LIC-API-0001",
        "licence_type": LicenceType.IMPORTER.value,
        "product_categories": ["tobacco"],
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": None,
        "statutory_reference": "Excise Licence Register 2026/9",
    }
    forbidden = client.post(
        "/v1/licences", json=body, headers=auth(tenant.requester.token, new_key("licence"))
    )
    assert forbidden.status_code == 403, forbidden.text

    created = client.post("/v1/licences", json=body, headers=auth(tenant.admin.token, new_key("licence-2")))
    assert created.status_code == 201, created.text
    assert created.json()["status"] == LicenceStatus.ACTIVE.value

    duplicate = client.post("/v1/licences", json=body, headers=auth(tenant.admin.token, new_key("licence-3")))
    assert duplicate.status_code == 409, duplicate.text

    # The new importer licence covers tobacco, so that category is now procurable.
    assert _order(client, tenant, product_category="tobacco").status_code == 201


def test_licence_with_an_unknown_category_is_rejected(client: TestClient, tenant: Tenant) -> None:
    response = client.post(
        "/v1/licences",
        json={
            "company_id": str(tenant.company.id),
            "licence_number": "LIC-API-0002",
            "licence_type": LicenceType.MANUFACTURER.value,
            "product_categories": ["unobtainium"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_to": None,
            "statutory_reference": "Excise Licence Register 2026/10",
        },
        headers=auth(tenant.admin.token, new_key("licence-bad")),
    )
    assert response.status_code == 422, response.text


def test_requesters_only_see_their_own_licences_and_products(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        other = create_company(session)
        create_licence(session, company_id=other.id)
        create_product(session, company_id=other.id)
        session.commit()

    licences = client.get("/v1/licences", headers=auth(tenant.requester.token)).json()["licences"]
    products = client.get("/v1/products", headers=auth(tenant.requester.token)).json()["products"]
    assert {row["company_id"] for row in licences} == {str(tenant.company.id)}
    assert {row["company_id"] for row in products} == {str(tenant.company.id)}

    all_licences = client.get("/v1/licences", headers=auth(tenant.admin.token)).json()["licences"]
    assert len(all_licences) > len(licences)


def test_a_device_credential_cannot_enumerate_the_register(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    """A field credential has no legitimate reason to read brands or entitlements."""
    with session_factory() as session:
        other = create_company(session)
        create_licence(session, company_id=other.id)
        create_product(session, company_id=other.id)
        session.commit()

    assert client.get("/v1/licences", headers=auth(tenant.device.token)).status_code == 403
    assert client.get("/v1/products", headers=auth(tenant.device.token)).status_code == 403


def test_an_operator_scoped_to_a_company_sees_only_its_own_register(
    client: TestClient,
    tenant: Tenant,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        other = create_company(session)
        create_licence(session, company_id=other.id)
        create_product(session, company_id=other.id)
        scoped = create_identity(
            session,
            role=Role.OPERATOR,
            api_token_secret=settings.api_token_secret,
            company_id=tenant.company.id,
        )
        session.commit()

    licences = client.get("/v1/licences", headers=auth(scoped.token)).json()["licences"]
    products = client.get("/v1/products", headers=auth(scoped.token)).json()["products"]
    assert {row["company_id"] for row in licences} == {str(tenant.company.id)}
    assert {row["company_id"] for row in products} == {str(tenant.company.id)}
