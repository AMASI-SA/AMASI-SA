"""Isolated payment inbox flow: original orders never run full order sync twice."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "salla_integration" / "payment_update_inbox.py"


def load_worker():
    package = types.ModuleType("salla_integration")
    package.__path__ = []
    order_sync = types.ModuleType("salla_integration.webhook_order_sync")
    order_sync._resolve_user_id = AsyncMock(return_value="owner-1")
    order_sync._order_payload = lambda body: body.get("data", {})
    order_sync.sync_order_from_verified_webhook = AsyncMock(return_value={"synced": True})
    scope = types.ModuleType("salla_integration.store_scope")
    scope.is_attribution_pilot_store = lambda _: False
    pymongo = types.ModuleType("pymongo")
    pymongo.ReturnDocument = types.SimpleNamespace(AFTER=1)
    names = {
        "salla_integration": package,
        "salla_integration.webhook_order_sync": order_sync,
        "salla_integration.store_scope": scope,
        "pymongo": pymongo,
    }
    with patch.dict(sys.modules, names):
        spec = importlib.util.spec_from_file_location(
            "salla_integration._payment_update_inbox_test", MODULE_PATH)
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
    return worker, order_sync


class InitialOrderEvidenceTests(unittest.TestCase):
    def test_created_event_is_staged_without_resyncing_order(self):
        worker, sync = load_worker()
        item = {"user_id":"owner-1", "merchant_id":"m1", "event":{"event":"order.created","merchant":"m1"}}
        evidence = types.ModuleType("operational_payment_salla_event_capture")
        evidence.stage_verified_payment_update = AsyncMock(return_value=True)
        with patch.dict(sys.modules, {"operational_payment_salla_event_capture":evidence}):
            outcome = asyncio.run(worker._apply(None, item))
        self.assertEqual(outcome, "done")
        sync.sync_order_from_verified_webhook.assert_not_awaited()
        evidence.stage_verified_payment_update.assert_awaited_once()

    def test_created_event_retries_staging_failure(self):
        worker, sync = load_worker()
        item = {"user_id":"owner-1", "merchant_id":"m1", "event":{"event":"order.created","merchant":"m1"}}
        evidence = types.ModuleType("operational_payment_salla_event_capture")
        evidence.stage_verified_payment_update = AsyncMock(return_value=False)
        with patch.dict(sys.modules, {"operational_payment_salla_event_capture":evidence}):
            outcome = asyncio.run(worker._apply(None, item))
        self.assertEqual(outcome, "pending")
        sync.sync_order_from_verified_webhook.assert_not_awaited()

    def test_payment_update_keeps_existing_sync_contract(self):
        worker, sync = load_worker()
        item = {"user_id":"owner-1", "merchant_id":"m1", "event":{"event":"order.payment.updated","merchant":"m1"}}
        evidence = types.ModuleType("operational_payment_salla_event_capture")
        evidence.stage_verified_payment_update = AsyncMock(return_value=True)
        with patch.dict(sys.modules, {"operational_payment_salla_event_capture":evidence}):
            outcome = asyncio.run(worker._apply(None, item))
        self.assertEqual(outcome, "done")
        sync.sync_order_from_verified_webhook.assert_awaited_once()
        evidence.stage_verified_payment_update.assert_awaited_once()

    def test_wrong_owner_blocks_both_paths(self):
        worker, sync = load_worker()
        sync._resolve_user_id = AsyncMock(return_value="another-owner")
        item = {"user_id":"owner-1", "merchant_id":"m1", "event":{"event":"order.created","merchant":"m1"}}
        self.assertEqual(asyncio.run(worker._apply(None, item)), "paused")
        sync.sync_order_from_verified_webhook.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
