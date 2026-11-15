"""Deferred processing must not turn partial business work into a done ACK."""
import asyncio
import json
from unittest.mock import AsyncMock

from mongomock_motor import AsyncMongoMockClient
import pytest

from salla_integration import easy_mode_webhook as easy
from salla_integration import webhook_event_capture as capture
from salla_integration import webhook_order_sync as orders
from salla_integration.abandoned_carts import _owner_for_merchant
from integrations_control_center.snapchat_capi_purchases import _resolve_salla_user_id


def event(name="order.created"):
    return {"event": name, "merchant": "synthetic-store",
            "data": {"id": "native-1", "reference_id": "order-1"}}


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setenv("SALLA_ORDERS_V3_SHADOW_ENABLED", "false")
    monkeypatch.setenv("MEZAN_SNAPCHAT_CAPI_ENABLED", "false")
    monkeypatch.setattr("salla_integration.shipping_label_inbox.enqueue_verified_label", AsyncMock())
    monkeypatch.setattr("salla_integration.payment_update_inbox.enqueue_verified_payment", AsyncMock(return_value=None))
    monkeypatch.setattr(capture, "sync_shipment_payload_from_verified_webhook",
                        AsyncMock(return_value={"attempted": False, "reason": "not_shipment_event"}))
    return AsyncMongoMockClient().db


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    {"attempted": True, "synced": False, "reason": "order_webhook_persist_failed"},
    {"attempted": True, "synced": True,
     "auto_fulfillment": {"accepted": False, "retry_required": True}},
    {"attempted": True, "synced": False, "reason": "stale_salla_snapshot",
     "customer_options_enrichment": {"reason": "source_changed"}},
])
async def test_partial_order_work_is_audited_then_raised_for_durable_retry(isolated, monkeypatch, result):
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", AsyncMock(return_value=result))
    with pytest.raises(capture.WebhookProcessingIncomplete, match="^salla_webhook_processing_incomplete$"):
        await easy.dispatch_event(isolated, event(), raise_on_processing_error=True)
    row = await isolated.salla_webhook_event_captures.find_one({})
    assert row["processing_retry_required"] is True
    assert row["processing_timings_ms"]["total"] >= row["processing_timings_ms"]["order_sync"] >= 0


@pytest.mark.asyncio
async def test_direct_internal_capture_keeps_result_contract(isolated, monkeypatch):
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook",
                        AsyncMock(return_value={"synced": False, "reason": "order_webhook_persist_failed"}))
    result = await easy.dispatch_event(isolated, event())
    assert result["ok"] is True
    assert result["capture"]["processing_retry_required"] is True


@pytest.mark.asyncio
async def test_intentionally_stale_snapshot_is_not_retried(isolated, monkeypatch):
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook",
                        AsyncMock(return_value={"synced": False, "reason": "stale_salla_snapshot"}))
    result = await easy.dispatch_event(isolated, event(), raise_on_processing_error=True)
    assert result["ok"] is True
    assert (await isolated.salla_webhook_event_captures.find_one({}))["processing_retry_required"] is False


@pytest.mark.asyncio
async def test_cart_failure_cannot_echo_private_exception_into_logs_or_audit(isolated, monkeypatch, caplog):
    private = "private-customer@example.test phone=private-phone"
    monkeypatch.setattr(capture, "persist_abandoned_cart_event", AsyncMock(side_effect=RuntimeError(private)))
    with pytest.raises(capture.WebhookProcessingIncomplete):
        await easy.dispatch_event(isolated, event("abandoned.cart"), raise_on_processing_error=True)
    row = await isolated.salla_webhook_event_captures.find_one({})
    assert private not in json.dumps(row, default=str)
    assert private not in caplog.text
    assert row["abandoned_cart_sync"]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_opaque_handler_exception_is_reduced_to_fixed_audit_code(isolated, monkeypatch):
    private = "private-customer@example.test"
    monkeypatch.setattr(capture, "sync_order_from_verified_webhook", AsyncMock(return_value={
        "synced": False, "reason": "order_webhook_persist_failed", "error": private,
    }))
    with pytest.raises(capture.WebhookProcessingIncomplete):
        await easy.dispatch_event(isolated, event(), raise_on_processing_error=True)
    row = await isolated.salla_webhook_event_captures.find_one({})
    assert private not in json.dumps(row, default=str)
    assert row["order_sync"]["error"] == "processing_error"


@pytest.mark.asyncio
async def test_cart_owner_rebinding_cannot_write_into_new_tenant(isolated):
    await isolated.salla_integrations.insert_one({"store_id": "synthetic-store", "user_id": "new-owner"})
    with orders.verified_webhook_owner("original-owner"):
        for resolver in (orders._resolve_user_id, _owner_for_merchant, _resolve_salla_user_id):
            with pytest.raises(orders.WebhookOwnerChanged, match="^salla_webhook_owner_changed$"):
                await resolver(isolated, "synthetic-store")
    assert await orders._resolve_user_id(isolated, "synthetic-store") == "new-owner"


@pytest.mark.asyncio
async def test_owner_binding_is_task_local_and_restored_on_failure(isolated):
    await isolated.salla_integrations.insert_many([
        {"store_id": "store-a", "user_id": "owner-a"},
        {"store_id": "store-b", "user_id": "owner-b"},
    ])
    async def checked(merchant, owner):
        with orders.verified_webhook_owner(owner):
            await asyncio.sleep(0)
            assert await orders._resolve_user_id(isolated, merchant) == owner
            with pytest.raises(orders.WebhookOwnerChanged):
                with orders.verified_webhook_owner("different-owner"):
                    await orders._resolve_user_id(isolated, merchant)
            assert await orders._resolve_user_id(isolated, merchant) == owner
    await asyncio.gather(checked("store-a", "owner-a"), checked("store-b", "owner-b"))
    assert await orders._resolve_user_id(isolated, "store-b") == "owner-b"
