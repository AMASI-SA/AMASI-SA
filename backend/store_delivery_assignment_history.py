"""Resolve an explicitly revoked courier assignment for a subsequent transfer."""
from typing import Any

from store_delivery_domain import normalize_text


async def released_assignment(db: Any, *, owner: str, order: dict, driver_id: str = "",
                              account_id: str = "", session=None) -> str | None:
    # The unassign transaction sets both timestamps to the same value. This
    # identifies the actual release, including records created before this fix.
    released_at = order.get("store_delivery_updated_at")
    if (order.get("store_delivery_status") != "unassigned" or not released_at
            or order.get("store_delivery_assignment_id")):
        return None
    number = normalize_text(order.get("order_number") or order.get("reference_id"))
    identity = normalize_text(order.get("order_id") or number)
    if not identity or not number:
        return None
    prior = await db["store_delivery_assignments"].find_one(
        {"user_id": owner, "order_id": identity, "order_number": number,
         "active": False, "status": "unassigned", "unassigned_at": released_at},
        {"_id": 0, "id": 1, "driver_id": 1}, session=session)
    if not prior or not prior.get("id") or not prior.get("driver_id"):
        return None
    if account_id:
        previous = await db["store_drivers"].find_one(
            {"user_id": owner, "id": prior["driver_id"]}, {"_id": 0, "account_user_id": 1}, session=session)
        if not previous or not previous.get("account_user_id") or previous["account_user_id"] == account_id:
            return None
    elif not driver_id or prior["driver_id"] == driver_id:
        return None
    return prior["id"]
