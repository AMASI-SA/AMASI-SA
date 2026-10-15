"""Read-only Salla customer-history lookup for order review pages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

from salla_integration.service import call_salla

from .mapper import OrderMappingError
from .models import OrderDTO
from .repository import OrderRepository
from .service import _map_row, get_order

SALLA_CUSTOMER_HISTORY_PER_PAGE = 50
SALLA_CUSTOMER_HISTORY_MAX_PAGES = 100

SallaRequest = Callable[..., Awaitable[dict[str, Any]]]


def normalize_saudi_mobile(value: Any) -> Optional[str]:
    """Return a Saudi mobile in canonical ``9665XXXXXXXX`` form."""

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


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _pagination_complete(payload: dict[str, Any], requested_page: int) -> bool:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        return False

    last_page = _positive_int(
        pagination.get("totalPages")
        or pagination.get("total_pages")
        or pagination.get("last_page")
    )
    current_page = _positive_int(
        pagination.get("currentPage")
        or pagination.get("current_page")
        or pagination.get("page")
        or requested_page
    )
    return bool(last_page and current_page >= last_page)


async def _load_salla_customer_orders(
    db: Any,
    *,
    user_id: str,
    customer_id: str,
    exclude_order_number: str,
    salla_request: SallaRequest,
    max_pages: int,
) -> tuple[list[OrderDTO], int, bool]:
    """Load this customer's authoritative order history from Salla.

    ``customer_id`` is supported by Salla's List Orders endpoint. The local
    ``unified_orders`` collection is deliberately not consulted for history;
    it is used only to load the order currently open in the review page.
    """

    previous_orders: list[OrderDTO] = []
    seen_order_numbers: set[str] = set()
    scanned_orders = 0
    scan_complete = False
    previous_page_signature: Optional[tuple[str, ...]] = None

    for page in range(1, max(1, int(max_pages)) + 1):
        payload = await salla_request(
            db,
            str(user_id),
            "GET",
            "/orders",
            params={
                "customer_id": str(customer_id),
                "page": page,
                "per_page": SALLA_CUSTOMER_HISTORY_PER_PAGE,
                "format": "light",
            },
        )
        raw_rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_rows, list):
            raise RuntimeError("Salla List Orders returned invalid customer history")
        if not raw_rows:
            scan_complete = True
            break

        scanned_orders += len(raw_rows)
        page_signature = tuple(
            str(row.get("reference_id") or row.get("order_number") or row.get("id") or "")
            for row in raw_rows
            if isinstance(row, dict)
        )
        if page_signature and page_signature == previous_page_signature:
            break
        previous_page_signature = page_signature

        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            try:
                candidate = _map_row(raw)
            except OrderMappingError:
                continue
            if candidate.order_number == str(exclude_order_number):
                continue
            if candidate.order_number in seen_order_numbers:
                continue
            previous_orders.append(candidate)
            seen_order_numbers.add(candidate.order_number)

        if _pagination_complete(payload, page):
            scan_complete = True
            break

    previous_orders.sort(key=lambda order: order.created_at, reverse=True)
    return previous_orders, scanned_orders, scan_complete


async def _resolve_salla_customer_id(
    db: Any,
    *,
    user_id: str,
    current_order: OrderDTO,
    salla_request: SallaRequest,
) -> str:
    customer_id = str(current_order.customer.customer_id or "").strip()
    if customer_id:
        return customer_id

    payload = await salla_request(
        db,
        str(user_id),
        "GET",
        f"/orders/{quote(str(current_order.order_id), safe='')}",
        params={"format": "light"},
    )
    raw_order = payload.get("data") if isinstance(payload, dict) else None
    customer = raw_order.get("customer") if isinstance(raw_order, dict) else None
    if not isinstance(customer, dict):
        return ""
    return str(customer.get("id") or "").strip()


async def get_customer_history(
    repository: OrderRepository,
    *,
    db: Any,
    user_id: str,
    order_number: str,
    salla_request: SallaRequest = call_salla,
    max_pages: int = SALLA_CUSTOMER_HISTORY_MAX_PAGES,
) -> CustomerHistoryResult:
    """Load the current local order and its prior orders directly from Salla."""

    current_order = await get_order(
        repository,
        user_id=str(user_id),
        order_number=str(order_number),
    )
    normalized_mobile = normalize_saudi_mobile(current_order.customer.mobile)
    customer_id = await _resolve_salla_customer_id(
        db,
        user_id=str(user_id),
        current_order=current_order,
        salla_request=salla_request,
    )

    if not customer_id:
        return CustomerHistoryResult(
            current_order=current_order,
            previous_orders=[],
            normalized_mobile=normalized_mobile,
            scanned_orders=0,
            scan_complete=True,
        )

    previous_orders, scanned_orders, scan_complete = await _load_salla_customer_orders(
        db,
        user_id=str(user_id),
        customer_id=customer_id,
        exclude_order_number=current_order.order_number,
        salla_request=salla_request,
        max_pages=max_pages,
    )
    return CustomerHistoryResult(
        current_order=current_order,
        previous_orders=previous_orders,
        normalized_mobile=normalized_mobile,
        scanned_orders=scanned_orders,
        scan_complete=scan_complete,
    )
