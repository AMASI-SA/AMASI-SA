"""Exercise the order search router without unrelated fulfillment installers."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from order_engine import _original_make_order_engine_router
from order_engine.repository import OrderDiscoveryRow


class Repository:
    def __init__(self):
        self.owners = []

    async def list_salla_orders(self, *, user_id, **kwargs):
        self.owners.append(user_id)
        rows = []
        for number, date in (("inside", "2026-10-17T20:59:59Z"), ("outside", "2026-10-17T21:00:00Z")):
            raw = {"id": number, "reference_id": number, "created_at": date,
                   "utm_campaign": "91247da2-a1d7-444a-9078-a786d23a305b",
                   "status": {"name": "تم التنفيذ", "slug": "completed"}}
            rows.append(OrderDiscoveryRow(number, date[:10], raw))
        rows.append(OrderDiscoveryRow("invalid", "", {"id": "invalid"}))
        return deepcopy(rows)


def test_campaign_and_inclusive_riyadh_date_return_matching_orders_without_500():
    repository = Repository()

    async def owner():
        return {"id": "owner-test", "role": "owner"}

    app = FastAPI()
    app.include_router(_original_make_order_engine_router(object(), owner, repository_factory=lambda _: repository), prefix="/api")
    response = TestClient(app).get("/api/orders-v2", params={
        "campaign_id": "91247da2-a1d7-444a-9078-a786d23a305b",
        "created_from": "2026-10-17T00:00:00+03:00",
        "created_to": "2026-10-18T00:00:00+03:00",
    })
    assert response.status_code == 200, response.text
    assert [row["order_number"] for row in response.json()["items"]] == ["inside"]
    assert response.json()["skipped_invalid"] == 1
    assert response.json()["result_summary"]["orders"] == 1
    assert repository.owners == ["owner-test"]
