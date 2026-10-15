"""Read-only service tests for the Mezan Order Engine."""

from copy import deepcopy

import pytest

from order_engine.repository import OrderDiscoveryRow
from order_engine.service import (
    InvalidOrderCursorError,
    OrderNotFoundError,
    _decode_cursor,
    _encode_cursor,
    get_order,
    get_orders,
    list_orders,
)


def make_raw(order_number: str, created_at: str) -> dict:
    return {
        "id": f"id-{order_number}",
        "reference_id": order_number,
        "date": {"date": created_at},
        "status": {
            "slug": "in_progress",
            "name": "قيد التنفيذ",
        },
        "customer": {"full_name": "عميل اختبار"},
        "amounts": {
            "total": {"amount": 100, "currency": "SAR"},
        },
        "items": [
            {
                "id": f"item-{order_number}",
                "quantity": 1,
                "product": {
                    "id": "p1",
                    "name": "منتج",
                    "sku": "SKU-1",
                },
            }
        ],
    }


class FakeOrderRepository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.calls = []

    async def list_salla_orders(
        self,
        *,
        user_id,
        limit,
        before_order_date=None,
        before_order_number=None,
        status_group=None,
        status_exact=None,
    ):
        self.calls.append({
            "method": "list",
            "user_id": user_id,
            "limit": limit,
            "before_order_date": before_order_date,
            "before_order_number": before_order_number,
            "status_group": status_group,
            "status_exact": status_exact,
        })

        rows = [
            row for row in self.rows
            if row["user_id"] == user_id
        ]

        if before_order_date and before_order_number:
            rows = [
                row for row in rows
                if (
                    row["order_date"] < before_order_date
                    or (
                        row["order_date"] == before_order_date
                        and row["order_number"] < before_order_number
                    )
                )
            ]

        rows.sort(
            key=lambda row: (
                row["order_date"],
                row["order_number"],
            ),
            reverse=True,
        )

        return [
            OrderDiscoveryRow(
                order_number=row["order_number"],
                order_date=row["order_date"],
                salla_raw=deepcopy(row["salla_raw"]),
            )
            for row in rows[:limit]
        ]

    async def get_salla_order(self, *, user_id, order_number):
        self.calls.append({
            "method": "get",
            "user_id": user_id,
            "order_number": order_number,
        })

        for row in self.rows:
            if (
                row["user_id"] == user_id
                and row["order_number"] == order_number
            ):
                return OrderDiscoveryRow(
                    order_number=row["order_number"],
                    order_date=row["order_date"],
                    salla_raw=deepcopy(row["salla_raw"]),
                )

        return None

    async def get_salla_orders(self, *, user_id, order_numbers):
        self.calls.append({
            "method": "get_many",
            "user_id": user_id,
            "order_numbers": list(order_numbers),
        })
        requested = set(order_numbers)
        return [
            OrderDiscoveryRow(
                order_number=row["order_number"],
                order_date=row["order_date"],
                salla_raw=deepcopy(row["salla_raw"]),
            )
            for row in self.rows
            if row["user_id"] == user_id and row["order_number"] in requested
        ]


@pytest.fixture
def rows():
    return [
        {
            "user_id": "u1",
            "order_number": "300",
            "order_date": "2026-07-13",
            "salla_raw": make_raw(
                "300",
                "2026-07-13 10:00:00",
            ),
        },
        {
            "user_id": "u1",
            "order_number": "200",
            "order_date": "2026-07-12",
            "salla_raw": make_raw(
                "200",
                "2026-07-12 10:00:00",
            ),
        },
        {
            "user_id": "u1",
            "order_number": "100",
            "order_date": "2026-07-11",
            "salla_raw": make_raw(
                "100",
                "2026-07-11 10:00:00",
            ),
        },
        {
            "user_id": "other",
            "order_number": "999",
            "order_date": "2026-07-14",
            "salla_raw": make_raw(
                "999",
                "2026-07-14 10:00:00",
            ),
        },
    ]


@pytest.mark.asyncio
async def test_list_orders_returns_creation_date_ordered_page(rows):
    repository = FakeOrderRepository(rows)

    page = await list_orders(
        repository,
        user_id="u1",
        limit=2,
    )

    assert [item.order_number for item in page.items] == ["300", "200"]
    assert page.next_cursor is not None


@pytest.mark.asyncio
async def test_cursor_returns_next_page_without_duplicates(rows):
    repository = FakeOrderRepository(rows)

    first = await list_orders(
        repository,
        user_id="u1",
        limit=2,
    )
    second = await list_orders(
        repository,
        user_id="u1",
        limit=2,
        cursor=first.next_cursor,
    )

    assert [item.order_number for item in second.items] == ["100"]
    assert {
        item.order_number for item in first.items
    }.isdisjoint({
        item.order_number for item in second.items
    })


@pytest.mark.asyncio
async def test_get_order_returns_exact_tenant_order(rows):
    repository = FakeOrderRepository(rows)

    order = await get_order(
        repository,
        user_id="u1",
        order_number="200",
    )

    assert order.order_number == "200"
    assert order.source.provider == "salla"


@pytest.mark.asyncio
async def test_get_orders_bulk_maps_exact_tenant_orders(rows):
    repository = FakeOrderRepository(rows)

    orders = await get_orders(
        repository,
        user_id="u1",
        order_numbers=["300", "200", "300", "999"],
    )

    assert set(orders) == {"300", "200"}
    assert [call["method"] for call in repository.calls] == ["get_many"]
    assert repository.calls[0]["order_numbers"] == ["300", "200", "999"]


@pytest.mark.asyncio
async def test_get_order_rejects_other_tenant(rows):
    repository = FakeOrderRepository(rows)

    with pytest.raises(OrderNotFoundError):
        await get_order(
            repository,
            user_id="u1",
            order_number="999",
        )


@pytest.mark.asyncio
async def test_invalid_raw_rows_are_skipped(rows):
    rows.insert(
        0,
        {
            "user_id": "u1",
            "order_number": "400",
            "order_date": "2026-07-14",
            "salla_raw": {
                "id": "400",
                "reference_id": "400",
            },
        },
    )
    repository = FakeOrderRepository(rows)

    page = await list_orders(
        repository,
        user_id="u1",
        limit=2,
    )

    assert [item.order_number for item in page.items] == ["300", "200"]
    assert page.skipped_invalid == 1


def test_cursor_round_trip():
    cursor = _encode_cursor("2026-07-13", "300")

    assert _decode_cursor(cursor) == {
        "order_date": "2026-07-13",
        "order_number": "300",
    }


def test_invalid_cursor_is_rejected():
    with pytest.raises(InvalidOrderCursorError):
        _decode_cursor("not-a-valid-cursor")


def test_service_has_no_mongo_collection_dependency():
    import ast
    import inspect
    import order_engine.service as service

    source = inspect.getsource(service)
    tree = ast.parse(source)

    imported_modules = set()
    attribute_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
        elif isinstance(node, ast.Attribute):
            attribute_names.add(node.attr)

    assert not any(
        module == "motor"
        or module.startswith("motor.")
        or module == "pymongo"
        or module.startswith("pymongo.")
        for module in imported_modules
    )

    forbidden_attributes = {
        "unified_orders",
        "find",
        "find_one",
        "insert_one",
        "update_one",
        "delete_one",
    }

    assert attribute_names.isdisjoint(forbidden_attributes)
