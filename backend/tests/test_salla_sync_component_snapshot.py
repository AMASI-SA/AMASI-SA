"""Real local owner transactions; sync must preserve component provenance."""
import unittest
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
from salla_integration import sync
from salla_integration import webhook_order_sync as webhook_sync
from salla_integration.webhook_event_capture import capture_unknown_event
from stock_component_consumption_service import PLANS, UNITS, LOCATIONS
from tests import test_g47_component_lifecycle_integration as fixture


class SyncComponentSnapshotTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook

    async def seed(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload(status="completed")
        self.assertTrue((await self.webhook(payload))["synced"])
        lifecycle = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertTrue(lifecycle["accepted"])
        self.inventory = {name: await self.db[name].find({}).to_list(100)
                          for name in (PLANS, UNITS, LOCATIONS, fulfillment.WORKFLOWS)}
        return payload

    async def resync(self, payload):
        with patch.object(sync, "_fetch_salla_order_details", AsyncMock(return_value=payload)), \
             patch.object(sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})), \
             patch.object(sync, "_record_order_adjustment", AsyncMock(return_value=None)), \
             patch("product_costs.attach_cost_to_order_doc", AsyncMock(return_value={})):
            return await sync.resync_single_order(self.db, "owner", payload["reference_id"])

    async def assert_inventory_unchanged(self):
        for name, before in self.inventory.items():
            self.assertEqual(await self.db[name].find({}).to_list(100), before, name)

    async def test_single_resync_newer_unchanged_contract_advances_acceptance(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        lifecycle = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertEqual(source["g47_salla_snapshot"]["source_updated_at"], fulfillment.component_provider_version(payload))
        self.assertEqual(lifecycle["snapshot_revision"], source["g47_salla_snapshot"]["revision"])
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_single_resync_material_change_stays_pending(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        payload["items"][0]["quantity"] = 3
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_single_resync_stale_cannot_replace_newer_snapshot(self):
        payload = await self.seed()
        before = await self.db.unified_orders.find_one({"user_id": "owner"})
        stale = deepcopy(payload)
        stale["updated_at"] = "2026-09-26T11:59:00+00:00"
        stale["items"][0]["quantity"] = 7
        result = await self.resync(stale)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result.get("skipped"), result)
        self.assertEqual(await self.db.unified_orders.find_one({"user_id": "owner"}), before)
        await self.assert_inventory_unchanged()

    async def test_batch_sync_advances_unchanged_contract_without_stock_work(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        with patch.object(sync, "call_salla", AsyncMock(return_value={
            "data": [payload], "pagination": {"totalPages": 1, "currentPage": 1}})), \
             patch.object(sync, "_enrich_order_receiving_bank", AsyncMock(return_value=payload)), \
             patch.object(sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})):
            result = await sync.run_orders_sync(self.db, "owner")
        self.assertEqual(result["errors_count"], 0, result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(source["g47_salla_snapshot"]["source_updated_at"], fulfillment.component_provider_version(payload))
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_scheduled_refresh_preserves_preparation_and_item_choices(self):
        from datetime import datetime, timedelta, timezone
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        payload["date"] = {"date": "2026-09-26 12:00:00", "timezone": "UTC"}
        end = datetime(2026, 9, 26, 13, tzinfo=timezone.utc)
        async def provider(db, user, method, path, **kw):
            self.assertEqual(method, "GET")
            if path == "/orders":
                return {"data": [payload], "pagination": {"totalPages": 1, "currentPage": 1}}
            self.assertEqual(path, "/orders/items")
            return {"data": payload["items"]}
        from resource_governor import governor
        with patch.object(sync, "call_salla", provider), \
             patch.object(sync, "_enrich_order_receiving_bank", AsyncMock(return_value=payload)), \
             patch.object(sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})), \
             patch.object(governor, "peek", return_value=("allowed", None)):
            result = await sync.run_orders_sync(self.db, "owner", refresh_window=(end - timedelta(hours=24), end))
        self.assertEqual(result["errors_count"], 0, result)
        self.assertFalse(result["truncated"], result)
        await self.assert_inventory_unchanged()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        self.assertEqual(source["raw_by_source"]["salla_direct"]["items"], payload["items"])

    async def test_single_resync_cancellation_stays_blocked_without_stock_work(self):
        payload = await self.seed()
        payload.update(updated_at="2026-09-26T12:00:10+00:00", status={"slug": "canceled"})
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        self.assertTrue(source["g47_salla_snapshot"]["cancelled"])
        await self.assert_inventory_unchanged()

    async def test_undated_cancellation_blocks_without_replacing_canonical_payload(self):
        payload = await self.seed()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        payload.pop("updated_at")
        payload["status"] = {"slug": "canceled"}
        result = await self.resync(payload)
        self.assertFalse(result["ok"], result)
        current = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(current["raw_by_source"], source["raw_by_source"])
        self.assertTrue(current["g47_salla_snapshot"]["requires_authoritative_refresh"])
        await self.assert_inventory_unchanged()

    async def test_payment_webhook_clears_balance_and_stale_event_cannot_restore_it(self):
        payload = self.source_payload(status="in_progress")
        payload["payment_method"] = "mada"
        payload["payment_actions"] = {"remaining_action": {"has_remaining_amount": True,
            "paid_amount": {"amount": 80}, "remaining_amount": {"amount": 20}}}
        self.assertTrue((await self.webhook(payload))["synced"])
        paid = deepcopy(payload)
        paid["updated_at"] = "2026-09-26T12:01:00+00:00"
        paid["payment_actions"]["remaining_action"] = {"has_remaining_amount": False,
            "paid_amount": {"amount": 100}, "remaining_amount": None}
        result = await self.webhook(paid, event="order.payment.updated")
        self.assertTrue(result["synced"], result)
        row = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(row["remaining_amount"], 0)
        self.assertEqual(row["paid_amount"], 100)
        self.assertEqual(row["payment_collection_status"], "paid")
        stale = await self.webhook(payload, event="order.payment.updated")
        self.assertFalse(stale["synced"])
        self.assertEqual((await self.db.unified_orders.find_one({"user_id": "owner"}))["remaining_amount"], 0)


    async def test_payment_inbox_retries_deduplicates_and_settles(self):
        from datetime import datetime, timedelta, timezone
        from salla_integration import payment_update_inbox as inbox
        payload = self.source_payload(status="in_progress")
        payload["payment_method"] = "mada"
        payload["payment_actions"] = {"remaining_action": {"has_remaining_amount": True,
            "paid_amount": {"amount": 80}, "remaining_amount": {"amount": 20}}}
        self.assertTrue((await self.webhook(payload))["synced"])
        payload["updated_at"] = "2026-09-26T12:01:00+00:00"
        payload["payment_actions"]["remaining_action"] = {"has_remaining_amount": False,
            "paid_amount": {"amount": 100}, "remaining_amount": None}
        event = {"event": "order.payment.updated", "merchant": "synthetic-store", "data": payload}
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        async def apply(_db, event):
            return await self.webhook(event["data"], event=event["event"])
        with patch.object(inbox, "_resolve_user_id", AsyncMock(return_value="owner")):
            key = await inbox.enqueue_verified_payment(self.db, event)
            self.assertEqual(await inbox.enqueue_verified_payment(self.db, event), key)
            self.assertEqual(await self.db[inbox.COLLECTION].count_documents({}), 1)
            with patch.object(inbox, "sync_order_from_verified_webhook", AsyncMock(side_effect=RuntimeError("synthetic failure"))):
                await inbox.run_once(self.db, now=now)
            queued = await self.db[inbox.COLLECTION].find_one({"_id": key})
            self.assertEqual(queued["state"], "pending")
            self.assertEqual(queued["attempts"], 1)
            with patch.object(inbox, "sync_order_from_verified_webhook", apply):
                await inbox.run_once(self.db, now=now + timedelta(minutes=1))
            self.assertEqual((await self.db[inbox.COLLECTION].find_one({"_id": key}))["state"], "done")
            row = await self.db.unified_orders.find_one({"user_id": "owner"})
            self.assertEqual(row["remaining_amount"], 0)
            self.assertEqual(row["paid_amount"], 100)

    async def test_payment_inbox_pauses_if_merchant_owner_changes(self):
        from datetime import datetime, timedelta, timezone
        from salla_integration import payment_update_inbox as inbox
        event = {"event": "order.payment.updated", "merchant": "synthetic-store", "data": self.source_payload()}
        with patch.object(inbox, "_resolve_user_id", AsyncMock(return_value="owner")):
            key = await inbox.enqueue_verified_payment(self.db, event)
        handler = AsyncMock()
        with patch.object(inbox, "_resolve_user_id", AsyncMock(return_value="other-owner")), patch.object(inbox, "sync_order_from_verified_webhook", handler):
            await inbox.run_once(self.db, now=datetime.now(timezone.utc) + timedelta(seconds=1))
        handler.assert_not_awaited()
        self.assertEqual((await self.db[inbox.COLLECTION].find_one({"_id": key}))["state"], "paused")


    async def test_cost_option_recovery_joins_real_owner_transaction(self):
        from salla_integration.cost_option_recovery import apply_options
        payload = self.source_payload()
        payload['items'][0]['options'] = []
        await self.webhook(payload)
        before = await self.db.unified_orders.find_one({'user_id': 'owner'})
        items = deepcopy(payload['items'])
        items[0]['options'] = [{'product_option_id': 'synthetic-option',
            'name': 'Renamed question', 'value': {'id': 'synthetic-choice', 'name': 'Renamed answer'}}]
        inventory = {name: await self.db[name].find({}).to_list(100)
                     for name in (PLANS, UNITS, LOCATIONS, fulfillment.WORKFLOWS)}
        with patch('accounting_shipping_native_observer.observe_delivery',
                   AsyncMock(side_effect=AssertionError('No financial observation'))) as observer:
            self.assertTrue(await apply_options(self.db, before, items))
            observer.assert_not_awaited()
        after = await self.db.unified_orders.find_one({'user_id': 'owner'})
        self.assertEqual(after['products'][0]['options_raw'], items[0]['options'])
        self.assertEqual(after['order_status_slug'], before['order_status_slug'])
        self.assertEqual(after['total_amount'], before['total_amount'])
        self.assertGreater(after['g47_salla_snapshot']['revision'], before['g47_salla_snapshot']['revision'])
        for name, prior in inventory.items():
            self.assertEqual(await self.db[name].find({}).to_list(100), prior, name)

    async def test_cost_option_recovery_rejects_concurrent_source_change(self):
        from salla_integration.cost_option_recovery import apply_options, SourceChanged
        payload = self.source_payload()
        payload['items'][0]['options'] = []
        await self.webhook(payload)
        before = await self.db.unified_orders.find_one({'user_id': 'owner'})
        items = deepcopy(payload['items'])
        items[0]['options'] = [{'product_option_id': 'option', 'value': {'id': 'choice'}}]
        await self.db.unified_orders.update_one({'user_id': 'owner'},
            {'$inc': {'g47_salla_snapshot.revision': 1}})
        with self.assertRaises(SourceChanged):
            await apply_options(self.db, before, items)
        after = await self.db.unified_orders.find_one({'user_id': 'owner'})
        self.assertEqual(after['products'], before['products'])


