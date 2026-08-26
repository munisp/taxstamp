"""Client-neutral TigerBeetle transfer contract.

This module deliberately has no network client or credential handling. A deployment
adapter must implement the protocol using an approved, version-pinned client and return
the observed immutable transfer for every confirmation decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TigerBeetleCreateResult(StrEnum):
    CREATED = "created"
    EXISTS = "exists"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class TigerBeetleTransfer:
    transfer_id: str
    debit_account_id: str
    credit_account_id: str
    ledger_code: int
    transfer_code: int
    transfer_flags: int
    amount_minor: int
    timestamp: int


class TigerBeetleClient(Protocol):
    """A deployment adapter must look up and create transfers by the stable ID."""

    def lookup_transfer(self, transfer_id: str) -> TigerBeetleTransfer | None: ...

    def create_transfer(self, transfer: TigerBeetleTransfer) -> TigerBeetleCreateResult: ...
