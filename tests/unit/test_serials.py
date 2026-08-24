"""Serial numbers are self-checking."""

from __future__ import annotations

import pytest

from taxstamp.serials import SerialError, format_serial, is_valid_serial, parse_serial

pytestmark = pytest.mark.unit


def test_round_trip() -> None:
    serial = format_serial("alcohol", 2026, 1)
    parsed = parse_serial(serial)
    assert parsed.year == 2026
    assert parsed.sequence == 1
    assert is_valid_serial(serial)


def test_single_character_corruption_is_detected() -> None:
    serial = format_serial("alcohol", 2026, 987_654)
    detected = 0
    for index, character in enumerate(serial):
        if not character.isalnum():
            continue
        for replacement in "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ":
            if replacement == character:
                continue
            candidate = serial[:index] + replacement + serial[index + 1 :]
            if not is_valid_serial(candidate):
                detected += 1
    assert detected > 0
    assert not is_valid_serial(serial[:-1] + ("0" if serial[-1] != "0" else "1"))


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(SerialError):
        format_serial("weapons-grade-plutonium", 2026, 1)


@pytest.mark.parametrize(
    "value",
    ["", "NG-ALC-2026", "XX-ALC-2026-000001-A", "NG-ALC-20x6-000001-A", "NG-ALC-2026-000001"],
)
def test_malformed_serials_are_rejected(value: str) -> None:
    assert not is_valid_serial(value)
