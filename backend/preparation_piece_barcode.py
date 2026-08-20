"""Stable barcode identities for physical preparation pieces.

The barcode is intentionally opaque. It contains only the deterministic piece
UUID. The identity is derived from the merchant + Salla order + Salla order
item + physical unit index, so moving/re-uploading the same order never creates
a second barcode for the same physical piece.

``batch_id`` is accepted for backwards call-site compatibility but is
intentionally excluded from the identity seed. A preparation file is a mutable
workflow container; it must never participate in the permanent identity of a
physical order piece.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

BARCODE_PREFIX = "MEZAN-PIECE:"
_PIECE_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def preparation_piece_identity_key(
    *,
    user_id: Any,
    order_number: Any,
    order_item_id: Any,
    unit_index: Any,
) -> str:
    """Return the permanent logical identity key for one physical order unit."""
    merchant = _text(user_id)
    order = _text(order_number)
    item = _text(order_item_id)
    unit = int(unit_index)
    if not merchant or not order or not item or unit <= 0:
        raise ValueError("invalid_preparation_piece_identity")
    return f"mezan-piece-v2:{merchant}:{order}:{item}:{unit}"


def preparation_piece_id(
    *,
    user_id: Any,
    batch_id: Any = None,
    order_number: Any,
    order_item_id: Any,
    unit_index: Any,
) -> str:
    """Return a durable id that is unchanged when the same piece is re-filed."""
    del batch_id  # A file/batch must never change the physical piece identity.
    raw = preparation_piece_identity_key(
        user_id=user_id,
        order_number=order_number,
        order_item_id=order_item_id,
        unit_index=unit_index,
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
    "preparation_piece_identity_key",
]
