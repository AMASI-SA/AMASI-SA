"""Customer-history normalization and matching tests."""

from copy import deepcopy

import pytest

from order_engine.customer_history import (
    get_customer_history,
    normalize_saudi_mobile,
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


def test_normalize_saudi_mobile_maps_required_formats_to_one_value():
    expected = "966570076958"
    assert normalize_saudi_mobile("570076958") == expected
    assert normalize_saudi_mobile("0570076958") == expected
    assert normalize_saudi_mobile("+966570076958") == expected
    assert normalize_saudi_mobile("00966570076958") == expected


@pytest.mark.asyncio
async def test_customer_history_matches_local_and_international_mobile_formats():
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
        {
            "user_id": "owner-1",
            "order_number": "050",
            "order_date": "2026-07-10",
            "raw": make_raw("050", "2026-07-10 10:00:00", "0555555555"),
        },
    ]

    result = await get_customer_history(
        FakeOrderRepository(rows),
        user_id="owner-1",
        order_number="300",
    )

    assert result.customer_found is True
    assert result.normalized_mobile == "966570076958"
    assert [order.order_number for order in result.previous_orders] == ["200", "100"]
    assert result.scan_complete is True
