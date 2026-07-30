"""Shared idempotent placement of received stock into a warehouse location."""
from __future__ import annotations

from typing import Any

from warehouse_location_routes import LOCATIONS


class InventoryLocationCapacityError(RuntimeError):
    """Raised when a location cannot accept the requested quantity."""


async def place_inventory_receipt(
    db: Any,
    *,
    merchant_id: str,
    location_id: str,
    receipt_id: str,
    inventory_item: dict[str, Any],
    quantity: int,
    scanned_barcode: str,
    occurred_at: str,
) -> bool:
    """Place one receipt in a location once.

    Returns ``True`` when the same receipt was already present. The receipt ID
    is the inventory-location idempotency boundary shared by purchase receipts
    and stock-preparation receipts.
    """
    await db[LOCATIONS].update_one(
        {
            "id": location_id,
            "user_id": merchant_id,
            "occupancy": None,
        },
        {
            "$set": {
                "occupancy": {
                    "items": [],
                    "total_quantity": 0,
                },
            },
        },
    )
    location_update = await db[LOCATIONS].update_one(
        {
            "id": location_id,
            "user_id": merchant_id,
            "state": {"$ne": "disabled"},
            "occupancy.items.receipt_id": {"$ne": receipt_id},
            "$expr": {
                "$lte": [
                    {
                        "$add": [
                            {
                                "$ifNull": [
                                    "$occupancy.total_quantity",
                                    0,
                                ],
                            },
                            quantity,
                        ],
                    },
                    {"$ifNull": ["$max_items", 1000000000]},
                ],
            },
        },
        {
            "$push": {"occupancy.items": inventory_item},
            "$inc": {"occupancy.total_quantity": quantity},
            "$set": {
                "state": "occupied",
                "last_verified_scan": scanned_barcode,
                "last_scan_verified_at": occurred_at,
                "updated_at": occurred_at,
            },
        },
    )
    if location_update.matched_count:
        return False

    applied = await db[LOCATIONS].find_one(
        {
            "id": location_id,
            "user_id": merchant_id,
            "occupancy.items.receipt_id": receipt_id,
        },
        {"_id": 0, "id": 1},
    )
    if applied:
        return True
    raise InventoryLocationCapacityError(
        "inventory_location_capacity_exceeded"
    )


__all__ = [
    "InventoryLocationCapacityError",
    "place_inventory_receipt",
]
