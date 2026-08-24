"""Batch integrity anchoring.

A Merkle root over the issued serials is computed locally and, when an anchoring
service is configured, submitted for external notarisation. Nothing claims to be
"registered on a blockchain" unless a configured service acknowledged the anchor.
"""

from __future__ import annotations

from dataclasses import dataclass

from taxstamp.jsontypes import require_str
from taxstamp.merkle import merkle_root
from taxstamp.providers.base import ProviderClient

__all__ = ["AnchorReceipt", "AnchorService", "merkle_root"]


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
