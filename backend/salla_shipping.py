"""Current outbound shipping facts, independent of historical shipment order.

Only provider-supplied identity and timestamps are observations. Sparse order
responses are not evidence that a carrier changed. This module performs no I/O.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shipping_companies import normalize_shipping_company

CURRENT_SHIPPING = "salla_shipping_current"
IDENTITY_FIELDS = {
    "shipping_company": "company_name", "shipping_company_code": "company_code",
    "shipping_company_logo": "company_logo", "shipping_method": "method",
}
SHIPMENT_FIELDS = {
    "salla_shipment_id": "shipment_id", "tracking_number": "tracking_number",
    "shipping_number": "tracking_number",
    "tracking_url": "tracking_url", "shipping_label_url": "label_url",
    "shipping_status": "status", "shipment_status": "status",
}
SHIPPING_FIELDS = {*IDENTITY_FIELDS, *SHIPMENT_FIELDS, "shipping_number"}
CANCELLED = {"cancelled", "canceled", "void", "deleted"}
RETURNS = {"return", "return_shipment", "reverse"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    return str(value).strip() or None


def _first(*values: Any) -> str | None:
    return next((text for value in values if (text := _text(value))), None)


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        value = _first(*(value.get(k) for k in ("name", "name_ar", "label", "title", "display_name")))
    value = _text(value)
    return value if value and not value.replace(".", "", 1).isdigit() else None


def _status(value: Any) -> str | None:
    obj = _dict(value)
    return (_first(obj.get("slug"), obj.get("code"), obj.get("name"), value) or "").casefold() or None


def provider_time(value: Any) -> str | None:
    obj = _dict(value)
    zone = obj.get("timezone")
    value = obj.get("date") if obj else value
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(str(zone or "Asia/Riyadh")))
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, KeyError):
        return None


def _media(value: Any) -> str | None:
    if isinstance(value, list):
        return next((url for item in value if (url := _media(item))), None)
    if isinstance(value, dict):
        return next((url for key in ("url", "label_url", "href", "pdf", "link") if (url := _media(value.get(key)))), None)
    return _text(value)


def _carrier(row: dict) -> dict:
    obj = _dict(row.get("courier")) or _dict(row.get("company")) or _dict(row.get("shipping_company"))
    return {
        "company_name": _first(_name(row.get("company_name")), _name(row.get("shipping_company")),
                               _name(row.get("courier_name")), _name(row.get("external_company_name")),
                               _name(obj), _name(row.get("company")), _name(row.get("courier"))),
        "company_code": _first(row.get("company_code"), row.get("shipping_company_code"), row.get("courier_code"), row.get("courier_id"),
                               row.get("company_id"), obj.get("code"), obj.get("id")),
        "company_logo": _media(row.get("company_logo") or row.get("shipping_company_logo") or row.get("courier_logo") or obj.get("logo")),
    }


def _has_identity(carrier: dict) -> bool:
    return bool(carrier.get("company_name") or carrier.get("company_code"))


def same_carrier(left: dict, right: dict) -> bool:
    a, b = left.get("company_code"), right.get("company_code")
    if a and b:
        return str(a) == str(b)
    a, b = left.get("company_name"), right.get("company_name")
    if not a or not b:
        return False
    if " ".join(str(a).casefold().split()) == " ".join(str(b).casefold().split()):
        return True
    left_key, _ = normalize_shipping_company(a)
    right_key, _ = normalize_shipping_company(b)
    # Generic local courier names cannot establish identity across stores.
    return left_key == right_key and left_key not in {"unknown", "mandoob"} and not left_key.startswith("other:")


def outbound_shipment(payload: dict, carrier: dict | None = None) -> dict:
    rows = payload.get("shipments")
    rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else [payload.get("shipment")]
    eligible = [row for row in rows if isinstance(row, dict) and _status(row.get("status")) not in CANCELLED
                and str(row.get("type") or "").casefold() not in RETURNS]
    if carrier and _has_identity(carrier):
        eligible = [row for row in eligible if same_carrier(carrier, _carrier(row))]
    # Preserve provider ordering if no provider clock is supplied; never sort
    # opaque numeric IDs as chronological evidence.
    return max(eligible, key=lambda row: provider_time(row.get("updated_at")) or provider_time(row.get("created_at")) or "", default={})


def extract_shipping(payload: dict, *, event_name: str = "order.snapshot", event_time: Any = None) -> dict | None:
    """Extract one observation from this payload, never a deep-merged history."""
    if not isinstance(payload, dict):
        return None
    shipment_event = "shipment" in event_name
    if "return" in event_name.casefold() or str(payload.get("type") or "").casefold() in RETURNS:
        return None
    shipping = _dict(payload.get("shipping"))
    carrier = _carrier(shipping)
    path = "shipping"
    if not _has_identity(carrier):
        carrier = _carrier({"shipping_company": payload.get("shipping_company"),
                            "shipping_company_code": payload.get("shipping_company_code"),
                            "shipping_company_logo": payload.get("shipping_company_logo"),
                            "courier_name": payload.get("courier_name") or payload.get("shipping_company_name"),
                            "courier_id": payload.get("courier_id"), "courier_code": payload.get("courier_code"),
                            "courier_logo": payload.get("courier_logo")})
        path = "shipping_company"
    if not _has_identity(carrier) and not shipment_event:
        name = _name(payload.get("delivery_method"))
        if name:
            carrier = {"company_name": name, "company_code": None, "company_logo": None}
            path = "delivery_method"
    shipment = payload if shipment_event else outbound_shipment(payload, carrier)
    if not _has_identity(carrier):
        carrier = _carrier(shipment)
        path = "shipment" if shipment_event else "shipments"
    if not _has_identity(carrier) and not shipment_event:
        return None
    status = "cancelled" if event_name.casefold().endswith((".cancelled", ".canceled")) else _status(shipment.get("status")) or _status(shipping.get("status")) or _status(payload.get("shipment_status") or payload.get("shipping_status"))
    facts = shipment or shipping or payload
    tracking = _dict(facts.get("tracking"))
    observation = {
        **carrier,
        "method": _first(_name(shipping.get("method")), _name(payload.get("shipping_method")), _name(shipment.get("method"))),
        "shipment_id": _first(shipment.get("id"), shipment.get("shipment_id"), payload.get("salla_shipment_id")),
        "provider_updated_at": provider_time(payload.get("updated_at")) or provider_time(_dict(payload.get("date")).get("updated")) or (provider_time(event_time) if shipment_event else None),
        "source_kind": "shipment" if shipment_event else "order", "source_path": path, "event_name": event_name,
    }
    if shipment_event and not observation["provider_updated_at"] and event_name.endswith((".created", ".creating")):
        observation["provider_updated_at"] = provider_time(payload.get("created_at"))
    for key, value in {
        "tracking_number": _first(facts.get("tracking_number"), facts.get("tracking_id"), facts.get("shipping_number"), facts.get("awb"), tracking.get("number")),
        "tracking_url": _first(facts.get("tracking_url"), facts.get("tracking_link"), tracking.get("url")),
        "label_url": _media(facts.get("label_url") or facts.get("shipping_label_url") or facts.get("label") or facts.get("awb_url") or facts.get("waybill_url")),
        "status": status,
    }.items():
        if value is not None:
            observation[key] = value
    if status in CANCELLED:
        observation.update(tracking_number=None, tracking_url=None, label_url=None)
    return observation


def accept_shipping(existing: dict, incoming: dict | None) -> dict | None:
    """Fence current shipping independently from order/component lifecycle clocks."""
    if not incoming:
        return None
    current = _dict(existing.get(CURRENT_SHIPPING))
    result = deepcopy(incoming)
    shipment_id = _text(existing.get("salla_shipment_id")) or current.get("shipment_id")
    superseded = list(current.get("superseded_shipment_ids") or [])
    if result.get("source_kind") == "shipment" and result.get("shipment_id") in superseded:
        return None
    if result.get("source_kind") == "order" and result.get("shipment_id") in superseded:
        # A newer order envelope may still embed an obsolete AWB. Its carrier
        # identity is usable; its archived shipment is not reactivation proof.
        for key in SHIPMENT_FIELDS.values():
            result.pop(key, None)
        result["shipment_id"] = None
    if result.get("source_kind") == "shipment" and shipment_id and result.get("shipment_id") == shipment_id and not _has_identity(result):
        result.update({key: current.get(key) or existing.get(root) for root, key in IDENTITY_FIELDS.items()})
    if result.get("source_kind") == "shipment" and result.get("status") in CANCELLED:
        if not shipment_id or result.get("shipment_id") != shipment_id:
            return None
        if not _has_identity(result):
            result.update({key: current.get(key) or existing.get(root) for root, key in IDENTITY_FIELDS.items()})
    if not _has_identity(result):
        return None
    previous_time = provider_time(current.get("provider_updated_at"))
    next_time = provider_time(result.get("provider_updated_at"))
    same = same_carrier(current, result)
    if previous_time and (not next_time or next_time < previous_time):
        return None
    if previous_time and next_time == previous_time:
        if not same or (current.get("status") in CANCELLED and result.get("status") not in CANCELLED):
            return None
    if current and not next_time and not same and result.get("event_name") not in {"order.updated", "order.created"}:
        return None
    # Sparse same-carrier observations preserve proven metadata/operational
    # fields. A changed identity cannot inherit the old carrier's ID or AWB.
    replacement = bool(result.get("shipment_id") and shipment_id and result["shipment_id"] != shipment_id)
    reset_label = replacement or result.get("status") in {"pending", "creating", "processing"}
    if replacement:
        result.setdefault("status", "pending")
    if not same or replacement:
        if shipment_id and shipment_id not in superseded:
            superseded.append(shipment_id)
    result["superseded_shipment_ids"] = superseded[-20:]
    if reset_label:
        for key in ("tracking_number", "tracking_url", "label_url"):
            result.setdefault(key, None)
    if same:
        for key, value in current.items():
            if key not in result or result.get(key) is None and key in {"company_name", "company_code", "company_logo", "method"}:
                result[key] = deepcopy(value)
        if result.get("shipment_id") is None:
            result["shipment_id"] = shipment_id
    elif existing:
        for key in SHIPMENT_FIELDS.values():
            result.setdefault(key, "pending" if key == "status" else None)
    return result


def shipping_root_fields(observation: dict) -> dict:
    return {root: deepcopy(observation.get(key)) for root, key in {**IDENTITY_FIELDS, **SHIPMENT_FIELDS}.items()
            if root in IDENTITY_FIELDS or key in observation}


def projected_shipping(row: dict) -> dict | None:
    current = _dict(row.get(CURRENT_SHIPPING))
    if not current:
        return None
    result = deepcopy(current)
    for root, key in {**IDENTITY_FIELDS, **SHIPMENT_FIELDS}.items():
        if root == "shipping_number" and "tracking_number" in row:
            continue
        if root in row:
            result[key] = deepcopy(row[root])
    return result
