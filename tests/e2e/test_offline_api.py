"""Offline bundles are signed and one-sided; synchronised scans are re-decided here."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.bloom import BloomFilter
from taxstamp.clock import FixedClock
from taxstamp.config import Settings
from taxstamp.models import Verification
from taxstamp.runtime import Runtime
from taxstamp.security import derive_secure_code, sign_document
from tests.support.api import auth, new_key
from tests.support.issuance import issue_stamps
from tests.support.tenant import Tenant

pytestmark = pytest.mark.e2e


@pytest.fixture
def serials(
    client: TestClient,
    runtime: Runtime,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    session_factory: sessionmaker[Session],
) -> list[str]:
    issued = issue_stamps(
        client,
        runtime=runtime,
        settings=settings,
        clock=clock,
        tenant=tenant,
        session_factory=session_factory,
        quantity=4,
    )
    activation = client.post(
        "/v1/stamps/activate",
        json={"serials": issued},
        headers=auth(tenant.operator.token, new_key("activate")),
    )
    assert activation.status_code == 200, activation.text
    return issued


def _publish(client: TestClient, tenant: Tenant) -> dict[str, object]:
    response = client.post("/v1/offline/bundles", headers=auth(tenant.operator.token, new_key("bundle")))
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _scan(serial: str, settings: Settings, clock: FixedClock, *, nonce: str) -> dict[str, object]:
    return {
        "serial": serial,
        "secure_code": derive_secure_code(serial, secret=settings.device_hmac_secret),
        "nonce": nonce,
        "captured_at": (clock.now() - dt.timedelta(hours=1)).isoformat(),
    }


def test_a_bundle_is_signed_and_covers_the_revoked_serials(
    client: TestClient, settings: Settings, tenant: Tenant, serials: list[str]
) -> None:
    voided = client.post(
        "/v1/stamps/void",
        json={"serials": [serials[0]], "reason": "Damaged during application at the line"},
        headers=auth(tenant.operator.token, new_key("void")),
    )
    assert voided.status_code == 200, voided.text
    bundle = _publish(client, tenant)
    payload = bundle["payload"]
    assert isinstance(payload, dict)
    assert payload["revoked_count"] == 1
    assert bundle["signature"] == sign_document(
        payload,
        secret=settings.offline_signing_secret,
        purpose="offline-revocation-bundle",
    )
    filter_ = BloomFilter.decode(
        str(payload["filter_base64"]),
        bits=int(str(payload["filter_bits"])),
        hash_count=int(str(payload["filter_hash_count"])),
    )
    assert filter_.probably_contains(serials[0], secret=settings.offline_filter_secret) is True
    assert filter_.probably_contains(serials[1], secret=settings.offline_filter_secret) is False
    assert "still requires an online verification" in str(bundle["semantics"])


def test_bundle_sequence_increases_and_the_latest_is_served(
    client: TestClient, tenant: Tenant, serials: list[str]
) -> None:
    first = _publish(client, tenant)
    second = _publish(client, tenant)
    first_payload, second_payload = first["payload"], second["payload"]
    assert isinstance(first_payload, dict) and isinstance(second_payload, dict)
    assert int(str(second_payload["sequence"])) == int(str(first_payload["sequence"])) + 1
    latest = client.get("/v1/offline/bundles/latest", headers=auth(tenant.device.token))
    assert latest.status_code == 200, latest.text
    served = latest.json()["payload"]
    assert served["sequence"] == second_payload["sequence"]


def test_no_bundle_yet_is_not_an_invented_empty_one(client: TestClient, tenant: Tenant) -> None:
    response = client.get("/v1/offline/bundles/latest", headers=auth(tenant.device.token))
    assert response.status_code == 404, response.text


def test_a_requester_may_not_publish_a_bundle(client: TestClient, tenant: Tenant) -> None:
    response = client.post("/v1/offline/bundles", headers=auth(tenant.requester.token, new_key("bundle")))
    assert response.status_code == 403, response.text


def test_synchronised_scans_are_decided_by_the_server(
    client: TestClient,
    settings: Settings,
    clock: FixedClock,
    tenant: Tenant,
    serials: list[str],
    session_factory: sessionmaker[Session],
) -> None:
    response = client.post(
        "/v1/offline/scans",
        json={
            "device_id": "SCANNER-01",
            "batch_sequence": 1,
            "scans": [
                _scan(serials[0], settings, clock, nonce="nonce-0001"),
                {**_scan(serials[1], settings, clock, nonce="nonce-0002"), "secure_code": "000000"},
            ],
        },
        headers=auth(tenant.device.token, new_key("sync")),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scan_count"] == 2
    assert body["accepted_count"] == 2
    assert body["duplicate_count"] == 0
    outcomes = {entry["serial"]: entry["outcome"] for entry in body["outcomes"]}
    assert outcomes[serials[0]] == "valid"
    # The device claimed nothing; the server found the second code wrong regardless.
    assert outcomes[serials[1]] == "secure_code_mismatch"
    with session_factory() as session:
        channels = list(session.execute(select(Verification.channel)).scalars().all())
    assert channels == ["offline_device", "offline_device"]


def test_replaying_the_same_batch_changes_nothing(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    payload = {
        "device_id": "SCANNER-01",
        "batch_sequence": 7,
        "scans": [_scan(serials[0], settings, clock, nonce="nonce-0001")],
    }
    first = client.post("/v1/offline/scans", json=payload, headers=auth(tenant.device.token, new_key("s")))
    assert first.status_code == 200, first.text
    second = client.post("/v1/offline/scans", json=payload, headers=auth(tenant.device.token, new_key("s2")))
    assert second.status_code == 200, second.text
    assert second.json()["content_hash"] == first.json()["content_hash"]
    assert second.json()["scan_count"] == 1
    history = client.get("/v1/offline/scans/SCANNER-01", headers=auth(tenant.device.token))
    assert history.status_code == 200, history.text
    assert len(history.json()["batches"]) == 1


def test_reusing_a_batch_number_for_different_scans_is_a_conflict(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    base = {"device_id": "SCANNER-01", "batch_sequence": 3}
    first = client.post(
        "/v1/offline/scans",
        json={**base, "scans": [_scan(serials[0], settings, clock, nonce="nonce-0001")]},
        headers=auth(tenant.device.token, new_key("s")),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/v1/offline/scans",
        json={**base, "scans": [_scan(serials[1], settings, clock, nonce="nonce-0002")]},
        headers=auth(tenant.device.token, new_key("s2")),
    )
    assert second.status_code == 409, second.text


def test_a_nonce_already_used_is_counted_as_a_duplicate(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    first = client.post(
        "/v1/offline/scans",
        json={
            "device_id": "SCANNER-01",
            "batch_sequence": 1,
            "scans": [_scan(serials[0], settings, clock, nonce="nonce-shared")],
        },
        headers=auth(tenant.device.token, new_key("s")),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/v1/offline/scans",
        json={
            "device_id": "SCANNER-01",
            "batch_sequence": 2,
            "scans": [
                _scan(serials[0], settings, clock, nonce="nonce-shared"),
                _scan(serials[1], settings, clock, nonce="nonce-fresh0"),
            ],
        },
        headers=auth(tenant.device.token, new_key("s2")),
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["accepted_count"] == 1
    assert body["duplicate_count"] == 1
    assert body["rejected_nonces"] == ["nonce-shared"]


def test_a_batch_repeating_a_nonce_within_itself_is_rejected(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    response = client.post(
        "/v1/offline/scans",
        json={
            "device_id": "SCANNER-01",
            "batch_sequence": 1,
            "scans": [
                _scan(serials[0], settings, clock, nonce="nonce-same01"),
                _scan(serials[1], settings, clock, nonce="nonce-same01"),
            ],
        },
        headers=auth(tenant.device.token, new_key("s")),
    )
    assert response.status_code == 422, response.text


def test_scans_captured_in_the_future_are_refused(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    scan = _scan(serials[0], settings, clock, nonce="nonce-future")
    scan["captured_at"] = (clock.now() + dt.timedelta(hours=2)).isoformat()
    response = client.post(
        "/v1/offline/scans",
        json={"device_id": "SCANNER-01", "batch_sequence": 1, "scans": [scan]},
        headers=auth(tenant.device.token, new_key("s")),
    )
    assert response.status_code == 422, response.text


def test_scans_older_than_the_window_are_refused(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    scan = _scan(serials[0], settings, clock, nonce="nonce-stale1")
    stale_by = dt.timedelta(hours=settings.offline_sync_max_staleness_hours + 1)
    scan["captured_at"] = (clock.now() - stale_by).isoformat()
    response = client.post(
        "/v1/offline/scans",
        json={"device_id": "SCANNER-01", "batch_sequence": 1, "scans": [scan]},
        headers=auth(tenant.device.token, new_key("s")),
    )
    assert response.status_code == 422, response.text


def test_a_requester_may_not_synchronise_scans(
    client: TestClient, settings: Settings, clock: FixedClock, tenant: Tenant, serials: list[str]
) -> None:
    response = client.post(
        "/v1/offline/scans",
        json={
            "device_id": "SCANNER-01",
            "batch_sequence": 1,
            "scans": [_scan(serials[0], settings, clock, nonce="nonce-0001")],
        },
        headers=auth(tenant.requester.token, new_key("s")),
    )
    assert response.status_code == 403, response.text
