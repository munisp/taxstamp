"""Startup-validated application configuration.

Every setting is explicit. Secrets have no usable defaults: the process refuses to
start in a non-development environment unless they are supplied and strong enough.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_network

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 48
_PLACEHOLDER = re.compile(r"change[_-]?me|placeholder|example|secret{2,}|password", re.IGNORECASE)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class KmsProvider(StrEnum):
    """Supported external key-management boundaries for storage encryption."""

    UNCONFIGURED = "unconfigured"
    AWS_KMS = "aws_kms"
    AZURE_KEY_VAULT = "azure_key_vault"
    GCP_KMS = "gcp_kms"
    PKCS11_HSM = "pkcs11_hsm"


class ConfigurationError(RuntimeError):
    """Raised when the effective configuration is unsafe or incomplete."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TAXSTAMP_",
        env_file=None,
        extra="forbid",
        frozen=True,
    )

    env: Environment = Environment.DEVELOPMENT
    service_name: str = "taxstamp"
    revision: str = "unknown"

    database_url: str
    database_pool_size: int = Field(default=10, ge=1, le=200)
    database_statement_timeout_ms: int = Field(default=15_000, ge=1_000, le=300_000)
    redis_url: str

    api_token_secret: str
    device_hmac_secret: str
    payment_webhook_secret: str
    audit_chain_secret: str

    require_tls: bool = True
    cors_allowed_origins: str = ""
    trusted_hosts: str = ""
    trust_proxy_headers: bool = False
    trusted_proxy_cidrs: str = ""

    # Encryption-at-rest is an infrastructure control. These settings are a
    # fail-closed application/deployment contract, not a substitute for live
    # cloud-KMS/HSM or managed-storage evidence.
    storage_encryption_required: bool = False
    kms_provider: KmsProvider = KmsProvider.UNCONFIGURED
    kms_key_reference: str = ""
    kms_hsm_backed: bool = False
    kms_key_rotation_days: int = Field(default=365, ge=1, le=730)
    storage_encryption_evidence_uri: str = ""
    postgres_storage_encryption_attested: bool = False
    redis_storage_encryption_attested: bool = False
    opensearch_storage_encryption_attested: bool = False

    # Sensitive request replay / signature policy.
    signature_max_skew_seconds: int = Field(default=300, ge=30, le=900)
    nonce_ttl_seconds: int = Field(default=900, ge=60, le=86_400)

    # Rate limits (requests per window, per principal).
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    rate_limit_default: int = Field(default=600, ge=1)
    rate_limit_verify: int = Field(default=1_200, ge=1)

    # Issuance policy.
    max_order_quantity: int = Field(default=5_000_000, ge=1)
    issuance_chunk_size: int = Field(default=25_000, ge=100, le=200_000)

    # Idempotency retention.
    idempotency_ttl_seconds: int = Field(default=86_400, ge=300)

    # Outbox relay policy.
    outbox_max_attempts: int = Field(default=8, ge=1, le=50)
    outbox_lease_seconds: int = Field(default=60, ge=5, le=3_600)
    outbox_batch_size: int = Field(default=100, ge=1, le=1_000)

    # External systems of record. Empty string means "not configured"; dependent
    # capabilities are then explicitly rejected instead of silently succeeding.
    firs_base_url: str = ""
    nafdac_base_url: str = ""
    son_base_url: str = ""
    customs_base_url: str = ""
    ledger_anchor_base_url: str = ""
    external_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    external_api_key: str = ""

    # Integration-ready endpoints. These settings only declare configured adapters;
    # they do not represent a validated provider connection or production approval.
    tigerbeetle_addresses: str = ""
    mojaloop_base_url: str = ""
    kafka_bootstrap_servers: str = ""
    kafka_security_protocol: str = "SASL_SSL"
    kafka_topic: str = "taxstamp.events.v1"
    kafka_publish_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    apisix_admin_url: str = ""
    keycloak_issuer_url: str = ""
    openappsec_management_url: str = ""
    permify_base_url: str = ""
    opensearch_url: str = ""
    fluvio_cluster_url: str = ""
    dapr_http_url: str = ""
    lakehouse_catalog_url: str = ""

    @field_validator(
        "api_token_secret",
        "device_hmac_secret",
        "payment_webhook_secret",
        "audit_chain_secret",
    )
    @classmethod
    def _validate_secret(cls, value: str) -> str:
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"secret must be at least {MIN_SECRET_LENGTH} characters")
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the postgresql+psycopg driver")
        return value

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("redis_url must be a redis:// or rediss:// URL")
        return value

    @field_validator("kms_key_reference", "storage_encryption_evidence_uri")
    @classmethod
    def _normalize_storage_encryption_reference(cls, value: str) -> str:
        return value.strip()

    @field_validator(
        "mojaloop_base_url",
        "apisix_admin_url",
        "keycloak_issuer_url",
        "openappsec_management_url",
        "permify_base_url",
        "opensearch_url",
        "fluvio_cluster_url",
        "dapr_http_url",
        "lakehouse_catalog_url",
    )
    @classmethod
    def _validate_optional_http_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if normalized and not normalized.startswith(("http://", "https://")):
            raise ValueError("configured integration endpoint must use http:// or https://")
        return normalized

    @field_validator("kafka_topic")
    @classmethod
    def _validate_kafka_topic(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 249:
            raise ValueError("kafka_topic must be 1-249 characters")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
            raise ValueError("kafka_topic contains unsupported characters")
        return normalized

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _validate_trusted_proxy_cidrs(cls, value: str) -> str:
        cidrs = [candidate.strip() for candidate in value.split(",") if candidate.strip()]
        for cidr in cidrs:
            try:
                ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError("trusted_proxy_cidrs must contain comma-separated IP CIDRs") from exc
        return ",".join(cidrs)

    @model_validator(mode="after")
    def _validate_deployment(self) -> Settings:
        if self.env in (Environment.PRODUCTION, Environment.STAGING):
            secrets = {
                "api_token_secret": self.api_token_secret,
                "device_hmac_secret": self.device_hmac_secret,
                "payment_webhook_secret": self.payment_webhook_secret,
                "audit_chain_secret": self.audit_chain_secret,
            }
            for name, value in secrets.items():
                if _PLACEHOLDER.search(value):
                    raise ValueError(f"{name} looks like a placeholder; supply a real secret")
            if len(set(secrets.values())) != len(secrets):
                raise ValueError("each secret must be distinct")
            if not self.require_tls:
                raise ValueError("require_tls cannot be disabled outside development/test")
            if "*" in self.cors_allowed_origins:
                raise ValueError("wildcard CORS origins are not permitted outside development")
            if not self.trusted_host_list:
                raise ValueError("trusted_hosts must be configured outside development/test")
            if "*" in self.trusted_host_list:
                raise ValueError("trusted_hosts cannot contain a wildcard outside development/test")
            self._validate_proxy_trust()
            if self.env is Environment.PRODUCTION and "sslmode=disable" in self.database_url:
                raise ValueError("database connections must not disable TLS in production")
            self._validate_storage_encryption()
            public_endpoints = {
                "mojaloop_base_url": self.mojaloop_base_url,
                "apisix_admin_url": self.apisix_admin_url,
                "keycloak_issuer_url": self.keycloak_issuer_url,
                "openappsec_management_url": self.openappsec_management_url,
                "permify_base_url": self.permify_base_url,
                "opensearch_url": self.opensearch_url,
                "fluvio_cluster_url": self.fluvio_cluster_url,
                "lakehouse_catalog_url": self.lakehouse_catalog_url,
            }
            for name, endpoint in public_endpoints.items():
                if endpoint and not endpoint.startswith("https://"):
                    raise ValueError(f"{name} must use https in staging or production")
            if self.kafka_bootstrap_servers and self.kafka_security_protocol not in ("SSL", "SASL_SSL"):
                raise ValueError("Kafka must use SSL or SASL_SSL in staging or production")
        return self

    def _validate_proxy_trust(self) -> None:
        if self.trust_proxy_headers and not self.trusted_proxy_cidr_list:
            raise ValueError("trusted_proxy_cidrs must be configured when trust_proxy_headers is enabled")

    def _validate_storage_encryption(self) -> None:
        """Require externally attested encrypted storage outside local/test use."""

        if not self.storage_encryption_required:
            raise ValueError("storage_encryption_required must be enabled in staging or production")
        if self.kms_provider is KmsProvider.UNCONFIGURED:
            raise ValueError("kms_provider must be configured in staging or production")
        if not self.kms_key_reference or _PLACEHOLDER.search(self.kms_key_reference):
            raise ValueError("kms_key_reference must identify a non-placeholder external KMS/HSM key")
        if not self.kms_hsm_backed:
            raise ValueError("kms_hsm_backed must be attested in staging or production")
        if not self.storage_encryption_evidence_uri or _PLACEHOLDER.search(
            self.storage_encryption_evidence_uri
        ):
            raise ValueError("storage_encryption_evidence_uri must identify deployment evidence")
        if not self.postgres_storage_encryption_attested:
            raise ValueError("postgres_storage_encryption_attested must be true in staging or production")
        if not self.redis_storage_encryption_attested:
            raise ValueError("redis_storage_encryption_attested must be true in staging or production")
        if self.opensearch_url and not self.opensearch_storage_encryption_attested:
            raise ValueError(
                "opensearch_storage_encryption_attested must be true when OpenSearch is configured"
            )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

    @property
    def trusted_proxy_cidr_list(self) -> list[str]:
        return [cidr.strip() for cidr in self.trusted_proxy_cidrs.split(",") if cidr.strip()]

    @property
    def is_test(self) -> bool:
        return self.env is Environment.TEST


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process."""
    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - exercised via test_config
        raise ConfigurationError(f"invalid configuration: {exc}") from exc


def reset_settings_cache() -> None:
    get_settings.cache_clear()
