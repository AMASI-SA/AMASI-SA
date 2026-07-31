"""Complete customer-history lookup tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import re

import pytest

from order_engine.customer_history import get_customer_history
from order_engine.customer_history_store import _mobile_regex
from order_engine.repository import OrderDiscoveryRow


def make_raw(order_number: str, created_at: str, mobile: str) -> dict:
    return {
        "id": f"id-{order_number}",
        "reference_id": order_number,
        "date": {"date": created_at},
        "status": {
            "slug": "completed",
            "name": "تم التنفيذ",
        },
        "customer": {
            "full_name": "عميل السجل الكامل",
            "mobile": mobile,
        },
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


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.index = 0

    def sort(self, fields):
        self.rows.sort(
            key=lambda row: (row["order_date"], row["order_number"]),
            reverse=True,
        )
        return self

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index >= len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.index]
        self.index += 1
        return deepcopy(row)


class FakeCollection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.last_query = None

    def find(self, query, projection):
        self.last_query = deepcopy(query)
        excluded = query["order_number"]["$ne"]
        rows = [
            row
            for row in self.rows
            if row["user_id"] == query["user_id"]
            and row["order_number"] != excluded
        ]
        return FakeCursor(rows)


class FakeMongoRepository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self._collection = FakeCollection(rows)
        self.pagination_calls = 0

    async def get_salla_order(self, *, user_id, order_number):
        for row in self.rows:
            if row["user_id"] == user_id and row["order_number"] == order_number:
                return OrderDiscoveryRow(
                    order_number=row["order_number"],
                    order_date=row["order_date"],
                    salla_raw=deepcopy(row["raw_by_source"]["salla_direct"]),
                    current_status=row.get("order_status"),
                )
        return None

    async def list_salla_orders(self, **kwargs):
        self.pagination_calls += 1
        raise AssertionError("complete Mongo lookup must not paginate all orders")


def mongo_row(order_number: str, created_at: str, mobile: str) -> dict:
    return {
        "user_id": "owner-1",
        "order_number": order_number,
        "order_date": created_at[:10],
        "order_status": "تم التنفيذ",
        "customer_mobile": mobile,
        "raw_by_source": {
            "salla_direct": make_raw(order_number, created_at, mobile),
        },
    }


def test_mobile_regex_accepts_supported_formatted_variants():
    pattern = _mobile_regex("966570076958")
    assert pattern is not None
    for value in (
        "570076958",
        "0570076958",
        "+966570076958",
        "00966570076958",
        "+966 57 007 6958",
        "05-700-76958",
    ):
        assert re.fullmatch(pattern, value)


@pytest.mark.asyncio
async def test_complete_lookup_finds_history_beyond_three_hundred_orders():
    rows = [
        mongo_row("999", "2026-07-31 10:00:00", "+966570076958")
    ]
    start = datetime(2025, 1, 1, 10, 0, 0)
    for index in range(350):
        created_at = (start + timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S")
        mobile = (
            "0570076958"
            if index % 3 == 0
            else "570076958"
            if index % 3 == 1
            else "+966570076958"
        )
        rows.append(mongo_row(f"{index + 1:03d}", created_at, mobile))

    repository = FakeMongoRepository(rows)
    result = await get_customer_history(
        repository,
        user_id="owner-1",
        order_number="999",
    )

    assert result.customer_found is True
    assert result.scan_complete is True
    assert result.scanned_orders == 350
    assert len(result.previous_orders) == 350
    assert repository.pagination_calls == 0

    query = repository._collection.last_query
    assert query["order_number"] == {"$ne": "999"}
    assert query["raw_by_source.salla_direct"] == {"$exists": True}
    assert len(query["$or"]) >= 6
