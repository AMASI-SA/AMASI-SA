"""Existing external AWBs remain readable after an order leaves completion."""
import pytest

from test_mobile_external_label_read_parity import external_case, KEY, PATH


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["shipped", "delivering", "delivered"])
@pytest.mark.parametrize("action", ["refresh", "complete-and-refresh"])
async def test_current_external_awb_can_be_reprinted_after_dispatch(monkeypatch, status, action):
    async with external_case(monkeypatch, status=status) as (db, calls, client):
        before = await db.unified_orders.find_one(KEY)
        workflow = await db.order_review_workflows.find_one(KEY)

        response = await client.post(PATH + "/" + action)

        assert calls and all(method == "GET" for method, _, _ in calls)
        assert await db.unified_orders.find_one(KEY) == before
        current = await db.order_review_workflows.find_one(KEY)
        assert current["stage"] == workflow["stage"]
        assert current["assembly_status"] == workflow["assembly_status"]
        assert "manual_label_refresh" not in current
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["ready"] is True
        assert result["label_url"] == "https://files.example/aramex-synthetic.pdf"
        assert result["shipment_id"] == "99"
        assert result["observed_order_status"] == status
