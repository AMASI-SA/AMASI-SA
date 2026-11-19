"""Lightweight shared rules for the governed store-courier workflow.

Keep these constants and pure validations separate from the API routers so the
standalone driver and handover surfaces do not import the legacy order engine.
The module must remain safe for the focused Store Delivery test environment.
"""
from __future__ import annotations

from typing import Any

from salla_shipping import extract_shipping
from shipping_companies import normalize_shipping_company
from store_delivery_domain import is_city_courier_company, normalize_shipping_carrier, normalize_text

WORKFLOWS = "order_review_workflows"

ASSIGNED_WAITING_PICKUP = "assigned_waiting_pickup"
DELIVERING = "delivering"
DELIVERED = "delivered"
CANCELLED = "cancelled"


def is_salla_store_courier(name: Any, app_id: Any) -> bool:
    normalized = normalize_shipping_carrier(name)
    if is_city_courier_company(normalized) or normalized in {"مندوب", "مندوب المتجر", "مندوب المحل", "mandoob"}:
        return True
    key, _ = normalize_shipping_company(normalized)
    return bool(normalized and normalized != "shipping" and key.startswith("other:") and app_id in (0, "0"))


def current_carrier(order: dict[str, Any]) -> tuple[str, Any]:
    current = order.get("salla_shipping_current")
    if isinstance(current, dict) and (
        current.get("company_name") or current.get("company_code") is not None
    ):
        return normalize_text(current.get("company_name") or ""), current.get("company_code")
    name = order.get("shipping_company")
    if isinstance(name, dict):
        name = name.get("name") or name.get("name_ar")
    name = normalize_text(name) if isinstance(name, str) else ""
    code = order.get("shipping_company_code")
    # Older orders can predate the current shipping projection. Honor an
    # explicit external provider on either legacy source before accepting.
    sources = order.get("raw_by_source")
    direct = sources.get("salla_direct") if isinstance(sources, dict) else None
    observed = extract_shipping(direct) if isinstance(direct, dict) else None
    if observed and (not name or is_salla_store_courier(name, code)):
        return normalize_text(observed.get("company_name")), observed.get("company_code")
    return name, code


def _status(value: Any) -> str:
    if isinstance(value, dict):
        # A customized current status can share a completed parent.
        for key in ("customized", "slug", "name", "name_ar", "label"):
            item = _status(value.get(key))
            if item:
                return item
        return ""
    text = normalize_text(value).casefold() if isinstance(value, str) else ""
    return {"تم التنفيذ": "completed", "جاري التوصيل": "delivering",
            "جار التوصيل": "delivering", "out_for_delivery": "delivering"}.get(text, text)


def store_courier_assignment_blocker(order: dict[str, Any], *, transfer: bool = False) -> str | None:
    name, code = current_carrier(order)
    if not is_salla_store_courier(name, code):
        return "salla_store_courier_required"
    sources = order.get("raw_by_source") or {}
    direct = sources.get("salla_direct") if isinstance(sources, dict) else None
    direct = direct if isinstance(direct, dict) else {}
    values = {_status(value) for value in (
        order.get("order_status_slug"), order.get("order_status"),
        direct.get("status"), direct.get("status_slug"),
    )} - {""}
    if "delivering" in values:
        values.discard("shipping")  # normalized shipping alone is ambiguous
    if values == {"completed"} or (transfer and values == {"delivering"}):
        return None
    return "salla_transfer_status_ineligible" if transfer else "salla_assignment_status_ineligible"


def eligibility_fence(order: dict[str, Any]) -> dict[str, Any]:
    # Protect only fields read by the policy and the observed assignment pointer.
    paths = ("order_status_slug", "order_status", "raw_by_source.salla_direct.status",
             "raw_by_source.salla_direct.status_slug", "salla_shipping_current.company_name",
             "salla_shipping_current.company_code", "shipping_company", "shipping_company_code",
             "store_delivery_assignment_id")
    if not order.get("salla_shipping_current"):
        paths += tuple("raw_by_source.salla_direct." + key for key in (
            "shipping", "shipping_company", "shipping_company_code", "courier_name",
            "shipping_company_name", "courier_id", "courier_code", "delivery_method", "shipments", "shipment"))
    result = {}
    for path in paths:
        value = order
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        result[path] = {"$eq": value}
    return result


def active_account_assignment(workflow: dict[str, Any]) -> bool:
    # V2 links are resolved against the active assignment collection. Legacy
    # account-based dispatch records custody on the workflow itself.
    return bool(workflow.get("store_courier_assignee_id")
                and not workflow.get("store_delivery_assignment_id")
                and workflow.get("store_courier_assignment_state") in {ASSIGNED_WAITING_PICKUP, DELIVERING})


__all__ = [
    "ASSIGNED_WAITING_PICKUP",
    "CANCELLED",
    "DELIVERED",
    "DELIVERING",
    "WORKFLOWS",
    "store_courier_assignment_blocker",
]
