"""Merkle roots are order-sensitive and domain-separated."""

from __future__ import annotations

import pytest

from taxstamp.merkle import inclusion_proof, merkle_root, verify_inclusion_proof

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


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 8, 33])
def test_every_leaf_proves_its_inclusion(size: int) -> None:
    leaves = [f"leaf-{index}" for index in range(size)]
    root = merkle_root(leaves)
    for index, leaf in enumerate(leaves):
        assert verify_inclusion_proof(leaf=leaf, proof=inclusion_proof(leaves, index), root=root)


def test_a_proof_does_not_verify_for_a_leaf_outside_the_tree() -> None:
    leaves = [f"leaf-{index}" for index in range(6)]
    proof = inclusion_proof(leaves, 3)
    assert not verify_inclusion_proof(leaf="forged", proof=proof, root=merkle_root(leaves))


def test_a_proof_does_not_verify_against_a_later_root() -> None:
    leaves = [f"leaf-{index}" for index in range(6)]
    proof = inclusion_proof(leaves, 0)
    grown = merkle_root([*leaves, "leaf-6"])
    assert not verify_inclusion_proof(leaf=leaves[0], proof=proof, root=grown)


def test_an_index_outside_the_tree_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the tree"):
        inclusion_proof(["a", "b"], 2)


def test_an_unknown_proof_position_is_rejected() -> None:
    leaves = ["a", "b"]
    proof = inclusion_proof(leaves, 0)
    tampered = tuple(type(step)("middle", step.hash_hex) for step in proof)
    with pytest.raises(ValueError, match="unknown proof position"):
        verify_inclusion_proof(leaf="a", proof=tampered, root=merkle_root(leaves))
