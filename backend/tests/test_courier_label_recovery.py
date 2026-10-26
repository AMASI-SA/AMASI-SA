import pytest
from mongomock_motor import AsyncMongoMockClient

import assembly_status_policy
import fulfillment_carrier_label as labels
import operational_atomic
from order_engine import shipping_label_service as shipping


@pytest.mark.asyncio
@pytest.mark.parametrize("label_type,ready,should_recover", [
    ("store_courier", True, True), ("carrier", True, False),
    ("store_courier", False, False),
])
async def test_recovery_requires_current_completed_store_courier(monkeypatch, label_type, ready, should_recover):
    db = AsyncMongoMockClient().test
    await db.order_review_workflows.insert_one({"user_id": "merchant", "order_number": "101",
        "stage": "completed", "assembly_status": "completed"})
    baseline = {"shipping_company": "مندوب الرياض"}
    fence = {"epoch": 0, "shipping": baseline, "assignment": {
        "store_courier_assignee_id": None, "store_courier_assignee_name": None,
        "store_delivery_assignment_id": None}}
    async def get_fence(*args, **kwargs): return fence
    async def refresh(*args, **kwargs): return {"label_type": label_type, "ready": ready, "order_status_completed": True}
    async def get_baseline(*args, **kwargs): return baseline
    async def status(*args, **kwargs): return "completed"
    async def owner(db, user_id, fn): return await fn(db)
    monkeypatch.setattr(shipping, "_assert_current_print_status", get_fence)
    monkeypatch.setattr(shipping, "refresh_shipping_label", refresh)
    monkeypatch.setattr(shipping, "_label_baseline", get_baseline)
    monkeypatch.setattr(assembly_status_policy, "source_status", status)
    monkeypatch.setattr(operational_atomic, "operational_owner", owner)
    assert await labels.recover_store_courier_label_for_handover(db, user_id="merchant", order_number="101") is should_recover
    workflow = await db.order_review_workflows.find_one({})
    assert (workflow.get("carrier_label_type") == "store_courier") is should_recover


@pytest.mark.asyncio
async def test_recovery_refuses_changed_shipping_snapshot(monkeypatch):
    db = AsyncMongoMockClient().test
    await db.order_review_workflows.insert_one({"user_id": "merchant", "order_number": "101", "stage": "completed", "assembly_status": "completed"})
    async def get_fence(*args, **kwargs): return {"epoch": 0, "shipping": {"carrier": "old"}, "assignment": {"store_courier_assignee_id": None, "store_courier_assignee_name": None, "store_delivery_assignment_id": None}}
    async def refresh(*args, **kwargs): return {"label_type": "store_courier", "ready": True, "order_status_completed": True}
    async def get_baseline(*args, **kwargs): return {"carrier": "new"}
    async def status(*args, **kwargs): return "completed"
    async def owner(db, user_id, fn): return await fn(db)
    monkeypatch.setattr(shipping, "_assert_current_print_status", get_fence)
    monkeypatch.setattr(shipping, "refresh_shipping_label", refresh)
    monkeypatch.setattr(shipping, "_label_baseline", get_baseline)
    monkeypatch.setattr(assembly_status_policy, "source_status", status)
    monkeypatch.setattr(operational_atomic, "operational_owner", owner)
    with pytest.raises(shipping.ShippingLabelError, match="تغيرت"):
        await labels.recover_store_courier_label_for_handover(db, user_id="merchant", order_number="101")
    assert "carrier_label_type" not in await db.order_review_workflows.find_one({})
