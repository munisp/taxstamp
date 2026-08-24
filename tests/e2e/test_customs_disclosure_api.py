"""Import regimes, regulator disclosure, transparency and retention over HTTP."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.merkle import ProofStep, verify_inclusion_proof
from taxstamp.runtime import Runtime
from tests.support.api import auth, new_key
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e

QUANTITY = 6
LAGOS = (6_524_379, 3_379_206)


@pytest.fixture
def serials(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> list[str]:
    return issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=QUANTITY,
    )


@pytest.fixture
def port(client: TestClient, tenant: Tenant) -> str:
    response = client.post(
        "/v1/facilities",
        json={
            "facility_code": "FAC-PORT",
            "name": "Apapa port",
            "kind": "port",
            "country": "NG",
            "state": "Lagos",
            "address": "Apapa Wharf Road, Apapa, Lagos",
            "latitude_e7": LAGOS[0],
            "longitude_e7": LAGOS[1],
            "company_id": str(tenant.company.id),
        },
        headers=auth(tenant.admin.token, new_key("facility")),
    )
    assert response.status_code == 201, response.text
    return "FAC-PORT"


def _declare(
    client: TestClient,
    tenant: Tenant,
    *,
    ref: str,
    regime: str,
    quantity: int,
) -> dict[str, object]:
    response = client.post(
        "/v1/consignments",
        json={
            "consignment_ref": ref,
            "company_id": str(tenant.company.id),
            "regime": regime,
            "product_id": str(tenant.product.id),
            "declared_quantity": quantity,
            "customs_declaration_reference": f"SAD-{ref}",
            "origin_country": "GH",
            "entry_facility_code": "FAC-PORT",
        },
        headers=auth(tenant.requester.token, new_key("declare")),
    )
    assert response.status_code == 201, response.text
    document: dict[str, object] = response.json()
    return document


def test_an_import_consignment_is_released_only_once_its_stamps_are_linked(
    client: TestClient, tenant: Tenant, serials: list[str], port: str
) -> None:
    _declare(client, tenant, ref="CNS-0001", regime="import_duty_paid", quantity=QUANTITY)

    premature = client.post(
        "/v1/consignments/CNS-0001/release",
        json={"customs_evidence_reference": "CUS-REL-1"},
        headers=auth(tenant.supervisor.token, new_key("release")),
    )
    assert premature.status_code == 409, premature.text

    linked = client.post(
        "/v1/consignments/CNS-0001/stamps",
        json={"serials": serials[: QUANTITY - 1]},
        headers=auth(tenant.requester.token, new_key("link")),
    )
    assert linked.status_code == 200, linked.text

    short = client.post(
        "/v1/consignments/CNS-0001/release",
        json={"customs_evidence_reference": "CUS-REL-1"},
        headers=auth(tenant.supervisor.token, new_key("release")),
    )
    assert short.status_code == 409, short.text

    remainder = client.post(
        "/v1/consignments/CNS-0001/stamps",
        json={"serials": serials[QUANTITY - 1 :]},
        headers=auth(tenant.requester.token, new_key("link")),
    )
    assert remainder.status_code == 200, remainder.text

    released = client.post(
        "/v1/consignments/CNS-0001/release",
        json={"customs_evidence_reference": "CUS-REL-1"},
        headers=auth(tenant.supervisor.token, new_key("release")),
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released"

    # A requester may not release its own goods into the domestic market.
    again = client.post(
        "/v1/consignments/CNS-0002/release",
        json={"customs_evidence_reference": "CUS-REL-2"},
        headers=auth(tenant.requester.token, new_key("release")),
    )
    assert again.status_code == 403, again.text


@pytest.mark.parametrize("regime", ["free_zone", "transit", "duty_free"])
def test_goods_outside_the_duty_paid_regime_cannot_be_released_domestically(
    client: TestClient, tenant: Tenant, port: str, regime: str
) -> None:
    _declare(client, tenant, ref=f"CNS-{regime}", regime=regime, quantity=10)
    response = client.post(
        f"/v1/consignments/CNS-{regime}/release",
        json={"customs_evidence_reference": "CUS-REL-9"},
        headers=auth(tenant.supervisor.token, new_key("release")),
    )
    assert response.status_code == 409, response.text
    assert "regime" in response.json()["error"]["detail"]


def test_a_stamp_cannot_cover_two_consignments(
    client: TestClient, tenant: Tenant, serials: list[str], port: str
) -> None:
    _declare(client, tenant, ref="CNS-0010", regime="import_duty_paid", quantity=3)
    _declare(client, tenant, ref="CNS-0011", regime="import_duty_paid", quantity=3)
    first = client.post(
        "/v1/consignments/CNS-0010/stamps",
        json={"serials": serials[:3]},
        headers=auth(tenant.requester.token, new_key("link")),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/v1/consignments/CNS-0011/stamps",
        json={"serials": serials[:3]},
        headers=auth(tenant.requester.token, new_key("link")),
    )
    assert second.status_code == 409, second.text


def test_a_regulator_export_is_hashed_signed_and_never_claims_delivery(
    client: TestClient, tenant: Tenant, serials: list[str], port: str
) -> None:
    _declare(client, tenant, ref="CNS-0020", regime="import_duty_paid", quantity=3)
    response = client.post(
        "/v1/exports/regulator",
        json={"export_ref": "EXP-REG-0001", "company_id": str(tenant.company.id)},
        headers=auth(tenant.supervisor.token, new_key("export")),
    )
    assert response.status_code == 201, response.text
    document = response.json()
    assert len(document["content_hash"]) == 64
    assert document["signature"]
    assert document["delivery"]["delivered"] is False
    assert document["delivery"]["reason"] == "no regulator repository endpoint is configured"
    assert document["payload"]["epcisBody"] is not None

    duplicate = client.post(
        "/v1/exports/regulator",
        json={"export_ref": "EXP-REG-0001", "company_id": str(tenant.company.id)},
        headers=auth(tenant.supervisor.token, new_key("export")),
    )
    assert duplicate.status_code == 409, duplicate.text


def test_a_portability_export_is_scoped_to_the_requesting_company(
    client: TestClient, tenant: Tenant, serials: list[str]
) -> None:
    response = client.post(
        "/v1/exports/portability",
        json={"export_ref": "EXP-PORT-0001", "company_id": str(tenant.company.id)},
        headers=auth(tenant.requester.token, new_key("export")),
    )
    assert response.status_code == 201, response.text
    document = response.json()["payload"]
    assert document["company"]["id"] == str(tenant.company.id)
    assert document["licences"]
    assert document["batches"]

    forbidden = client.post(
        "/v1/exports/portability",
        json={"export_ref": "EXP-PORT-0002", "company_id": "00000000-0000-0000-0000-0000000000ff"},
        headers=auth(tenant.requester.token, new_key("export")),
    )
    assert forbidden.status_code == 403, forbidden.text


def test_a_transparency_checkpoint_proves_inclusion_without_claiming_an_anchor(
    client: TestClient, tenant: Tenant, serials: list[str]
) -> None:
    created = client.post(
        "/v1/transparency/checkpoints",
        headers=auth(tenant.admin.token, new_key("checkpoint")),
    )
    assert created.status_code == 201, created.text
    checkpoint = created.json()
    assert checkpoint["tree_size"] > 0
    assert checkpoint["external_anchor"]["anchored"] is False

    # The next checkpoint chains onto this one rather than restating it.
    successor = client.post(
        "/v1/transparency/checkpoints",
        headers=auth(tenant.admin.token, new_key("checkpoint")),
    )
    assert successor.status_code == 201, successor.text
    assert successor.json()["prev_root_hash"] == checkpoint["root_hash"]
    assert successor.json()["tree_size"] > checkpoint["tree_size"]

    reference = checkpoint["checkpoint_ref"]
    proof = client.get(
        f"/v1/transparency/checkpoints/{reference}/proof",
        params={"audit_seq": 1},
        headers=auth(tenant.analyst.token),
    )
    assert proof.status_code == 200, proof.text
    body = proof.json()
    assert body["verified"] is True
    assert body["root_hash"] == checkpoint["root_hash"]
    # The proof must also verify for a third party holding only the published root.
    steps = tuple(ProofStep(position=step["position"], hash_hex=step["hash"]) for step in body["path"])
    assert verify_inclusion_proof(leaf=body["leaf_hash"], proof=steps, root=checkpoint["root_hash"])

    latest = client.get("/v1/transparency/checkpoints/latest", headers=auth(tenant.analyst.token))
    assert latest.status_code == 200, latest.text
    assert latest.json()["checkpoint_ref"] == successor.json()["checkpoint_ref"]


def test_the_retention_policy_is_published_with_its_anchoring_state(
    client: TestClient, settings: Settings, tenant: Tenant
) -> None:
    response = client.get("/v1/retention-policy", headers=auth(tenant.requester.token))
    assert response.status_code == 200, response.text
    document = response.json()
    kinds = {entry["name"] for entry in document["classes"]}
    assert {"financial", "audit", "fiscal_marks", "traceability", "customs"} <= kinds
    assert document["erasure_supported"] is False
    assert document["expiry_behaviour"] == "archive_only"
    assert document["legal_hold"]["supported"] is True
    assert document["external_anchoring_configured"] is bool(settings.ledger_anchor_base_url)


def test_reconciliation_stays_clean_after_traceability_and_disclosure_activity(
    client: TestClient, tenant: Tenant, serials: list[str], port: str
) -> None:
    _declare(client, tenant, ref="CNS-0030", regime="import_duty_paid", quantity=QUANTITY)
    client.post(
        "/v1/consignments/CNS-0030/stamps",
        json={"serials": serials},
        headers=auth(tenant.requester.token, new_key("link")),
    )
    client.post(
        "/v1/exports/regulator",
        json={"export_ref": "EXP-REG-0030", "company_id": str(tenant.company.id)},
        headers=auth(tenant.supervisor.token, new_key("export")),
    )
    client.post("/v1/transparency/checkpoints", headers=auth(tenant.admin.token, new_key("checkpoint")))
    report = client.post("/v1/ops/reconciliation", headers=auth(tenant.admin.token))
    assert report.status_code == 200, report.text
    assert report.json()["clean"] is True, report.json()
