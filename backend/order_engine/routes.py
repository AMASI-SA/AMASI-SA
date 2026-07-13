"""Read-only HTTP routes for the Mezan Order Engine.

Mounted under the parent `/api` router:

- GET /api/orders-v2
- GET /api/orders-v2/{order_number}

Sprint 001 rules:
- Owner-only backend authorization
- Read-only
- No Salla HTTP calls
- No Qoyod calls
- No database writes
- Routes call Service only
- Routes never query Mongo directly
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from .models import OrderDTO
from .repository import MongoOrderRepository, OrderRepository
from .service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidOrderCursorError,
    OrderNotFoundError,
    get_order,
    list_orders,
)


class OrderListResponse(BaseModel):
    """Public list response without exposing storage details."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderDTO]
    next_cursor: Optional[str] = None
    limit: int
    skipped_invalid: int = 0


def _is_owner(user: Any) -> bool:
    """Support the current role field and older explicit owner flag."""

    if not isinstance(user, dict):
        return False

    role = str(user.get("role") or "").strip().lower()

    return role == "owner" or user.get("is_owner") is True


def _require_owner(user: Any) -> dict:
    if not _is_owner(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "owner_only",
                "message": "هذه الصفحة متاحة للمالك فقط.",
            },
        )

    return user


def make_order_engine_router(
    db: Any,
    current_user: Callable,
    *,
    repository_factory: Callable[[Any], OrderRepository] = MongoOrderRepository,
) -> APIRouter:
    """Build the Order Engine router.

    `repository_factory` is injectable so HTTP contract tests do not need
    MongoDB or the running `server.py` application.
    """

    router = APIRouter(
        prefix="/orders-v2",
        tags=["order-engine"],
    )

    def repository() -> OrderRepository:
        return repository_factory(db)

    @router.get(
        "",
        response_model=OrderListResponse,
        summary="List canonical Salla orders",
    )
    async def list_order_rows(
        limit: int = Query(
            DEFAULT_LIMIT,
            ge=1,
            le=MAX_LIMIT,
        ),
        cursor: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> OrderListResponse:
        owner = _require_owner(user)

        try:
            page = await list_orders(
                repository(),
                user_id=str(owner["id"]),
                limit=limit,
                cursor=cursor,
            )
        except InvalidOrderCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_orders_cursor",
                    "message": "مؤشر تحميل الطلبات غير صالح.",
                },
            ) from exc

        return OrderListResponse(
            items=page.items,
            next_cursor=page.next_cursor,
            limit=limit,
            skipped_invalid=page.skipped_invalid,
        )

    @router.get(
        "/{order_number}",
        response_model=OrderDTO,
        summary="Get one canonical Salla order",
    )
    async def get_order_row(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> OrderDTO:
        owner = _require_owner(user)

        try:
            return await get_order(
                repository(),
                user_id=str(owner["id"]),
                order_number=order_number,
            )
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_not_found",
                    "order_number": str(order_number),
                },
            ) from exc

    return router
