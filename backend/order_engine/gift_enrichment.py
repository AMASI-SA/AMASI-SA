"""Explicit gift enrichment using durable Mezan fields and Salla details.

This module never infers gift status from product names, customer text, notes,
or arbitrary options. It only accepts explicit provider fields/labels and updates
Mezan storage. It performs no Qoyod calls and no Salla writes.
"""
from __future__ import annotations

from typing import Any, Optional

from salla_integration.sync import resync_single_order


_GIFT_LABELS = {
    "gift",
    "gift order",
    "is gift",
    "هدية",
    "هديه",
    "طلب هدية",
    "طلب هديه",
    "طلب كهدية",
    "طلب كهديه",
    "إهداء",
    "اهداء",
}
_EXPLICIT_KEYS = {
    "is_gift",
    "gift",
    "gift_order",
    "order_type",
    "order_kind",
    "type_of_order",
}
_ORDER_TYPE_FIELD_LABELS = {
    "order type",
    "type of order",
    "نوع الطلب",
}
_LABEL_KEYS = ("name", "label", "title", "key", "field_name")
_VALUE_KEYS = ("value", "selected", "answer", "text", "display_value")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _norm(value)
    if text in {"true", "1", "yes", "enabled"}:
        return True
    if text in {"false", "0", "no", "disabled"}:
        return False
    return None


def _named_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("name", "label", "title", "value", "slug", "code", "type"):
        if value.get(key) not in (None, "", [], {}):
            return value.get(key)
    return None


def _explicit_label_value_signal(value: dict[str, Any], *, path: str) -> dict[str, Any] | None:
    """Detect explicit custom-field structures such as:

    {"name": "نوع الطلب", "value": "طلب كهدية"}

    This is still provider-explicit data; it is not inferred from notes,
    product names, or arbitrary option text.
    """
    field_label = None
    field_label_key = None
    for key in _LABEL_KEYS:
        candidate = value.get(key)
        if candidate not in (None, "", [], {}):
            field_label = _named_value(candidate)
            field_label_key = key
            break

    if _norm(field_label) not in {_norm(item) for item in _ORDER_TYPE_FIELD_LABELS}:
        return None

    for key in _VALUE_KEYS:
        candidate = value.get(key)
        if candidate in (None, "", [], {}):
            continue
        candidate = _named_value(candidate)
        if _norm(candidate) in {_norm(item) for item in _GIFT_LABELS}:
            return {
                "is_gift": True,
                "path": f"{path}.{key}",
                "value": candidate,
                "label_path": f"{path}.{field_label_key}",
                "label": field_label,
            }

    return None


def extract_explicit_gift_signal(value: Any, *, path: str = "raw", depth: int = 0) -> dict[str, Any]:
    """Return a positive/negative explicit gift signal or unknown.

    Positive signals take priority. A negative signal is accepted only from an
    explicit gift boolean key. Generic ``type`` fields are intentionally ignored.
    """
    if depth > 10:
        return {"is_gift": None, "path": None, "value": None}

    negative: Optional[dict[str, Any]] = None

    if isinstance(value, list):
        for index, child in enumerate(value[:100]):
            signal = extract_explicit_gift_signal(child, path=f"{path}[{index}]", depth=depth + 1)
            if signal.get("is_gift") is True:
                return signal
            if signal.get("is_gift") is False and negative is None:
                negative = signal
        return negative or {"is_gift": None, "path": None, "value": None}

    if not isinstance(value, dict):
        return {"is_gift": None, "path": None, "value": None}

    pair_signal = _explicit_label_value_signal(value, path=path)
    if pair_signal is not None:
        return pair_signal

    normalized_explicit_keys = {_norm(item) for item in _EXPLICIT_KEYS}
    normalized_gift_labels = {_norm(item) for item in _GIFT_LABELS}

    for raw_key, child in value.items():
        key = _norm(raw_key)
        child_path = f"{path}.{raw_key}"

        if key in normalized_explicit_keys:
            candidate = _named_value(child)
            bool_candidate = _bool_value(candidate)
            if bool_candidate is True:
                return {"is_gift": True, "path": child_path, "value": candidate}
            if _norm(candidate) in normalized_gift_labels:
                return {"is_gift": True, "path": child_path, "value": candidate}
            if bool_candidate is False and negative is None:
                negative = {"is_gift": False, "path": child_path, "value": candidate}

        signal = extract_explicit_gift_signal(child, path=child_path, depth=depth + 1)
        if signal.get("is_gift") is True:
            return signal
        if signal.get("is_gift") is False and negative is None:
            negative = signal

    return negative or {"is_gift": None, "path": None, "value": None}


