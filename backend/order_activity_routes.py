"""HTTP surface for isolated order activity ledger."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from order_activity_v1 import (
    OrderActivityNotFoundError,
    read_order_activity,
    refresh_order_activity_from_salla,
)


def _require_owner(user: Any) -> dict:
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_only"},
        )

    role = str(user.get("role") or "").strip().lower()

    if role != "owner" and user.get("is_owner") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "owner_only",
                "message": "هذه الصفحة متاحة للمالك فقط.",
            },
        )

    return user


def make_order_activity_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/orders-v2",
        tags=["order-activity-v1"],
    )

    @router.get("/{order_number}/activity")
    async def get_order_activity(
        order_number: str,
        user: dict = Depends(current_user),
    ):
        owner = _require_owner(user)

        return await read_order_activity(
            db,
            user_id=str(owner["id"]),
            order_number=order_number,
        )

    @router.post("/{order_number}/activity/refresh")
    async def refresh_order_activity(
        order_number: str,
        user: dict = Depends(current_user),
    ):
        owner = _require_owner(user)

        try:
            return await refresh_order_activity_from_salla(
                db,
                user_id=str(owner["id"]),
                order_number=order_number,
            )

        except OrderActivityNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_activity_order_not_found",
                    "order_number": str(order_number),
                },
            ) from exc

        except Exception as exc:
            # Never expose tokens / provider response bodies.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "order_activity_refresh_failed",
                    "message": "تعذر تحديث سجل الطلب من سلة.",
                },
            ) from exc

    return router
