"""Component editing and stock-cost authority helpers for Mezan OS."""
from __future__ import annotations

from typing import Any


def component_cost_metadata(*, track_inventory: bool, amount: float | None, purchase_cost: float | None = None) -> dict[str, Any]:
    """Return explicit cost authority metadata.

    Stock components may start with a manual estimate, but purchase invoices are
    the authoritative source once an approved purchase cost exists. Services do
    not use purchase inventory costing, so their maintained unit cost remains the
    operational authority.
    """
    if track_inventory:
        if purchase_cost is not None:
            return {
                "unit_cost": purchase_cost,
                "initial_unit_cost": amount,
                "cost_source": "purchase_invoice",
                "cost_authoritative": True,
                "purchase_cost_pending": False,
            }
        return {
            "unit_cost": amount,
            "initial_unit_cost": amount,
            "cost_source": "manual_initial",
            "cost_authoritative": False,
            "purchase_cost_pending": True,
        }
    return {
        "unit_cost": amount,
        "initial_unit_cost": None,
        "cost_source": "manual_service",
        "cost_authoritative": True,
        "purchase_cost_pending": False,
    }
