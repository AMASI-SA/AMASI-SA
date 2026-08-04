"""Stable barcode identities for physical preparation pieces.

The barcode is intentionally opaque.  It contains only the deterministic
piece UUID, while the server resolves merchant, order, file and service data
from Mezan's own piece registry.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

BARCODE_PREFIX = "MEZAN-PIECE:"
_PIECE_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def preparation_piece_id(
    *,
    user_id: Any,
    batch_id: Any,
    order_number: Any,
    order_item_id: Any,
    unit_index: Any,
) -> str:
    """Return the same durable id used by the piece materialisation engine."""
    raw = (
        f"mezan-piece:{_text(user_id)}:{_text(batch_id)}:"
        f"{_text(order_number)}:{_text(order_item_id)}:{int(unit_index)}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex


def preparation_piece_barcode(**identity: Any) -> str:
    return f"{BARCODE_PREFIX}{preparation_piece_id(**identity)}"


def parse_preparation_piece_barcode(value: Any) -> str | None:
    """Accept a Mezan QR payload or a raw scanner-entered piece UUID."""
    raw = _text(value)
    if raw.upper().startswith(BARCODE_PREFIX):
        raw = raw[len(BARCODE_PREFIX) :].strip()
    return raw.lower() if _PIECE_ID_RE.fullmatch(raw) else None


__all__ = [
    "BARCODE_PREFIX",
    "parse_preparation_piece_barcode",
    "preparation_piece_barcode",
    "preparation_piece_id",
]
