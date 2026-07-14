"""Application service for read-only Order Item identities.

Responsibilities
----------------
- Validate and normalize public service inputs.
- Coordinate the OrderItemRepository read contract.
- Translate repository errors into stable service errors.
- Return canonical OrderItemIdentityDTO contracts.

This module performs no:
- Database access
- HTTP or FastAPI operations
- Supplier assignment
- Inventory mutation
- Preparation workflow
- Cost calculation
"""

from __future__ import annotations

from typing import Optional

from .models import OrderItemIdentityDTO
from .repository import (
    DEFAULT_ITEM_PAGE_LIMIT,
    MAX_ITEM_PAGE_LIMIT,
    InvalidOrderItemCursorError,
    OrderItemNotFoundError,
    OrderItemPage,
    OrderItemRepository,
)


class InvalidOrderItemRequestError(ValueError):
    """Raised when required Order Item request data is invalid."""


class InvalidOrderItemCursorRequestError(ValueError):
    """Raised when a public pagination cursor is invalid."""


class OrderItemServiceNotFoundError(LookupError):
    """Stable service-level error for a missing order item."""


def _required_text(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()

    if not normalized:
        raise InvalidOrderItemRequestError(
            f"{field_name} is required"
        )

    return normalized


def _normalise_limit(limit: object) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_ITEM_PAGE_LIMIT

    return max(1, min(value, MAX_ITEM_PAGE_LIMIT))


def _normalise_cursor(cursor: Optional[str]) -> Optional[str]:
    if cursor is None:
        return None

    normalized = str(cursor).strip()
    return normalized or None


class OrderItemService:
    """Read-only use cases for operational item identities."""

    def __init__(self, repository: OrderItemRepository):
        self._repository = repository

    async def list_items(
        self,
        *,
        user_id: str,
        limit: int = DEFAULT_ITEM_PAGE_LIMIT,
        cursor: Optional[str] = None,
    ) -> OrderItemPage:
        normalized_user_id = _required_text(
            user_id,
            field_name="user_id",
        )

        try:
            return await self._repository.list_items(
                user_id=normalized_user_id,
                limit=_normalise_limit(limit),
                cursor=_normalise_cursor(cursor),
            )
        except InvalidOrderItemCursorError as exc:
            raise InvalidOrderItemCursorRequestError(
                "invalid order item cursor"
            ) from exc

    async def get_items_for_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> list[OrderItemIdentityDTO]:
        normalized_user_id = _required_text(
            user_id,
            field_name="user_id",
        )
        normalized_order_number = _required_text(
            order_number,
            field_name="order_number",
        )

        return await self._repository.get_items_for_order(
            user_id=normalized_user_id,
            order_number=normalized_order_number,
        )

    async def get_item(
        self,
        *,
        user_id: str,
        order_number: str,
        order_item_id: str,
    ) -> OrderItemIdentityDTO:
        normalized_user_id = _required_text(
            user_id,
            field_name="user_id",
        )
        normalized_order_number = _required_text(
            order_number,
            field_name="order_number",
        )
        normalized_order_item_id = _required_text(
            order_item_id,
            field_name="order_item_id",
        )

        try:
            return await self._repository.get_item(
                user_id=normalized_user_id,
                order_number=normalized_order_number,
                order_item_id=normalized_order_item_id,
            )
        except OrderItemNotFoundError as exc:
            raise OrderItemServiceNotFoundError(
                f"order item not found: "
                f"{normalized_order_item_id}"
            ) from exc
