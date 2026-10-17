"""Courier printing uses current provider identity and confirmed completion."""
import pytest

import order_engine.shipping_label_service as shipping
from order_engine.shipping_label_service import issue_shipping_label as real_issue
from order_engine.shipping_label_service import _ensure_order_completed as real_complete
from test_shipping_print_legacy_path import setup, OWNER, ORDER, dump


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "under_review", "cancelled", "shipped", "delivered", None])
async def test_courier_refresh_cannot_bypass_salla_completion(setup, status):
    db, state = setup
    state["order"]["status"] = status
    state["order"]["shipping"] = {"company_name": "مندوب الرياض"}
    before = await dump(db)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert caught.value.code == "order_status_not_completed"
    assert await dump(db) == before


@pytest.mark.asyncio
async def test_current_shipment_can_prove_courier_for_completion(setup, monkeypatch):
    db, state = setup
    state["order"].update(status="in_progress", shipping={})
    state["rows"] = [{"id": "200", "order_id": "salla-order",
                      "courier_name": "مندوب الرياض", "meta": {"app_id": 0}}]
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": {"assembly_status": "completed"}})
    # Isolate carrier discovery from the separately covered execution policy.
    from types import SimpleNamespace
    import order_engine.service as service
    import review_local_policy as policy
    class Order(SimpleNamespace):
        def model_copy(self, update):
            return self
    async def get_order(*args, **kwargs):
        return Order()
    async def complete(*args):
        return {**state["order"], "status": "completed"}, True
    monkeypatch.setattr(service, "get_order", get_order)
    monkeypatch.setattr(policy, "assembly_execution_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr(shipping, "_ensure_order_completed", complete)
    result, changed = await shipping._ensure_internal_order_completed(
        db, OWNER, ORDER, "salla-order", state["order"])
    assert changed and result["status"] == "completed"
    assert "/shipments" in state["calls"]


@pytest.mark.asyncio
async def test_external_order_carrier_cannot_use_old_courier_shipment(setup):
    db, state = setup
    state["order"]["status"] = "completed"
    state["order"]["shipping"] = {"company_name": "Aramex", "company_code": "aramex"}
    state["rows"] = [{"id": "200", "order_id": "salla-order", "courier_name": "مندوب الرياض"}]
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert caught.value.code == "shipping_snapshot_changed"


@pytest.mark.asyncio
async def test_saved_document_keeps_current_courier_name_without_awb(setup):
    db, state = setup
    state["order"].update(status="completed", shipping={"company_name": "مندوب الرياض"})
    result = await shipping.refresh_shipping_label(db, OWNER, ORDER)
    assert result["courier_name"] == result["print_data"]["courier_name"] == "مندوب الرياض"
    assert result["ready"] and result["shipment_id"] is None
    assert result["tracking_number"] is None and result["label_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [True, False])
async def test_issue_uses_shared_print_only_after_actual_salla_readback(setup, monkeypatch, confirmed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import order_engine.service as service
    import review_local_policy as policy
    db, state = setup
    state["order"].update(status="in_progress", shipping={})
    state["rows"] = [{"id": "200", "order_id": "salla-order",
                      "courier_name": "مندوب الرياض", "meta": {"app_id": 0},
                      "ship_to": {"name": "Recipient", "address_line": "Current address"}}]
    await db.order_review_workflows.update_one({"user_id": OWNER}, {"$set": {"assembly_status": "completed"}})
    from salla_shipping import CURRENT_SHIPPING
    await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        CURRENT_SHIPPING: {"company_name": "مندوب الرياض", "company_code": "0"},
    }})
    class Order(SimpleNamespace):
        def model_copy(self, update):
            return self
    monkeypatch.setattr(service, "get_order", AsyncMock(return_value=Order()))
    monkeypatch.setattr(policy, "assembly_execution_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr(shipping, "_best_effort_resync", AsyncMock())
    monkeypatch.setattr(shipping, "_ensure_order_completed", real_complete)
    monkeypatch.setattr(shipping.asyncio, "sleep", AsyncMock())
    read_provider = shipping.call_salla
    posts = []
    async def provider(db, owner, method, path, **kwargs):
        if method == "POST":
            posts.append((path, kwargs.get("json")))
            if confirmed:
                state["order"]["status"] = "completed"
            return {"success": True}
        return await read_provider(db, owner, method, path, **kwargs)
    monkeypatch.setattr(shipping, "call_salla", provider)
    if confirmed:
        result = await real_issue(db, OWNER, ORDER)
        assert result["ready"] and result["label_type"] == "store_courier"
        assert result["tracking_number"] is None
        assert result["print_data"]["qr_code"]
        assert result["print_data"]["address"]["address_line"] == "Current address"
    else:
        with pytest.raises(shipping.ShippingLabelError) as caught:
            await real_issue(db, OWNER, ORDER)
        assert caught.value.code == "order_status_not_completed"
    assert posts == [("/orders/salla-order/status", {"slug": "completed"})]
