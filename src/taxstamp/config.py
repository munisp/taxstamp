"""Startup-validated application configuration.

Every setting is explicit. Secrets have no usable defaults: the process refuses to
start in a non-development environment unless they are supplied and strong enough.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_LENGTH = 48
_PLACEHOLDER = re.compile(r"change[_-]?me|placeholder|example|secret{2,}|password", re.IGNORECASE)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


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
    export_signing_secret: str
    transparency_signing_secret: str
    #: Signs offline revocation bundles. A device trusts a bundle only under this key, so
    #: it cannot be handed a forged revocation list.
    offline_signing_secret: str
    #: Keys the revocation Bloom filter. Separate from the signing key: a distributed
    #: bundle discloses filter membership to whoever holds this key, and that must not
    #: also let them mint bundles.
    offline_filter_secret: str
    #: Keys the pseudonymous fingerprint of a consumer's address. Rotating it severs the
    #: link between past and future consumer checks, which is why it is separate.
    consumer_fingerprint_secret: str

    # Federated identity for human principals. Empty issuer means "not configured", and
    # federated bearer tokens are then refused rather than accepted unverified; devices
    # and service accounts always use the platform's own credentials.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    #: Defaults to the issuer's standard JWKS location when left empty.
    oidc_jwks_url: str = ""
    oidc_leeway_seconds: int = Field(default=30, ge=0, le=120)
    oidc_jwks_cache_seconds: int = Field(default=600, ge=60, le=86_400)
    #: Authentication methods that count as multi-factor, as asserted in ``amr``.
    oidc_mfa_methods: str = "mfa,otp,hwk,swk,pop"
    #: Authentication context class that counts as multi-factor, as asserted in ``acr``.
    oidc_mfa_acr: str = "mfa"
    #: Roles that may not hold a federated session without a multi-factor assertion.
    oidc_mfa_required_roles: str = "admin,supervisor,treasury,auditor"

    # External authorisation engine. See taxstamp.authz.policy for the decision order.
    permify_base_url: str = ""
    permify_tenant_id: str = ""
    permify_api_key: str = ""
    permify_schema_version: str = ""
    permify_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    #: One of disabled, shadow, enforcing. Enforcing refuses requests the engine cannot
    #: answer, so it must not be selected before the engine is highly available.
    authz_external_mode: str = "disabled"

    require_tls: bool = True
    cors_allowed_origins: str = ""
    trusted_hosts: str = ""

    # Sensitive request replay / signature policy.
    signature_max_skew_seconds: int = Field(default=300, ge=30, le=900)
    nonce_ttl_seconds: int = Field(default=900, ge=60, le=86_400)

    # Rate limits (requests per window, per principal).
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3_600)
    rate_limit_default: int = Field(default=600, ge=1)
    rate_limit_verify: int = Field(default=1_200, ge=1)
    #: Consumer checks are unauthenticated, so they are limited per client address.
    rate_limit_consumer_verify: int = Field(default=30, ge=1)

    # Offline operation.
    offline_bundle_ttl_hours: int = Field(default=24, ge=1, le=168)
    #: How old a captured scan may be when a device finally reconnects. Beyond this the
    #: batch is refused rather than backdated into the register.
    offline_sync_max_staleness_hours: int = Field(default=72, ge=1, le=720)

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
    #: Where a regulator export is delivered. Empty means no delivery channel exists, and
    #: the platform then reports the export as undelivered rather than claiming a filing.
    regulator_repository_base_url: str = ""
    external_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    external_api_key: str = ""

    @field_validator(
        "api_token_secret",
        "device_hmac_secret",
        "payment_webhook_secret",
        "audit_chain_secret",
        "export_signing_secret",
        "transparency_signing_secret",
        "offline_signing_secret",
        "offline_filter_secret",
        "consumer_fingerprint_secret",
    )
    @classmethod
    def _validate_secret(cls, value: str) -> str:
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"secret must be at least {MIN_SECRET_LENGTH} characters")
        return value

    @field_validator("authz_external_mode")
    @classmethod
    def _validate_authz_mode(cls, value: str) -> str:
        allowed = {"disabled", "shadow", "enforcing"}
        if value not in allowed:
            raise ValueError(f"authz_external_mode must be one of {sorted(allowed)}")
        return value

    @field_validator("oidc_issuer", "oidc_jwks_url", "permify_base_url")
    @classmethod
    def _validate_https_endpoint(cls, value: str) -> str:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("endpoint must be an http:// or https:// URL")
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

    @model_validator(mode="after")
    def _validate_deployment(self) -> Settings:
        if self.env in (Environment.PRODUCTION, Environment.STAGING):
            secrets = {
                "api_token_secret": self.api_token_secret,
                "device_hmac_secret": self.device_hmac_secret,
                "payment_webhook_secret": self.payment_webhook_secret,
                "audit_chain_secret": self.audit_chain_secret,
                "export_signing_secret": self.export_signing_secret,
                "transparency_signing_secret": self.transparency_signing_secret,
                "offline_signing_secret": self.offline_signing_secret,
                "offline_filter_secret": self.offline_filter_secret,
                "consumer_fingerprint_secret": self.consumer_fingerprint_secret,
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
            if self.env is Environment.PRODUCTION and "sslmode=disable" in self.database_url:
                raise ValueError("database connections must not disable TLS in production")
            for name, endpoint in (
                ("oidc_issuer", self.oidc_issuer),
                ("oidc_jwks_url", self.oidc_jwks_url),
                ("permify_base_url", self.permify_base_url),
            ):
                if endpoint.startswith("http://"):
                    raise ValueError(f"{name} must use TLS outside development/test")
        if self.authz_external_mode != "disabled" and not self.permify_base_url:
            raise ValueError("authz_external_mode requires permify_base_url")
        if self.oidc_audience and not self.oidc_issuer:
            raise ValueError("oidc_audience requires oidc_issuer")
        return self

    @property
    def effective_oidc_jwks_url(self) -> str:
        """The configured JWKS location, or the issuer's standard discovery path."""
        if self.oidc_jwks_url:
            return self.oidc_jwks_url
        if not self.oidc_issuer:
            return ""
        return self.oidc_issuer.rstrip("/") + "/protocol/openid-connect/certs"

    @property
    def oidc_mfa_method_set(self) -> frozenset[str]:
        return frozenset(
            method.strip().lower() for method in self.oidc_mfa_methods.split(",") if method.strip()
        )

    @property
    def oidc_mfa_role_set(self) -> frozenset[str]:
        return frozenset(
            role.strip().lower() for role in self.oidc_mfa_required_roles.split(",") if role.strip()
        )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]

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
