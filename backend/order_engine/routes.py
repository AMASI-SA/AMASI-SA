"""Read-only HTTP routes for the Mezan Order Engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from salla_integration.order_commerce_enrichment import enrich_single_order_commerce
from .address_diagnostic import build_order_address_diagnostic
from .campaign_enrichment import enrich_order_campaigns
from .city_enrichment import enrich_order_cities
from .commerce_diagnostic import build_order_commerce_diagnostic
from .customer_history import get_customer_history
from .filter_summary import (
    build_order_filter_summary,
    build_order_status_diagnostic,
)
from .gift_db_enrichment import enrich_order_gifts
from .gift_diagnostic import build_gift_diagnostic
from .gift_enrichment import enrich_single_order_gift
from .models import OrderDTO
from .product_image_enrichment import enrich_order_item_images
from .recipient_enrichment import enrich_order_recipients
from .repository import MongoOrderRepository, OrderRepository
from .salla_refresh import refresh_order_from_salla
from .shipping_label_service import (
    ShippingLabelError,
    issue_shipping_label,
    refresh_shipping_label,
)
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


class CustomerHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_found: bool
    normalized_mobile: Optional[str] = None
    current_order: OrderDTO
    previous_orders: list[OrderDTO]
    previous_order_count: int = 0
    scanned_orders: int = 0
    scan_complete: bool = False


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


def _normalized_order_status(value: Any) -> str:
    return " ".join(
        str(value or "").replace("_", " ").strip().casefold().split()
    )


def _is_waiting_for_payment_order(order: OrderDTO) -> bool:
    for raw_status in (
        order.status_native,
        order.status,
        order.payment.status,
    ):
        value = _normalized_order_status(raw_status)
        if not value:
            continue
        if "الدفع" in value and ("انتظار" in value or "بإنتظار" in value):
            return True
        if "payment" in value and any(
            marker in value for marker in ("pending", "awaiting", "waiting")
        ):
            return True
    return False


async def _latest_sold_product_order_page(
    repository: OrderRepository,
    *,
    user_id: str,
    limit: int,
    cursor: Optional[str],
) -> tuple[list[OrderDTO], Optional[str], int]:
    """Fill one feed page while consuming, but never returning, payment-pending orders."""
    items: list[OrderDTO] = []
    next_cursor = cursor
    skipped_invalid = 0
    seen_cursors: set[str] = set()

    while len(items) < limit:
        page = await list_orders(
            repository,
            user_id=user_id,
            limit=limit - len(items),
            cursor=next_cursor,
        )
        skipped_invalid += page.skipped_invalid
        items.extend(
            order
            for order in page.items
            if not _is_waiting_for_payment_order(order)
        )

        page_cursor = page.next_cursor
        if not page_cursor:
            next_cursor = None
            break
        if page_cursor == next_cursor or page_cursor in seen_cursors:
            next_cursor = None
            break
        seen_cursors.add(page_cursor)
        next_cursor = page_cursor

    return items, next_cursor, skipped_invalid


def make_order_engine_router(
    db: Any,
    current_user: Callable,
    *,
    repository_factory: Callable[[Any], OrderRepository] = MongoOrderRepository,
    customer_history_salla_request: Optional[Callable] = None,
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
        "/latest-sold-products",
        response_model=OrderListResponse,
    )
    async def list_latest_sold_product_rows(
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: Optional[str] = Query(default=None),
        user: dict = Depends(current_user),
    ) -> OrderListResponse:
        """Return latest order items without dashboard-only enrichments."""
        owner = _require_owner(user)
        owner_id = str(owner["id"])
        try:
            items, next_cursor, skipped_invalid = await _latest_sold_product_order_page(
                repository(),
                user_id=owner_id,
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

        flat_items = [
            item
            for order in items
            for item in order.items
        ]
        enriched_flat_items = await enrich_order_item_images(
            db,
            user_id=owner_id,
            items=flat_items,
        )
        enriched_orders: list[OrderDTO] = []
        item_offset = 0
        for order in items:
            item_count = len(order.items)
            enriched_orders.append(
                order.model_copy(
                    update={
                        "items": enriched_flat_items[
                            item_offset:item_offset + item_count
                        ]
                    }
                )
            )
            item_offset += item_count

        return OrderListResponse(
            items=enriched_orders,
            next_cursor=next_cursor,
            limit=limit,
            skipped_invalid=skipped_invalid,
        )

    @router.get(
        "/{order_number}/customer-history",
        response_model=CustomerHistoryResponse,
    )
    async def get_order_customer_history(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> CustomerHistoryResponse:
        owner = _require_owner(user)
        try:
            result = await get_customer_history(
                repository(),
                db=db,
                user_id=str(owner["id"]),
                order_number=str(order_number),
                **(
                    {"salla_request": customer_history_salla_request}
                    if customer_history_salla_request is not None
                    else {}
                ),
            )
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_not_found",
                    "order_number": str(order_number),
                },
            ) from exc

        return CustomerHistoryResponse(
            customer_found=result.customer_found,
            normalized_mobile=result.normalized_mobile,
            current_order=result.current_order,
            previous_orders=result.previous_orders,
            previous_order_count=len(result.previous_orders),
            scanned_orders=result.scanned_orders,
            scan_complete=result.scan_complete,
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

    @router.post(
        "/{order_number}/refresh-from-salla",
        summary="Refresh one order from Salla Order Details",
    )
    async def refresh_one_order_from_salla(
        order_number: str,
        force: bool = Query(default=True),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        result = await refresh_order_from_salla(
            db,
            str(owner["id"]),
            str(order_number),
            force=bool(force),
        )

        if result.get("ok") and result.get("found"):
            return result

        if result.get("ok") and not result.get("found"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    **result,
                    "message": "لم يتم العثور على الطلب في سلة.",
                },
            )

        if result.get("needs_reauth"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    **result,
                    "message": "صلاحية قراءة الطلبات في سلة تحتاج إعادة تفويض المتجر.",
                },
            )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                **result,
                "message": result.get("message") or "تعذّر تحديث الطلب من سلة.",
            },
        )

    @router.post("/{order_number}/read")
    async def mark_order_read(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        owner_id = str(owner["id"])
        normalized = str(order_number or "").strip()
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "order_number_required"},
            )

        read_at = datetime.now(timezone.utc).isoformat()
        result = await db.unified_orders.update_one(
            {"user_id": owner_id, "order_number": normalized},
            {
                "$set": {
                    "mezan_read_at": read_at,
                    "mezan_read_by": owner_id,
                }
            },
        )
        if not result.matched_count:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "order_not_found",
                    "order_number": normalized,
                },
            )
        return {
            "ok": True,
            "order_number": normalized,
            "read": True,
            "read_at": read_at,
            "source": "mezan_local",
        }

    @router.post("/{order_number}/shipping-label/refresh")
    async def refresh_order_shipping_label(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await refresh_shipping_label(
                db,
                str(owner["id"]),
                str(order_number),
            )
        except ShippingLabelError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "order_number": str(order_number),
                },
            ) from exc

    @router.post("/{order_number}/shipping-label")
    async def create_order_shipping_label(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = _require_owner(user)
        try:
            return await issue_shipping_label(
                db,
                str(owner["id"]),
                str(order_number),
            )
        except ShippingLabelError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "order_number": str(order_number),
                },
            ) from exc

    @router.get("/{order_number}", response_model=OrderDTO)
    async def get_order_row(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> OrderDTO:
        owner = _require_owner(user)
        owner_id = str(owner["id"])

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
