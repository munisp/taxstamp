"""The encryption checker fails closed without complete, non-placeholder evidence."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_checker() -> Any:
    path = Path(__file__).parents[2] / "scripts" / "check_storage_encryption.py"
    spec = importlib.util.spec_from_file_location("storage_encryption_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _complete_env() -> dict[str, str]:
    return {
        "TAXSTAMP_ENV": "production",
        "TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED": "true",
        "TAXSTAMP_KMS_PROVIDER": "aws_kms",
        "TAXSTAMP_KMS_KEY_REFERENCE": (
            "arn:aws:kms:af-south-1:123456789012:key/" "01234567-89ab-cdef-0123-456789abcdef"
        ),
        "TAXSTAMP_KMS_HSM_BACKED": "true",
        "TAXSTAMP_KMS_KEY_ROTATION_DAYS": "365",
        "TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI": "https://evidence.taxstamp.ng/changes/CHG-123",
    }


def _complete_attestation() -> dict[str, object]:
    key = _complete_env()["TAXSTAMP_KMS_KEY_REFERENCE"]
    return {
        "key_management": {
            "provider": "aws_kms",
            "key_reference": key,
            "hsm_backed": True,
            "rotation_days": 365,
            "rotation_evidence_uri": "https://evidence.taxstamp.ng/changes/CHG-124",
            "access_review_evidence_uri": "https://evidence.taxstamp.ng/changes/CHG-125",
            "recovery_exercise_evidence_uri": "https://evidence.taxstamp.ng/changes/CHG-126",
        },
        "assets": {
            name: {
                "encryption_at_rest": True,
                "backup_encryption": True,
                "storage_scope": "managed-encrypted-service",
                "key_reference": key,
                "evidence_uri": f"https://evidence.taxstamp.ng/changes/{name}",
                "backup_restore_evidence_uri": f"https://evidence.taxstamp.ng/changes/{name}-restore",
                "retention_deletion_evidence_uri": f"https://evidence.taxstamp.ng/changes/{name}-retention",
                "verified_at": "2026-08-25T12:00:00Z",
            }
            for name in ("postgres", "redis", "opensearch")
        },
    }


def test_complete_storage_encryption_evidence_is_attested_not_live_verified() -> None:
    checker = _load_checker()
    result = checker.assess(_complete_env(), _complete_attestation(), ("postgres", "redis", "opensearch"))
    assert result["overall_status"] == "evidence_attested_not_live_verified"
    assert result["live_provider_verified"] is False


def test_missing_open_search_evidence_fails_closed() -> None:
    checker = _load_checker()
    attestation = _complete_attestation()
    assets = attestation["assets"]
    assert isinstance(assets, dict)
    assets.pop("opensearch")
    result = checker.assess(_complete_env(), attestation, ("postgres", "redis", "opensearch"))
    assert result["overall_status"] == "not_verified"
    assert result["assets"][2]["status"] == "not_verified"


def test_strict_production_reports_each_missing_attestation_field() -> None:
    checker = _load_checker()
    result = checker.assess(
        {"TAXSTAMP_ENV": "production"},
        {"schema": checker.ATTESTATION_SCHEMA},
        ("postgres", "redis", "opensearch"),
        strict_production=True,
    )
    fields = {item["field"] for item in result["strict_production_findings"]}
    assert result["mode"] == "strict_production"
    assert "TAXSTAMP_KMS_KEY_ROTATION_DAYS" in fields
    assert "key_management" in fields
    assert result["assets"][0]["strict_production_findings"][0]["field"] == "assets.postgres"


def test_complete_evidence_passes_strict_production_field_validation() -> None:
    checker = _load_checker()
    result = checker.assess(
        _complete_env(),
        {"schema": checker.ATTESTATION_SCHEMA, **_complete_attestation()},
        ("postgres", "redis", "opensearch"),
        strict_production=True,
    )
    assert result["overall_status"] == "evidence_attested_not_live_verified"


def test_strict_production_rejects_access_review_uri_with_query_string() -> None:
    checker = _load_checker()
    attestation = _complete_attestation()
    key_management = attestation["key_management"]
    assert isinstance(key_management, dict)
    key_management["access_review_evidence_uri"] = (
        "https://evidence.taxstamp.ng/changes/CHG-125?ticket=CHG-125"
    )

    result = checker.assess(
        _complete_env(),
        {"schema": checker.ATTESTATION_SCHEMA, **attestation},
        ("postgres", "redis", "opensearch"),
        strict_production=True,
    )

    fields = {item["field"] for item in result["strict_production_findings"]}
    assert result["overall_status"] == "not_verified"
    assert "key_management.access_review_evidence_uri" in fields


def test_evidence_uri_accepts_valid_https_port_and_rejects_unsafe_authority_forms() -> None:
    checker = _load_checker()

    assert checker.is_evidence_uri("https://evidence.taxstamp.ng:8443/changes/CHG-125/")
    assert not checker.is_evidence_uri("https://evidence.taxstamp.ng/")
    assert not checker.is_evidence_uri("https://evidence.taxstamp.ng:0/changes/CHG-125")
    assert not checker.is_evidence_uri("https://evidence.taxstamp.ng:65536/changes/CHG-125")
    assert not checker.is_evidence_uri("https://evidence.taxstamp.ng:not-a-port/changes/CHG-125")
    assert not checker.is_evidence_uri("https://token@evidence.taxstamp.ng/changes/CHG-125")
