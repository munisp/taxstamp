#!/usr/bin/env python3
"""Convert a constrained, non-secret storage-encryption YAML attestation to JSON.

This utility validates a repository-defined evidence schema only. It does not
contact KMS/HSM providers, databases, Redis, OpenSearch, or evidence URIs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

PLACEHOLDER = re.compile(r"change[_-]?me|placeholder|example|replace|secrets?|password", re.IGNORECASE)
SUPPORTED_PROVIDERS = {"aws_kms", "azure_key_vault", "gcp_kms", "pkcs11_hsm"}
ATTESTATION_SCHEMA = "taxstamp.storage-encryption-attestation.v1"
ASSET_NAMES = {"postgres", "redis", "opensearch"}
ASSET_FIELDS = {
    "encryption_at_rest",
    "backup_encryption",
    "storage_scope",
    "key_reference",
    "evidence_uri",
    "backup_restore_evidence_uri",
    "retention_deletion_evidence_uri",
    "verified_at",
}
KEY_MANAGEMENT_FIELDS = {
    "provider",
    "key_reference",
    "hsm_backed",
    "rotation_days",
    "rotation_evidence_uri",
    "access_review_evidence_uri",
    "recovery_exercise_evidence_uri",
}


def is_non_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not bool(PLACEHOLDER.search(value))


def is_evidence_uri(value: object) -> bool:
    valid = is_non_placeholder(value)
    uri = str(value) if isinstance(value, str) else ""
    valid = valid and uri == uri.strip()
    parsed = urlparse(uri)
    valid = valid and parsed.scheme in {"https", "s3", "gs", "az", "file"}
    valid = valid and not parsed.query and not parsed.fragment
    valid = valid and parsed.username is None and parsed.password is None
    try:
        port = parsed.port
    except ValueError:
        port = None
        valid = False
    if parsed.scheme == "file":
        valid = valid and parsed.netloc == "" and parsed.path.startswith("/")
    else:
        valid = valid and not (parsed.scheme != "https" and port is not None)
        valid = valid and not (port is not None and not 1 <= port <= 65535)
        valid = valid and bool(parsed.netloc and parsed.path and parsed.path != "/")
    return valid


def is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def mapping(value: object, field: str, errors: list[str]) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    errors.append(f"{field} must be a mapping.")
    return None


def validate_key_management(value: object, errors: list[str]) -> None:
    record = mapping(value, "key_management", errors)
    if record is None:
        return
    unknown = sorted(set(record) - KEY_MANAGEMENT_FIELDS)
    if unknown:
        errors.append(f"key_management has unknown fields: {', '.join(unknown)}.")
    missing = sorted(KEY_MANAGEMENT_FIELDS - set(record))
    if missing:
        errors.append(f"key_management is missing fields: {', '.join(missing)}.")
        return
    provider = record.get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        errors.append("key_management.provider must be a supported provider.")
    if not is_non_placeholder(record.get("key_reference")):
        errors.append("key_management.key_reference must be non-placeholder text.")
    if record.get("hsm_backed") is not True:
        errors.append("key_management.hsm_backed must be true.")
    rotation_days = record.get("rotation_days")
    if not isinstance(rotation_days, int) or isinstance(rotation_days, bool) or not 1 <= rotation_days <= 730:
        errors.append("key_management.rotation_days must be an integer from 1 to 730.")
    for field in (
        "rotation_evidence_uri",
        "access_review_evidence_uri",
        "recovery_exercise_evidence_uri",
    ):
        if not is_evidence_uri(record.get(field)):
            errors.append(f"key_management.{field} must be a valid non-secret evidence URI.")


def validate_asset_record(asset: str, value: object, key_reference: object, errors: list[str]) -> None:
    """Validate one storage asset with the exact strict evidence-field set."""

    record = mapping(value, f"assets.{asset}", errors)
    if record is None:
        return
    unknown = sorted(set(record) - ASSET_FIELDS)
    if unknown:
        errors.append(f"assets.{asset} has unknown fields: {', '.join(unknown)}.")
    missing = sorted(ASSET_FIELDS - set(record))
    if missing:
        errors.append(f"assets.{asset} is missing fields: {', '.join(missing)}.")
        return
    checks = (
        ("encryption_at_rest", record.get("encryption_at_rest") is True, "must be true"),
        ("backup_encryption", record.get("backup_encryption") is True, "must be true"),
        (
            "storage_scope",
            record.get("storage_scope") in {"managed-encrypted-service", "host-encrypted-volume"},
            "is invalid",
        ),
        (
            "key_reference",
            record.get("key_reference") == key_reference,
            "must match key_management.key_reference",
        ),
    )
    for field, valid, reason in checks:
        if not valid:
            errors.append(f"assets.{asset}.{field} {reason}.")
    for field in ("evidence_uri", "backup_restore_evidence_uri", "retention_deletion_evidence_uri"):
        if not is_evidence_uri(record.get(field)):
            errors.append(f"assets.{asset}.{field} must be a valid non-secret evidence URI.")
    if not is_utc_timestamp(record.get("verified_at")):
        errors.append(f"assets.{asset}.verified_at must be an ISO-8601 UTC string; quote it in YAML.")


def validate_assets(value: object, key_reference: object, errors: list[str]) -> None:
    assets = mapping(value, "assets", errors)
    if assets is None:
        return
    if not assets:
        errors.append("assets must contain at least one storage asset.")
    for asset, raw_record in assets.items():
        if asset not in ASSET_NAMES:
            errors.append(f"assets.{asset} is not a supported storage asset.")
        else:
            validate_asset_record(asset, raw_record, key_reference, errors)


def validate_attestation(document: object) -> tuple[dict[str, Any] | None, list[str]]:
    """Return the JSON-serialisable attestation document and deterministic errors."""

    errors: list[str] = []
    root = mapping(document, "root", errors)
    if root is None:
        return None, errors
    allowed = {"schema", "synthetic_example", "environment", "key_management", "assets"}
    unknown = sorted(set(root) - allowed)
    if unknown:
        errors.append(f"root has unknown fields: {', '.join(unknown)}.")
    if root.get("schema") != ATTESTATION_SCHEMA:
        errors.append(f"schema must equal {ATTESTATION_SCHEMA}.")
    if root.get("environment") not in {"staging", "production"}:
        errors.append("environment must be staging or production.")
    validate_key_management(root.get("key_management"), errors)
    key_management = root.get("key_management")
    key_reference = key_management.get("key_reference") if isinstance(key_management, Mapping) else None
    validate_assets(root.get("assets"), key_reference, errors)
    return dict(root), errors


def load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"Unable to read YAML: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and convert a storage-encryption YAML attestation to JSON."
    )
    parser.add_argument("--input", type=Path, required=True, help="YAML attestation to validate.")
    parser.add_argument(
        "--output", type=Path, help="JSON destination; required unless --validate-only is used."
    )
    parser.add_argument("--validate-only", action="store_true", help="Validate YAML without writing JSON.")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"YAML input does not exist: {args.input}")
    if not args.validate_only and args.output is None:
        parser.error("--output is required unless --validate-only is set.")
    if args.validate_only and args.output is not None:
        parser.error("--validate-only cannot be combined with --output.")

    try:
        document = load_yaml(args.input)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    converted, errors = validate_attestation(document)
    if errors:
        print("YAML attestation validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if args.validate_only:
        print("YAML attestation schema validation passed.")
        return 0

    if converted is None:
        print("YAML attestation conversion produced no document.", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(converted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote validated JSON attestation to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
