"""Request middleware: request identity, security headers, metrics and TLS policy."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from taxstamp.api.deps import request_id
from taxstamp.observability import Metrics

Handler = Callable[[Request], Awaitable[Response]]
logger = structlog.get_logger(__name__)

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, metrics: Metrics, require_tls: bool) -> None:
        super().__init__(app)  # type: ignore[arg-type]  # Starlette accepts any ASGI app
        self._metrics = metrics
        self._require_tls = require_tls

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        identifier = request_id(request)
        structlog.contextvars.bind_contextvars(request_id=identifier, path=request.url.path)
        if self._require_tls and not self._is_secure(request):
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "tls_required", "message": "requests must be made over TLS"}},
                headers={"x-request-id": identifier},
            )
        started = time.perf_counter()
        route = request.scope.get("route")
        route_name = getattr(route, "path", request.url.path)
        try:
            response = await call_next(request)
        finally:
            duration = time.perf_counter() - started
            self._metrics["latency"].labels(route=route_name, method=request.method).observe(duration)
        self._metrics["requests"].labels(
            route=route_name, method=request.method, status=str(response.status_code)
        ).inc()
        response.headers["x-request-id"] = identifier
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        structlog.contextvars.unbind_contextvars("request_id", "path")
        return response

    @staticmethod
    def _is_secure(request: Request) -> bool:
        if request.url.scheme == "https":
            return True
        forwarded = request.headers.get("x-forwarded-proto", "")
        return forwarded.split(",")[0].strip().lower() == "https"
