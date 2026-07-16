"""Explicit gift enrichment using authoritative Salla Order Details.

This module never infers gift status from product names, customer text, notes,
or options. It only accepts explicit provider fields/labels and updates Mezan
storage. It performs no Qoyod calls and no Salla writes.
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


def extract_explicit_gift_signal(value: Any, *, path: str = "raw", depth: int = 0) -> dict[str, Any]:
    """Return a positive/negative explicit gift signal or unknown.

    Positive signals take priority. A negative signal is accepted only from an
    explicit gift boolean key. Generic ``type`` fields are intentionally ignored.
    """
    if depth > 10:
        return {"is_gift": None, "path": None, "value": None}

    positive: Optional[dict[str, Any]] = None
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

    for raw_key, child in value.items():
        key = _norm(raw_key)
        child_path = f"{path}.{raw_key}"

        if key in {_norm(item) for item in _EXPLICIT_KEYS}:
            candidate = _named_value(child)
            bool_candidate = _bool_value(candidate)
            if bool_candidate is True:
                return {"is_gift": True, "path": child_path, "value": candidate}
            if _norm(candidate) in {_norm(item) for item in _GIFT_LABELS}:
                return {"is_gift": True, "path": child_path, "value": candidate}
            if bool_candidate is False and negative is None:
                negative = {"is_gift": False, "path": child_path, "value": candidate}

        signal = extract_explicit_gift_signal(child, path=child_path, depth=depth + 1)
        if signal.get("is_gift") is True:
            return signal
        if signal.get("is_gift") is False and negative is None:
            negative = signal

    return negative or {"is_gift": None, "path": None, "value": None}


async def enrich_single_order_gift(db: Any, *, user_id: str, order_number: str) -> dict[str, Any]:
    """Resync one order from Salla details and persist explicit gift status."""
    normalized = str(order_number or "").strip()
    if not normalized:
        return {"ok": False, "error": "missing_order_number"}

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
        {"_id": 0, "raw_by_source.salla_direct": 1, "is_gift": 1},
    )
    raw_by_source = (row or {}).get("raw_by_source") or {}
    raw = raw_by_source.get("salla_direct") if isinstance(raw_by_source, dict) else None
    raw = raw if isinstance(raw, dict) else {}

    signal = extract_explicit_gift_signal(raw)
    is_gift = signal.get("is_gift")
    if is_gift is None:
        return {
            "ok": True,
            "found": True,
            "order_number": normalized,
            "is_gift": None,
            "explicit_signal_found": False,
            "message": "Salla Order Details did not expose an explicit gift field.",
        }

    await db.unified_orders.update_one(
        {"user_id": str(user_id), "order_number": normalized},
        {"$set": {
            "is_gift": bool(is_gift),
            "gift_source": "salla_order_details",
            "gift_source_path": str(signal.get("path") or "")[:240],
        }},
    )
    return {
        "ok": True,
        "found": True,
        "order_number": normalized,
        "is_gift": bool(is_gift),
        "explicit_signal_found": True,
        "source_path": signal.get("path"),
    }
