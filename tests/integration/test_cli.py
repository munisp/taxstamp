"""The administrative CLI, run as a real process against the real database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.config import Settings
from taxstamp.models import Credential
from tests.support.api import auth, new_key

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(settings: Settings) -> dict[str, str]:
    return {
        **os.environ,
        "TAXSTAMP_ENV": settings.env.value,
        "TAXSTAMP_DATABASE_URL": settings.database_url,
        "TAXSTAMP_REDIS_URL": settings.redis_url,
        "TAXSTAMP_API_TOKEN_SECRET": settings.api_token_secret,
        "TAXSTAMP_DEVICE_HMAC_SECRET": settings.device_hmac_secret,
        "TAXSTAMP_PAYMENT_WEBHOOK_SECRET": settings.payment_webhook_secret,
        "TAXSTAMP_AUDIT_CHAIN_SECRET": settings.audit_chain_secret,
        "TAXSTAMP_EXPORT_SIGNING_SECRET": settings.export_signing_secret,
        "TAXSTAMP_TRANSPARENCY_SIGNING_SECRET": settings.transparency_signing_secret,
        "TAXSTAMP_REQUIRE_TLS": "false",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }


def _run(settings: Settings, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and module, no shell
        [sys.executable, "-m", "taxstamp.cli", *args],
        cwd=REPO_ROOT,
        env=_env(settings),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _field(output: str, name: str) -> str:
    for line in output.splitlines():
        key, _, value = line.partition("=")
        if key == name:
            return value
    raise AssertionError(f"{name} missing from CLI output: {output!r}")


def test_bootstrap_issues_a_working_credential(
    client: TestClient, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    company = _run(
        settings,
        "create-company",
        "--tin",
        "22001234567",
        "--name",
        "CLI Distillers Plc",
        "--kyb-status",
        "verified",
        "--risk-tier",
        "low",
    )
    assert company.returncode == 0, company.stderr
    company_id = _field(company.stdout, "company_id")

    tariff = _run(
        settings,
        "add-tariff",
        "--product-category",
        "alcohol",
        "--unit-price-major",
        "12.50",
        "--vat-bps",
        "750",
        "--effective-from",
        "2020-01-01T00:00:00+00:00",
        "--statutory-reference",
        "Excise Tariff Schedule 1",
    )
    assert tariff.returncode == 0, tariff.stderr

    licence = _run(
        settings,
        "issue-licence",
        "--company-id",
        company_id,
        "--licence-number",
        "LIC-CLI-0001",
        "--licence-type",
        "manufacturer",
        "--product-categories",
        "alcohol,tobacco",
        "--valid-from",
        "2020-01-01T00:00:00+00:00",
        "--statutory-reference",
        "Excise Licence Register 2020/17",
    )
    assert licence.returncode == 0, licence.stderr
    assert _field(licence.stdout, "licence_id")

    principal = _run(
        settings,
        "create-principal",
        "--subject",
        "cli-requester",
        "--role",
        "requester",
        "--display-name",
        "CLI Requester",
        "--company-id",
        company_id,
    )
    assert principal.returncode == 0, principal.stderr
    token = _field(principal.stdout, "token")

    with session_factory() as session:
        stored = session.execute(select(Credential.token_hash)).scalars().all()
    assert token not in stored  # only a keyed hash is persisted

    created = client.post(
        "/v1/orders",
        json={
            "company_id": company_id,
            "product_category": "alcohol",
            "quantity": 10,
            "delivery_state": "Lagos",
            "delivery_address": "12 Marina Road, Lagos Island, Lagos",
        },
        headers=auth(token, new_key("order")),
    )
    assert created.status_code == 201, created.text
    assert created.json()["subtotal_minor"] == 12_500


def test_reconcile_and_audit_chain_report_clean_state(settings: Settings) -> None:
    reconcile = _run(settings, "reconcile")
    assert reconcile.returncode == 0, reconcile.stdout + reconcile.stderr
    chain = _run(settings, "verify-audit-chain")
    assert chain.returncode == 0, chain.stdout + chain.stderr
    assert "'intact': True" in chain.stdout


def test_unknown_command_is_rejected(settings: Settings) -> None:
    result = _run(settings, "delete-everything")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
