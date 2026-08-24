"""A deterministic, keyed Bloom filter for offline revocation checks.

An offline device cannot query the register, so it is handed a compact filter of the
revoked serials. The filter is one-sided in the safe direction: it may report a serial
as *possibly revoked* when it is not, but it can never report a revoked serial as clean.
A device that gets a positive answer must therefore refuse the stamp or re-check online;
it must never treat a negative answer as proof of anything beyond "not revoked".

Bit positions are derived with a keyed hash so that possession of a filter does not let
an attacker mint serials that collide with revoked ones by construction.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import math
from dataclasses import dataclass
from hashlib import sha256

MIN_BITS = 1_024
MAX_BITS = 1 << 22
HASH_COUNT = 7


def sized_bits(expected_items: int, *, false_positive_rate: float = 0.001) -> int:
    """Bit length that holds ``expected_items`` at the requested error rate."""
    if expected_items <= 0:
        return MIN_BITS
    if not 0 < false_positive_rate < 1:
        raise ValueError("false_positive_rate must be between 0 and 1")
    ideal = -(expected_items * math.log(false_positive_rate)) / (math.log(2) ** 2)
    rounded = 1 << max(int(ideal - 1).bit_length(), MIN_BITS.bit_length() - 1)
    return min(max(rounded, MIN_BITS), MAX_BITS)


def _positions(item: str, *, bits: int, hash_count: int, secret: str) -> tuple[int, ...]:
    digest = hmac.new(secret.encode("utf-8"), f"bloom:{item}".encode(), sha256).digest()
    width = max((bits - 1).bit_length(), 1)
    byte_span = (width + 7) // 8
    if hash_count * byte_span > len(digest):
        raise ValueError("filter parameters need more hash material than one digest provides")
    return tuple(
        int.from_bytes(digest[index * byte_span : (index + 1) * byte_span], "big") % bits
        for index in range(hash_count)
    )


@dataclass(frozen=True, slots=True)
class BloomFilter:
    """An immutable filter. ``build`` is the only way to create a populated one."""

    bits: int
    hash_count: int
    payload: bytes

    @classmethod
    def build(
        cls, items: tuple[str, ...], *, bits: int, secret: str, hash_count: int = HASH_COUNT
    ) -> BloomFilter:
        if bits % 8 != 0 or not MIN_BITS <= bits <= MAX_BITS:
            raise ValueError(f"bits must be a multiple of 8 between {MIN_BITS} and {MAX_BITS}")
        buffer = bytearray(bits // 8)
        for item in items:
            for position in _positions(item, bits=bits, hash_count=hash_count, secret=secret):
                buffer[position // 8] |= 1 << (position % 8)
        return cls(bits=bits, hash_count=hash_count, payload=bytes(buffer))

    @classmethod
    def decode(cls, encoded: str, *, bits: int, hash_count: int) -> BloomFilter:
        try:
            payload = base64.b64decode(encoded, validate=True)
        except binascii.Error as exc:
            raise ValueError("encoded filter is not valid base64") from exc
        if len(payload) * 8 != bits:
            raise ValueError("filter payload length does not match its declared bit count")
        return cls(bits=bits, hash_count=hash_count, payload=payload)

    def encode(self) -> str:
        return base64.b64encode(self.payload).decode("ascii")

    def probably_contains(self, item: str, *, secret: str) -> bool:
        """True when every bit for ``item`` is set: possibly present, never certainly."""
        return all(
            self.payload[position // 8] >> (position % 8) & 1
            for position in _positions(item, bits=self.bits, hash_count=self.hash_count, secret=secret)
        )
