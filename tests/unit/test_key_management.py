"""KMS/HSM settings are represented without loading provider credentials."""

from __future__ import annotations

from taxstamp.config import KmsProvider, Settings
from taxstamp.key_management import key_management_profile

from .test_config import BASE


def test_key_management_profile_exposes_only_non_secret_contract() -> None:
    settings = Settings(
        **{
            **BASE,
            "kms_provider": KmsProvider.PKCS11_HSM,
            "kms_key_reference": "pkcs11:token=taxstamp;object=storage-master;type=secret-key",
            "kms_hsm_backed": True,
            "storage_encryption_evidence_uri": "https://evidence.example.ng/changes/CHG-123",
        }
    )
    profile = key_management_profile(settings)
    assert profile.is_configured is True
    assert profile.provider is KmsProvider.PKCS11_HSM
    assert "KMS/HSM key policy" in profile.required_evidence[0]
