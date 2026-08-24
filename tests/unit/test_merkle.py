"""Merkle roots are order-sensitive and domain-separated."""

from __future__ import annotations

import pytest

from taxstamp.providers.anchor import merkle_root

pytestmark = pytest.mark.unit


def test_single_leaf_root_is_stable() -> None:
    assert merkle_root(["a"]) == merkle_root(["a"])


def test_order_changes_the_root() -> None:
    assert merkle_root(["a", "b"]) != merkle_root(["b", "a"])


def test_leaf_and_node_hashes_are_separated() -> None:
    assert merkle_root(["a", "b"]) != merkle_root([merkle_root(["a"]) + merkle_root(["b"])])


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one leaf"):
        merkle_root([])
