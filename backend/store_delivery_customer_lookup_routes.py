"""Customer-service lookup for store-driver deliveries.

Customer service searches by order number/id and sees the canonical order,
current store-driver assignment and active delivery instructions in one read.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
from store_delivery_domain import normalize_text
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS


def _merchant_user_id(user: dict[str, Any]) -> str:
    role = normalize_text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


def _require_customer_service(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "delivery_instruction_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    permission = "store_delivery.instructions.manage"
    allowed = (
        role in {"owner", "admin", "operations", "customer_service"}
        or user.get("is_owner") is True
        or permission in set(user.get("extra_permissions") or [])
    ) and permission not in set(user.get("denied_permissions") or [])
    if not allowed:
        raise HTTPException(status_code=403, detail={"code": "delivery_instruction_permission_required"})
    return user


def make_store_delivery_customer_lookup_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/customer-service", tags=["Store Delivery Customer Service"])

    @router.get("/order/{identifier}")
    async def lookup_order(identifier: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_customer_service(user)
        user_id = _merchant_user_id(actor)
        value = normalize_text(identifier)
        order = await db[ORDERS].find_one(
            {
                "user_id": user_id,
                "$or": [
                    {"order_number": value},
                    {"order_id": value},
                    {"reference_id": value},
                    {"tracking_number": value},
                ],
            },
            {
                "_id": 0,
                "order_id": 1,
                "order_number": 1,
                "order_status": 1,
                "payment_status": 1,
                "remaining_amount": 1,
                "customer_name": 1,
                "customer_mobile": 1,
                "shipping_city": 1,
                "shipping_district": 1,
                "shipping_street": 1,
                "tracking_number": 1,
            },
        )
        if not order:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"})
        canonical_id = normalize_text(order.get("order_id") or order.get("order_number"))
        assignment = await db[ASSIGNMENTS].find_one(
            {"user_id": user_id, "order_id": canonical_id, "active": True},
            {"_id": 0, "user_id": 0},
        )
        instructions = await db[STORE_DELIVERY_INSTRUCTIONS].find(
            {"user_id": user_id, "order_id": canonical_id, "status": "active"},
            {"_id": 0, "user_id": 0},
        ).sort("created_at", -1).to_list(length=100)
        return {
            "order": order,
            "canonical_order_id": canonical_id,
            "assignment": assignment,
            "instructions": instructions,
            "has_store_driver": assignment is not None,
        }

    return router


__all__ = ["make_store_delivery_customer_lookup_router"]
