"""Domain errors mapped to stable API error codes."""

from __future__ import annotations

from http import HTTPStatus


class DomainError(Exception):
    """Base class for errors that map to a client-visible response."""

    status: int = HTTPStatus.BAD_REQUEST
    code: str = "domain_error"

    def __init__(self, message: str, *, detail: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ValidationFailed(DomainError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_failed"


class NotFound(DomainError):
    status = HTTPStatus.NOT_FOUND
    code = "not_found"


class Conflict(DomainError):
    status = HTTPStatus.CONFLICT
    code = "conflict"


class IdempotencyKeyReused(DomainError):
    status = HTTPStatus.CONFLICT
    code = "idempotency_key_reused"


class Unauthenticated(DomainError):
    status = HTTPStatus.UNAUTHORIZED
    code = "unauthenticated"


class Forbidden(DomainError):
    status = HTTPStatus.FORBIDDEN
    code = "forbidden"


class RateLimited(DomainError):
    status = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate_limited"


class IllegalState(DomainError):
    status = HTTPStatus.CONFLICT
    code = "illegal_state"


class CapabilityNotConfigured(DomainError):
    """A capability depends on an external system of record that is not configured.

    The platform refuses the request instead of returning a fabricated success.
    """

    status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "capability_not_configured"


class CapabilityNotSupported(DomainError):
    """The platform deliberately does not implement this capability."""

    status = HTTPStatus.NOT_IMPLEMENTED
    code = "capability_not_supported"


class DependencyUnavailable(DomainError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "dependency_unavailable"