async def _stored_order_signal(db: Any, *, user_id: str, order_number: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read explicit gift fields already normalized at the document root."""
    row = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": str(order_number)},
        {
            "_id": 0,
            "is_gift": 1,
            "gift": 1,
            "gift_order": 1,
            "order_type": 1,
            "order_kind": 1,
            "type_of_order": 1,
            "raw_by_source.salla_direct": 1,
        },
    )
    if not isinstance(row, dict):
        return None, {}

    root_signal = extract_explicit_gift_signal(
        {
            key: row.get(key)
            for key in (
                "is_gift",
                "gift",
                "gift_order",
                "order_type",
                "order_kind",
                "type_of_order",
            )
            if row.get(key) not in (None, "", [], {})
        },
        path="unified_orders",
    )
    return root_signal, row


async def enrich_single_order_gift(db: Any, *, user_id: str, order_number: str) -> dict[str, Any]:
    """Persist explicit gift status, preferring durable stored order fields."""
    normalized = str(order_number or "").strip()
    if not normalized:
        return {"ok": False, "error": "missing_order_number"}

    # First authority: the normalized root fields already stored in unified_orders.
    # Salla exports expose `order_type = هدية`; no API call is needed when present.
    root_signal, row = await _stored_order_signal(
        db,
        user_id=str(user_id),
        order_number=normalized,
    )
    if row and root_signal and root_signal.get("is_gift") is not None:
        is_gift = bool(root_signal.get("is_gift"))
        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": normalized},
            {"$set": {
                "is_gift": is_gift,
                "gift_source": "unified_orders_order_type",
                "gift_source_path": str(root_signal.get("path") or "")[:240],
            }},
        )
        return {
            "ok": True,
            "found": True,
            "order_number": normalized,
            "is_gift": is_gift,
            "explicit_signal_found": True,
            "source": "unified_orders",
            "source_path": root_signal.get("path"),
        }

    # Fallback only when the durable root fields do not contain an explicit signal.
    resync = await resync_single_order(db, str(user_id), normalized)
    if not resync.get("ok") or not resync.get("found"):
        return {
            "ok": False,
            "order_number": normalized,
            "stage": "resync_single_order",
            "resync": resync,
        }

    row = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": normalized},
        {
            "_id": 0,
            "raw_by_source.salla_direct": 1,
            "is_gift": 1,
            "gift": 1,
            "gift_order": 1,
            "order_type": 1,
            "order_kind": 1,
            "type_of_order": 1,
        },
    )

    # Re-check root fields because the resync mapper may have populated order_type.
    root_signal = extract_explicit_gift_signal(
        {
            key: (row or {}).get(key)
            for key in (
                "is_gift",
                "gift",
                "gift_order",
                "order_type",
                "order_kind",
                "type_of_order",
            )
            if (row or {}).get(key) not in (None, "", [], {})
        },
        path="unified_orders",
    )
    if root_signal.get("is_gift") is not None:
        signal = root_signal
        source = "unified_orders"
    else:
        raw_by_source = (row or {}).get("raw_by_source") or {}
        raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        signal = extract_explicit_gift_signal(raw)
        source = "salla_order_details"

    is_gift = signal.get("is_gift")
    if is_gift is None:
        return {
            "ok": True,
            "found": True,
            "order_number": normalized,
            "is_gift": None,
            "explicit_signal_found": False,
            "message": "No explicit gift field was found in stored order_type or Salla Order Details.",
        }

    await db.unified_orders.update_one(
        {"user_id": str(user_id), "order_number": normalized},
        {"$set": {
            "is_gift": bool(is_gift),
            "gift_source": source,
            "gift_source_path": str(signal.get("path") or "")[:240],
        }},
    )
    return {
        "ok": True,
        "found": True,
        "order_number": normalized,
        "is_gift": bool(is_gift),
        "explicit_signal_found": True,
        "source": source,
        "source_path": signal.get("path"),
    }
