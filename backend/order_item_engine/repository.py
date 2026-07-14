"""Read-only repository gateway for Order Item identities.

Architecture
------------
The Order Item repository does not query MongoDB or Salla directly.

It consumes the canonical Order Engine read contract:

    OrderRepository
        → Order Engine Service
        → OrderDTO
        → Order Item Mapper
        → OrderItemIdentityDTO[]

Sprint 002 rules:
- Read-only
- No operational persistence
- No supplier assignment
- No inventory mutation
- No preparation workflow
- No cost calculation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from order_engine.repository import OrderRepository
from order_engine.service import (
    OrderNotFoundError,
    get_order,
    list_orders,
)

from .mapper import map_order_item_identities
from .models import OrderItemIdentityDTO


DEFAULT_ITEM_PAGE_LIMIT = 15
MAX_ITEM_PAGE_LIMIT = 50


class OrderItemNotFoundError(LookupError):
    """Raised when one exact operational item identity does not exist."""


@dataclass(frozen=True)
class OrderItemPage:
    """Read-only page of operational item identities."""

    items: list[OrderItemIdentityDTO]
    next_cursor: Optional[str]
    source_order_count: int
    skipped_invalid_orders: int = 0


class OrderItemRepository(Protocol):
    """Storage-agnostic read contract for Order Item identities."""

    async def list_items(
        self,
        *,
        user_id: str,
        limit: int = DEFAULT_ITEM_PAGE_LIMIT,
        cursor: Optional[str] = None,
    ) -> OrderItemPage:
        """Return identities derived from canonical orders."""

    async def get_items_for_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> list[OrderItemIdentityDTO]:
        """Return every identity belonging to one exact order."""

    async def get_item(
        self,
        *,
        user_id: str,
        order_number: str,
        order_item_id: str,
    ) -> OrderItemIdentityDTO:
        """Return one exact identity inside one exact order."""


def _normalise_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_ITEM_PAGE_LIMIT

    return max(1, min(value, MAX_ITEM_PAGE_LIMIT))


class OrderEngineItemRepository:
    """Order Item repository backed by the canonical Order Engine.

    This is a temporary read bridge. It does not persist a second copy of
    Order Items and therefore cannot diverge from the canonical OrderDTO.
    """

    def __init__(self, order_repository: OrderRepository):
        self._order_repository = order_repository

    async def list_items(
        self,
        *,
        user_id: str,
        limit: int = DEFAULT_ITEM_PAGE_LIMIT,
        cursor: Optional[str] = None,
    ) -> OrderItemPage:
        safe_limit = _normalise_limit(limit)

        order_page = await list_orders(
            self._order_repository,
            user_id=str(user_id),
            limit=safe_limit,
            cursor=cursor,
        )

        identities: list[OrderItemIdentityDTO] = []

        for order in order_page.items:
            identities.extend(
                map_order_item_identities(order)
            )

        return OrderItemPage(
            items=identities,
            next_cursor=order_page.next_cursor,
            source_order_count=len(order_page.items),
            skipped_invalid_orders=order_page.skipped_invalid,
        )

    async def get_items_for_order(
        self,
        *,
        user_id: str,
        order_number: str,
    ) -> list[OrderItemIdentityDTO]:
        order = await get_order(
            self._order_repository,
            user_id=str(user_id),
            order_number=str(order_number),
        )

        return map_order_item_identities(order)

    async def get_item(
        self,
        *,
        user_id: str,
        order_number: str,
        order_item_id: str,
    ) -> OrderItemIdentityDTO:
        normalized_item_id = str(order_item_id or "").strip()

        if not normalized_item_id:
            raise OrderItemNotFoundError(
                "order item not found"
            )

        try:
            identities = await self.get_items_for_order(
                user_id=str(user_id),
                order_number=str(order_number),
            )
        except OrderNotFoundError as exc:
            raise OrderItemNotFoundError(
                f"order item not found: {normalized_item_id}"
            ) from exc

        for identity in identities:
            if identity.order_item_id == normalized_item_id:
                return identity

        raise OrderItemNotFoundError(
            f"order item not found: {normalized_item_id}"
        )
