"""Read-only HTTP routes for the Mezan Order Engine."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from salla_integration.auto_sync import schedule_salla_auto_sync
from salla_integration.order_commerce_enrichment import enrich_single_order_commerce
from .address_diagnostic import build_order_address_diagnostic
from .campaign_enrichment import enrich_order_campaigns
from .city_enrichment import enrich_order_cities
from .commerce_diagnostic import build_order_commerce_diagnostic
from .filter_summary import (
    build_order_filter_summary,
    build_order_status_diagnostic,
)
from .gift_db_enrichment import enrich_order_gifts
from .gift_diagnostic import build_gift_diagnostic
from .gift_enrichment import enrich_single_order_gift
from .models import OrderDTO
from .recipient_enrichment import enrich_order_recipients
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


class ExactStatusCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    label: str
    count: int = 0


class OrderFilterSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = 0
    status_cards: list[ExactStatusCard]
    status_counts: dict[str, int]


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
        status_exact: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> OrderListResponse:
        owner = _require_owner(user)
        owner_id = str(owner["id"])
        schedule_salla_auto_sync(db, owner_id)

        try:
            page = await list_orders(
                repository(),
                user_id=owner_id,
                limit=limit,
                cursor=cursor,
                status_group=status_group,
                status_exact=status_exact,
            )
        except InvalidOrderCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_orders_cursor",
                    "message": "مؤشر تحميل الطلبات غير صالح.",
                },
            ) from exc

        enriched_items = await enrich_order_cities(
            db,
            user_id=owner_id,
            orders=page.items,
        )
        enriched_items = await enrich_order_gifts(
            db,
            user_id=owner_id,
            orders=enriched_items,
        )
        enriched_items = await enrich_order_campaigns(
            db,
            user_id=owner_id,
            orders=enriched_items,
        )
        enriched_items = await enrich_order_recipients(
            db,
            user_id=owner_id,
            orders=enriched_items,
        )
        return OrderListResponse(
            items=enriched_items,
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

    @router.get("/diagnostics/address/{order_number}")
    async def get_address_diagnostic(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        return await build_order_address_diagnostic(
            db,
            user_id=str(owner["id"]),
            order_number=str(order_number),
        )

    @router.get("/diagnostics/commerce/{order_number}")
    async def get_commerce_diagnostic(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        return await build_order_commerce_diagnostic(
            db,
            user_id=str(owner["id"]),
            order_number=str(order_number),
        )

    @router.get("/diagnostics/gift/{order_number}")
    async def get_order_gift_diagnostic(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        return await build_gift_diagnostic(
            db,
            user_id=str(owner["id"]),
            order_number=str(order_number),
        )

    @router.post("/actions/enrich-commerce/{order_number}")
    async def enrich_order_commerce(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        result = await enrich_single_order_commerce(
            db,
            user_id=str(owner["id"]),
            order_number=str(order_number),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result,
            )
        return result

    @router.post("/actions/enrich-gift/{order_number}")
    async def enrich_order_gift(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        result = await enrich_single_order_gift(
            db,
            user_id=str(owner["id"]),
            order_number=str(order_number),
        )
        if not result.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result,
            )
        return result

    @router.get("/{order_number}", response_model=OrderDTO)
    async def get_order_row(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> OrderDTO:
        owner = _require_owner(user)
        owner_id = str(owner["id"])
        schedule_salla_auto_sync(db, owner_id)

        try:
            order = await get_order(
                repository(),
                user_id=owner_id,
                order_number=order_number,
            )
            enriched = await enrich_order_cities(
                db,
                user_id=owner_id,
                orders=[order],
            )
            enriched = await enrich_order_gifts(
                db,
                user_id=owner_id,
                orders=enriched,
            )
            enriched = await enrich_order_campaigns(
                db,
                user_id=owner_id,
                orders=enriched,
            )
            enriched = await enrich_order_recipients(
                db,
                user_id=owner_id,
                orders=enriched,
            )
            return enriched[0]
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_not_found",
                    "order_number": str(order_number),
                },
            ) from exc

    return router
