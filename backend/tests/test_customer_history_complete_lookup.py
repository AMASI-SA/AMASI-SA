"""Complete direct-Salla customer-history pagination tests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from order_engine.customer_history import get_customer_history
from order_engine.repository import OrderDiscoveryRow


def make_raw(order_number: str, created_at: str) -> dict:
    return {
        "id": f"id-{order_number}",
        "reference_id": order_number,
        "date": {"date": created_at},
        "status": {"slug": "completed", "name": "تم التنفيذ"},
        "customer": {
            "id": "cust-history-1",
            "full_name": "عميل السجل الكامل",
            "mobile": "+966570076958",
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


class CurrentOrderRepository:
    def __init__(self, current: dict):
        self.current = deepcopy(current)
        self.history_reads = 0

    async def get_salla_order(self, *, user_id, order_number):
        return OrderDiscoveryRow(
            order_number=order_number,
            order_date=self.current["date"]["date"][:10],
            salla_raw=deepcopy(self.current),
        )

    async def list_salla_orders(self, **kwargs):
        self.history_reads += 1
        raise AssertionError("local pagination must not be used for this card")


class PaginatedSallaRequest:
    def __init__(self, pages: list[list[dict]]):
        self.pages = deepcopy(pages)
        self.params: list[dict] = []

    async def __call__(self, db, user_id, method, path, *, params=None, json=None):
        self.params.append(deepcopy(params))
        page = int(params["page"])
        return {
            "data": deepcopy(self.pages[page - 1]),
            "pagination": {
                "currentPage": page,
                "totalPages": len(self.pages),
            },
        }


@pytest.mark.asyncio
async def test_complete_lookup_fetches_all_salla_pages_and_excludes_current_order():
    current = make_raw("999", "2026-07-31 10:00:00")
    start = datetime(2025, 1, 1, 10, 0, 0)
    historical = [
        make_raw(
            f"{index + 1:03d}",
            (start + timedelta(days=index)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        for index in range(350)
    ]
    all_rows = [current, *historical]
    pages = [all_rows[index:index + 50] for index in range(0, len(all_rows), 50)]
    repository = CurrentOrderRepository(current)
    salla_request = PaginatedSallaRequest(pages)

    result = await get_customer_history(
        repository,
        db=object(),
        user_id="owner-1",
        order_number="999",
        salla_request=salla_request,
    )

    assert result.customer_found is True
    assert result.scan_complete is True
    assert result.scanned_orders == 351
    assert len(result.previous_orders) == 350
    assert repository.history_reads == 0
    assert [params["page"] for params in salla_request.params] == list(range(1, 9))
    assert all(params["customer_id"] == "cust-history-1" for params in salla_request.params)
