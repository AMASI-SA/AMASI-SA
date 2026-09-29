"""Paused-finance operational paths against the isolated real Mongo fixture."""
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests import test_g47_component_lifecycle_integration as lifecycle
import fulfillment_v2_routes as fulfillment
from accounting_atomic import atomic_owner
from operational_atomic import operational_owner
from stock_component_consumption_service import PLANS, UNITS, CLAIMS, LOCATIONS


class OperationalBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = lifecycle.ComponentRouteTests()
        await self.fixture.asyncSetUp()
        self.db = self.fixture.db
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": True}})
        self.finance = ["general_ledger", "accounting_journal_groups_v2", "liabilities",
            "purchase_invoices", "mz2_opening_balance_drafts", "mezan_inventory_cost_events_v1"]

    async def asyncTearDown(self):
        if hasattr(self, "fixture"):
            await self.fixture.asyncTearDown()

    async def assert_no_finance(self):
        for name in self.finance:
            self.assertEqual(await self.db[name].count_documents({}), 0, name)

    async def assert_no_components(self):
        for name in (PLANS, UNITS, CLAIMS):
            self.assertEqual(await self.db[name].count_documents({}), 0, name)
        self.assertEqual(await self.fixture.on_hand(), 20)
        await self.assert_no_finance()

    async def canonical(self, created=lifecycle.WHEN, number="order-1"):
        document = {"user_id": "owner", "order_number": number, "order_status_slug": "under_review"}
        if created is not None:
            document["order_date"] = created
            document["raw_by_source"] = {"salla_direct": {"date": created}}
        await self.db.unified_orders.insert_one(document)

    async def test_paused_review_piece_pack_handoff_preserve_component_atomicity_and_no_finance(self):
        before = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        response, provider = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        provider.assert_awaited_once()
        await self.fixture.seed_physical()
        for index in (1, 2):
            response = await self.fixture.mark_piece(f"piece-{index}")
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.fixture.on_hand(), 16)
        await self.fixture.seed_batch()
        for action in ("pack", "pack", "handoff"):
            response = await self.fixture.client.post(f"/fulfillment-v2/batches/batch/{action}", json={})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.fixture.on_hand(), 16)
        after = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.assertEqual({k: v for k, v in before.items() if k != "revision"},
                         {k: v for k, v in after.items() if k != "revision"})
        await self.assert_no_finance()

    async def test_missing_owner_bootstraps_only_serialization_never_financial_control(self):
        await self.db.mz2_atomic_owners.delete_one({"_id": "owner"})
        response, _ = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        state = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.assertEqual(set(state), {"_id", "revision"})
        with self.assertRaises(HTTPException) as failure:
            await atomic_owner(self.db, "owner", AsyncMock())
        self.assertEqual(failure.exception.status_code, 423)
        await self.assert_no_finance()

    async def test_absent_pause_field_allows_operations_but_not_finance(self):
        await self.db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$unset": {"writes_paused": ""}})
        response, _ = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        callback = AsyncMock()
        with self.assertRaises(HTTPException):
            await atomic_owner(self.db, "owner", callback)
        callback.assert_not_awaited()
        self.assertNotIn("writes_paused", await self.db.mz2_atomic_owners.find_one({"_id": "owner"}))

    async def test_parallel_first_operations_share_one_owner_serialization_row(self):
        await self.db.mz2_atomic_owners.delete_one({"_id": "owner"})
        async def write(index):
            async def callback(scoped):
                await scoped.order_review_events.insert_one({"user_id": "owner", "id": index})
            await operational_owner(self.db, "owner", callback)
        await asyncio.gather(*(write(index) for index in range(4)))
        row = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        self.assertEqual(row, {"_id": "owner", "revision": 4})
        self.assertEqual(await self.db.order_review_events.count_documents({}), 4)

    async def test_caught_denied_writes_and_nested_finance_abort_entire_operation(self):
        async def finance(scoped):
            await atomic_owner(scoped, "owner", AsyncMock())
        async def captured_raw(scoped):
            await atomic_owner(self.db, "owner", AsyncMock())
        async def control(scoped):
            from accounting_write_control import set_write_state
            await set_write_state(self.db, owner="owner", actor_id="owner", paused=False, revision=0, reason="forbidden")
        attempts = [finance, captured_raw, control]
        for collection in self.finance + ["settings", "mz2_atomic_owners", "mezan_cost_resources_v2"]:
            async def mutation(scoped, collection=collection):
                await scoped[collection].insert_one({"user_id": "owner", "id": "forbidden"})
            attempts.append(mutation)
        for attempt in attempts:
            with self.subTest(attempt=attempt.__name__):
                before = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
                async def callback(scoped):
                    await scoped.order_review_events.insert_one({"user_id": "owner", "event_type": "must_rollback"})
                    try:
                        await attempt(scoped)
                    except HTTPException:
                        pass
                    return {"ok": True}
                with self.assertRaises(HTTPException):
                    await operational_owner(self.db, "owner", callback)
                self.assertEqual(await self.db.order_review_events.count_documents({}), 0)
                self.assertEqual(await self.db.mz2_atomic_owners.find_one({"_id": "owner"}), before)
        await self.assert_no_finance()

    async def test_financial_session_escape_and_write_pipeline_are_denied(self):
        async def raw(scoped):
            return scoped._db
        async def session(scoped):
            return scoped._session
        async def command(scoped):
            return scoped.command
        async def aggregate(scoped):
            return await scoped.unified_orders.aggregate([{"$merge": "general_ledger"}]).to_list(1)
        async def unknown(scoped):
            return await scoped.unified_orders.bulk_write([])
        for attempt in (raw, session, command, aggregate, unknown):
            with self.subTest(attempt=attempt.__name__), self.assertRaises(HTTPException):
                await operational_owner(self.db, "owner", attempt)
        await self.assert_no_finance()

    async def test_owner_isolation_and_profile_widening_are_denied(self):
        async def other(scoped):
            await scoped.order_review_events.insert_one({"user_id": "other"})
        async def widen(scoped):
            await operational_owner(scoped, "owner", AsyncMock(), profile="opening_prepare")
        for attempt in (other, widen):
            with self.assertRaises(HTTPException):
                await operational_owner(self.db, "owner", attempt)
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_opening_profile_allows_draft_replacement_state_but_no_replace_delete_or_post(self):
        await self.db.mz2_opening_balance_drafts.insert_one({"id": "draft", "user_id": "owner", "status": "reviewed"})
        async def replace_state(scoped):
            await scoped.mz2_opening_balance_drafts.find_one_and_update({"id": "draft"}, {"$set": {"status": "replaced"}})
        await operational_owner(self.db, "owner", replace_state, profile="opening_prepare")
        self.assertEqual((await self.db.mz2_opening_balance_drafts.find_one({"id": "draft"}))["status"], "replaced")
        async def replace(scoped):
            await scoped.mz2_opening_balance_drafts.replace_one({"id": "draft"}, {"id": "draft", "user_id": "owner", "status": "posted"})
        async def delete(scoped):
            await scoped.mz2_opening_balance_drafts.delete_one({"id": "draft"})
        async def post(scoped):
            await scoped.mz2_opening_balance_drafts.update_one({"id": "draft"}, {"$set": {"status": "posted"}})
        for attempt in (replace, delete, post):
            async def caught(scoped):
                await scoped.mz2_opening_balance_audit.insert_one({"user_id": "owner", "event_type": "must_rollback"})
                try:
                    await attempt(scoped)
                except HTTPException:
                    pass
            with self.assertRaises(HTTPException):
                await operational_owner(self.db, "owner", caught, profile="opening_prepare")
            self.assertEqual(await self.db.mz2_opening_balance_audit.count_documents({}), 0)
            self.assertEqual((await self.db.mz2_opening_balance_drafts.find_one({"id": "draft"}))["status"], "replaced")

    async def test_physical_consumption_preserves_existing_lot_cost_and_forbids_cost_edits(self):
        await self.db[LOCATIONS].update_one({"id": "materials"}, {"$set": {"occupancy.items.0.unit_cost": 7}})
        response, _ = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        await self.fixture.seed_physical()
        response = await self.fixture.mark_piece("piece-1")
        self.assertEqual(response.status_code, 200, response.text)
        lot = await self.db[LOCATIONS].find_one({"id": "materials"})
        self.assertEqual(lot["occupancy"]["items"][0]["unit_cost"], 7)
        changed = copy.deepcopy(lot["occupancy"])
        changed["items"][0]["unit_cost"] = 99
        async def edit(scoped):
            await scoped[LOCATIONS].update_one({"id": "materials"}, {"$set": {"occupancy": changed}})
        with self.assertRaises(HTTPException):
            await operational_owner(self.db, "owner", edit)
        self.assertEqual((await self.db[LOCATIONS].find_one({"id": "materials"}))["occupancy"], lot["occupancy"])

    async def test_bnpl_order_attribution_keeps_metadata_only_and_amount_unchanged(self):
        from bnpl.billing_eligible import propagate_status_to_billing_eligible
        await self.db.payment_transactions.insert_one({"id": "txn", "user_id": "owner", "provider": "tamara",
            "order_number": "order-1", "amount": 100, "status": "captured", "created_at": lifecycle.WHEN})
        async def attribute(scoped):
            return await propagate_status_to_billing_eligible(scoped, "owner", order_number="order-1",
                new_status="delivered", event_at=lifecycle.WHEN)
        result = await operational_owner(self.db, "owner", attribute)
        self.assertEqual(result["updated"], 1)
        transaction = await self.db.payment_transactions.find_one({"id": "txn"})
        self.assertEqual(transaction["amount"], 100)
        self.assertEqual(transaction["billing_eligible_at"], lifecycle.WHEN)
        async def amount(scoped):
            await scoped.payment_transactions.update_one({"id": "txn"}, {"$set": {"amount": 1}})
        with self.assertRaises(HTTPException):
            await operational_owner(self.db, "owner", amount)
        self.assertEqual((await self.db.payment_transactions.find_one({"id": "txn"}))["amount"], 100)

    async def test_legacy_without_setting_allows_canonical_review_scan_pack_no_component_effects(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        await self.canonical(created=None)
        response, provider = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        provider.assert_awaited_once()
        await self.fixture.seed_physical()
        response = await self.fixture.mark_piece("piece-1")
        self.assertEqual(response.status_code, 200, response.text)
        await self.fixture.seed_batch()
        for action in ("pack", "handoff"):
            response = await self.fixture.client.post(f"/fulfillment-v2/batches/batch/{action}", json={})
            self.assertEqual(response.status_code, 200, response.text)
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "order-1"})
        self.assertEqual(marker["operational_cohort"], fulfillment.LEGACY_COMPONENT_COHORT)
        await self.assert_no_components()

    async def test_configured_historical_order_legacy_but_new_order_full_g47(self):
        await self.canonical(created="2020-01-01T00:00:00+00:00")
        response, _ = await self.fixture.accept(self.fixture.order(created="2020-01-01T00:00:00+00:00"))
        self.assertEqual(response.status_code, 200, response.text)
        await self.assert_no_components()
        response, _ = await self.fixture.accept(self.fixture.order(number="new"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.db[UNITS].count_documents({"order_id": "new", "state": "reserved"}), 2)
        await self.assert_no_finance()

    async def test_configured_missing_or_invalid_creation_is_fail_closed(self):
        for created in (None, "not-a-date"):
            await self.db.unified_orders.delete_many({})
            await self.canonical(created=created)
            await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
                "order_date": "2020-01-01T00:00:00+00:00", "received_at": "2020-01-01T00:00:00+00:00"}})
            response, provider = await self.fixture.accept()
            self.assertEqual(response.status_code, 409, response.text)
            provider.assert_not_awaited()
            self.assertEqual(response.json()["detail"]["code"], "component_source_created_at_required")
            await self.assert_no_components()

    async def test_configured_missing_canonical_cannot_be_hidden_by_valid_dto(self):
        response, provider = await self.fixture.accept(seed_canonical=False)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_canonical_order_required")
        provider.assert_not_awaited()
        await self.assert_no_components()

    async def test_creation_equal_to_cutoff_is_full_g47_and_stockout_never_falls_back(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "g47_inventory.component_lifecycle_starts_at": lifecycle.WHEN}})
        await self.canonical()
        await self.db[LOCATIONS].update_one({"id": "materials"}, {"$set": {
            "occupancy.items.0.quantity": 1, "occupancy.total_quantity": 1}})
        response, provider = await self.fixture.accept()
        self.assertEqual(response.status_code, 409, response.text)
        provider.assert_not_awaited()
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[UNITS].count_documents({}), 0)
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertEqual(marker["state"], "blocked")
        self.assertNotEqual(marker.get("operational_cohort"), fulfillment.LEGACY_COMPONENT_COHORT)
        await self.db[LOCATIONS].update_one({"id": "materials"}, {"$set": {
            "occupancy.items.0.quantity": 20, "occupancy.total_quantity": 20}})
        response, _ = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 2)
        await self.assert_no_finance()

    async def test_config_change_during_provider_call_invalidates_legacy_acceptance(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        await self.canonical()
        async def enable(*args):
            await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory.component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
            return "sent", None
        response, _ = await self.fixture.accept(sync=AsyncMock(side_effect=enable))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_acceptance_changed")
        self.assertIsNone(await self.db[fulfillment.WORKFLOWS].find_one({"stage": "reviewed"}))
        await self.assert_no_components()

    async def test_canonical_timezone_aliases_and_unknown_zone_cannot_make_new_order_legacy(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "g47_inventory.component_lifecycle_starts_at": "2026-09-29T21:00:00+00:00"}})
        await self.canonical()
        for alias in ("timezone", "timezone_name", "tz"):
            for value_key in ("date", "datetime", "value", "created"):
                with self.subTest(alias=alias, value_key=value_key):
                    await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
                        "raw_by_source.salla_direct.date": {value_key: "2026-09-29 22:00:00", alias: "UTC"}}})
                    self.assertFalse(await fulfillment.legacy_component_cohort(self.db, user_id="owner", order_number="order-1"))
        for raw_date in ({"date": "2026-09-29 22:00:00", "timezone_name": "not/a-zone"},
                         {"date": "2026-09-29T20:00:00Z", "created": "2026-09-29T22:00:00Z", "timezone": "UTC"}):
            await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
                "raw_by_source.salla_direct.date": raw_date}})
            with self.assertRaises(HTTPException) as failure:
                await fulfillment.legacy_component_cohort(self.db, user_id="owner", order_number="order-1")
            self.assertEqual(failure.exception.detail["code"], "component_source_created_at_required")
        await self.assert_no_components()

    async def test_pack_new_canonical_cohort_cannot_use_historical_dto_fallback(self):
        await self.canonical()
        await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
            "order_date": "2020-01-01T00:00:00+00:00"}})
        await self.fixture.seed_batch()
        old = AsyncMock(return_value=self.fixture.order(created="2020-01-01T00:00:00+00:00"))
        with patch.object(lifecycle.order_service, "get_order", old):
            response = await self.fixture.client.post("/fulfillment-v2/batches/batch/pack", json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_reservation_missing")
        old.assert_not_awaited()
        self.assertEqual((await self.db[fulfillment.BATCHES].find_one({"id": "batch"}))["status"], "printed")
        await self.assert_no_components()

    async def test_existing_plan_never_falls_back_when_configuration_removed(self):
        response, _ = await self.fixture.accept()
        self.assertEqual(response.status_code, 200, response.text)
        await self.fixture.seed_physical()
        await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
            "raw_by_source.salla_direct.date": "invalid", "order_date": "2020-01-01T00:00:00+00:00"}})
        response = await self.fixture.mark_piece("piece-1")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_source_created_at_required")
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 0)
        self.assertEqual(await self.fixture.on_hand(), 20)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory": []}})
        response = await self.fixture.mark_piece("piece-1")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_configuration_required")
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        response = await self.fixture.mark_piece("piece-1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.fixture.on_hand(), 18)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 1)
        await self.assert_no_finance()

    async def test_paused_webhook_refresh_cancel_remain_idempotent(self):
        payload = self.fixture.source_payload()
        self.assertTrue((await self.fixture.webhook(payload))["synced"])
        self.assertTrue((await self.fixture.webhook(payload))["synced"])
        self.assertTrue((await self.fixture.refresh(payload))["ok"])
        self.assertEqual(await self.db[UNITS].count_documents({}), 2)
        self.assertTrue((await self.fixture.webhook(self.fixture.source_payload(version=lifecycle.LATER), "order.cancelled"))["synced"])
        self.assertEqual(await self.db[UNITS].count_documents({"state": "released"}), 2)
        await self.assert_no_finance()

    async def test_real_verified_webhook_paused_unconfigured_ingests_and_replays_without_finance(self):
        # Real tenant resolution, canonical upsert, mapper and fulfillment path;
        # no mocked persistence or component callback, only isolated local DB.
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        await self.db.salla_integrations.insert_one({"store_id": "synthetic-store", "user_id": "owner"})
        payload = self.fixture.source_payload()
        event = {"event": "order.created", "merchant": "synthetic-store", "data": payload}
        for _ in range(2):
            result = await lifecycle.webhook.sync_order_from_verified_webhook(self.db, event)
            self.assertTrue(result["synced"], result)
        self.assertEqual(await self.db.unified_orders.count_documents({"user_id": "owner", "order_number": "intake-order"}), 1)
        canonical = await self.db.unified_orders.find_one({"order_number": "intake-order"})
        self.assertEqual(canonical["raw_by_source"]["salla_direct"], payload)
        updated = self.fixture.source_payload(version=lifecycle.LATER, status="in_progress")
        result = await lifecycle.webhook.sync_order_from_verified_webhook(self.db,
            {"event": "order.updated", "merchant": "synthetic-store", "data": updated})
        self.assertTrue(result["synced"], result)
        canonical = await self.db.unified_orders.find_one({"order_number": "intake-order"})
        self.assertEqual(canonical["raw_by_source"]["salla_direct"], updated)
        self.assertEqual(canonical["order_status_slug"], "in_progress")
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "intake-order"})
        self.assertEqual(marker["operational_cohort"], fulfillment.LEGACY_COMPONENT_COHORT)
        finance_callback = AsyncMock()
        with self.assertRaises(HTTPException) as failure:
            await atomic_owner(self.db, "owner", finance_callback)
        self.assertEqual(failure.exception.status_code, 423)
        finance_callback.assert_not_awaited()
        await self.assert_no_components()

    async def test_g47_exception_after_canonical_commit_retains_visible_order_and_retry_marker(self):
        await self.db.salla_integrations.insert_one({"store_id": "synthetic-store", "user_id": "owner"})
        payload = self.fixture.source_payload()
        event = {"event": "order.created", "merchant": "synthetic-store", "data": payload}
        with patch.object(fulfillment, "reconcile_component_order_lifecycle",
                          AsyncMock(side_effect=RuntimeError("isolated G47 failure"))):
            result = await lifecycle.webhook.sync_order_from_verified_webhook(self.db, event)
        self.assertTrue(result["synced"], result)
        self.assertTrue(result["auto_fulfillment"]["retry_required"], result)
        self.assertEqual(result["auto_fulfillment"]["error_code"], "component_intake_retry_required")
        canonical = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "intake-order"})
        self.assertEqual(canonical["raw_by_source"]["salla_direct"], payload)
        self.assertTrue(canonical["g47_salla_snapshot"]["component_pending"])
        marker = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({"order_number": "intake-order"})
        self.assertEqual(marker["state"], "blocked")
        self.assertTrue(marker["retry_required"])
        await self.assert_no_components()
        replay = await lifecycle.webhook.sync_order_from_verified_webhook(self.db, event)
        self.assertTrue(replay["synced"], replay)
        self.assertEqual(await self.db.unified_orders.count_documents({"order_number": "intake-order"}), 1)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "reserved"}), 2)
        await self.assert_no_finance()
