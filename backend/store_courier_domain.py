"""Lightweight shared rules for the governed store-courier workflow.

Keep these constants and pure validations separate from the API routers so the
standalone driver and handover surfaces do not import the legacy order engine.
"""
from __future__ import annotations

from typing import Any

from store_delivery_domain import normalize_text

WORKFLOWS = "order_review_workflows"

ASSIGNED_WAITING_PICKUP = "assigned_waiting_pickup"
DELIVERING = "delivering"
DELIVERED = "delivered"
CANCELLED = "cancelled"


def store_courier_assignment_blocker(workflow: dict[str, Any]) -> str | None:
    """Return the first reason a shipment cannot be assigned to a driver."""
    if normalize_text(workflow.get("carrier_label_type")) != "store_courier":
        return "store_courier_label_required"
    if workflow.get("carrier_label_ready") is not True:
        return "store_courier_label_not_ready"
    if workflow.get("carrier_label_print_confirmed") is not True:
        return "store_courier_label_not_confirmed"
    if (
        normalize_text(workflow.get("stage")) != "completed"
        or normalize_text(workflow.get("assembly_status")) != "completed"
    ):
        return "store_courier_order_not_completed"
    return None


__all__ = [
    "ASSIGNED_WAITING_PICKUP",
    "CANCELLED",
    "DELIVERED",
    "DELIVERING",
    "WORKFLOWS",
    "store_courier_assignment_blocker",
]
