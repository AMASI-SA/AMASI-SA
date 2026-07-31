"""Read-only customer-history lookup for Order Engine review pages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .customer_history_store import MongoCustomerHistoryStore
from .mapper import OrderMappingError
from .models import OrderDTO
from .repository import OrderRepository
from .service import MAX_LIMIT, _map_row, get_order, list_orders

CUSTOMER_HISTORY_MAX_PAGES = 6


def normalize_saudi_mobile(value: Any) -> Optional[str]:
    """Return a Saudi mobile in canonical ``9665XXXXXXXX`` form.

    Supported source formats include ``5XXXXXXXX``, ``05XXXXXXXX``,
    ``+9665XXXXXXXX`` and ``009665XXXXXXXX``. Unsupported values return
    ``None`` so unrelated malformed numbers cannot be matched accidentally.
    """

    digits = re.sub(r"\D", "", str(value or "").strip())
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("9660"):
        digits = f"966{digits[4:]}"

    if re.fullmatch(r"9665\d{8}", digits):
        return digits
    if re.fullmatch(r"05\d{8}", digits):
        return f"966{digits[1:]}"
    if re.fullmatch(r"5\d{8}", digits):
        return f"966{digits}"
    return None


def _normalize_email(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().casefold()
    return normalized or None


def customer_matches(current: OrderDTO, candidate: OrderDTO) -> bool:
    current_mobile = normalize_saudi_mobile(current.customer.mobile)
    candidate_mobile = normalize_saudi_mobile(candidate.customer.mobile)
    if current_mobile and candidate_mobile:
        return current_mobile == candidate_mobile

    current_email = _normalize_email(current.customer.email)
    candidate_email = _normalize_email(candidate.customer.email)
    return bool(
        current_email
        and candidate_email
        and current_email == candidate_email
    )


@dataclass(frozen=True)
class CustomerHistoryResult:
    current_order: OrderDTO
    previous_orders: list[OrderDTO]
    normalized_mobile: Optional[str]
    scanned_orders: int
    scan_complete: bool

    @property
    def customer_found(self) -> bool:
        return bool(self.previous_orders)


def _mongo_history_store(repository: OrderRepository) -> Optional[MongoCustomerHistoryStore]:
    collection = getattr(repository, "_collection", None)
    if collection is None:
        return None
    return MongoCustomerHistoryStore(collection)


async def _load_complete_history(
    repository: OrderRepository,
    *,
    current_order: OrderDTO,
    user_id: str,
    normalized_mobile: Optional[str],
    normalized_email: Optional[str],
) -> Optional[tuple[list[OrderDTO], int]]:
    store = _mongo_history_store(repository)
    if store is None:
        return None

    rows = await store.find_customer_orders(
        user_id=str(user_id),
        normalized_mobile=normalized_mobile,
        normalized_email=normalized_email,
        exclude_order_number=current_order.order_number,
    )

    previous_orders: list[OrderDTO] = []
    seen_order_numbers: set[str] = set()
    for row in rows:
        if row.order_number in seen_order_numbers:
            continue
        try:
            candidate = _map_row(
                row.salla_raw,
                current_status=row.current_status,
            )
        except OrderMappingError:
            continue
        if customer_matches(current_order, candidate):
            previous_orders.append(candidate)
            seen_order_numbers.add(candidate.order_number)

    previous_orders.sort(key=lambda order: order.created_at, reverse=True)
    return previous_orders, len(rows)


async def _load_paginated_fallback(
    repository: OrderRepository,
    *,
    current_order: OrderDTO,
    user_id: str,
    max_pages: int,
) -> tuple[list[OrderDTO], int, bool]:
    previous_orders: list[OrderDTO] = []
    seen_order_numbers: set[str] = set()
    cursor: Optional[str] = None
    scanned_orders = 0
    scan_complete = False

    for _ in range(max(1, int(max_pages))):
        page = await list_orders(
            repository,
            user_id=str(user_id),
            limit=MAX_LIMIT,
            cursor=cursor,
        )
        scanned_orders += len(page.items)

        for candidate in page.items:
            if candidate.order_number == current_order.order_number:
                continue
            if candidate.order_number in seen_order_numbers:
                continue
            if customer_matches(current_order, candidate):
                previous_orders.append(candidate)
                seen_order_numbers.add(candidate.order_number)

        cursor = page.next_cursor
        if not cursor:
            scan_complete = True
            break

    previous_orders.sort(key=lambda order: order.created_at, reverse=True)
    return previous_orders, scanned_orders, scan_complete


async def get_customer_history(
    repository: OrderRepository,
    *,
    user_id: str,
    order_number: str,
    max_pages: int = CUSTOMER_HISTORY_MAX_PAGES,
) -> CustomerHistoryResult:
    """Load the current order and all matching prior orders without writes."""

    current_order = await get_order(
        repository,
        user_id=str(user_id),
        order_number=str(order_number),
    )
    normalized_mobile = normalize_saudi_mobile(current_order.customer.mobile)
    normalized_email = _normalize_email(current_order.customer.email)

    if not normalized_mobile and not normalized_email:
        return CustomerHistoryResult(
            current_order=current_order,
            previous_orders=[],
            normalized_mobile=None,
            scanned_orders=0,
            scan_complete=True,
        )

    complete = await _load_complete_history(
        repository,
        current_order=current_order,
        user_id=str(user_id),
        normalized_mobile=normalized_mobile,
        normalized_email=normalized_email,
    )
    if complete is not None:
        previous_orders, scanned_orders = complete
        return CustomerHistoryResult(
            current_order=current_order,
            previous_orders=previous_orders,
            normalized_mobile=normalized_mobile,
            scanned_orders=scanned_orders,
            scan_complete=True,
        )

    previous_orders, scanned_orders, scan_complete = await _load_paginated_fallback(
        repository,
        current_order=current_order,
        user_id=str(user_id),
        max_pages=max_pages,
    )
    return CustomerHistoryResult(
        current_order=current_order,
        previous_orders=previous_orders,
        normalized_mobile=normalized_mobile,
        scanned_orders=scanned_orders,
        scan_complete=scan_complete,
    )
