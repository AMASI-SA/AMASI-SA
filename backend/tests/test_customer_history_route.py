"""HTTP contract for normalized customer history."""

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from order_engine import (
    _original_make_order_engine_router as make_order_engine_router,
)
from order_engine.repository import OrderDiscoveryRow


def make_raw(order_number: str, created_at: str, mobile: str) -> dict:
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


class FakeOrderRepository:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

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
        rows = [row for row in self.rows if row["user_id"] == user_id]
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
            key=lambda row: (row["order_date"], row["order_number"]),
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
            if row["user_id"] == user_id and row["order_number"] == order_number:
                return OrderDiscoveryRow(
                    order_number=row["order_number"],
                    order_date=row["order_date"],
                    salla_raw=deepcopy(row["raw"]),
                )
        return None


def test_customer_history_endpoint_returns_200_and_customer_found():
    rows = [
        {
            "user_id": "owner-1",
            "order_number": "300",
            "order_date": "2026-07-13",
            "raw": make_raw("300", "2026-07-13 10:00:00", "+966570076958"),
        },
        {
            "user_id": "owner-1",
            "order_number": "200",
            "order_date": "2026-07-12",
            "raw": make_raw("200", "2026-07-12 10:00:00", "0570076958"),
        },
        {
            "user_id": "owner-1",
            "order_number": "100",
            "order_date": "2026-07-11",
            "raw": make_raw("100", "2026-07-11 10:00:00", "570076958"),
        },
    ]
    repository = FakeOrderRepository(rows)

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    app = FastAPI()
    app.include_router(
        make_order_engine_router(
            db=object(),
            current_user=current_user,
            repository_factory=lambda db: repository,
        ),
        prefix="/api",
    )
    client = TestClient(app)

    response = client.get("/api/orders-v2/300/customer-history")

    assert response.status_code == 200
    payload = response.json()
    assert payload["customer_found"] is True
    assert payload["normalized_mobile"] == "966570076958"
    assert payload["previous_order_count"] == 2
    assert [row["order_number"] for row in payload["previous_orders"]] == ["200", "100"]
