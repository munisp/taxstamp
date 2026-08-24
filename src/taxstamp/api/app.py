"""Application factory.

The app refuses to serve until its configuration validates, and it exposes only the
routes defined here. Handlers are synchronous on purpose: the database driver is
blocking, so FastAPI runs them in its worker threadpool instead of stalling the loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from taxstamp import __version__
from taxstamp.api.errors import register_error_handlers
from taxstamp.api.middleware import RequestContextMiddleware
from taxstamp.api.routers import (
    ops,
    orders,
    payments,
    registry,
    stamps,
    treasury,
    verification,
)
from taxstamp.config import Settings, get_settings
from taxstamp.observability import configure_logging
from taxstamp.runtime import Runtime, build_runtime

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None, *, runtime: Runtime | None = None) -> FastAPI:
    resolved = settings or (runtime.settings if runtime else get_settings())
    configure_logging(service=resolved.service_name, revision=resolved.revision)

    owns_runtime = runtime is None
    active_runtime = runtime or build_runtime(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime = active_runtime
        logger.info(
            "service_started",
            env=resolved.env.value,
            revision=resolved.revision,
            database_ready=active_runtime.check_database(),
            redis_ready=active_runtime.check_redis(),
        )
        try:
            yield
        finally:
            if owns_runtime:
                active_runtime.close()

    app = FastAPI(
        title="Nigerian Excise Tax Stamp Platform",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved.env.value != "production" else None,
        redoc_url=None,
    )
    app.state.runtime = active_runtime

    app.add_middleware(
        RequestContextMiddleware,
        metrics=active_runtime.metrics,
        require_tls=resolved.require_tls,
    )
    if resolved.trusted_host_list:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.trusted_host_list)
    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["authorization", "content-type", "idempotency-key", "x-request-id"],
            max_age=600,
        )

    register_error_handlers(app)
    app.include_router(ops.router)
    app.include_router(orders.router)
    app.include_router(payments.router)
    app.include_router(registry.router)
    app.include_router(stamps.router)
    app.include_router(treasury.router)
    app.include_router(verification.router)
    return app
