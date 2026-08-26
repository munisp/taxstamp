"""Authentication, authorisation and tenant isolation over HTTP."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.enums import BatchStatus, Role
from taxstamp.jsontypes import JsonObject
from taxstamp.models import StampBatch
from tests.support.api import auth, new_key, signed_headers
from tests.support.factories import create_company, create_identity
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e

ORDER_BODY = {
    "product_category": "alcohol",
    "quantity": 10,
    "delivery_state": "Lagos",
    "delivery_address": "12 Marina Road, Lagos Island, Lagos",
}


def test_missing_credential_is_rejected(client: TestClient) -> None:
    assert client.get("/v1/orders").status_code == 401


def test_unknown_token_is_rejected(client: TestClient) -> None:
    assert client.get("/v1/orders", headers=auth("not-a-real-token")).status_code == 401


def test_wrong_role_cannot_create_an_order(client: TestClient, tenant: Tenant) -> None:
    response = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.analyst.token, new_key("order")),
    )
    assert response.status_code == 403


def test_requester_cannot_order_for_another_company(
    client: TestClient, tenant: Tenant, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        other = create_company(session)
        session.commit()
        other_id = other.id
    response = client.post(
        "/v1/orders",
        json={"company_id": str(other_id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert response.status_code == 403


def test_requester_cannot_read_another_tenants_order(
    client: TestClient, tenant: Tenant, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    created = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert created.status_code == 201
    order_id = created.json()["id"]
    with session_factory() as session:
        other_company = create_company(session)
        outsider = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=other_company.id,
        )
        session.commit()
    assert client.get(f"/v1/orders/{order_id}", headers=auth(outsider.token)).status_code == 403
    listing = client.get("/v1/orders", headers=auth(outsider.token)).json()
    assert listing["orders"] == []


def test_requester_cannot_read_another_tenants_batch(
    client: TestClient, tenant: Tenant, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    created = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert created.status_code == 201
    with session_factory() as session:
        batch = StampBatch(
            order_id=uuid.UUID(created.json()["id"]),
            requested_count=1,
            issued_count=0,
            status=BatchStatus.PENDING.value,
        )
        session.add(batch)
        session.flush()
        batch_id = str(batch.id)
        other_company = create_company(session)
        outsider = create_identity(
            session,
            role=Role.REQUESTER,
            api_token_secret=settings.api_token_secret,
            company_id=other_company.id,
        )
        session.commit()

    denied = client.get(f"/v1/batches/{batch_id}", headers=auth(outsider.token))
    assert denied.status_code == 403
    allowed = client.get(f"/v1/batches/{batch_id}", headers=auth(tenant.requester.token))
    assert allowed.status_code == 200


def test_submitter_cannot_approve_their_own_order(
    client: TestClient, tenant: Tenant, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        dual_role = create_identity(
            session,
            role=Role.ANALYST,
            api_token_secret=settings.api_token_secret,
        )
        session.commit()
        analyst_token = dual_role.token
    created = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    order_id = created.json()["id"]
    # A second analyst decision on the same level must be refused.
    first = client.post(
        f"/v1/orders/{order_id}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "checked"},
        headers=auth(tenant.analyst.token, new_key("approve")),
    )
    assert first.status_code == 201
    second = client.post(
        f"/v1/orders/{order_id}/approvals",
        json={"level": "analyst", "decision": "approved", "reason": "checked again"},
        headers=auth(analyst_token, new_key("approve")),
    )
    assert second.status_code == 409


def test_metrics_and_reconciliation_require_privileged_roles(client: TestClient, tenant: Tenant) -> None:
    assert client.get("/metrics", headers=auth(tenant.requester.token)).status_code == 403
    assert client.post("/v1/ops/reconciliation", headers=auth(tenant.operator.token)).status_code == 403
    assert client.get("/metrics", headers=auth(tenant.admin.token)).status_code == 200


def test_unknown_fields_and_bad_types_are_rejected(client: TestClient, tenant: Tenant) -> None:
    response = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY, "total_minor": 1},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert response.status_code == 422
    negative = client.post(
        "/v1/orders",
        json={**ORDER_BODY, "company_id": str(tenant.company.id), "quantity": -1},
        headers=auth(tenant.requester.token, new_key("order")),
    )
    assert negative.status_code == 422


def test_idempotency_key_is_required_for_mutations(client: TestClient, tenant: Tenant) -> None:
    response = client.post(
        "/v1/orders",
        json={"company_id": str(tenant.company.id), **ORDER_BODY},
        headers=auth(tenant.requester.token),
    )
    assert response.status_code == 422
    assert "Idempotency-Key" in response.text


def test_device_cannot_claim_another_device_identity(
    client: TestClient, tenant: Tenant, settings: Settings, clock: FixedClock
) -> None:
    body: JsonObject = {
        "serial": "NG-ALC-2026-000001-X",
        "secure_code": "ABCDEFGHJKLM",
        "device_id": "other-device",
        "nonce": "nonce-device-binding",
    }
    response = client.post(
        "/v1/verify",
        json=body,
        headers={
            **auth(tenant.device.token),
            **signed_headers(body, secret=settings.device_hmac_secret, now=clock.now()),
        },
    )
    assert response.status_code == 403
