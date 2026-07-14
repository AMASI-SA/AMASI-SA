"""Application service tests for the Order Item Engine."""

from datetime import datetime, timezone

import pytest

from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemSourceDTO,
)
from order_item_engine.repository import (
    OrderItemNotFoundError,
    OrderItemPage,
)
from order_item_engine.service import (
    InvalidOrderItemRequestError,
    OrderItemService,
    OrderItemServiceNotFoundError,
)


def make_identity(
    *,
    order_number: str = "300",
    order_item_id: str = "item-300-a",
) -> OrderItemIdentityDTO:
    return OrderItemIdentityDTO(
        order_item_id=order_item_id,
        order_id=f"source-{order_number}",
        order_number=order_number,
        order_created_at=datetime(
            2026,
            7,
            14,
            10,
            0,
            tzinfo=timezone.utc,
        ),
        line_index=0,
        source=OrderItemSourceDTO(
            source_order_id=f"source-{order_number}",
            source_order_item_id=order_item_id,
        ),
        name="منتج اختبار",
        quantity=1,
    )


class FakeItemRepository:
    def __init__(self):
        self.calls = []
        self.page = OrderItemPage(
            items=[make_identity()],
            next_cursor="next-page",
            source_order_count=1,
            skipped_invalid_orders=0,
        )

    async def list_items(
        self,
        *,
        user_id,
        limit,
        cursor=None,
    ):
        self.calls.append({
            "method": "list",
            "user_id": user_id,
            "limit": limit,
            "cursor": cursor,
        })
        return self.page

    async def get_items_for_order(
        self,
        *,
        user_id,
        order_number,
    ):
        self.calls.append({
            "method": "get_items_for_order",
            "user_id": user_id,
            "order_number": order_number,
        })
        return [
            make_identity(
                order_number=order_number,
                order_item_id=f"{order_number}-item",
            )
        ]

    async def get_item(
        self,
        *,
        user_id,
        order_number,
        order_item_id,
    ):
        self.calls.append({
            "method": "get_item",
            "user_id": user_id,
            "order_number": order_number,
            "order_item_id": order_item_id,
        })

        if order_item_id == "missing":
            raise OrderItemNotFoundError(
                "order item not found"
            )

        return make_identity(
            order_number=order_number,
            order_item_id=order_item_id,
        )


@pytest.mark.asyncio
async def test_lists_items_through_repository():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    page = await service.list_items(
        user_id="owner-1",
        limit=20,
        cursor="cursor-1",
    )

    assert page is repository.page
    assert repository.calls == [{
        "method": "list",
        "user_id": "owner-1",
        "limit": 20,
        "cursor": "cursor-1",
    }]


@pytest.mark.asyncio
async def test_list_limit_is_clamped_to_fifty():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    await service.list_items(
        user_id="owner-1",
        limit=500,
    )

    assert repository.calls[0]["limit"] == 50


@pytest.mark.asyncio
async def test_invalid_list_limit_uses_default():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    await service.list_items(
        user_id="owner-1",
        limit="invalid",
    )

    assert repository.calls[0]["limit"] == 15


@pytest.mark.asyncio
async def test_blank_cursor_becomes_none():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    await service.list_items(
        user_id="owner-1",
        cursor="   ",
    )

    assert repository.calls[0]["cursor"] is None


@pytest.mark.asyncio
async def test_gets_items_for_exact_order():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    items = await service.get_items_for_order(
        user_id=" owner-1 ",
        order_number=" 300 ",
    )

    assert len(items) == 1
    assert items[0].order_number == "300"
    assert repository.calls[0] == {
        "method": "get_items_for_order",
        "user_id": "owner-1",
        "order_number": "300",
    }


@pytest.mark.asyncio
async def test_gets_one_exact_item():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    item = await service.get_item(
        user_id="owner-1",
        order_number="300",
        order_item_id="item-300-a",
    )

    assert item.order_item_id == "item-300-a"


@pytest.mark.asyncio
async def test_repository_not_found_is_translated():
    repository = FakeItemRepository()
    service = OrderItemService(repository)

    with pytest.raises(
        OrderItemServiceNotFoundError,
        match="missing",
    ):
        await service.get_item(
            user_id="owner-1",
            order_number="300",
            order_item_id="missing",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "kwargs", "expected_field"),
    [
        (
            "list_items",
            {"user_id": ""},
            "user_id",
        ),
        (
            "get_items_for_order",
            {
                "user_id": "owner-1",
                "order_number": "",
            },
            "order_number",
        ),
        (
            "get_item",
            {
                "user_id": "owner-1",
                "order_number": "300",
                "order_item_id": "",
            },
            "order_item_id",
        ),
    ],
)
async def test_required_identifiers_are_validated(
    method_name,
    kwargs,
    expected_field,
):
    service = OrderItemService(FakeItemRepository())

    with pytest.raises(
        InvalidOrderItemRequestError,
        match=expected_field,
    ):
        await getattr(service, method_name)(**kwargs)


def test_service_has_no_io_framework_or_storage_dependency():
    import ast
    import inspect
    import order_item_engine.service as service_module

    tree = ast.parse(
        inspect.getsource(service_module)
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