class StaleCreationOptionsMongoTests(unittest.IsolatedAsyncioTestCase):
    """Verified events keep the current snapshot while recovering absent choices.

    Persistence, owner fencing, source CAS and component revalidation use the
    disposable replica set. Only unrelated analytics and outbound boundaries
    are doubled; no source writer or transaction implementation is replaced.
    """

    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    NUMBER = "synthetic-option-race"
    MERCHANT = "synthetic-options-store"

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        await self.db.salla_integrations.insert_one({
            "store_id": self.MERCHANT, "user_id": "owner",
        })

    def event_pair(self):
        current = self.source_payload(
            number=self.NUMBER, version="2026-09-26T12:00:01+00:00",
            status="in_progress",
        )
        current["id"] = "synthetic-native-order"
        current["currency"] = "SAR"
        current["amounts"] = {"total": {"amount": 110, "currency": "SAR"}}
        current["items"] = []
        for identity, price in (("line-small", 40), ("line-large", 60)):
            current["items"].append({
                "id": identity, "product_id": "p", "quantity": 1,
                "product": {"id": "p", "name": "Synthetic product", "sku": "SYN-P"},
                "name": "Synthetic product", "sku": "SYN-P",
                "product_sku_id": "synthetic-common-variant", "currency": "SAR",
                "amounts": {
                    "price_without_tax": {"amount": price, "currency": "SAR"},
                    "total_discount": {"amount": 0, "currency": "SAR"},
                    "tax": {"percent": 10, "amount": price / 10, "currency": "SAR"},
                    "total": {"amount": price * 1.1, "currency": "SAR"},
                },
            })
        created = deepcopy(current)
        created["updated_at"] = "2026-09-26T12:00:00+00:00"
        created["status"] = {"slug": "under_review", "name": "under_review"}
        for item, answer in zip(created["items"], ("Small", "Large")):
            item["options"] = [{
                "product_option_id": "synthetic-size-option", "name": "Size",
                "value": {"id": "synthetic-" + answer.lower(), "name": answer},
            }]
        # Neither SKU, product, variant nor array position identifies a line.
        created["items"].reverse()
        return current, created

    async def ingest(self, payload, event):
        with patch("mezan_attribution_ledger_sync.safe_sync_order_to_attribution_ledger",
                   AsyncMock(return_value={"synced": False})), \
             patch("first_party_attribution.link_order_attribution",
                   AsyncMock(return_value={"linked": False})):
            return await webhook_sync.sync_order_from_verified_webhook(self.db, {
                "event": event, "merchant": self.MERCHANT, "data": deepcopy(payload),
            })

    async def current_order(self):
        return await self.db.unified_orders.find_one({
            "user_id": "owner", "order_number": self.NUMBER,
        })

    async def unrelated_state(self):
        # All existing and newly created collections are checked, including
        # invoices, reservations, preparation, historical events and costs.
        permitted = {"unified_orders", "mz2_atomic_owners", "salla_webhook_event_captures"}
        return {
            name: await self.db[name].find({}).sort("_id", 1).to_list(1000)
            for name in sorted(await self.db.list_collection_names())
            if name not in permitted
        }

    async def deliver_stale(self, payload, event="order.created"):
        forbidden = (
            "httpx.AsyncClient.send",
            "fulfillment_v2_routes.auto_route_instant_order",
            "accounting_shipping_native_observer.observe_delivery",
            "mezan_attribution_ledger_sync.safe_sync_order_to_attribution_ledger",
            "first_party_attribution.link_order_attribution",
            "integrations_control_center.snapchat_capi_purchases.enqueue_snapchat_purchase_from_salla_event",
        )
        with ExitStack() as stack:
            calls = [stack.enter_context(patch(
                target, AsyncMock(side_effect=AssertionError("Option enrichment must not call " + target)),
            )) for target in forbidden]
            stack.enter_context(patch("salla_orders_v3.worker.shadow_enabled", return_value=False))
            result = await capture_unknown_event(self.db, {
                "event": event, "merchant": self.MERCHANT, "data": deepcopy(payload),
            }, known_events=())
            for call in calls:
                call.assert_not_awaited()
        self.assertTrue(result["captured"], result)
        self.assertFalse(result["order_sync"]["synced"], result)
        self.assertTrue(result["no_salla_api_calls"], result)
        self.assertTrue(result["no_qoyod_calls"], result)
        return result

    async def seed_sparse_status(self):
        current, created = self.event_pair()
        result = await self.ingest(current, "order.status.updated")
        self.assertTrue(result["synced"], result)
        source = await self.current_order()
        self.assertEqual(len(source["products"]), 2)
        self.assertTrue(all(not item.get("options") for item in source["products"]))
        self.assertTrue(all("options" not in item for item in
                            source["raw_by_source"]["salla_direct"]["items"]))
        return current, created

    async def test_stale_creation_enriches_native_lines_without_other_writes(self):
        current, created = await self.seed_sparse_status()
        for name in ("mezan_supplier_invoices_v2", "mezan_preparation_piece_events_v1",
                     "mezan_preparation_route_events_v1", "order_adjustments"):
            await self.db[name].insert_one({
                "_id": "synthetic-preserved-history", "user_id": "owner",
                "order_number": self.NUMBER, "immutable_value": "preserve",
            })
        await self.db.unified_orders.update_one({"user_id": "owner", "order_number": self.NUMBER}, {
            "$set": {"historical_cost_snapshot": {"synthetic_total": 29},
                     "preparation_history": [{"event": "synthetic-history"}]},
        })
        before = await self.current_order()
        unrelated = await self.unrelated_state()

        result = await self.deliver_stale(created)

        self.assertEqual(result["order_sync"]["reason"], "stale_salla_snapshot")
        self.assertEqual(result["order_sync"].get("customer_options_enrichment"), {
            "attempted": True, "applied": True, "line_count": 2,
            "reason": "customer_options_enriched",
        })
        self.assertEqual(result["order_mutation_scope"], "customer_options_only")
        choices = {item["id"]: item["options"] for item in created["items"]}
        expected = deepcopy(before)
        for item in expected["products"]:
            options = choices[item["order_item_id"]]
            item["options_raw"] = options
            item["options"] = [{"name": "Size", "value": options[0]["value"]["name"]}]
        for item in expected["raw_by_source"]["salla_direct"]["items"]:
            item["options"] = choices[item["id"]]
        expected["g47_salla_snapshot"]["revision"] += 1
        expected["g47_salla_snapshot"]["component_pending"] = True
        self.assertEqual(await self.current_order(), expected)
        self.assertEqual(await self.unrelated_state(), unrelated)
        capture = await self.db.salla_webhook_event_captures.find_one({"event": "order.created"})
        self.assertEqual(capture["order_sync"], result["order_sync"])
        self.assertEqual(capture["order_mutation_scope"], "customer_options_only")
        self.assertEqual(expected["order_status_slug"], current["status"]["slug"])

    async def test_rich_creation_first_survives_newer_sparse_status(self):
        current, created = self.event_pair()
        self.assertTrue((await self.ingest(created, "order.created"))["synced"])
        first = await self.current_order()
        result = await self.ingest(current, "order.status.updated")
        self.assertTrue(result["synced"], result)
        after = await self.current_order()
        expected = {item["id"]: item["options"] for item in created["items"]}
        actual = {item["id"]: item.get("options")
                  for item in after["raw_by_source"]["salla_direct"]["items"]}
        self.assertEqual(actual, expected)
        self.assertEqual({item["order_item_id"]: item["options"] for item in after["products"]},
                         {item["order_item_id"]: item["options"] for item in first["products"]})
        self.assertEqual(after["order_status_slug"], "in_progress")
        self.assertEqual(after["g47_salla_snapshot"]["source_updated_at"],
                         fulfillment.component_provider_version(current))

    async def test_duplicate_stale_creation_does_not_increment_snapshot(self):
        _, created = await self.seed_sparse_status()
        first = await self.deliver_stale(created)
        self.assertTrue(first["order_sync"].get("customer_options_enrichment", {}).get("applied"), first)
        before = await self.current_order()
        unrelated = await self.unrelated_state()

        duplicate = await self.deliver_stale(created)

        self.assertFalse(duplicate["order_sync"]["customer_options_enrichment"]["applied"])
        self.assertEqual(duplicate["order_mutation_scope"], "none")
        self.assertEqual(await self.current_order(), before)
        self.assertEqual(await self.unrelated_state(), unrelated)
        self.assertEqual(await self.db.salla_webhook_event_captures.count_documents({}), 1)
        capture = await self.db.salla_webhook_event_captures.find_one({})
        self.assertEqual(capture["delivery_count"], 2)

    async def test_concurrent_source_change_rejects_stale_choices_under_real_owner_fence(self):
        from salla_integration import cost_option_recovery as recovery
        _, created = await self.seed_sparse_status()
        real_apply = recovery.apply_options
        concurrent = {}

        async def change_before_transaction(db, order, items):
            self.assertFalse(concurrent, "Retry must requalify the changed source before applying options")
            # A legacy writer outside the source owner's transaction commits
            # after qualification. Real Mongo must abort the stale transaction;
            # its retry must reject this new source, not import old choices.
            await self.db.unified_orders.update_one({"user_id": "owner", "order_number": self.NUMBER}, {
                "$inc": {"g47_salla_snapshot.revision": 1},
                "$set": {"raw_by_source.salla_direct.items.0.quantity": 3,
                          "products.0.quantity": 3},
            })
            concurrent["order"] = await self.current_order()
            return await real_apply(db, order, items)

        unrelated = await self.unrelated_state()
        with patch.object(recovery, "apply_options", side_effect=change_before_transaction) as apply:
            result = await self.deliver_stale(created)
        apply.assert_awaited_once()
        enrichment = result["order_sync"]["customer_options_enrichment"]
        self.assertFalse(enrichment["applied"], result)
        self.assertEqual(await self.current_order(), concurrent["order"])
        self.assertEqual(await self.unrelated_state(), unrelated)

    async def test_explicit_empty_native_choices_are_not_replaced(self):
        current, created = self.event_pair()
        for item in current["items"]:
            item["options"] = []
        self.assertTrue((await self.ingest(current, "order.status.updated"))["synced"])
        before = await self.current_order()
        unrelated = await self.unrelated_state()

        result = await self.deliver_stale(created)

        self.assertFalse(result["order_sync"].get("customer_options_enrichment", {}).get("applied", False))
        self.assertEqual(result["order_mutation_scope"], "none")
        self.assertEqual(await self.current_order(), before)
        self.assertEqual(await self.unrelated_state(), unrelated)

    async def test_stale_updated_event_cannot_import_creation_choices(self):
        _, created = await self.seed_sparse_status()
        before = await self.current_order()
        unrelated = await self.unrelated_state()

        result = await self.deliver_stale(created, event="order.status.updated")

        self.assertEqual(result["order_sync"]["reason"], "stale_salla_snapshot")
        self.assertNotIn("customer_options_enrichment", result["order_sync"])
        self.assertEqual(await self.current_order(), before)
        self.assertEqual(await self.unrelated_state(), unrelated)

    async def test_other_owner_with_same_reference_is_unchanged(self):
        _, created = await self.seed_sparse_status()
        other = deepcopy(await self.current_order())
        other.pop("_id")
        other["user_id"] = "other-owner"
        other["g47_salla_snapshot"]["revision"] = 73
        await self.db.unified_orders.insert_one(other)
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "other-owner", "revision": 11, "writes_paused": False, "control_revision": 0,
        })
        other_before = await self.db.unified_orders.find_one({"user_id": "other-owner"})
        other_fence = await self.db.mz2_atomic_owners.find_one({"_id": "other-owner"})
        unrelated = await self.unrelated_state()

        result = await self.deliver_stale(created)

        self.assertTrue(result["order_sync"].get("customer_options_enrichment", {}).get("applied"), result)
        self.assertEqual(await self.db.unified_orders.find_one({"user_id": "other-owner"}), other_before)
        self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "other-owner"}), other_fence)
        self.assertEqual(await self.unrelated_state(), unrelated)


if __name__ == "__main__":
    unittest.main()
