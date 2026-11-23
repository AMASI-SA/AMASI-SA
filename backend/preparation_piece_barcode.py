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
import hashlib
import json
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
    bundle_supplier_reference: dict | None = None,
) -> str:
    """Return a durable id that is unchanged when the same piece is re-filed."""
    del batch_id  # A file/batch must never change the physical piece identity.
    if bundle_supplier_reference is not None:
        reference = bundle_supplier_reference
        if (not isinstance(reference, dict) or type(reference.get("version")) is not int
                or reference["version"] != 1 or type(reference.get("leaf_unit_index")) is not int
                or reference["leaf_unit_index"] < 1 or type(unit_index) is not int or unit_index < 1
                or any(not isinstance(reference.get(key), str) or not reference[key].strip()
                       for key in ("unit_id", "plan_id", "leaf_id", "leaf_fingerprint"))):
            raise ValueError("invalid_preparation_bundle_leaf_identity")
        raw = {"owner": _text(user_id), "order": _text(order_number), "line": _text(order_item_id),
               "sale_unit": unit_index, "unit_id": reference["unit_id"],
               "leaf_id": reference["leaf_id"], "leaf_unit_index": reference["leaf_unit_index"]}
        if not all(raw[key] for key in ("owner", "order", "line")):
            raise ValueError("invalid_preparation_bundle_leaf_identity")
        digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str).encode()).hexdigest()
        return uuid.uuid5(uuid.NAMESPACE_URL, "mezan-bundle-supplier-piece-v1:" + digest).hex
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
