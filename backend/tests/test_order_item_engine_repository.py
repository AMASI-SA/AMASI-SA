"""Read-only repository tests for the Order Item Engine."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from order_engine.repository import OrderDiscoveryRow
from order_item_engine.repository import (
    OrderEngineItemRepository,
    OrderItemNotFoundError,
)


def make_raw(
    order_number: str,
    *,
    item_ids: list[str],
) -> dict:
    return {
        "id": f"source-{order_number}",
        "reference_id": order_number,
        "date": {
            "date": "2026-07-14 10:00:00",
        },
        "status": {
            "slug": "in_progress",
            "name": "قيد التنفيذ",
        },
        "customer": {
            "full_name": "عميل اختبار",
        },
        "amounts": {
            "total": {
                "amount": 100,
                "currency": "SAR",
            },
        },
        "items": [
            {
                "id": item_id,
                "quantity": 1,
                "product": {
                    "id": "product-1",
                    "name": f"منتج {item_id}",
                    "sku": "SKU-1",
                },
            }
            for item_id in item_ids
        ],
    }


class FakeOrderRepository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.calls = []
        self.write_calls = 0

    async def list_salla_orders(
        self,
        *,
        user_id,
        limit,
        before_order_date=None,
        before_order_number=None,
    ):
        self.calls.append({
            "method": "list",
            "user_id": user_id,
            "limit": limit,
            "before_order_date": before_order_date,
            "before_order_number": before_order_number,
        })

        rows = [
            row
            for row in self.rows
            if row["user_id"] == user_id
        ]

        if before_order_date and before_order_number:
            rows = [
                row
                for row in rows
                if (
                    row["order_date"] < before_order_date
                    or (
                        row["order_date"] == before_order_date
                        and row["order_number"]
                        < before_order_number
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
                salla_raw=deepcopy(row["raw"]),
            )
            for row in rows[:limit]
        ]

    async def get_salla_order(
        self,
        *,
        user_id,
        order_number,
    ):
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
                    salla_raw=deepcopy(row["raw"]),
                )

        return None

    async def insert_one(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("repository must not write")


@pytest.fixture
def rows():
    return [
        {
            "user_id": "owner-1",
            "order_number": "300",
            "order_date": "2026-07-14",
            "raw": make_raw(
                "300",
                item_ids=["item-300-a", "item-300-b"],
            ),
        },
        {
            "user_id": "owner-1",
            "order_number": "200",
            "order_date": "2026-07-13",
            "raw": make_raw(
                "200",
                item_ids=["item-200-a"],
            ),
        },
        {
            "user_id": "other",
            "order_number": "999",
            "order_date": "2026-07-15",
            "raw": make_raw(
                "999",
                item_ids=["item-999-a"],
            ),
        },
    ]


@pytest.mark.asyncio
async def test_lists_item_identities_from_canonical_orders(rows):
    source_repository = FakeOrderRepository(rows)
    repository = OrderEngineItemRepository(
        source_repository
    )

    page = await repository.list_items(
        user_id="owner-1",
        limit=2,
    )

    assert page.source_order_count == 2
    assert len(page.items) == 3
    assert [
        item.order_number for item in page.items
    ] == ["300", "300", "200"]

    assert [
        item.line_index for item in page.items
    ] == [0, 1, 0]

    assert source_repository.write_calls == 0


@pytest.mark.asyncio
async def test_gets_all_items_for_one_order(rows):
    repository = OrderEngineItemRepository(
        FakeOrderRepository(rows)
    )

    items = await repository.get_items_for_order(
        user_id="owner-1",
        order_number="300",
    )

    assert len(items) == 2
    assert all(
        item.order_number == "300"
        for item in items
    )


@pytest.mark.asyncio
async def test_gets_one_exact_item(rows):
    repository = OrderEngineItemRepository(
        FakeOrderRepository(rows)
    )

    items = await repository.get_items_for_order(
        user_id="owner-1",
        order_number="300",
    )
    target_id = items[1].order_item_id

    result = await repository.get_item(
        user_id="owner-1",
        order_number="300",
        order_item_id=target_id,
    )

    assert result.order_item_id == target_id
    assert result.line_index == 1


@pytest.mark.asyncio
async def test_rejects_item_from_other_order(rows):
    repository = OrderEngineItemRepository(
        FakeOrderRepository(rows)
    )

    other_items = await repository.get_items_for_order(
        user_id="owner-1",
        order_number="200",
    )

    with pytest.raises(OrderItemNotFoundError):
        await repository.get_item(
            user_id="owner-1",
            order_number="300",
            order_item_id=other_items[0].order_item_id,
        )


@pytest.mark.asyncio
async def test_rejects_cross_tenant_order(rows):
    repository = OrderEngineItemRepository(
        FakeOrderRepository(rows)
    )

    with pytest.raises(OrderItemNotFoundError):
        await repository.get_item(
            user_id="owner-1",
            order_number="999",
            order_item_id="anything",
        )


@pytest.mark.asyncio
async def test_empty_item_id_is_rejected(rows):
    repository = OrderEngineItemRepository(
        FakeOrderRepository(rows)
    )

    with pytest.raises(OrderItemNotFoundError):
        await repository.get_item(
            user_id="owner-1",
            order_number="300",
            order_item_id="",
        )


def test_repository_has_no_write_api():
    forbidden = {
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "replace_one",
        "bulk_write",
    }

    assert set(
        dir(OrderEngineItemRepository)
    ).isdisjoint(forbidden)


def test_repository_has_no_direct_storage_dependency():
    import ast
    import inspect
    import order_item_engine.repository as repository

    tree = ast.parse(
        inspect.getsource(repository)
    )

    imported_modules = set()
    attribute_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(
                alias.name for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)
        elif isinstance(node, ast.Attribute):
            attribute_names.add(node.attr)

    forbidden_modules = {
        "motor",
        "pymongo",
        "requests",
        "httpx",
        "fastapi",
        "server",
    }

    assert not any(
        module == blocked
        or module.startswith(f"{blocked}.")
        for module in imported_modules
        for blocked in forbidden_modules
    )

    assert attribute_names.isdisjoint({
        "unified_orders",
        "raw_by_source",
        "find",
        "find_one",
        "insert_one",
        "update_one",
        "delete_one",
    })
