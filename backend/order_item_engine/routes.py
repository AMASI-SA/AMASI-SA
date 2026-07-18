"""Read-only HTTP routes for the Mezan Order Item Engine.

Public endpoints
----------------
- GET /api/order-items-v2
- GET /api/orders-v2/{order_number}/items
- GET /api/orders-v2/{order_number}/items/{order_item_id}

Rules
-----
- Owner-only backend authorization.
- Routes call OrderItemService only.
- No Mongo queries inside routes.
- No database writes.
- No supplier, inventory, preparation or cost operations.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from order_engine.product_image_enrichment import enrich_order_item_images
from order_engine.repository import MongoOrderRepository

from .models import OrderItemIdentityDTO
from .repository import OrderEngineItemRepository
from .service import (
    InvalidOrderItemCursorRequestError,
    InvalidOrderItemRequestError,
    OrderItemService,
    OrderItemServiceNotFoundError,
)


class OrderItemListResponse(BaseModel):
    """Public paginated response for operational item identities."""

    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemIdentityDTO]
    next_cursor: Optional[str] = None
    limit: int
    source_order_count: int
    skipped_invalid_orders: int = 0


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


def _default_service_factory(db: Any) -> OrderItemService:
    order_repository = MongoOrderRepository(db)
    item_repository = OrderEngineItemRepository(
        order_repository
    )

    return OrderItemService(item_repository)


def make_order_item_engine_router(
    db: Any,
    current_user: Callable,
    *,
    service_factory: Callable[
        [Any],
        OrderItemService,
    ] = _default_service_factory,
) -> APIRouter:
    """Build the read-only Order Item Engine router."""

    router = APIRouter(tags=["order-item-engine"])

    def service() -> OrderItemService:
        return service_factory(db)

    @router.get(
        "/order-items-v2",
        response_model=OrderItemListResponse,
        summary="List canonical operational order items",
    )
    async def list_order_items(
        limit: int = Query(
            15,
            ge=1,
            le=50,
        ),
        cursor: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> OrderItemListResponse:
        owner = _require_owner(user)

        try:
            page = await service().list_items(
                user_id=str(owner["id"]),
                limit=limit,
                cursor=cursor,
            )
        except InvalidOrderItemCursorRequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_order_items_cursor",
                    "message": "مؤشر تحميل عناصر الطلبات غير صالح.",
                },
            ) from exc
        except InvalidOrderItemRequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_order_item_request",
                    "message": str(exc),
                },
            ) from exc

        enriched_items = await enrich_order_item_images(
            db,
            user_id=str(owner["id"]),
            items=page.items,
        )
        return OrderItemListResponse(
            items=enriched_items,
            next_cursor=page.next_cursor,
            limit=limit,
            source_order_count=page.source_order_count,
            skipped_invalid_orders=page.skipped_invalid_orders,
        )

    @router.get(
        "/orders-v2/{order_number}/items",
        response_model=list[OrderItemIdentityDTO],
        summary="List operational items for one order",
    )
    async def list_items_for_order(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> list[OrderItemIdentityDTO]:
        owner = _require_owner(user)

        try:
            items = await service().get_items_for_order(
                user_id=str(owner["id"]),
                order_number=order_number,
            )
            return await enrich_order_item_images(
                db,
                user_id=str(owner["id"]),
                items=items,
            )
        except InvalidOrderItemRequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_order_item_request",
                    "message": str(exc),
                },
            ) from exc

    @router.get(
        "/orders-v2/{order_number}/items/{order_item_id:path}",
        response_model=OrderItemIdentityDTO,
        summary="Get one exact operational order item",
    )
    async def get_one_order_item(
        order_number: str,
        order_item_id: str,
        user: dict = Depends(current_user),
    ) -> OrderItemIdentityDTO:
        owner = _require_owner(user)

        try:
            item = await service().get_item(
                user_id=str(owner["id"]),
                order_number=order_number,
                order_item_id=order_item_id,
            )
            enriched = await enrich_order_item_images(
                db,
                user_id=str(owner["id"]),
                items=[item],
            )
            return enriched[0]
        except InvalidOrderItemRequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_order_item_request",
                    "message": str(exc),
                },
            ) from exc
        except OrderItemServiceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_item_not_found",
                    "order_number": str(order_number),
                    "order_item_id": str(order_item_id),
                },
            ) from exc

    return router
