"""Read-only service tests for the Mezan Order Engine."""

from copy import deepcopy

import pytest

from order_engine.service import (
    InvalidOrderCursorError,
    OrderNotFoundError,
    _decode_cursor,
    _encode_cursor,
    get_order,
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


class FakeAsyncCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, fields):
        for key, direction in reversed(fields):
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.writes = 0

    def find(self, query, projection):
        rows = [
            row for row in self.rows
            if row.get("user_id") == query.get("user_id")
            and isinstance(
                ((row.get("raw_by_source") or {}).get("salla_direct")),
                dict,
            )
        ]

        cursor_or = query.get("$or")
        if cursor_or:
            date_limit = cursor_or[0]["order_date"]["$lt"]
            exact_date = cursor_or[1]["order_date"]
            number_limit = cursor_or[1]["order_number"]["$lt"]

            rows = [
                row for row in rows
                if (
                    str(row.get("order_date") or "") < date_limit
                    or (
                        str(row.get("order_date") or "") == exact_date
                        and str(row.get("order_number") or "") < number_limit
                    )
                )
            ]

        return FakeAsyncCursor(rows)

    async def find_one(self, query, projection):
        for row in self.rows:
            if (
                row.get("user_id") == query.get("user_id")
                and row.get("order_number") == query.get("order_number")
                and isinstance(
                    ((row.get("raw_by_source") or {}).get("salla_direct")),
                    dict,
                )
            ):
                return deepcopy(row)

        return None

    async def insert_one(self, *args, **kwargs):
        self.writes += 1
        raise AssertionError("Order Engine service must not write")

    async def update_one(self, *args, **kwargs):
        self.writes += 1
        raise AssertionError("Order Engine service must not write")


class FakeDB:
    def __init__(self, rows):
        self.unified_orders = FakeCollection(rows)


@pytest.fixture
def rows():
    return [
        {
            "user_id": "u1",
            "order_number": "300",
            "order_date": "2026-07-13",
            "raw_by_source": {
                "salla_direct": make_raw(
                    "300",
                    "2026-07-13 10:00:00",
                )
            },
        },
        {
            "user_id": "u1",
            "order_number": "200",
            "order_date": "2026-07-12",
            "raw_by_source": {
                "salla_direct": make_raw(
                    "200",
                    "2026-07-12 10:00:00",
                )
            },
        },
        {
            "user_id": "u1",
            "order_number": "100",
            "order_date": "2026-07-11",
            "raw_by_source": {
                "salla_direct": make_raw(
                    "100",
                    "2026-07-11 10:00:00",
                )
            },
        },
        {
            "user_id": "other",
            "order_number": "999",
            "order_date": "2026-07-14",
            "raw_by_source": {
                "salla_direct": make_raw(
                    "999",
                    "2026-07-14 10:00:00",
                )
            },
        },
    ]


@pytest.mark.asyncio
async def test_list_orders_returns_creation_date_ordered_page(rows):
    db = FakeDB(rows)

    page = await list_orders(db, user_id="u1", limit=2)

    assert [item.order_number for item in page.items] == ["300", "200"]
    assert page.next_cursor is not None
    assert db.unified_orders.writes == 0


@pytest.mark.asyncio
async def test_cursor_returns_next_page_without_duplicates(rows):
    db = FakeDB(rows)

    first = await list_orders(db, user_id="u1", limit=2)
    second = await list_orders(
        db,
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
    db = FakeDB(rows)

    order = await get_order(
        db,
        user_id="u1",
        order_number="200",
    )

    assert order.order_number == "200"
    assert order.source.provider == "salla"


@pytest.mark.asyncio
async def test_get_order_rejects_other_tenant(rows):
    db = FakeDB(rows)

    with pytest.raises(OrderNotFoundError):
        await get_order(
            db,
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
            "raw_by_source": {
                "salla_direct": {
                    "id": "400",
                    "reference_id": "400",
                }
            },
        },
    )
    db = FakeDB(rows)

    page = await list_orders(db, user_id="u1", limit=2)

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
