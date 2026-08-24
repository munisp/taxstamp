"""Credential hashing and request signature verification.

Secrets are never logged, tokens are stored only as keyed hashes, and every
comparison of secret material uses a constant-time primitive.
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
from dataclasses import dataclass
from hashlib import sha256

from taxstamp.canonical import CanonicalisationError, canonical_bytes
from taxstamp.clock import ensure_utc
from taxstamp.jsontypes import JsonObject

TOKEN_BYTES = 32
SECURE_CODE_BYTES = 10


def generate_token() -> str:
    """Return a fresh opaque bearer token. Shown once, never stored in clear text."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str, *, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), sha256).hexdigest()


def tokens_match(candidate_hash: str, stored_hash: str) -> bool:
    return hmac.compare_digest(candidate_hash, stored_hash)


def generate_secure_code() -> str:
    """Human-transcribable secret printed on a stamp; verified against a keyed hash."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def derive_secure_code(serial: str, *, secret: str) -> str:
    """Deterministically derive a stamp's secure code.

    The code is never stored: it is derived for printing and re-derived when an
    authorised operator re-exports a batch, while verification compares keyed hashes.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    digest = hmac.new(secret.encode("utf-8"), f"code:{serial}".encode(), sha256).digest()
    return "".join(alphabet[byte % len(alphabet)] for byte in digest[:12])


def hash_secure_code(serial: str, code: str, *, secret: str) -> str:
    message = f"{serial}:{code}".encode()
    return hmac.new(secret.encode("utf-8"), message, sha256).hexdigest()


def secure_codes_match(candidate: str, stored: str) -> bool:
    return hmac.compare_digest(candidate, stored)


def document_hash(document: JsonObject) -> str:
    """Content hash of a canonicalised document, for export and checkpoint evidence."""
    return sha256(canonical_bytes(document)).hexdigest()


def sign_document(document: JsonObject, *, secret: str, purpose: str) -> str:
    """Sign a released document under a purpose-separated key.

    The purpose string keeps an export signature from being valid as a checkpoint
    signature, or as a request signature, even though one secret backs all of them.
    """
    key = hmac.new(secret.encode("utf-8"), f"purpose:{purpose}".encode(), sha256).digest()
    return hmac.new(key, canonical_bytes(document), sha256).hexdigest()


def document_signature_matches(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)


class SignatureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SignedRequest:
    body: JsonObject
    signature: str
    timestamp: dt.datetime


def signing_payload(body: JsonObject, timestamp: dt.datetime) -> bytes:
    aware = ensure_utc(timestamp)
    return b"v1|" + str(int(aware.timestamp())).encode() + b"|" + canonical_bytes(body)


def sign_request(body: JsonObject, timestamp: dt.datetime, *, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), signing_payload(body, timestamp), sha256).hexdigest()


def verify_signed_request(
    request: SignedRequest,
    *,
    secret: str,
    now: dt.datetime,
    max_skew_seconds: int,
) -> None:
    """Verify signature then freshness. Raises ``SignatureError`` on any failure."""
    try:
        expected = sign_request(request.body, request.timestamp, secret=secret)
    except CanonicalisationError as exc:
        raise SignatureError(f"body is not signable: {exc}") from exc
    if not hmac.compare_digest(expected, request.signature):
        raise SignatureError("signature mismatch")
    skew = abs((ensure_utc(now) - ensure_utc(request.timestamp)).total_seconds())
    if skew > max_skew_seconds:
        raise SignatureError(f"timestamp skew {int(skew)}s exceeds {max_skew_seconds}s")
