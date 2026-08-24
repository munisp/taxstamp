"""Uniform error responses.

Clients receive a stable code and a safe message. Unexpected exceptions are logged with
their type and a request id but never leaked to the caller.
"""

from __future__ import annotations

from http import HTTPStatus

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from taxstamp.errors import Conflict, DomainError
from taxstamp.jsontypes import JsonObject, JsonValue

logger = structlog.get_logger(__name__)


def error_body(code: str, message: str, detail: JsonObject | None = None) -> JsonObject:
    body: JsonObject = {"error": {"code": code, "message": message}}
    if detail:
        body["error"] = {"code": code, "message": message, "detail": detail}
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        logger.info(
            "domain_error",
            code=exc.code,
            status=exc.status,
            path=request.url.path,
            message=exc.message,
        )
        detail: JsonObject = dict(exc.detail)
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, detail or None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request
        fields: list[JsonValue] = []
        fields.extend(sorted({".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()}))
        return JSONResponse(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            content=error_body("validation_failed", "the request body failed validation", {"fields": fields}),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("integrity_error", path=request.url.path, error=type(exc.orig).__name__)
        conflict = Conflict("the request conflicts with existing data")
        return JSONResponse(status_code=conflict.status, content=error_body(conflict.code, conflict.message))

    @app.exception_handler(OperationalError)
    async def _operational(request: Request, exc: OperationalError) -> JSONResponse:
        logger.error("database_unavailable", path=request.url.path, error=type(exc.orig).__name__)
        return JSONResponse(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            content=error_body("dependency_unavailable", "the database is unavailable"),
        )

    @app.exception_handler(DBAPIError)
    async def _dbapi(request: Request, exc: DBAPIError) -> JSONResponse:
        logger.error("database_error", path=request.url.path, error=type(exc.orig).__name__)
        return JSONResponse(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            content=error_body("dependency_unavailable", "the database rejected the operation"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(HTTPStatus(exc.status_code).name.lower(), str(exc.detail or "request failed")),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content=error_body("internal_error", "the request could not be completed"),
        )
