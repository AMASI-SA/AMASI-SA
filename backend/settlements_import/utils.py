"""Small utilities shared by parsers."""
from __future__ import annotations

import re
from typing import Any


def to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("\u200e", "").replace("\u200f", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_str(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s


_PAYMENT_METHOD_MAP = {
    "مدى": "mada",
    "mada": "mada",
    "البطاقة الائتمانية": "credit_card",
    "credit card": "credit_card",
    "creditcard": "credit_card",
    "visa": "credit_card",
    "mastercard": "credit_card",
    "apple pay": "applepay",
    "applepay": "applepay",
    "tamara": "tamara",
    "تمارا": "tamara",
    "tabby": "tabby",
    "تابي": "tabby",
    "stc pay": "stcpay",
    "stcpay": "stcpay",
}


def normalize_payment_method(value: str) -> str:
    """Return canonical key matching what `payment_methods.py` produces.

    We can't import `payment_methods.resolve_account_key` from here
    because that module is async and account-aware. The mapping here
    only converts the Arabic display strings inside Salla invoices
    to the canonical keys we know the rest of the system uses.
    """
    if not value:
        return ""
    s = value.strip().lower().replace("ـ", "")
    if s in _PAYMENT_METHOD_MAP:
        return _PAYMENT_METHOD_MAP[s]
    # Try without case
    for k, v in _PAYMENT_METHOD_MAP.items():
        if k.lower() == s:
            return v
        if k in value:
            return v
    return re.sub(r"[^a-z0-9_]+", "_", s).strip("_")
