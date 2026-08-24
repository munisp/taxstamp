"""Canonical JSON serialisation used for signatures, hashes and dedupe keys.

Byte-for-byte stability matters: keys are sorted, separators are fixed, non-ASCII is
escaped, and floats are rejected because they are not exactly representable.
"""

from __future__ import annotations

import hashlib
import json

from taxstamp.jsontypes import JsonObject, JsonValue


class CanonicalisationError(ValueError):
    pass


def _reject_floats(value: JsonValue) -> None:
    if isinstance(value, float):
        raise CanonicalisationError("floating point values cannot be canonicalised")
    if isinstance(value, list):
        for item in value:
            _reject_floats(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_floats(item)


def canonical_bytes(document: JsonObject) -> bytes:
    _reject_floats(document)
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_hash(document: JsonObject) -> str:
    return sha256_hex(canonical_bytes(document))
