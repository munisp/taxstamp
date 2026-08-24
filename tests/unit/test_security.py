"""Signature and credential primitives."""

from __future__ import annotations

import datetime as dt

import pytest

from taxstamp.security import (
    SignatureError,
    SignedRequest,
    derive_secure_code,
    generate_token,
    hash_secure_code,
    hash_token,
    sign_request,
    verify_signed_request,
)

pytestmark = pytest.mark.unit
SECRET = "unit-test-secret-" + "z" * 40
NOW = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)


def test_token_hash_is_keyed_and_not_reversible() -> None:
    token = generate_token()
    assert token not in hash_token(token, secret=SECRET)
    assert hash_token(token, secret=SECRET) != hash_token(token, secret=SECRET + "x")


def test_signature_round_trip() -> None:
    body = {"serial": "NG-ALC-2026-000001-A", "nonce": "abcdefgh"}
    signature = sign_request(body, NOW, secret=SECRET)
    verify_signed_request(
        SignedRequest(body=body, signature=signature, timestamp=NOW),
        secret=SECRET,
        now=NOW,
        max_skew_seconds=300,
    )


def test_tampered_body_fails() -> None:
    body = {"serial": "NG-ALC-2026-000001-A"}
    signature = sign_request(body, NOW, secret=SECRET)
    with pytest.raises(SignatureError):
        verify_signed_request(
            SignedRequest(body={"serial": "NG-ALC-2026-000002-B"}, signature=signature, timestamp=NOW),
            secret=SECRET,
            now=NOW,
            max_skew_seconds=300,
        )


def test_unsignable_body_fails_instead_of_raising_canonicalisation_error() -> None:
    body = {"serial": "NG-ALC-2026-000001-A"}
    signature = sign_request(body, NOW, secret=SECRET)
    with pytest.raises(SignatureError):
        verify_signed_request(
            SignedRequest(body={"amount_minor": 335938.0}, signature=signature, timestamp=NOW),
            secret=SECRET,
            now=NOW,
            max_skew_seconds=300,
        )


def test_stale_timestamp_fails() -> None:
    body = {"serial": "NG-ALC-2026-000001-A"}
    signature = sign_request(body, NOW, secret=SECRET)
    with pytest.raises(SignatureError):
        verify_signed_request(
            SignedRequest(body=body, signature=signature, timestamp=NOW),
            secret=SECRET,
            now=NOW + dt.timedelta(seconds=301),
            max_skew_seconds=300,
        )


def test_secure_code_derivation_is_deterministic_and_key_dependent() -> None:
    serial = "NG-ALC-2026-000001-A"
    assert derive_secure_code(serial, secret=SECRET) == derive_secure_code(serial, secret=SECRET)
    assert derive_secure_code(serial, secret=SECRET) != derive_secure_code(serial, secret=SECRET + "x")
    code = derive_secure_code(serial, secret=SECRET)
    assert hash_secure_code(serial, code, secret=SECRET) != code
