"""Read-only HTTP routes for the Mezan Order Engine."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from salla_integration.auto_sync import schedule_salla_auto_sync
from .filter_summary import (
    build_order_filter_summary,
    build_order_status_diagnostic,
)
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
    model_config = ConfigDict(extra="forbid")
    items: list[OrderDTO]
    next_cursor: Optional[str] = None
    limit: int
    skipped_invalid: int = 0


class OrderStatusCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: int = 0
    under_review: int = 0
    processing: int = 0
    completed: int = 0
    shipping: int = 0
    cancelled: int = 0
    refunded: int = 0
    other: int = 0


class QoyodOrderCounts(BaseModel):
    """Qoyod card counts may degrade independently from Salla status cards.

    `None` means the expensive Qoyod classifier was unavailable or exceeded its
    bounded timeout. It must not be represented as a factual zero.
    """

    model_config = ConfigDict(extra="forbid")
    from_date: str = "2026-07-01"
    sent: Optional[int] = None
    eligible_not_sent: Optional[int] = None
    available: bool = True
    error: Optional[str] = None


class OrderFilterSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status_counts: OrderStatusCounts
    qoyod: QoyodOrderCounts


def _is_owner(user: Any) -> bool:
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
    router = APIRouter(prefix="/orders-v2", tags=["order-engine"])

    def repository() -> OrderRepository:
        return repository_factory(db)

    @router.get("", response_model=OrderListResponse)
    async def list_order_rows(
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: Optional[str] = Query(default=None),
        status_group: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> OrderListResponse:
        owner = _require_owner(user)
        schedule_salla_auto_sync(db, str(owner["id"]))

        try:
            page = await list_orders(
                repository(),
                user_id=str(owner["id"]),
                limit=limit,
                cursor=cursor,
                status_group=status_group,
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
        "/filters/summary",
        response_model=OrderFilterSummaryResponse,
    )
    async def get_filter_summary(
        user: dict = Depends(current_user),
    ) -> OrderFilterSummaryResponse:
        owner = _require_owner(user)
        summary = await build_order_filter_summary(
            db,
            user_id=str(owner["id"]),
        )
        return OrderFilterSummaryResponse(**summary)

    @router.get("/filters/status-diagnostic")
    async def get_status_diagnostic(
        sample_limit: int = Query(default=100, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        return await build_order_status_diagnostic(
            db,
            user_id=str(owner["id"]),
            sample_limit=sample_limit,
        )

    @router.get("/{order_number}", response_model=OrderDTO)
    async def get_order_row(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> OrderDTO:
        owner = _require_owner(user)
        schedule_salla_auto_sync(db, str(owner["id"]))

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
