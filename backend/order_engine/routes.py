"""Order Engine routes extended with read-only customer history."""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from .customer_history import get_customer_history
from .models import OrderDTO
from .repository import MongoOrderRepository, OrderRepository
from .routes_base import *  # noqa: F401,F403 - preserve the existing public route contract
from .routes_base import (
    _require_owner,
    make_order_engine_router as _make_base_order_engine_router,
)
from .service import OrderNotFoundError


class CustomerHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_found: bool
    normalized_mobile: Optional[str] = None
    current_order: OrderDTO
    previous_orders: list[OrderDTO]
    previous_order_count: int = 0
    scanned_orders: int = 0
    scan_complete: bool = False


def make_order_engine_router(
    db: Any,
    current_user: Callable,
    *,
    repository_factory: Callable[[Any], OrderRepository] = MongoOrderRepository,
) -> APIRouter:
    """Return all existing Order Engine routes plus customer history."""

    router = _make_base_order_engine_router(
        db,
        current_user,
        repository_factory=repository_factory,
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
                repository_factory(db),
                user_id=str(owner["id"]),
                order_number=str(order_number),
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

    return router
