"""Pinned ISO 4217 input vocabulary; no exchange rates or financial policy."""
import json
from pathlib import Path

CURRENCY_CATALOG = json.loads(Path(__file__).with_name("accounting_currency_codes.json").read_text(encoding="utf-8"))
CURRENCY_CODES = frozenset(CURRENCY_CATALOG["codes"])


def validate_currency_code(value: str) -> str:
    """Keep existing case-insensitive codes; reject anything outside the list."""
    if not isinstance(value, str) or value.upper() not in CURRENCY_CODES:
        raise ValueError("accounting_currency_not_supported")
    return value.upper()
