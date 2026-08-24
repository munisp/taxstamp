"""Precise JSON types.

The codebase forbids ``Any``: JSON payloads are typed with these recursive aliases so
that decoding a document still requires explicit narrowing before use.
"""

from __future__ import annotations

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def require_str(document: JsonObject, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError(f"field {key!r} must be a string")  # noqa: TRY004 - untrusted payload
    return value


def require_int(document: JsonObject, key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"field {key!r} must be an integer")  # noqa: TRY004 - untrusted payload
    return value


def require_bool(document: JsonObject, key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"field {key!r} must be a boolean")  # noqa: TRY004 - untrusted payload
    return value


def optional_int(document: JsonObject, key: str) -> int | None:
    value = document.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(  # noqa: TRY004 - untrusted payload
            f"field {key!r} must be an integer when present"
        )
    return value


def optional_str(document: JsonObject, key: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - untrusted payload
            f"field {key!r} must be a string when present"
        )
    return value
