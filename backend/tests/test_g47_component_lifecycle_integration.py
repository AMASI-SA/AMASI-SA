"""Existing ASGI routes + real owner transactions/warehouse stock.

Only authentication/catalog enrichment/provider transport are test doubles.
No stock/transaction/service mock and no dependency on the repository conftest.
"""
import asyncio
import os
import unittest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import fulfillment_v2_routes as fulfillment
import order_review_routes as review
import preparation_piece_operations as pieces
import stock_preparation_order_routes as manufacturing
import salla_integration.webhook_order_sync as webhook
import order_engine.salla_refresh as refresh
import order_engine.service as order_service
from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO, ShippingDTO, AddressDTO
from order_item_engine.mapper import map_order_item_identities
from stock_component_consumption_service import PLANS, UNITS, LOCATIONS, PRODUCTS, RESOURCES, PRODUCT_BINDINGS

WHEN = "2026-09-26T12:00:00+00:00"
LATER = "2026-09-26T13:00:00+00:00"


class ComponentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["g47_integration_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.actor = {"id": "owner", "role": "owner", "name": "Synthetic operator"}
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {
            "component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00",
        }})
        await self.db[PRODUCTS].insert_one({"id": "p", "mezan_product_id": "mp", "salla_product_id": "p",
            "user_id": "owner", "name": "Synthetic product", "sku": "SYN-P", "details_loaded": True})
        await self.db[RESOURCES].insert_many([
            {"id": "material", "user_id": "owner", "kind": "stock_component", "track_inventory": True},
            {"id": "service", "user_id": "owner", "kind": "service", "track_inventory": False},
        ])
        await self.db[PRODUCT_BINDINGS].insert_many([
            {"id": "recipe-material", "user_id": "owner", "salla_product_id": "p", "resource_id": "material", "quantity": 2},
            {"id": "recipe-service", "user_id": "owner", "salla_product_id": "p", "resource_id": "service", "quantity": 7},
        ])
        await self.db[LOCATIONS].insert_many([
            {"id": "materials", "user_id": "owner", "warehouse_id": "wh", "state": "occupied",
             "occupancy": {"items": [{"item_type": "stock_component", "resource_id": "material",
                 "receipt_id": "material-lot", "quantity": 20, "sku": "SYN-P"}], "total_quantity": 20}},
            {"id": "finished", "user_id": "owner", "warehouse_id": "wh", "state": "empty",
             "code": "FINISHED", "barcode_value": "FINISHED", "max_items": 100, "occupancy": None},
        ])
        await self.db[manufacturing.WAREHOUSES].insert_one({"id": "wh", "user_id": "owner", "name": "Synthetic warehouse"})
        await self.db[manufacturing.SUPPLIERS].insert_one({"id": "supplier", "user_id": "owner", "company_name": "Synthetic supplier"})
        self.context = {"merchant_id": "owner", "actor_id": "owner", "is_owner": True,
            "permissions": {"fulfillment.pack.confirm", "fulfillment.carrier.handoff", "inventory.preparation.create", "inventory.preparation.work", "inventory.preparation.receive"},
            "responsibilities": set(), "warehouse_ids": ["wh"]}
        self.patches = [
            patch.object(fulfillment, "_actor_context", AsyncMock(return_value=self.context)),
            patch.object(pieces, "_actor_context", AsyncMock(return_value=self.context)),
            patch.object(manufacturing, "_actor_context", AsyncMock(return_value=self.context)),
            patch.object(manufacturing, "_validate_assigned_operator", AsyncMock(return_value={"id": "owner", "name": "Synthetic operator"})),
            patch.object(pieces, "sync_completed_carrier_label", AsyncMock(return_value={"ok": True})),
        ]
        for replacement in self.patches:
            replacement.start()
        self.app = FastAPI()
        async def actor():
            return self.actor
        self.app.include_router(fulfillment.make_fulfillment_v2_router(self.db, actor))
        self.app.include_router(review.make_order_review_router(self.db, actor))
        self.app.include_router(pieces.make_preparation_piece_operations_router(self.db, actor))
        self.app.include_router(manufacturing.make_stock_preparation_order_router(self.db, actor))
        self.client = AsyncClient(transport=ASGITransport(app=self.app, raise_app_exceptions=False), base_url="http://test")

    async def asyncTearDown(self):
        if not hasattr(self, "client"):
            return
        await self.client.aclose()
        for replacement in reversed(self.patches):
            replacement.stop()
        self.assertTrue(self.db.name.startswith("g47_integration_"))
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    def order(self, number="order-1", quantity=2, status="under_review", created=WHEN):
        return OrderDTO(order_id=number, order_number=number, created_at=datetime.fromisoformat(created),
            source=OrderSourceDTO(source_order_id=number), status=status,
            payment=PaymentDTO(method="cod"), shipping=ShippingDTO(address=AddressDTO(city="Synthetic city", street="Synthetic street")), items=[OrderItemDTO(
                order_item_id="line-1", product_id="p", name="Synthetic product", sku="SYN-P", quantity=quantity,
            )])

    async def on_hand(self):
        location = await self.db[LOCATIONS].find_one({"id": "materials"})
        return location["occupancy"]["items"][0]["quantity"]

    async def accept(self, order=None, sync=None):
        order = order or self.order()
        transport = sync or AsyncMock(return_value=("sent", None))
        with patch.object(review, "get_order", AsyncMock(return_value=order)), \
             patch.object(review, "_review_item_identities", AsyncMock(return_value=map_order_item_identities(order))), \
             patch.object(review, "_sync_salla_reviewed", transport):
            response = await self.client.post(f"/order-reviews-v1/{order.order_number}/complete", json={"expected_revision": 0})
        return response, transport

    async def seed_physical(self, order_number="order-1", quantity=2):
        await self.db[fulfillment.WORKFLOWS].update_one({"user_id": "owner", "order_number": order_number},
            {"$set": {"stage": "ready_to_ship", "items": [], "operational_items": []}}, upsert=True)
        await self.db[pieces.PIECES].insert_many([{
            "piece_id": f"piece-{i}", "id": f"piece-{i}", "user_id": "owner", "order_number": order_number,
            "order_item_id": "line-1", "unit_index": i, "product_id": "p",
            "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "assembly_status": "pending",
        } for i in range(1, quantity + 1)])

    async def mark_piece(self, piece_id):
        return await self.client.post(f"/preparation-work-v1/assembly/pieces/{piece_id}/ready", json={"client_request_id": "synthetic-request-" + piece_id})

    async def test_acceptance_stockout_blocks_provider_and_retry_has_no_partial_reservation(self):
        await self.db[LOCATIONS].update_one({"id": "materials"}, {"$set": {"occupancy.items.0.quantity": 1, "occupancy.total_quantity": 1}})
        response, provider = await self.accept()
        self.assertEqual(response.status_code, 409, response.text)
        provider.assert_not_awaited()
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[UNITS].count_documents({}), 0)
        intent = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertEqual(intent["state"], "blocked")
        self.assertTrue(intent["retry_required"])
        await self.db[LOCATIONS].update_one({"id": "materials"}, {"$set": {"occupancy.items.0.quantity": 20, "occupancy.total_quantity": 20}})
        response, provider = await self.accept()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 2)
        self.assertEqual(await self.on_hand(), 20)
        units = await self.db[UNITS].find({}).to_list(10)
        self.assertTrue(all(len(unit["resource_demands"]) == 1 for unit in units))

    async def test_cancel_during_provider_call_blocks_final_acceptance(self):
        order = self.order()
        async def cancel_before_provider_response(*args):
            reserved = await self.db[UNITS].count_documents({"state": "reserved"})
            self.assertEqual(reserved, 2)
            await fulfillment.auto_route_instant_order(self.db, user_id="owner",
                order=order.model_copy(update={"status": "canceled"}), source_updated_at=LATER)
            return "sent", None
        response, _ = await self.accept(order, sync=AsyncMock(side_effect=cancel_before_provider_response))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_acceptance_changed")
        self.assertEqual(await self.db[fulfillment.WORKFLOWS].count_documents({"stage": "reviewed"}), 0)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "released"}), 2)
        self.assertEqual(await self.on_hand(), 20)

    async def test_physical_completion_rolls_back_stock_and_ready_state_on_event_failure(self):
        response, _ = await self.accept()
        self.assertEqual(response.status_code, 200, response.text)
        await self.seed_physical()
        response = await self.mark_piece("piece-1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.on_hand(), 18)
        await self.db.command({"collMod": pieces.PIECE_EVENTS, "validator": {"event_type": {"$ne": "assembly_piece_marked_ready"}}, "validationLevel": "strict"})
        response = await self.mark_piece("piece-2")
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(await self.on_hand(), 18)
        self.assertEqual((await self.db[pieces.PIECES].find_one({"piece_id": "piece-2"}))["assembly_status"], "pending")
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 1)
        await self.db.command({"collMod": pieces.PIECE_EVENTS, "validator": {}})
        response = await self.mark_piece("piece-2")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.on_hand(), 16)
        response = await self.mark_piece("piece-2")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["idempotent"])
        self.assertEqual(await self.on_hand(), 16)

    async def test_claimed_cancel_releases_only_unconsumed_and_stale_refresh_cannot_resurrect(self):
        order = self.order()
        response, _ = await self.accept(order)
        self.assertEqual(response.status_code, 200, response.text)
        await self.seed_physical()
        self.assertEqual((await self.mark_piece("piece-1")).status_code, 200)
        await self.db[fulfillment.WORKFLOWS].update_one({"order_number": order.order_number}, {"$set": {"claim_batch_id": "already-claimed"}})
        result = await fulfillment.auto_route_instant_order(self.db, user_id="owner",
            order=order.model_copy(update={"status": "canceled"}), source_updated_at=LATER)
        self.assertEqual(result["component_lifecycle"]["state"], "reconciliation_required")
        self.assertEqual(await self.db[UNITS].count_documents({"state": "released"}), 1)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 1)
        self.assertEqual(await self.on_hand(), 18)
        result = await fulfillment.auto_route_instant_order(self.db, user_id="owner", order=order, source_updated_at=WHEN)
        self.assertEqual(result["component_lifecycle"]["state"], "stale_ignored")
        result = await fulfillment.auto_route_instant_order(self.db, user_id="owner", order=order, source_updated_at="2026-09-26T14:00:00+00:00")
        self.assertFalse(result["component_lifecycle"]["accepted"])
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 0)

    async def test_rollout_cutoff_is_explicit_and_historical_refresh_never_backfills(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        result = await fulfillment.auto_route_instant_order(self.db, user_id="owner", order=self.order(), source_updated_at=WHEN)
        self.assertEqual(result["component_lifecycle"]["error_code"], "component_configuration_required")
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory.component_lifecycle_starts_at": WHEN}})
        result = await fulfillment.auto_route_instant_order(self.db, user_id="owner",
            order=self.order(number="historical", created="2020-01-01T00:00:00+00:00"), source_updated_at=WHEN)
        self.assertEqual(result["component_lifecycle"]["error_code"], "component_historical_order_requires_review")
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)

    async def test_manufacturing_receipt_and_prebuilt_virtual_completion_do_not_consume_twice(self):
        response = await self.client.post("/inventory-v2/stock-preparation-orders", json={
            "idempotency_key": "manufacture-synthetic", "supplier_id": "supplier", "assigned_employee_id": "owner",
            "destination_warehouse_id": "wh", "items": [{"product_id": "mp", "quantity": 2, "specifications": []}],
        })
        self.assertEqual(response.status_code, 201, response.text)
        work = response.json()["order"]
        order_id, item_id = work["id"], work["items"][0]["id"]
        self.assertEqual(await self.on_hand(), 20)
        for revision, action in ((1, "start_preparation"), (2, "mark_ready_for_receipt")):
            response = await self.client.post(f"/inventory-v2/stock-preparation-orders/{order_id}/actions", json={"action": action, "expected_revision": revision})
            self.assertEqual(response.status_code, 200, response.text)
        receipts = []
        for index in (1, 2):
            payload = {"idempotency_key": f"manufacture-receipt-{index}", "item_id": item_id,
                "location_id": "finished", "scanned_barcode": "FINISHED", "quantity": 1}
            response = await self.client.post(f"/inventory-v2/stock-preparation-orders/{order_id}/receipts", json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            receipt = response.json()["receipt"]
            self.assertEqual(len(receipt["component_provenance"]["units"]), 1)
            self.assertEqual(receipt["component_provenance"]["units"][0]["unit_index"], index)
            receipts.append(receipt)
        self.assertEqual(await self.on_hand(), 16)
        replay = await self.client.post(f"/inventory-v2/stock-preparation-orders/{order_id}/receipts", json=payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertTrue(replay.json()["duplicate"])
        self.assertEqual(await self.on_hand(), 16)
        await self.db[fulfillment.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "prebuilt-sale", "stage": "pending_review",
            "revision": 0, "items": [{"order_item_id": "line-1", "preparation_route": "direct_assembly", "product_id": "p", "quantity": 1}]})
        response, _ = await self.accept(self.order(number="prebuilt-sale", quantity=1))
        self.assertEqual(response.status_code, 200, response.text)
        workflow = await self.db[fulfillment.WORKFLOWS].find_one({"order_number": "prebuilt-sale"})
        piece_id = workflow["items"][0]["direct_assembly_piece_ids"][0]
        response = await self.mark_piece(piece_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.on_hand(), 16)
        unit = await self.db[UNITS].find_one({"order_id": "prebuilt-sale"})
        self.assertTrue(unit["prebuilt"])
        self.assertEqual(unit["state"], "consumed")
        product_rows = fulfillment._inventory_rows(await self.db[LOCATIONS].find({}).to_list(10))
        self.assertFalse(any(row["location_id"] == "materials" for row in product_rows))


    def source_payload(self, number="intake-order", version=WHEN, status="under_review"):
        return {"id": number, "reference_id": number, "date": WHEN, "updated_at": version,
            "status": {"slug": status, "name": status}, "payment_method": "cod",
            "amounts": {"total": {"amount": 100, "currency": "SAR"}},
            "shipping_address": {"city": "Synthetic city", "street": "Synthetic street"},
            "items": [{"id": "line-1", "product_id": "p", "name": "Synthetic product", "sku": "SYN-P", "quantity": 2}]}

    async def webhook(self, payload, event="order.created"):
        with patch.object(webhook, "_resolve_user_id", AsyncMock(return_value="owner")), \
             patch("mezan_attribution_ledger_sync.safe_sync_order_to_attribution_ledger", AsyncMock(return_value={"synced": False})), \
             patch("first_party_attribution.link_order_attribution", AsyncMock(return_value={"linked": False})):
            return await webhook.sync_order_from_verified_webhook(self.db, {"event": event, "merchant": "synthetic-store", "data": payload})

    async def refresh(self, payload):
        async def provider(_db, _user, method, path, **kwargs):
            self.assertEqual(method, "GET")
            if path == "/orders/items":
                return {"data": payload["items"]}
            if path == "/orders/" + payload["id"]:
                return {"data": payload}
            self.fail("Unexpected external route: " + path)
        async def bank(_db, _user, details):
            return details
        with patch.object(refresh, "call_salla", provider), patch.object(refresh, "_enrich_order_receiving_bank", bank):
            return await refresh.refresh_order_from_salla(self.db, "owner", payload["reference_id"], force=True)

    async def test_real_webhook_refresh_replay_and_stale_canonical_snapshot(self):
        payload = self.source_payload()
        result = await self.webhook(payload)
        self.assertTrue(result["synced"], result)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 2, result)
        first = await self.db[PLANS].find_one({})
        self.assertTrue((await self.webhook(payload))["synced"])
        result = await self.refresh(payload)
        self.assertTrue(result["ok"], result)
        self.assertEqual(await self.db[PLANS].count_documents({}), 1)
        self.assertEqual(await self.db[UNITS].count_documents({}), 2)
        self.assertEqual((await self.db[PLANS].find_one({}))["_id"], first["_id"])
        self.assertEqual(await self.on_hand(), 20)
        cancellation = self.source_payload(version=LATER)
        result = await self.webhook(cancellation, "order.cancelled")
        self.assertTrue(result["synced"], result)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "released"}), 2, result)
        result = await self.refresh(payload)
        self.assertEqual(result.get("reason"), "stale_salla_snapshot", result)
        stored = await self.db.unified_orders.find_one({"order_number": payload["reference_id"]})
        self.assertEqual(stored["order_status_slug"], "canceled")
        self.assertEqual(stored["raw_by_source"]["salla_direct"]["status"]["slug"], "canceled")
        equal = await self.webhook(self.source_payload(version=LATER), "order.updated")
        self.assertEqual(equal["reason"], "stale_salla_snapshot")
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 0)

    async def test_verified_webhook_ack_exposes_durable_failure_and_replay_recovers(self):
        payload = self.source_payload()
        with patch.object(order_service, "get_order", AsyncMock(side_effect=RuntimeError("synthetic mapper interruption"))):
            result = await self.webhook(payload)
        self.assertTrue(result["synced"], result)
        self.assertTrue(result["auto_fulfillment"]["retry_required"])
        intent = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertEqual(intent["state"], "blocked")
        self.assertFalse(intent["accepted"])
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)
        result = await self.webhook(payload)
        self.assertTrue(result["synced"], result)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 2)
        self.assertEqual((await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({}))["state"], "reserved")

    async def seed_batch(self, number="order-1"):
        await self.db[fulfillment.BATCHES].insert_one({"id": "batch", "user_id": "owner", "order_numbers": [number],
            "status": "printed", "print_count": 1, "claimed_by": "owner"})
        await self.db[fulfillment.WORKFLOWS].update_one({"user_id": "owner", "order_number": number},
            {"$set": {"stage": "ready_to_ship", "claim_batch_id": "batch"}}, upsert=True)

    async def test_pack_fallback_consumes_atomically_and_handoff_does_not_consume_twice(self):
        self.assertEqual((await self.accept())[0].status_code, 200)
        await self.seed_batch()
        await self.db.command({"collMod": fulfillment.BATCHES, "validator": {"status": {"$ne": "packed"}}, "validationLevel": "strict"})
        result = await self.client.post("/fulfillment-v2/batches/batch/pack", json={})
        self.assertEqual(result.status_code, 500, result.text)
        self.assertEqual(await self.on_hand(), 20)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 0)
        await self.db.command({"collMod": fulfillment.BATCHES, "validator": {}})
        for _ in range(2):
            result = await self.client.post("/fulfillment-v2/batches/batch/pack", json={})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(await self.on_hand(), 16)
        result = await self.client.post("/fulfillment-v2/batches/batch/handoff", json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.on_hand(), 16)
        self.assertEqual((await self.db[fulfillment.WORKFLOWS].find_one({"order_number": "order-1"}))["stage"], "completed")

    async def test_new_no_plan_batch_and_cancelled_packed_batch_cannot_handoff(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        result = await self.webhook(self.source_payload())
        self.assertTrue(result["synced"], result)
        await self.seed_batch("intake-order")
        result = await self.client.post("/fulfillment-v2/batches/batch/pack", json={})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"]["code"], "component_configuration_required")
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory.component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
        result = await self.client.post("/fulfillment-v2/batches/batch/pack", json={})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"]["code"], "component_reservation_missing")
        self.assertTrue((await self.webhook(self.source_payload()))["synced"])
        result = await self.client.post("/fulfillment-v2/batches/batch/pack", json={})
        self.assertEqual(result.status_code, 200, result.text)
        await self.webhook(self.source_payload(version=LATER), "order.cancelled")
        result = await self.client.post("/fulfillment-v2/batches/batch/handoff", json={})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual((await self.db[fulfillment.BATCHES].find_one({"id": "batch"}))["status"], "packed")
        self.assertEqual(await self.on_hand(), 16)
