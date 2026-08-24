"""Process-wide runtime wiring.

One place builds the database engine, Redis client, provider clients and clock, so the
API, the worker and the CLI all share identical behaviour and configuration checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from prometheus_client import CollectorRegistry
from redis import Redis
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from taxstamp.authz.permify import PermifyClient, PermifyConfig
from taxstamp.authz.policy import ExternalMode, PolicyEngine
from taxstamp.clock import Clock, SystemClock
from taxstamp.config import Settings, get_settings
from taxstamp.db import create_db_engine, create_session_factory
from taxstamp.gates import RateLimiter, ReplayGuard
from taxstamp.identity.oidc import OidcConfig, OidcVerifier
from taxstamp.observability import Metrics, build_metrics
from taxstamp.providers.anchor import AnchorService
from taxstamp.providers.base import ProviderClient, ProviderConfig
from taxstamp.providers.compliance import ComplianceService, Registry


def _provider(settings: Settings, name: str, base_url: str) -> ProviderClient:
    return ProviderClient(
        ProviderConfig(
            name=name,
            base_url=base_url,
            api_key=settings.external_api_key,
            timeout_seconds=settings.external_timeout_seconds,
        )
    )


@dataclass(slots=True)
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    redis: Redis
    replay_guard: ReplayGuard
    rate_limiter: RateLimiter
    compliance: ComplianceService
    anchor: AnchorService
    oidc: OidcVerifier
    policy: PolicyEngine
    clock: Clock
    registry: CollectorRegistry
    metrics: Metrics

    def check_database(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True

    def check_redis(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception:  # noqa: BLE001 - readiness must not raise
            return False

    def close(self) -> None:
        # redis-py does not annotate Redis.close
        self.redis.close()  # type: ignore[no-untyped-call]
        self.engine.dispose()


def build_runtime(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    http_client: httpx.Client | None = None,
) -> Runtime:
    resolved = settings or get_settings()
    engine = create_db_engine(resolved)
    redis = Redis.from_url(resolved.redis_url, decode_responses=True, socket_timeout=2.0)
    registry = CollectorRegistry()
    compliance_clients = {
        Registry.FIRS: _provider(resolved, "FIRS", resolved.firs_base_url),
        Registry.NAFDAC: _provider(resolved, "NAFDAC", resolved.nafdac_base_url),
        Registry.SON: _provider(resolved, "SON", resolved.son_base_url),
        Registry.CUSTOMS: _provider(resolved, "Customs", resolved.customs_base_url),
    }
    anchor_client = _provider(resolved, "LedgerAnchor", resolved.ledger_anchor_base_url)
    permify = PermifyClient(
        PermifyConfig(
            base_url=resolved.permify_base_url,
            tenant_id=resolved.permify_tenant_id,
            api_key=resolved.permify_api_key,
            timeout_seconds=resolved.permify_timeout_seconds,
            schema_version=resolved.permify_schema_version,
        )
    )
    if http_client is not None:
        compliance_clients = {
            name: client.with_client(http_client) for name, client in compliance_clients.items()
        }
        anchor_client = anchor_client.with_client(http_client)
        permify = permify.with_client(http_client)
    metrics = build_metrics(registry)
    return Runtime(
        settings=resolved,
        engine=engine,
        session_factory=create_session_factory(engine),
        redis=redis,
        replay_guard=ReplayGuard(redis, ttl_seconds=resolved.nonce_ttl_seconds),
        rate_limiter=RateLimiter(redis, window_seconds=resolved.rate_limit_window_seconds),
        compliance=ComplianceService(compliance_clients),
        anchor=AnchorService(anchor_client),
        oidc=OidcVerifier(
            OidcConfig(
                issuer=resolved.oidc_issuer,
                audience=resolved.oidc_audience,
                jwks_url=resolved.effective_oidc_jwks_url,
                leeway_seconds=resolved.oidc_leeway_seconds,
                jwks_cache_seconds=resolved.oidc_jwks_cache_seconds,
                required_methods=resolved.oidc_mfa_method_set,
                required_acr=resolved.oidc_mfa_acr,
            )
        ),
        policy=PolicyEngine(
            client=permify,
            mode=ExternalMode(resolved.authz_external_mode),
            metrics=metrics,
        ),
        clock=clock or SystemClock(),
        registry=registry,
        metrics=metrics,
    )
