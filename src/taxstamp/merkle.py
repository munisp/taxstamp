"""Merkle trees with inclusion proofs.

Leaf and interior hashes are domain-separated as in RFC 6962, so a leaf digest can
never be replayed as an interior node. An odd node at a level is promoted unchanged
rather than duplicated, which avoids the second-preimage ambiguity of duplication.

Proof generation follows exactly the same level-by-level construction as the root, so
a verifier that only has a leaf, its index, the tree size and the proof recomputes the
published root without access to the database.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from hashlib import sha256


def _leaf_hash(leaf: str) -> bytes:
    return sha256(b"\x00" + leaf.encode("utf-8")).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return sha256(b"\x01" + left + right).digest()


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        raise ValueError("merkle root requires at least one leaf")
    level = [_leaf_hash(leaf) for leaf in leaves]
    while len(level) > 1:
        level = _next_level(level)
    return level[0].hex()


def _next_level(level: list[bytes]) -> list[bytes]:
    nxt: list[bytes] = []
    for index in range(0, len(level) - 1, 2):
        nxt.append(_node_hash(level[index], level[index + 1]))
    if len(level) % 2 == 1:
        nxt.append(level[-1])
    return nxt


@dataclass(frozen=True, slots=True)
class ProofStep:
    """One sibling on the path from a leaf to the root."""

    position: str
    hash_hex: str


def inclusion_proof(leaves: list[str], index: int) -> tuple[ProofStep, ...]:
    """The sibling hashes needed to recompute the root from ``leaves[index]``."""
    if not leaves:
        raise ValueError("inclusion proof requires at least one leaf")
    if not 0 <= index < len(leaves):
        raise ValueError("leaf index is outside the tree")
    level = [_leaf_hash(leaf) for leaf in leaves]
    position = index
    steps: list[ProofStep] = []
    while len(level) > 1:
        paired = len(level) - (len(level) % 2)
        if position < paired:
            if position % 2 == 0:
                steps.append(ProofStep("right", level[position + 1].hex()))
            else:
                steps.append(ProofStep("left", level[position - 1].hex()))
        # A promoted odd node has no sibling at this level and contributes no step.
        level = _next_level(level)
        position = position // 2 if position < paired else len(level) - 1
    return tuple(steps)


def verify_inclusion_proof(*, leaf: str, proof: tuple[ProofStep, ...], root: str) -> bool:
    """Recompute the root from a leaf and its proof, in constant-time comparison."""
    current = _leaf_hash(leaf)
    for step in proof:
        sibling = bytes.fromhex(step.hash_hex)
        if step.position == "right":
            current = _node_hash(current, sibling)
        elif step.position == "left":
            current = _node_hash(sibling, current)
        else:
            raise ValueError(f"unknown proof position: {step.position!r}")
    return hmac.compare_digest(current.hex(), root)
