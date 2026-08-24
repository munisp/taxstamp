"""The revocation filter must never miss a revoked serial."""

from __future__ import annotations

import base64

import pytest

from taxstamp.bloom import HASH_COUNT, MAX_BITS, MIN_BITS, BloomFilter, sized_bits

pytestmark = pytest.mark.unit

SECRET = "bloom-test-secret-" + "z" * 40
SERIALS = tuple(f"NG-ALC-2026-{index:08d}" for index in range(500))


def _filter(items: tuple[str, ...] = SERIALS) -> BloomFilter:
    return BloomFilter.build(items, bits=sized_bits(len(items)), secret=SECRET)


def test_every_member_is_reported_as_possibly_present() -> None:
    """The property the whole design rests on: no false negatives, ever."""
    built = _filter()
    for serial in SERIALS:
        assert built.probably_contains(serial, secret=SECRET) is True


def test_non_members_are_mostly_reported_absent() -> None:
    built = _filter()
    strangers = [f"NG-TOB-2027-{index:08d}" for index in range(500)]
    positives = sum(1 for serial in strangers if built.probably_contains(serial, secret=SECRET))
    # A false positive is safe (the device refuses and checks online) but should be rare.
    assert positives < 25


def test_a_different_key_cannot_query_the_filter() -> None:
    built = _filter()
    other = "another-bloom-secret-" + "y" * 40
    matched = sum(1 for serial in SERIALS if built.probably_contains(serial, secret=other))
    assert matched < len(SERIALS) // 2


def test_round_trip_through_the_wire_format_preserves_membership() -> None:
    built = _filter()
    decoded = BloomFilter.decode(built.encode(), bits=built.bits, hash_count=built.hash_count)
    assert decoded == built
    assert decoded.probably_contains(SERIALS[0], secret=SECRET) is True


def test_payload_of_the_wrong_size_is_refused() -> None:
    built = _filter()
    with pytest.raises(ValueError, match="payload"):
        BloomFilter.decode(built.encode(), bits=built.bits * 2, hash_count=built.hash_count)


def test_payload_that_is_not_base64_is_refused() -> None:
    with pytest.raises(ValueError, match="base64"):
        BloomFilter.decode("not base64 at all!", bits=MIN_BITS, hash_count=HASH_COUNT)


def test_truncated_payload_is_refused_rather_than_zero_padded() -> None:
    built = _filter()
    raw = base64.b64decode(built.encode())
    truncated = base64.b64encode(raw[:-1]).decode("ascii")
    with pytest.raises(ValueError, match="payload"):
        BloomFilter.decode(truncated, bits=built.bits, hash_count=built.hash_count)


def test_bit_count_is_bounded() -> None:
    assert sized_bits(0) == MIN_BITS
    assert sized_bits(10**9) == MAX_BITS
    with pytest.raises(ValueError, match="bits"):
        BloomFilter.build(SERIALS, bits=MIN_BITS // 2, secret=SECRET)
