"""Customer-history normalization and direct-Salla lookup tests."""

from copy import deepcopy

import pytest

from order_engine.customer_history import get_customer_history, normalize_saudi_mobile
from order_engine.repository import OrderDiscoveryRow


def make_raw(
    order_number: str,
    created_at: str,
    mobile: str,
    *,
    customer_id: str = "cust-42",
) -> dict:
    return {
        "id": f"id-{order_number}",
        "reference_id": order_number,
        "date": {"date": created_at},
        "status": {"slug": "in_progress", "name": "قيد التنفيذ"},
        "customer": {
            "id": customer_id,
            "full_name": "عميل اختبار",
            "mobile": mobile,
        },
        "amounts": {"total": {"amount": 100, "currency": "SAR"}},
        "items": [
            {
                "id": f"item-{order_number}",
                "quantity": 1,
                "product": {"id": "p1", "name": "منتج", "sku": "SKU-1"},
            }
        ],
    }


class CurrentOrderOnlyRepository:
    def __init__(self, current_raw: dict):
        self.current_raw = deepcopy(current_raw)
        self.local_history_reads = 0

    async def get_salla_order(self, *, user_id, order_number):
        if order_number != self.current_raw["reference_id"]:
            return None
        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=self.current_raw["date"]["date"][:10],
            salla_raw=deepcopy(self.current_raw),
        )

    async def list_salla_orders(self, **kwargs):
        self.local_history_reads += 1
        raise AssertionError("history card must not read local historical orders")


class FakeSallaRequest:
    def __init__(self, rows: list[dict]):
        self.rows = deepcopy(rows)
        self.calls: list[dict] = []

    async def __call__(self, db, user_id, method, path, *, params=None, json=None):
        self.calls.append({
            "db": db,
            "user_id": user_id,
            "method": method,
            "path": path,
            "params": deepcopy(params),
        })
        return {
            "data": deepcopy(self.rows),
            "pagination": {"currentPage": 1, "totalPages": 1},
        }


def test_normalize_saudi_mobile_maps_required_formats_to_one_value():
    expected = "966570076958"
    assert normalize_saudi_mobile("570076958") == expected
    assert normalize_saudi_mobile("0570076958") == expected
    assert normalize_saudi_mobile("+966570076958") == expected
    assert normalize_saudi_mobile("00966570076958") == expected


@pytest.mark.asyncio
async def test_customer_history_reads_previous_orders_from_salla_by_customer_id_only():
    current = make_raw("300", "2026-07-13 10:00:00", "+966570076958")
    repository = CurrentOrderOnlyRepository(current)
    salla_request = FakeSallaRequest([
        current,
        make_raw("200", "2026-07-12 10:00:00", "0555555555"),
        make_raw("100", "2026-07-11 10:00:00", "0500000000"),
    ])
    db = object()

    result = await get_customer_history(
        repository,
        db=db,
        user_id="owner-1",
        order_number="300",
        salla_request=salla_request,
    )

    assert [order.order_number for order in result.previous_orders] == ["200", "100"]
    assert result.scanned_orders == 3
    assert result.scan_complete is True
    assert repository.local_history_reads == 0
    assert len(salla_request.calls) == 1
    assert salla_request.calls[0] == {
        "db": db,
        "user_id": "owner-1",
        "method": "GET",
        "path": "/orders",
        "params": {
            "customer_id": "cust-42",
            "page": 1,
            "per_page": 50,
            "format": "light",
        },
    }


@pytest.mark.asyncio
async def test_customer_history_resolves_missing_customer_id_from_current_salla_order():
    current = make_raw("300", "2026-07-13 10:00:00", "+966570076958")
    current["customer"].pop("id")
    repository = CurrentOrderOnlyRepository(current)

    calls = []

    async def salla_request(db, user_id, method, path, *, params=None, json=None):
        calls.append({"path": path, "params": deepcopy(params)})
        if path == "/orders/id-300":
            resolved = deepcopy(current)
            resolved["customer"]["id"] = "cust-resolved-1"
            return {"data": resolved}
        assert path == "/orders"
        return {
            "data": [
                make_raw(
                    "200",
                    "2026-07-12 10:00:00",
                    "0555555555",
                    customer_id="cust-resolved-1",
                )
            ],
            "pagination": {"currentPage": 1, "totalPages": 1},
        }

    result = await get_customer_history(
        repository,
        db=object(),
        user_id="owner-1",
        order_number="300",
        salla_request=salla_request,
    )

    assert [order.order_number for order in result.previous_orders] == ["200"]
    assert result.scan_complete is True
    assert repository.local_history_reads == 0
    assert calls == [
        {"path": "/orders/id-300", "params": {"format": "light"}},
        {
            "path": "/orders",
            "params": {
                "customer_id": "cust-resolved-1",
                "page": 1,
                "per_page": 50,
                "format": "light",
            },
        },
    ]
