"""One read-only Salla Orders API gateway for V3."""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from salla_integration.service import SallaError, call_salla


ORDERS_PER_PAGE = 30
MAX_PAGES_PER_RUN = 200
MAX_ATTEMPTS = 3

ProviderCall = Callable[..., Awaitable[dict[str, Any]]]


def _rows(response: Any) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("Salla Orders endpoint returned invalid payload")
    return [deepcopy(row) for row in data if isinstance(row, dict)]


def _pagination(response: Any, requested_page: int) -> tuple[int, int, bool]:
    pagination = response.get("pagination") if isinstance(response, dict) else None
    pagination = pagination if isinstance(pagination, dict) else {}
    current = int(
        pagination.get("currentPage")
        or pagination.get("current_page")
        or pagination.get("page")
        or requested_page
    )
    total_pages = int(
        pagination.get("totalPages")
        or pagination.get("total_pages")
        or pagination.get("last_page")
        or 0
    )
    links = pagination.get("links")
    next_link = links.get("next") if isinstance(links, dict) else None
    exhausted = bool(total_pages and current >= total_pages)
    if not total_pages and pagination:
        exhausted = not bool(next_link)
    return current, total_pages, exhausted


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
        self.max_attempts = max(1, int(max_attempts))

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
        updated_at_gt: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        params: dict[str, Any] = {
            "page": max(1, int(page)),
            "per_page": ORDERS_PER_PAGE,
            "format": "light",
        }
        if from_date:
            params["from_date"] = from_date
        if to_date:
            params["to_date"] = to_date
        if updated_at_gt:
            params["updated_at_gt"] = updated_at_gt
        response = await self._get(user_id, "/orders", params=params)
        return _rows(response), response

    async def iter_light_orders(
        self,
        user_id: str,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        updated_at_gt: Optional[str] = None,
        max_pages: int = MAX_PAGES_PER_RUN,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1
        while page <= max(1, int(max_pages)):
            rows, response = await self.list_light_orders_page(
                user_id,
                page=page,
                from_date=from_date,
                to_date=to_date,
                updated_at_gt=updated_at_gt,
            )
            if not rows:
                break
            yield rows
            current, _total, exhausted = _pagination(response, page)
            if exhausted:
                break
            page = max(page + 1, current + 1)
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
        return [deepcopy(row) for row in data if isinstance(row, dict)]
