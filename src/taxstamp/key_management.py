"""Provider-neutral KMS/HSM deployment contract.

Storage-layer encryption is performed by a managed database, encrypted volume,
or search service—not by this Python process.  This module keeps application
configuration explicit and provides a safe hand-off object for deployment code
without loading provider credentials or exposing key material.
"""

from __future__ import annotations

from dataclasses import dataclass

from taxstamp.config import KmsProvider, Settings


@dataclass(frozen=True, slots=True)
class KeyManagementProfile:
    """Non-secret declaration of the key-management boundary for a deployment."""

    provider: KmsProvider
    key_reference: str
    hsm_backed: bool
    rotation_days: int
    evidence_uri: str

    @property
    def is_configured(self) -> bool:
        return self.provider is not KmsProvider.UNCONFIGURED and bool(self.key_reference)

    @property
    def required_evidence(self) -> tuple[str, ...]:
        return (
            "KMS/HSM key policy and non-secret key identifier",
            "HSM-backed service attestation or PKCS#11 HSM inventory",
            "key-rotation and recovery exercise record",
            "encrypted primary, replica, backup, export and deletion evidence",
        )


def key_management_profile(settings: Settings) -> KeyManagementProfile:
    """Build a non-secret profile; no provider request is attempted here."""

    return KeyManagementProfile(
        provider=settings.kms_provider,
        key_reference=settings.kms_key_reference,
        hsm_backed=settings.kms_hsm_backed,
        rotation_days=settings.kms_key_rotation_days,
        evidence_uri=settings.storage_encryption_evidence_uri,
    )
