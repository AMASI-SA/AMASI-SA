"""HTTP contract tests for the owner-only Order Engine routes."""

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from order_engine.repository import OrderDiscoveryRow
from order_engine.routes import make_order_engine_router


def make_raw(order_number: str, created_at: str) -> dict:
    return {
        "id": f"id-{order_number}",
        "reference_id": order_number,
        "date": {"date": created_at},
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


class FakeRepository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.write_calls = 0

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
                salla_raw=deepcopy(row["raw"]),
            )
            for row in rows[:limit]
        ]

    async def get_salla_order(self, *, user_id, order_number):
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


ROWS = [
    {
        "user_id": "owner-1",
        "order_number": "300",
        "order_date": "2026-07-13",
        "raw": make_raw("300", "2026-07-13 10:00:00"),
    },
    {
        "user_id": "owner-1",
        "order_number": "200",
        "order_date": "2026-07-12",
        "raw": make_raw("200", "2026-07-12 10:00:00"),
    },
    {
        "user_id": "owner-1",
        "order_number": "100",
        "order_date": "2026-07-11",
        "raw": make_raw("100", "2026-07-11 10:00:00"),
    },
]


def build_client(user):
    repository = FakeRepository(ROWS)

    async def current_user():
        return deepcopy(user)

    app = FastAPI()
    app.include_router(
        make_order_engine_router(
            db=object(),
            current_user=current_user,
            repository_factory=lambda db: repository,
        ),
        prefix="/api",
    )

    return TestClient(app), repository


def test_owner_can_list_orders_with_default_limit():
    client, repository = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get("/api/orders-v2")

    assert response.status_code == 200
    payload = response.json()

    assert payload["limit"] == 15
    assert [
        row["order_number"]
        for row in payload["items"]
    ] == ["300", "200", "100"]
    assert repository.write_calls == 0


def test_latest_sold_products_feed_returns_orders_without_enrichment():
    client, repository = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get("/api/orders-v2/latest-sold-products?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["limit"] == 2
    assert [row["order_number"] for row in payload["items"]] == ["300", "200"]
    assert payload["items"][0]["items"][0]["name"] == "منتج"
    assert repository.write_calls == 0


def test_non_owner_is_forbidden():
    client, _ = build_client({
        "id": "employee-1",
        "role": "operations",
    })

    response = client.get("/api/orders-v2")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "owner_only"


def test_legacy_is_owner_flag_is_supported():
    client, _ = build_client({
        "id": "owner-1",
        "is_owner": True,
    })

    response = client.get("/api/orders-v2")

    assert response.status_code == 200


def test_limit_above_fifty_is_rejected():
    client, _ = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get("/api/orders-v2?limit=51")

    assert response.status_code == 422


def test_invalid_cursor_returns_clean_400():
    client, _ = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get(
        "/api/orders-v2?cursor=invalid-cursor"
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]["code"]
        == "invalid_orders_cursor"
    )


def test_owner_can_get_exact_order():
    client, _ = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get("/api/orders-v2/200")

    assert response.status_code == 200
    assert response.json()["order_number"] == "200"


def test_missing_order_returns_clean_404():
    client, _ = build_client({
        "id": "owner-1",
        "role": "owner",
    })

    response = client.get("/api/orders-v2/999")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "order_not_found",
        "order_number": "999",
    }


def test_router_source_has_no_direct_mongo_or_qoyod_dependency():
    import ast
    import inspect
    import order_engine.routes as routes

    tree = ast.parse(inspect.getsource(routes))

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

    assert not any(
        module == "motor"
        or module.startswith("motor.")
        or module == "pymongo"
        or module.startswith("pymongo.")
        or "qoyod" in module
        for module in imported_modules
    )

    assert attribute_names.isdisjoint({
        "unified_orders",
        "find",
        "find_one",
        "insert_one",
        "update_one",
        "delete_one",
    })
