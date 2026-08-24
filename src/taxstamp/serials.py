"""Serial number formatting and validation.

Serials are ``NG-<CAT>-<YEAR>-<SEQ>-<CHECK>``. The check character is a Luhn mod-N
character over the payload, so a mistyped serial is rejected before any lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SERIAL_RE = re.compile(r"^NG-(?P<cat>[A-Z]{3})-(?P<year>\d{4})-(?P<seq>\d{10})-(?P<check>[0-9A-Z])$")
CATEGORY_CODES: dict[str, str] = {
    "tobacco": "TOB",
    "alcohol": "ALC",
    "pharmaceuticals": "PHA",
    "beverages": "BEV",
}


class SerialError(ValueError):
    pass


def category_code(product_category: str) -> str:
    try:
        return CATEGORY_CODES[product_category]
    except KeyError as exc:
        raise SerialError(f"unknown product category: {product_category!r}") from exc


def _check_character(payload: str) -> str:
    """Luhn mod-34 check character over the serial payload."""
    factor = 2
    total = 0
    modulus = len(ALPHABET)
    for char in reversed(payload):
        if char == "-":
            continue
        try:
            code_point = ALPHABET.index(char)
        except ValueError as exc:
            raise SerialError(f"invalid serial character: {char!r}") from exc
        addend = factor * code_point
        factor = 1 if factor == 2 else 2
        addend = (addend // modulus) + (addend % modulus)
        total += addend
    remainder = total % modulus
    return ALPHABET[(modulus - remainder) % modulus]


def format_serial(product_category: str, year: int, sequence: int) -> str:
    if sequence <= 0:
        raise SerialError("sequence must be positive")
    if sequence > 9_999_999_999:
        raise SerialError("sequence exceeds the serial format capacity")
    if not 2000 <= year <= 9999:
        raise SerialError("year out of range")
    payload = f"NG-{category_code(product_category)}-{year:04d}-{sequence:010d}"
    return f"{payload}-{_check_character(payload)}"


@dataclass(frozen=True, slots=True)
class ParsedSerial:
    category_code: str
    year: int
    sequence: int


def parse_serial(serial: str) -> ParsedSerial:
    match = SERIAL_RE.match(serial)
    if match is None:
        raise SerialError("serial does not match the required format")
    payload = serial.rsplit("-", 1)[0]
    if _check_character(payload) != match.group("check"):
        raise SerialError("serial check character is invalid")
    return ParsedSerial(
        category_code=match.group("cat"),
        year=int(match.group("year")),
        sequence=int(match.group("seq")),
    )


def is_valid_serial(serial: str) -> bool:
    try:
        parse_serial(serial)
    except SerialError:
        return False
    return True
