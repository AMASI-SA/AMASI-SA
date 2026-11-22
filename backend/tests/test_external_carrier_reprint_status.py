"""Existing external AWBs remain readable after an order leaves completion."""
import pytest
from unittest.mock import AsyncMock

from test_mobile_external_label_read_parity import external_case, KEY, PATH
from test_manual_courier_label_refresh_race import scenario
from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["shipped", "delivering", "delivered"])
async def test_external_reprint_statuses_do_not_enable_local_document_generation(monkeypatch, status):
    db, calls = await scenario(monkeypatch, status=status, external=False)
    before = await db.unified_orders.find_one(KEY)
    document = AsyncMock(side_effect=AssertionError("Local document generation must remain blocked"))
    monkeypatch.setattr(shipping, "_internal_delivery_document", document)

    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", "3001", issue=False, completed_only=True)

    assert error.value.code == "order_status_not_completed"
    assert document.await_count == 0
    assert all(method == "GET" for method, _, _ in calls)
    assert await db.unified_orders.find_one(KEY) == before
