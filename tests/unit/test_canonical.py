"""Canonical serialisation determinism."""

from __future__ import annotations

import pytest

from taxstamp.canonical import CanonicalisationError, canonical_bytes, canonical_hash

pytestmark = pytest.mark.unit


def test_key_order_does_not_change_the_hash() -> None:
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_value_change_changes_the_hash() -> None:
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_floats_are_rejected() -> None:
    with pytest.raises(CanonicalisationError):
        canonical_bytes({"amount": 1.1})


def test_non_ascii_is_escaped_stably() -> None:
    assert canonical_bytes({"name": "Ẹ"}) == canonical_bytes({"name": "Ẹ"})
    assert b"\\u" in canonical_bytes({"name": "Ẹ"})
