"""Complete, validated Salla item reads; no persistence or inferred authority.

The list subtype survives ordinary internal handling, but JSON only carries a
plain list. Callers must explicitly pass this internal proof to a fenced writer;
a field in a webhook payload cannot confer snapshot authority.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit


MAX_ITEM_PAGES = 100
MAX_ITEM_ROWS = 5000


def _identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid_order_item_identity")
    result = str(value).strip()
    if not result:
        raise ValueError("invalid_order_item_identity")
    return result


def _optional_identity(value: Any) -> str | None:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    return _identity(value)


def _nested_identity(row: dict, key: str) -> str | None:
    nested = row.get(key)
    if nested is None:
        return None
    if not isinstance(nested, dict):
        raise ValueError("invalid_order_item_identity_container")
    return _optional_identity(nested.get("id"))


def _quantity(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid_order_item_quantity")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid_order_item_quantity") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("invalid_order_item_quantity")
    return result


def _contract(rows: Any, order_id: str) -> tuple:
    if not isinstance(rows, list):
        raise ValueError("invalid_order_items")
    if len(rows) > MAX_ITEM_ROWS:
        raise ValueError("order_items_row_limit")
    identities = set()
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_order_item")
        identity = _identity(row.get("id"))
        if identity in identities:
            raise ValueError("duplicate_order_item_id")
        identities.add(identity)
        quantity = _quantity(row.get("quantity"))
        product_id = _optional_identity(row.get("product_id"))
        nested_product_id = _nested_identity(row, "product")
        if product_id and nested_product_id and product_id != nested_product_id:
            raise ValueError("conflicting_order_item_product_identity")
        for observed_order in (
            _optional_identity(row.get("order_id")), _nested_identity(row, "order"),
        ):
            if observed_order is not None and observed_order != order_id:
                raise ValueError("order_item_order_mismatch")
        result.append((identity, quantity, product_id or nested_product_id))
    return tuple(sorted(result, key=lambda item: item[0]))


def validate_order_items(rows: list[dict], *, order_id: Any) -> None:
    """Validate rows without creating or asserting complete-snapshot provenance."""
    _contract(rows, _identity(order_id))


class CompleteOrderItems(list):
    """Validated complete membership tied to one native Salla order ID.

    This is internal provenance, not a substitute for the writer's version,
    ownership and transaction guards. Identity/quantity/product changes revoke
    the captured proof; harmless enrichment may use ``with_rows``.
    """

    __slots__ = ("_order_id", "_original_contract")

    def __init__(self, rows: list[dict], *, order_id: Any):
        self._order_id = _identity(order_id)
        contract = _contract(rows, self._order_id)
        super().__init__(deepcopy(rows))
        self._original_contract = contract

    @property
    def order_id(self) -> str:
        return self._order_id

    def validate_for(self, order_id: Any, rows: list[dict] | None = None):
        """Return this proof or raise ValueError; recheck after mutable handling."""
        if _identity(order_id) != self._order_id:
            raise ValueError("order_item_snapshot_order_mismatch")
        if _contract(self, self._order_id) != self._original_contract:
            raise ValueError("order_item_snapshot_changed")
        if rows is not None and _contract(rows, self._order_id) != self._original_contract:
            raise ValueError("order_item_snapshot_changed")
        return self

    def with_rows(self, enriched: list[dict]):
        """Retain provenance only for the same line identities, quantities/products."""
        self.validate_for(self._order_id, enriched)
        return CompleteOrderItems(enriched, order_id=self._order_id)


def _integer(value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid_item_pagination")
    if isinstance(value, str) and not value.strip().isdecimal():
        raise ValueError("invalid_item_pagination")
    result = int(value)
    if result < minimum:
        raise ValueError("invalid_item_pagination")
    return result


def _pagination_field(metadata: dict, keys: tuple[str, ...], *, minimum=1, required=False):
    values = [_integer(metadata[key], minimum=minimum) for key in keys if key in metadata]
    if not values:
        if required:
            raise ValueError("invalid_item_pagination")
        return None
    if len(set(values)) != 1:
        raise ValueError("conflicting_item_pagination")
    return values[0]


def _pagination(metadata: Any, page: int) -> tuple[tuple, bool]:
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("invalid_item_pagination")
    current = _pagination_field(metadata, ("currentPage", "current_page", "page"), required=True)
    if current != page:
        raise ValueError("invalid_item_page")
    total_pages = _pagination_field(metadata, ("totalPages", "total_pages", "last_page"), minimum=0)
    total_rows = _pagination_field(metadata, ("total", "totalItems", "total_items"), minimum=0)
    per_page = _pagination_field(metadata, ("perPage", "per_page"))
    links = metadata.get("links")
    if "links" in metadata and not isinstance(links, dict):
        raise ValueError("invalid_item_pagination")
    has_next = isinstance(links, dict) and "next" in links
    continuation = None
    if has_next:
        next_link = links["next"]
        if next_link is None or next_link == "":
            continuation = False
        elif isinstance(next_link, str):
            try:
                pages = parse_qs(urlsplit(next_link).query, keep_blank_values=True).get("page", [])
                next_page = _integer(pages[0], minimum=1) if len(pages) == 1 else None
            except ValueError as exc:
                raise ValueError("invalid_item_next_page") from exc
            if next_page != page + 1:
                raise ValueError("invalid_item_next_page")
            continuation = True
        else:
            raise ValueError("invalid_item_next_page")
    if total_pages is None:
        if continuation is None:
            raise ValueError("item_pagination_completion_unknown")
        exhausted = not continuation
    else:
        if total_pages > MAX_ITEM_PAGES:
            raise ValueError("order_items_page_limit")
        if page > max(1, total_pages):
            raise ValueError("invalid_item_page")
        exhausted = total_pages == 0 or page == total_pages
        if continuation is not None and continuation == exhausted:
            raise ValueError("conflicting_item_pagination")
    if total_rows is not None and total_rows > MAX_ITEM_ROWS:
        raise ValueError("order_items_row_limit")
    return (total_pages, total_rows, per_page, has_next), exhausted


async def fetch_complete_order_items(
    db: Any, user_id: str, internal_id: Any, *,
    request: Callable[..., Awaitable[dict]],
) -> CompleteOrderItems:
    """Read the documented complete array or every explicit item page.

    Pass ``request=call_salla`` at the existing provider boundary. Never follow
    provider pagination URLs; later requests keep the same endpoint/order ID.
    No partial result escapes on provider, identity or pagination failure.
    """
    order_id = _identity(internal_id)
    items = []
    seen = set()
    pagination_contract = None
    for page in range(1, MAX_ITEM_PAGES + 1):
        params = {"order_id": order_id}
        if page > 1:
            params["page"] = page
        response = await request(db, user_id, "GET", "/orders/items", params=params)
        if not isinstance(response, dict) or response.get("success") is False:
            raise ValueError("invalid_order_items_response")
        rows = response.get("data")
        row_contract = _contract(rows, order_id)
        for identity, _, _ in row_contract:
            if identity in seen:
                raise ValueError("duplicate_order_item_id")
            seen.add(identity)
        if len(items) + len(rows) > MAX_ITEM_ROWS:
            raise ValueError("order_items_row_limit")
        if "pagination" not in response:
            if page != 1:
                raise ValueError("item_pagination_disappeared")
            return CompleteOrderItems(rows, order_id=order_id)
        current_contract, exhausted = _pagination(response["pagination"], page)
        if pagination_contract is not None and current_contract != pagination_contract:
            raise ValueError("item_pagination_changed")
        pagination_contract = current_contract
        total_pages, total_rows, per_page, _ = current_contract
        if (total_pages == 0 and rows
                or per_page is not None and len(rows) > per_page
                or not rows and (page != 1 or not exhausted)):
            raise ValueError("incomplete_order_items")
        items.extend(deepcopy(rows))
        if total_rows is not None and len(items) > total_rows:
            raise ValueError("conflicting_item_pagination")
        if exhausted:
            if total_rows is not None and len(items) != total_rows:
                raise ValueError("incomplete_order_items")
            return CompleteOrderItems(items, order_id=order_id)
        if page < MAX_ITEM_PAGES:
            await asyncio.sleep(0.25)
    raise ValueError("order_items_page_limit")
