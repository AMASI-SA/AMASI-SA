"""Read-only eligibility for a driver's current work, not delivery history.

Use the synchronized canonical Salla order, never the assignment's last status
write. Parent/original status categories do not describe the current custom
status (Salla's delivering status may have a completed parent).
"""
from typing import Any

from store_delivery_domain import normalize_text


def _current_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        values: set[str] = set()
        for key in ("slug", "name", "name_ar", "label", "title", "customized"):
            values.update(_current_values(value.get(key)))
        return values or ({"unknown"} if value else set())
    text = normalize_text(value).casefold()
    if not text:
        return set()
    if text in {"delivering", "out_for_delivery", "جاري التوصيل"}:
        return {"delivering"}
    return {text}


def is_current_driver_delivery(order: dict[str, Any] | None) -> bool:
    if not isinstance(order, dict):
        return False
    snapshot = order.get("g47_salla_snapshot") or {}
    if not isinstance(snapshot, dict) or snapshot.get("requires_authoritative_refresh"):
        return False
    sources = order.get("raw_by_source") or {}
    if not isinstance(sources, dict):
        return False
    raw = sources.get("salla_direct") or {}
    if not isinstance(raw, dict):
        return False
    values: set[str] = set()
    for value in (order.get("order_status"), order.get("order_status_slug"),
                  raw.get("status"), raw.get("status_slug")):
        values.update(_current_values(value))
    # Historical normalized `shipping` is ambiguous on its own. A current
    # explicit delivering observation may disambiguate it; `shipped` never can.
    return values - {"shipping"} == {"delivering"}
