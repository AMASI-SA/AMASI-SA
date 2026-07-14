"""HTTP contract tests for the read-only Order Item API."""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemSourceDTO,
)
from order_item_engine.repository import OrderItemPage
from order_item_engine.routes import (
    make_order_item_engine_router,
)
from order_item_engine.service import (
    InvalidOrderItemCursorRequestError,
    OrderItemServiceNotFoundError,
)


def make_identity(
    order_number="300",
    order_item_id="item-300-a",
):
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


class FakeService:
    def __init__(self):
        self.calls = []
        self.raise_cursor_error = False

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

        if self.raise_cursor_error:
            raise InvalidOrderItemCursorRequestError(
                "invalid cursor"
            )

        return OrderItemPage(
            items=[make_identity()],
            next_cursor="next",
            source_order_count=1,
            skipped_invalid_orders=0,
        )

    async def get_items_for_order(
        self,
        *,
        user_id,
        order_number,
    ):
        self.calls.append({
            "method": "order",
            "user_id": user_id,
            "order_number": order_number,
        })

        return [
            make_identity(
                order_number=order_number,
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
            "method": "item",
            "user_id": user_id,
            "order_number": order_number,
            "order_item_id": order_item_id,
        })

        if order_item_id == "missing":
            raise OrderItemServiceNotFoundError(
                "missing"
            )

        return make_identity(
            order_number=order_number,
            order_item_id=order_item_id,
        )


def make_client(*, user, service):
    app = FastAPI()

    async def current_user():
        return user

    app.include_router(
        make_order_item_engine_router(
            db=object(),
            current_user=current_user,
            service_factory=lambda _: service,
        ),
        prefix="/api",
    )

    return TestClient(app)


def test_owner_can_list_order_items():
    service = FakeService()
    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=service,
    )

    response = client.get(
        "/api/order-items-v2?limit=20"
    )

    assert response.status_code == 200
    body = response.json()

    assert body["limit"] == 20
    assert body["source_order_count"] == 1
    assert len(body["items"]) == 1
    assert service.calls[0]["user_id"] == "owner-1"


def test_non_owner_is_forbidden():
    client = make_client(
        user={
            "id": "user-1",
            "role": "user",
        },
        service=FakeService(),
    )

    response = client.get(
        "/api/order-items-v2"
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "owner_only"


def test_invalid_cursor_returns_400():
    service = FakeService()
    service.raise_cursor_error = True

    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=service,
    )

    response = client.get(
        "/api/order-items-v2?cursor=bad"
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]["code"]
        == "invalid_order_items_cursor"
    )


def test_lists_items_for_one_order():
    service = FakeService()
    client = make_client(
        user={
            "id": "owner-1",
            "is_owner": True,
        },
        service=service,
    )

    response = client.get(
        "/api/orders-v2/300/items"
    )

    assert response.status_code == 200
    assert response.json()[0]["order_number"] == "300"


def test_gets_one_exact_item():
    service = FakeService()
    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=service,
    )

    response = client.get(
        "/api/orders-v2/300/items/item-300-a"
    )

    assert response.status_code == 200
    assert (
        response.json()["order_item_id"]
        == "item-300-a"
    )


def test_item_id_with_colons_is_supported():
    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=FakeService(),
    )

    response = client.get(
        "/api/orders-v2/300/items/"
        "salla:300:item-1"
    )

    assert response.status_code == 200
    assert (
        response.json()["order_item_id"]
        == "salla:300:item-1"
    )


def test_missing_item_returns_404():
    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=FakeService(),
    )

    response = client.get(
        "/api/orders-v2/300/items/missing"
    )

    assert response.status_code == 404
    assert (
        response.json()["detail"]["code"]
        == "order_item_not_found"
    )


def test_post_is_not_supported():
    client = make_client(
        user={
            "id": "owner-1",
            "role": "owner",
        },
        service=FakeService(),
    )

    response = client.post(
        "/api/order-items-v2"
    )

    assert response.status_code == 405


def test_routes_have_no_direct_database_operations():
    import inspect
    import order_item_engine.routes as routes

    source = inspect.getsource(routes)

    forbidden = {
        ".find(",
        "find_one(",
        "insert_one(",
        "update_one(",
        "delete_one(",
        "unified_orders",
        "raw_by_source",
    }

    assert all(
        token not in source
        for token in forbidden
    )
