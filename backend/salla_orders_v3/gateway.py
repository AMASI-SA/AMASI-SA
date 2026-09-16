"""One read-only Salla Orders API gateway for V3."""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from salla_integration.service import SallaError, call_salla

from .config import (
    MAX_DISCOVERY_PAGES_PER_RUN,
    MAX_PROVIDER_ATTEMPTS,
    ORDERS_PER_PAGE,
    validate_provider_read_request,
)
from .normalizer import has_order_item_identity


# Compatibility aliases retained for the existing internal import surface.
MAX_PAGES_PER_RUN = MAX_DISCOVERY_PAGES_PER_RUN
MAX_ATTEMPTS = MAX_PROVIDER_ATTEMPTS

ProviderCall = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class PaginationPage:
    current_page: int
    total_pages: int | None
    next_page: int | None
    exhausted: bool

    @property
    def continuation(self) -> bool:
        return not self.exhausted


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("Salla Orders endpoint returned invalid payload")
    if any(not isinstance(row, dict) for row in data):
        raise RuntimeError("Salla Orders endpoint returned invalid order")
    return [deepcopy(row) for row in data]


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}") from exc
    if parsed < 1:
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}")
    return parsed


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}") from exc
    if parsed < 0:
        raise RuntimeError(f"Salla Orders pagination metadata has invalid {field}")
    return parsed


def _first_defined(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _pagination(response: Any, requested_page: int) -> PaginationPage:
    pagination = response.get("pagination") if isinstance(response, dict) else None
    if not isinstance(pagination, dict) or not pagination:
        raise RuntimeError("Salla Orders pagination metadata is missing")

    current_value = _first_defined(
        pagination,
        "currentPage",
        "current_page",
        "page",
    )
    current = _positive_int(current_value, field="current page")
    if current != requested_page:
        raise RuntimeError("Salla Orders pagination metadata changed the requested page")

    total_value = _first_defined(
        pagination,
        "totalPages",
        "total_pages",
        "last_page",
    )
    total_pages = (
        _nonnegative_int(total_value, field="total pages")
        if total_value is not None
        else None
    )
    if total_pages is not None and current > max(1, total_pages):
        raise RuntimeError("Salla Orders pagination metadata is inconsistent")

    links = pagination.get("links")
    next_link = links.get("next") if isinstance(links, dict) else None
    if total_pages is not None:
        exhausted = total_pages == 0 or current >= total_pages
        next_page = None if exhausted else current + 1
    else:
        exhausted = not bool(next_link)
        next_page = None if exhausted else current + 1
    return PaginationPage(
        current_page=current,
        total_pages=total_pages,
        next_page=next_page,
        exhausted=exhausted,
    )


class SallaOrdersGateway:
    def __init__(
        self,
        db: Any,
        *,
        call_provider: ProviderCall = call_salla,
        sleep: Callable[[float], Any] = asyncio.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.db = db
        self._call_provider = call_provider
        self._sleep = sleep
        self.max_attempts = max(1, min(int(max_attempts), MAX_ATTEMPTS))

    async def _pause(self, seconds: float) -> None:
        result = self._sleep(seconds)
        if inspect.isawaitable(result):
            await result

    async def _get(
        self,
        user_id: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        validate_provider_read_request("GET", path)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._call_provider(
                    self.db,
                    str(user_id),
                    "GET",
                    path,
                    params=params,
                )
                if not isinstance(response, dict):
                    raise RuntimeError(f"Salla {path} returned invalid response")
                return response
            except SallaError as exc:
                last_error = exc
                transient = exc.status_code == 429 or exc.status_code >= 500
                if not transient or attempt >= self.max_attempts:
                    raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    raise
            await self._pause(0.25 * (2 ** (attempt - 1)))
        raise RuntimeError(f"Salla GET failed: {path}") from last_error

    async def list_light_orders_page(
        self,
        user_id: str,
        *,
        page: int,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], PaginationPage]:
        params: dict[str, Any] = {
            "page": max(1, int(page)),
            "per_page": ORDERS_PER_PAGE,
            "format": "light",
        }
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        response = await self._get(user_id, "/orders", params=params)
        return _rows(response), _pagination(response, int(params["page"]))

    async def iter_light_orders(
        self,
        user_id: str,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        max_pages: int = MAX_PAGES_PER_RUN,
        start_page: int = 1,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = max(1, int(start_page))
        pages_read = 0
        page_budget = max(1, min(int(max_pages), MAX_PAGES_PER_RUN))
        while pages_read < page_budget:
            rows, pagination = await self.list_light_orders_page(
                user_id,
                page=page,
                from_date=from_date,
                to_date=to_date,
            )
            yield rows
            pages_read += 1
            if pagination.exhausted:
                break
            if pagination.next_page is None:
                raise RuntimeError("Salla Orders pagination metadata omitted next page")
            page = pagination.next_page
            await self._pause(0.15)

    async def resolve_light_order(
        self,
        user_id: str,
        order_number: str,
    ) -> Optional[dict[str, Any]]:
        normalized = str(order_number or "").strip()
        response = await self._get(
            user_id,
            "/orders",
            params={
                "reference_id": normalized,
                "per_page": ORDERS_PER_PAGE,
                "format": "light",
            },
        )
        for row in _rows(response):
            reference = str(row.get("reference_id") or "").strip()
            internal_id = str(row.get("id") or "").strip()
            if normalized in {reference, internal_id}:
                return row
        return None

    async def get_light_order_details(
        self,
        user_id: str,
        internal_order_id: str,
    ) -> dict[str, Any]:
        internal_id = str(internal_order_id or "").strip()
        if not internal_id:
            raise ValueError("internal_order_id is required")
        response = await self._get(
            user_id,
            f"/orders/{internal_id}",
            params={"format": "light"},
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Salla Order Details returned invalid payload")
        return deepcopy(data)

    async def get_order_items(
        self,
        user_id: str,
        internal_order_id: str,
    ) -> list[dict[str, Any]]:
        internal_id = str(internal_order_id or "").strip()
        if not internal_id:
            raise ValueError("internal_order_id is required")
        response = await self._get(
            user_id,
            "/orders/items",
            params={"order_id": internal_id},
        )
        data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError("Salla List Order Items returned invalid payload")
        if any(not isinstance(row, dict) for row in data):
            raise RuntimeError("Salla List Order Items returned invalid item")
        if any(not has_order_item_identity(row) for row in data):
            raise RuntimeError("Salla List Order Items returned item without identity")
        return [deepcopy(row) for row in data]
