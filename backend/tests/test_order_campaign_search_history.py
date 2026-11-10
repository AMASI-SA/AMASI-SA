from types import SimpleNamespace
from copy import deepcopy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from order_engine import _original_make_order_engine_router
from order_engine.repository import MongoOrderRepository
from order_engine.repository import OrderDiscoveryRow


def make_raw(number, created):
    return {"id": number, "reference_id": number, "created_at": created,
            "amounts": {"total": {"amount": 100, "currency": "SAR"}},
            "items": []}


class FakeRepository:
    def __init__(self, rows): self.rows = rows
    async def list_salla_orders(self, *, user_id, limit):
        return [OrderDiscoveryRow(order_number=row["order_number"], order_date=row["order_date"], salla_raw=row["raw"])
                for row in self.rows if row["user_id"] == user_id][:limit]



class Cursor:
    def __init__(self, rows): self.rows = rows
    def max_time_ms(self, value): assert value == 5000; return self
    def batch_size(self, value): assert value == 250; return self
    def __aiter__(self):
        async def values():
            for row in self.rows: yield deepcopy(row)
        return values()


@pytest.mark.asyncio
async def test_old_campaign_is_found_without_hydrating_5000_unrelated_orders():
    cid = "7a9c4ad6-f520-432f-859e-d7985c0c1f50"
    rows = [{"order_number": str(i), "raw_by_source": {"salla_direct": {"utm_campaign": "other"}}} for i in range(5001)]
    rows.append({"order_number": "old", "raw_by_source": {"salla_direct": {"source_details": {"utm_campaign": cid}}}})
    class Collection:
        def find(self, query, projection):
            assert query["user_id"] == "owner"
            assert "raw_by_source.salla_direct" not in projection
            assert not any("items" in key or "customer" in key or "payment" in key for key in projection)
            return Cursor(rows)
    class Repo(MongoOrderRepository):
        async def get_salla_orders(self, *, user_id, order_numbers):
            assert user_id == "owner"
            assert order_numbers == ["old"]
            return [SimpleNamespace(order_number="old", order_date="2025-01-01")]
    result = await Repo(SimpleNamespace(unified_orders=Collection())).list_campaign_orders(user_id="owner", campaign_id=cid)
    assert [row.order_number for row in result] == ["old"]


def test_filtered_results_have_next_page_and_cursor_cannot_cross_filters():
    rows = []
    for i in range(55):
        raw = make_raw(str(i), "2026-11-10 10:00:00")
        raw["utm_campaign"] = "campaign-1"
        rows.append({"user_id": "owner", "order_number": str(i), "order_date": "2026-11-10", "raw": raw})
    repo = FakeRepository(rows)
    async def user(): return {"id": "owner", "role": "owner"}
    app = FastAPI()
    app.include_router(_original_make_order_engine_router(db=object(), current_user=user, repository_factory=lambda db: repo), prefix="/api")
    client = TestClient(app)
    first = client.get("/api/orders-v2", params={"campaign_id": "campaign-1", "limit": 50})
    assert first.status_code == 200
    data = first.json()
    assert len(data["items"]) == 50
    cursor = data["next_cursor"]
    second = client.get("/api/orders-v2", params={"campaign_id": "campaign-1", "limit": 50, "cursor": cursor})
    assert second.status_code == 200
    assert len(second.json()["items"]) == 5
    assert second.json()["next_cursor"] is None
    assert not ({row["order_number"] for row in data["items"]} & {row["order_number"] for row in second.json()["items"]})
    assert client.get("/api/orders-v2", params={"campaign_id": "other", "cursor": cursor}).status_code == 400
