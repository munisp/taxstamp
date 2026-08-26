#!/usr/bin/env python3
"""Evaluate non-secret KMS/HSM and encryption-at-rest deployment evidence.

The checker deliberately does not contact a cloud KMS, HSM, database, Redis, or
OpenSearch cluster. A local repository cannot cryptographically establish that a
provider disk, snapshot, replica, backup, or HSM is correctly configured. It
therefore reports only an evidence-attestation state and fails closed whenever
required records are missing, malformed, placeholder-filled, or inconsistent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PLACEHOLDER = re.compile(r"change[_-]?me|placeholder|example|replace|secrets?|password", re.IGNORECASE)
SUPPORTED_PROVIDERS = {"aws_kms", "azure_key_vault", "gcp_kms", "pkcs11_hsm"}
REQUIRED_ASSETS = ("postgres", "redis", "opensearch")
ATTESTATION_SCHEMA = "taxstamp.storage-encryption-attestation.v1"


@dataclass(frozen=True, slots=True)
class AssetResult:
    asset: str
    status: str
    findings: list[str]
    strict_production_findings: list[dict[str, str]]


def parse_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=value files without evaluating shell syntax."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


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
    """Accept ISO-8601 UTC values without treating their date as a freshness proof."""

    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def strict_finding(field: str, reason: str) -> dict[str, str]:
    return {"field": field, "reason": reason}


def load_attestation(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def assess_asset(
    asset: str,
    configured_key: str,
    attestation: dict[str, Any],
    *,
    strict_production: bool,
) -> AssetResult:
    assets = attestation.get("assets")
    record = assets.get(asset) if isinstance(assets, dict) else None
    findings: list[str] = []
    strict_findings: list[dict[str, str]] = []
    if not isinstance(record, dict):
        if strict_production:
            strict_findings.append(strict_finding(f"assets.{asset}", "required attestation object is absent"))
        return AssetResult(asset, "not_verified", ["Missing asset encryption attestation."], strict_findings)

    if record.get("encryption_at_rest") is not True:
        findings.append("encryption_at_rest is not affirmed.")
    if record.get("backup_encryption") is not True:
        findings.append("backup_encryption is not affirmed.")
    if record.get("storage_scope") not in {"managed-encrypted-service", "host-encrypted-volume"}:
        findings.append("storage_scope must identify a managed encrypted service or host-encrypted volume.")
    if record.get("key_reference") != configured_key:
        findings.append("Asset key reference does not match configured KMS/HSM key reference.")
    if not is_evidence_uri(record.get("evidence_uri")):
        findings.append("Asset evidence_uri is missing, unsafe, or placeholder text.")
    if not is_non_placeholder(record.get("verified_at")):
        findings.append("verified_at is missing or placeholder text.")

    if strict_production:
        strict_checks = (
            ("encryption_at_rest", record.get("encryption_at_rest") is True, "must be true"),
            ("backup_encryption", record.get("backup_encryption") is True, "must be true"),
            (
                "storage_scope",
                record.get("storage_scope") in {"managed-encrypted-service", "host-encrypted-volume"},
                "must be managed-encrypted-service or host-encrypted-volume",
            ),
            (
                "key_reference",
                record.get("key_reference") == configured_key,
                "must match the configured KMS/HSM key",
            ),
            (
                "evidence_uri",
                is_evidence_uri(record.get("evidence_uri")),
                "must be a non-placeholder evidence URI",
            ),
            ("verified_at", is_utc_timestamp(record.get("verified_at")), "must be an ISO-8601 UTC timestamp"),
            (
                "backup_restore_evidence_uri",
                is_evidence_uri(record.get("backup_restore_evidence_uri")),
                "must reference a successful encrypted backup restore exercise",
            ),
            (
                "retention_deletion_evidence_uri",
                is_evidence_uri(record.get("retention_deletion_evidence_uri")),
                "must reference retention/deletion or crypto-erasure evidence",
            ),
        )
        for field, valid, reason in strict_checks:
            if not valid:
                strict_findings.append(strict_finding(f"assets.{asset}.{field}", reason))

    status = "evidence_attested" if not findings and not strict_findings else "not_verified"
    return AssetResult(asset, status, findings, strict_findings)


def standard_environment_findings(
    env: dict[str, str], provider: str, key_reference: str, evidence_uri: str
) -> list[str]:
    """Validate the provider-neutral configuration required in every check mode."""

    findings: list[str] = []
    if env.get("TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED", "").lower() != "true":
        findings.append("TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED must be true.")
    if provider not in SUPPORTED_PROVIDERS:
        findings.append("TAXSTAMP_KMS_PROVIDER is missing or unsupported.")
    if not is_non_placeholder(key_reference):
        findings.append("TAXSTAMP_KMS_KEY_REFERENCE is missing or placeholder text.")
    if env.get("TAXSTAMP_KMS_HSM_BACKED", "").lower() != "true":
        findings.append("TAXSTAMP_KMS_HSM_BACKED must be true with an HSM/KMS attestation.")
    if not is_evidence_uri(evidence_uri):
        findings.append("TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI is missing, unsafe, or placeholder text.")
    return findings


def strict_environment_findings(
    env: dict[str, str], provider: str, key_reference: str, evidence_uri: str
) -> list[dict[str, str]]:
    """Return exact production configuration fields that remain invalid."""

    rotation_days = env.get("TAXSTAMP_KMS_KEY_ROTATION_DAYS", "")
    checks = (
        ("TAXSTAMP_ENV", env.get("TAXSTAMP_ENV") == "production", "must be exactly production"),
        (
            "TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED",
            env.get("TAXSTAMP_STORAGE_ENCRYPTION_REQUIRED", "").lower() == "true",
            "must be true",
        ),
        ("TAXSTAMP_KMS_PROVIDER", provider in SUPPORTED_PROVIDERS, "must be a supported provider"),
        ("TAXSTAMP_KMS_KEY_REFERENCE", is_non_placeholder(key_reference), "must be non-placeholder"),
        (
            "TAXSTAMP_KMS_HSM_BACKED",
            env.get("TAXSTAMP_KMS_HSM_BACKED", "").lower() == "true",
            "must be true",
        ),
        (
            "TAXSTAMP_KMS_KEY_ROTATION_DAYS",
            rotation_days.isdecimal() and 1 <= int(rotation_days) <= 730,
            "must be a whole number between 1 and 730",
        ),
        (
            "TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI",
            is_evidence_uri(evidence_uri),
            "must be a non-placeholder evidence URI",
        ),
    )
    return [strict_finding(field, reason) for field, valid, reason in checks if not valid]


def standard_key_management_findings(key_management: object, provider: str, key_reference: str) -> list[str]:
    """Validate the base key-management attestation that all modes require."""

    if not isinstance(key_management, dict):
        return ["Missing key_management attestation object."]
    findings: list[str] = []
    if key_management.get("provider") != provider:
        findings.append("Attested KMS/HSM provider does not match configuration.")
    if key_management.get("key_reference") != key_reference:
        findings.append("Attested KMS/HSM key reference does not match configuration.")
    if key_management.get("hsm_backed") is not True:
        findings.append("Attestation does not affirm HSM-backed KMS/key protection.")
    if not is_evidence_uri(key_management.get("rotation_evidence_uri")):
        findings.append("Missing valid key-rotation evidence URI.")
    return findings


def strict_key_management_findings(
    key_management: object, env: dict[str, str], provider: str, key_reference: str
) -> list[dict[str, str]]:
    """Return exact missing/invalid key-management evidence fields for production."""

    if not isinstance(key_management, dict):
        return [strict_finding("key_management", "required attestation object is absent")]
    rotation_days = env.get("TAXSTAMP_KMS_KEY_ROTATION_DAYS", "")
    checks = (
        ("provider", key_management.get("provider") == provider, "must match TAXSTAMP_KMS_PROVIDER"),
        (
            "key_reference",
            key_management.get("key_reference") == key_reference,
            "must match TAXSTAMP_KMS_KEY_REFERENCE",
        ),
        ("hsm_backed", key_management.get("hsm_backed") is True, "must be true"),
        (
            "rotation_days",
            rotation_days.isdecimal() and key_management.get("rotation_days") == int(rotation_days),
            "must match TAXSTAMP_KMS_KEY_ROTATION_DAYS",
        ),
        (
            "rotation_evidence_uri",
            is_evidence_uri(key_management.get("rotation_evidence_uri")),
            "must reference a completed key-rotation exercise",
        ),
        (
            "access_review_evidence_uri",
            is_evidence_uri(key_management.get("access_review_evidence_uri")),
            "must reference a completed KMS/HSM access review",
        ),
        (
            "recovery_exercise_evidence_uri",
            is_evidence_uri(key_management.get("recovery_exercise_evidence_uri")),
            "must reference a completed KMS/HSM recovery exercise",
        ),
    )
    return [strict_finding(f"key_management.{field}", reason) for field, valid, reason in checks if not valid]


def assess(
    env: dict[str, str],
    attestation: dict[str, Any],
    required_assets: tuple[str, ...],
    *,
    strict_production: bool = False,
) -> dict[str, Any]:
    """Return a fail-closed evidence assessment; it never makes a live-provider claim."""

    provider = env.get("TAXSTAMP_KMS_PROVIDER", "")
    key_reference = env.get("TAXSTAMP_KMS_KEY_REFERENCE", "")
    evidence_uri = env.get("TAXSTAMP_STORAGE_ENCRYPTION_EVIDENCE_URI", "")
    key_management = attestation.get("key_management")
    global_findings = standard_environment_findings(env, provider, key_reference, evidence_uri)
    global_findings.extend(standard_key_management_findings(key_management, provider, key_reference))
    strict_findings = (
        strict_environment_findings(env, provider, key_reference, evidence_uri)
        + strict_key_management_findings(key_management, env, provider, key_reference)
        if strict_production
        else []
    )

    if strict_production and attestation.get("schema") != ATTESTATION_SCHEMA:
        strict_findings.append(strict_finding("schema", f"must equal {ATTESTATION_SCHEMA}"))

    results = [
        assess_asset(asset, key_reference, attestation, strict_production=strict_production)
        for asset in required_assets
    ]
    all_attested = (
        not global_findings
        and not strict_findings
        and all(result.status == "evidence_attested" for result in results)
    )
    return {
        "schema": "taxstamp.storage-encryption-check.v1",
        "overall_status": "evidence_attested_not_live_verified" if all_attested else "not_verified",
        "verification_scope": "configuration-and-attestation-only",
        "mode": "strict_production" if strict_production else "standard",
        "global_findings": global_findings,
        "strict_production_findings": strict_findings,
        "assets": [asdict(result) for result in results],
        "required_assets": list(required_assets),
        "live_provider_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check storage-encryption configuration and attestation evidence."
    )
    parser.add_argument(
        "--env-file", type=Path, required=True, help="Non-secret deployment environment file to inspect."
    )
    parser.add_argument(
        "--attestation", type=Path, help="Completed non-secret storage-encryption attestation JSON."
    )
    parser.add_argument("--output", type=Path, help="Optional path to write JSON findings.")
    parser.add_argument("--assets", nargs="+", choices=REQUIRED_ASSETS, default=list(REQUIRED_ASSETS))
    parser.add_argument(
        "--strict-production",
        action="store_true",
        help="Require production environment, rotation period, and all KMS/HSM and asset evidence fields.",
    )
    args = parser.parse_args()

    if not args.env_file.is_file():
        parser.error(f"environment file does not exist: {args.env_file}")

    result = assess(
        parse_env_file(args.env_file),
        load_attestation(args.attestation),
        tuple(args.assets),
        strict_production=args.strict_production,
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["overall_status"] == "evidence_attested_not_live_verified" else 1


if __name__ == "__main__":
    sys.exit(main())
