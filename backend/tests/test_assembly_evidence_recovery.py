"""Current approval/coverage recovery; only transaction boundary/provider are fake."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
import unittest
import assembly_completion_delivery as delivery
import assembly_evidence_recovery as recovery
import preparation_piece_operations as pieces
from order_review_business_snapshot import build_snapshot, REVIEW_SCOPE_VERSION
from order_review_completion import approval_identity
from test_manual_courier_label_refresh_race import scenario


async def seed(monkeypatch, status="completed"):
    db, calls = await scenario(monkeypatch, status=status, external=True)
    key = {"user_id": "owner", "order_number": "3001"}
    await db.unified_orders.update_one(key, {"$set": {"order_date": "2026-11-08T09:00:00Z"}})
    async def transaction(database, owner, callback):
        return await callback(database)
    monkeypatch.setattr(recovery, "operational_owner", transaction)
    monkeypatch.setattr(delivery, "operational_owner", transaction)
    await db[pieces.PIECES].update_one(key, {"$set": {
        "status": "ready_for_assembly", "preparation_receipt_status": "received",
        "supplier_dispatch_status": "received"}})
    await db.order_review_workflows.update_one(key, {"$set": {
        "revision": 7, "completion_mode": "mezan_local_v1", "review_completion_operation_id": "approval",
        "fulfillment_decision": {"lines": [{"order_item_id": "salla:3001:line", "quantity": 1,
            "configured": True, "requires_preparation": True}]}}})
    source = await db.unified_orders.find_one(key)
    order = await pieces._current_assembly_order(db, **key)
    approval = {**key, "_id": "approval", "state": "completed", "completion_mode": "mezan_local_v1",
        "approval_contract_version": REVIEW_SCOPE_VERSION, "acceptance_snapshot": {}, "revision": 1}
    approval["business_snapshot"] = build_snapshot(source, order, {}, identity=approval_identity(approval),
        schema_version=REVIEW_SCOPE_VERSION)
    await db.order_review_completion_operations.insert_one(approval)
    import fulfillment_v2_routes as fulfillment
    from stock_component_consumption_service import PLANS
    await db[PLANS].insert_one({"user_id": "owner", "order_id": "3001", "source_version": {"value": 1}})
    await db[fulfillment.COMPONENT_LIFECYCLES].insert_one({**key, "state": "reserved", "accepted": True, "generation": 1})
    workflow = await db.order_review_workflows.find_one(key)
    pending = {**delivery.pending_operation("operator", ""), "attempts": 1, "status_attempted": True,
        "status_attempted_at": "2026-11-10T00:00:00Z", "status_dispatch_count": 1,
        "source_fingerprint": delivery.source_fingerprint(source),
        "workflow_fingerprint": delivery.workflow_fingerprint(workflow)}
    await db.order_review_workflows.update_one(key, {"$set": {delivery.FIELD: pending}})
    # Absent -> explicit false is not a product/approval change, but old hash blocks it.
    await db.unified_orders.update_one(key, {"$set": {"g47_salla_snapshot.component_pending": False}})
    async def resolve(*args):
        current = await db.unified_orders.find_one(key)
        return "123", deepcopy(current["raw_by_source"]["salla_direct"])
    monkeypatch.setattr(delivery.shipping, "_resolve_order", resolve)
    monkeypatch.setattr(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("No provider write")))
    async def label(database, owner, number, publish):
        result = {"ready": True, "label_url": "https://files.example/current.pdf", "label_type": "carrier"}
        await publish(database, result)
        return result
    monkeypatch.setattr(delivery.labels, "read_current_carrier_label", label)
    return db, key, pending


@pytest.mark.asyncio
async def test_completed_order_recovers_and_prints_without_repeating_status(monkeypatch):
    db, key, pending = await seed(monkeypatch)
    result = await delivery.resume(db, **key)
    assert result["ready"] and result["order_status_completed"], result
    workflow = await db.order_review_workflows.find_one(key)
    assert workflow[delivery.FIELD]["status_dispatch_count"] == 1
    assert workflow[delivery.FIELD]["status_attempted_at"] == pending["status_attempted_at"]
    delivery.shipping.call_salla.assert_not_awaited()


class RealMongoRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_claim_recovers_with_real_owner_transactions(self):
        import os
        from motor.motor_asyncio import AsyncIOMotorClient
        from uuid import uuid4
        import test_manual_courier_label_refresh_race as fixture
        import operational_atomic
        uri = os.environ.get("MZ2_TEST_MONGO_URI")
        if not uri:
            self.skipTest("Disposable replica-set URI required")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        client = AsyncIOMotorClient(uri)
        db = client["assembly_recovery_" + uuid4().hex]
        class Client:
            manual_courier_race = db
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(fixture, "AsyncMongoMockClient", Client)
            try:
                database, key, _ = await seed(mp)
                await db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0,
                    "writes_paused": False, "control_revision": 0})
                mp.setattr(recovery, "operational_owner", operational_atomic.operational_owner)
                mp.setattr(delivery, "operational_owner", operational_atomic.operational_owner)
                result = await delivery.resume(database, **key)
                self.assertTrue(result["ready"], result)
                self.assertTrue(result["order_status_completed"], result)
            finally:
                await client.drop_database(db.name)
                client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["lease", "piece", "provider_items", "provider_identity", "provider_carrier", "provider_shipping"])
async def test_recovery_cannot_accept_a_race_or_partial_provider_read(monkeypatch, race):
    db, key, _ = await seed(monkeypatch)
    original = delivery.shipping._resolve_order
    async def resolve(*args):
        internal, order = await original(*args)
        if race == "lease":
            await db.order_review_workflows.update_one(key, {"$set": {"assembly_delivery.claim": "another-worker"}})
        elif race == "piece":
            await db[pieces.PIECES].update_one(key, {"$set": {"active_hold_id": "new-hold"}})
        elif race == "provider_items":
            order.pop("items")
        elif race == "provider_identity":
            order["reference_id"] = "other"
        elif race == "provider_carrier":
            order["shipping"]["company_code"] = "other-carrier"
        else:
            for field in ("shipping", "shipments", "shipping_company_name", "shipping_company_code"):
                order.pop(field, None)
        return internal, order
    monkeypatch.setattr(delivery.shipping, "_resolve_order", resolve)
    before = (await db.order_review_workflows.find_one(key))[delivery.FIELD]["source_fingerprint"]
    result = await delivery.resume(db, **key)
    assert not result["ready"]
    current = (await db.order_review_workflows.find_one(key))[delivery.FIELD]
    assert current["source_fingerprint"] == before
    delivery.shipping.call_salla.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_blocked_operation_rejoins_bounded_worker(monkeypatch):
    db, key, _ = await seed(monkeypatch)
    await db.order_review_workflows.update_one(key, {"$set": {
        "assembly_delivery.state": "requires_attention",
        "assembly_delivery.error_code": "assembly_completion_evidence_changed"}})
    assert await delivery.run_once(db) == 1
    assert (await db.order_review_workflows.find_one(key))[delivery.FIELD]["state"] == "confirmed"


@pytest.mark.asyncio
async def test_piece_hold_during_restarted_delivery_cannot_publish(monkeypatch):
    db, key, _ = await seed(monkeypatch)
    original = delivery.shipping._resolve_order
    reads = 0
    async def resolve(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            await db[pieces.PIECES].update_one(key, {"$set": {"active_hold_id": "new-hold"}})
        return await original(*args)
    monkeypatch.setattr(delivery.shipping, "_resolve_order", resolve)
    result = await delivery.resume(db, **key)
    assert reads == 2 and not result["ready"]
    assert result["error_code"] == "assembly_evidence_reapproval_required"


@pytest.mark.asyncio
async def test_recovery_preserves_attempt_budget(monkeypatch):
    db, key, _ = await seed(monkeypatch)
    await db.order_review_workflows.update_one(key, {"$set": {
        "assembly_delivery.state": "requires_attention",
        "assembly_delivery.error_code": "assembly_completion_evidence_changed",
        "assembly_delivery.attempts": delivery.MAX_ATTEMPTS}})
    assert await delivery.run_once(db) == 0
    assert (await db.order_review_workflows.find_one(key))[delivery.FIELD]["attempts"] == delivery.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_in_progress_recovery_sends_once_then_confirms_by_readback(monkeypatch):
    db, key, _ = await seed(monkeypatch, status="in_progress")
    posts = []
    async def post(database, owner, method, path, **kwargs):
        assert method == "POST" and path == "/orders/123/status"
        assert kwargs == {"json": {"slug": "completed"}, "single_post_attempt": True}
        posts.append(path)
        await db.unified_orders.update_one(key, {"$set": {
            "order_status": "completed", "raw_by_source.salla_direct.status.slug": "completed"}})
        return {"success": True}
    monkeypatch.setattr(delivery.shipping, "call_salla", post)
    first = await delivery.resume(db, **key)
    assert not first["ready"] and first["error_code"] == "completion_readback_pending", first
    second = await delivery.resume(db, **key, manual=True)
    assert second["ready"] and second["order_status_completed"], second
    assert posts == ["/orders/123/status"]


@pytest.mark.asyncio
async def test_completed_webhook_and_audit_revision_during_restarted_read_are_allowed(monkeypatch):
    db, key, _ = await seed(monkeypatch, status="in_progress")
    original = delivery.shipping._resolve_order
    reads = 0
    async def resolve(*args):
        nonlocal reads
        reads += 1
        if reads == 2:
            await db.unified_orders.update_one(key, {"$set": {
                "order_status": "completed", "raw_by_source.salla_direct.status.slug": "completed"}})
            await db.order_review_workflows.update_one(key, {"$inc": {"revision": 1}})
        return await original(*args)
    monkeypatch.setattr(delivery.shipping, "_resolve_order", resolve)
    result = await delivery.resume(db, **key)
    assert reads == 2 and result["ready"], result
    delivery.shipping.call_salla.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["quantity", "options", "hold", "receipt", "services", "approval", "component", "workflow"])
async def test_unsafe_changes_remain_blocked(monkeypatch, change):
    db, key, _ = await seed(monkeypatch)
    if change == "quantity":
        await db.unified_orders.update_one(key, {"$set": {"raw_by_source.salla_direct.items.0.quantity": 2}})
    elif change == "options":
        await db.unified_orders.update_one(key, {"$set": {"raw_by_source.salla_direct.items.0.options": [{"name": "Color", "value": "Red"}]}})
    elif change == "hold":
        await db[pieces.PIECES].update_one(key, {"$set": {"active_hold_id": "hold"}})
    elif change == "receipt":
        await db[pieces.PIECES].update_one(key, {"$set": {"status": "sent", "preparation_receipt_status": "pending"}})
    elif change == "services":
        await db[pieces.PIECES].update_one(key, {"$set": {"services": [{"name": "Embroidery", "status": "pending"}]}})
    elif change == "approval":
        await db.order_review_completion_operations.update_one(key, {"$set": {"superseded_by": "new"}})
    elif change == "component":
        await db.unified_orders.update_one(key, {"$set": {"g47_salla_snapshot.component_pending": True}})
    else:
        await db.order_review_workflows.update_one(key, {"$set": {"items": [{"new": True}]}})
    result = await delivery.resume(db, **key)
    assert not result["ready"]
    delivery.shipping.call_salla.assert_not_awaited()
