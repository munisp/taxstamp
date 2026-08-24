"""Batch integrity anchoring.

A Merkle root over the issued serials is computed locally and, when an anchoring
service is configured, submitted for external notarisation. Nothing claims to be
"registered on a blockchain" unless a configured service acknowledged the anchor.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from taxstamp.jsontypes import require_str
from taxstamp.providers.base import ProviderClient


def merkle_root(leaves: list[str]) -> str:
    """RFC 6962-style Merkle root with domain separation between leaf and node hashes."""
    if not leaves:
        raise ValueError("merkle root requires at least one leaf")
    level = [sha256(b"\x00" + leaf.encode("utf-8")).digest() for leaf in leaves]
    while len(level) > 1:
        nxt: list[bytes] = []
        for index in range(0, len(level) - 1, 2):
            nxt.append(sha256(b"\x01" + level[index] + level[index + 1]).digest())
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


@dataclass(frozen=True, slots=True)
class AnchorReceipt:
    root: str
    external_reference: str
    anchored_at: str


class AnchorService:
    def __init__(self, client: ProviderClient) -> None:
        self._client = client

    @property
    def configured(self) -> bool:
        return self._client.configured

    def anchor(self, *, batch_id: str, root: str) -> AnchorReceipt:
        self._client.require_configured()
        response = self._client.post_json("/v1/anchors", {"batch_id": batch_id, "root": root})
        return AnchorReceipt(
            root=require_str(response, "root"),
            external_reference=require_str(response, "reference"),
            anchored_at=require_str(response, "anchored_at"),
        )
