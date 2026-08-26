from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "convert_storage_encryption_yaml.py"
SPEC = importlib.util.spec_from_file_location("storage_attestation_yaml", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def valid_document() -> dict[str, object]:
    key_reference = "arn:aws:kms:af-south-1:111122223333:key/4f3e2d1c-0b9a-4876-8c5d-4e3f2a1b0c9d"
    uri_root = "https://evidence.company.internal/records/CHG-2026-042"
    return {
        "schema": "taxstamp.storage-encryption-attestation.v1",
        "environment": "staging",
        "key_management": {
            "provider": "aws_kms",
            "key_reference": key_reference,
            "hsm_backed": True,
            "rotation_days": 365,
            "rotation_evidence_uri": f"{uri_root}/rotation",
            "access_review_evidence_uri": f"{uri_root}/access-review",
            "recovery_exercise_evidence_uri": f"{uri_root}/recovery-exercise",
        },
        "assets": {
            "postgres": {
                "encryption_at_rest": True,
                "backup_encryption": True,
                "storage_scope": "managed-encrypted-service",
                "key_reference": key_reference,
                "evidence_uri": f"{uri_root}/postgres",
                "backup_restore_evidence_uri": f"{uri_root}/postgres-restore",
                "retention_deletion_evidence_uri": f"{uri_root}/postgres-retention",
                "verified_at": "2026-08-25T18:30:00Z",
            }
        },
    }


def test_valid_document_is_accepted() -> None:
    converted, errors = MODULE.validate_attestation(valid_document())

    assert errors == []
    assert converted is not None
    assert converted["environment"] == "staging"


def test_timestamp_must_be_a_quoted_yaml_string() -> None:
    document = valid_document()
    assets = document["assets"]
    assert isinstance(assets, dict)
    postgres = assets["postgres"]
    assert isinstance(postgres, dict)
    postgres["verified_at"] = 20260825

    _, errors = MODULE.validate_attestation(document)

    assert "assets.postgres.verified_at must be an ISO-8601 UTC string; quote it in YAML." in errors


def test_access_review_uri_rejects_query_string() -> None:
    document = valid_document()
    key_management = document["key_management"]
    assert isinstance(key_management, dict)
    key_management["access_review_evidence_uri"] = (
        "https://evidence.company.internal/access-review?ticket=CHG-42"
    )

    _, errors = MODULE.validate_attestation(document)

    assert "key_management.access_review_evidence_uri must be a valid non-secret evidence URI." in errors


def test_converter_uri_parser_accepts_https_port_and_rejects_invalid_port() -> None:
    assert MODULE.is_evidence_uri("https://evidence.company.internal:8443/records/CHG-42/access-review")
    assert not MODULE.is_evidence_uri(
        "https://evidence.company.internal:invalid/records/CHG-42/access-review"
    )
