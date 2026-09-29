"""Real HMAC -> dispatch -> capture -> canonical persistence on loopback Mongo.

No server/bootstrap import, .env loading, external client, or mocked persistence.
The old coupling is reproduced only by rebinding the operational entry point to
the unchanged real financial atomic_owner; all HTTP handling remains real.
"""
import hashlib
import hmac
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import APIRouter, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from tests import test_g47_component_lifecycle_integration as lifecycle
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import (
    AUDIT_COLLECTION, GENERAL_LEDGER_COLLECTION, GROUPS_COLLECTION, SEQUENCES_COLLECTION,
)
import fulfillment_v2_routes as fulfillment
from salla_integration.routes import attach_salla_routes
from stock_component_consumption_service import PLANS, UNITS, CLAIMS


class PausedSallaWebhookHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = lifecycle.ComponentRouteTests()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.db = self.fixture.db
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": True}})
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        await self.db.salla_integrations.insert_one({"store_id": "synthetic-store", "user_id": "owner"})
        self.before_control = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.secret = "isolated-webhook-http-test-key-not-a-live-credential"
        environment = patch.dict(os.environ, {
            "SALLA_WEBHOOK_SECRET": self.secret,
            "SALLA_ORDERS_V3_SHADOW_ENABLED": "false",
            "MEZAN_SNAPCHAT_CAPI_ENABLED": "false",
        })
        environment.start()
        self.addCleanup(environment.stop)

        # attach_salla_routes imports this dependency to declare unrelated
        # authenticated routes. The public webhook never calls it.
        fake_server = types.ModuleType("server")
        async def current_user():
            raise AssertionError("public webhook must use HMAC, not a browser session")
        fake_server.current_user = current_user
        router = APIRouter()
        with patch.dict(sys.modules, {"server": fake_server}):
            attach_salla_routes(router, self.db)
        app = FastAPI()
        app.include_router(router)
        self.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://isolated.test")
        self.addAsyncCleanup(self.client.aclose)

    async def post_event(self, payload, event="order.created", *, valid_signature=True):
        raw = json.dumps({"event": event, "merchant": "synthetic-store", "data": payload},
                         sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
        return await self.client.post("/salla/webhooks/app", content=raw, headers={
            "content-type": "application/json", "x-salla-security-strategy": "Signature",
            "x-salla-signature": signature if valid_signature else "0" * 64,
        })

    async def assert_no_finance_or_component_effects(self):
        for collection in ("general_ledger", GROUPS_COLLECTION, GENERAL_LEDGER_COLLECTION,
                           AUDIT_COLLECTION, SEQUENCES_COLLECTION, "liabilities", "mz2_recognition_events",
                           "purchase_invoices", "mz2_opening_balance_drafts", "mz2_opening_evidence",
                           "mezan_inventory_cost_events_v1", "mz2_inventory_cost_states",
                           "mz2_purchase_receiving_operations", PLANS, UNITS, CLAIMS):
            self.assertEqual(await self.db[collection].count_documents({}), 0, collection)
        self.assertEqual(await self.fixture.on_hand(), 20)
        after = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.assertEqual({key: value for key, value in after.items() if key != "revision"},
                         {key: value for key, value in self.before_control.items() if key != "revision"})
        callback = AsyncMock()
        with self.assertRaises(HTTPException) as failure:
            await atomic_owner(self.db, "owner", callback)
        self.assertEqual(failure.exception.status_code, 423)
        self.assertEqual(failure.exception.detail["code"], "mz2_writes_paused")
        callback.assert_not_awaited()

    async def test_http_200_old_pause_failure_then_fixed_ingestion_replay_update_and_deferred_recovery(self):
        payload = self.fixture.source_payload()
        # Historical coupling: ACK 200/capture can succeed while canonical
        # persistence fails at the real financial pause gate before its callback.
        with patch.object(fulfillment, "operational_owner", atomic_owner):
            response = await self.post_event(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["stored"])
        old_sync = response.json()["capture"]["order_sync"]
        self.assertFalse(old_sync["synced"])
        self.assertEqual(old_sync["reason"], "order_webhook_persist_failed")
        self.assertIn("mz2_writes_paused", old_sync["error"])
        old_capture = await self.db.salla_webhook_event_captures.find_one({})
        self.assertTrue(old_capture["verified_before_capture"])
        self.assertEqual(old_capture["order_sync"], old_sync)
        self.assertEqual(await self.db.unified_orders.count_documents({}), 0)
        await self.assert_no_finance_or_component_effects()

        for _ in range(2):
            response = await self.post_event(payload)
            self.assertEqual(response.status_code, 200, response.text)
            capture = response.json()["capture"]
            self.assertTrue(capture["order_sync"]["synced"], capture)
            self.assertEqual(capture["order_mutation_scope"], "full_order_from_webhook")
            self.assertTrue(capture["no_salla_api_calls"])
            self.assertTrue(capture["no_qoyod_calls"])
        self.assertEqual(await self.db.unified_orders.count_documents({"order_number": "intake-order"}), 1)
        self.assertEqual(await self.db.salla_webhook_event_captures.count_documents({}), 1)
        replay_capture = await self.db.salla_webhook_event_captures.find_one({})
        self.assertEqual(replay_capture["delivery_count"], 3)
        self.assertTrue(replay_capture["order_sync"]["synced"])

        updated = self.fixture.source_payload(version=lifecycle.LATER, status="in_progress")
        response = await self.post_event(updated, "order.updated")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["capture"]["order_sync"]["synced"])
        canonical = await self.db.unified_orders.find_one({"order_number": "intake-order"})
        self.assertEqual(canonical["order_status_slug"], "in_progress")
        self.assertEqual(canonical["raw_by_source"]["salla_direct"], updated)
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "intake-order"})
        self.assertEqual(marker["operational_cohort"], fulfillment.LEGACY_COMPONENT_COHORT)

        deferred = self.fixture.source_payload(number="deferred-order")
        with patch.object(fulfillment, "reconcile_component_order_lifecycle",
                          AsyncMock(side_effect=RuntimeError("isolated G47 interruption"))):
            response = await self.post_event(deferred)
        self.assertEqual(response.status_code, 200, response.text)
        sync = response.json()["capture"]["order_sync"]
        self.assertTrue(sync["synced"], sync)
        self.assertTrue(sync["auto_fulfillment"]["retry_required"], sync)
        self.assertEqual(sync["auto_fulfillment"]["error_code"], "component_intake_retry_required")
        canonical = await self.db.unified_orders.find_one({"order_number": "deferred-order"})
        self.assertEqual(canonical["raw_by_source"]["salla_direct"], deferred)
        self.assertTrue(canonical["g47_salla_snapshot"]["component_pending"])
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "deferred-order"})
        self.assertEqual(marker["state"], "blocked")
        self.assertTrue(marker["retry_required"])
        saved = await self.db.salla_webhook_event_captures.find_one({"payload.data.reference_id": "deferred-order"})
        self.assertEqual(saved["order_sync"], sync)
        await self.assert_no_finance_or_component_effects()

        response = await self.post_event(deferred)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["capture"]["order_sync"]["synced"])
        self.assertEqual(await self.db.unified_orders.count_documents({"order_number": "deferred-order"}), 1)
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "deferred-order"})
        self.assertEqual(marker["operational_cohort"], fulfillment.LEGACY_COMPONENT_COHORT)
        await self.assert_no_finance_or_component_effects()

    async def test_invalid_hmac_rejected_before_capture_or_canonical_write(self):
        response = await self.post_event(self.fixture.source_payload(), valid_signature=False)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_SIGNATURE")
        self.assertEqual(await self.db.salla_webhook_event_captures.count_documents({}), 0)
        self.assertEqual(await self.db.unified_orders.count_documents({}), 0)
        await self.assert_no_finance_or_component_effects()
