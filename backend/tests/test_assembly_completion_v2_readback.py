"""Blocked v2 reads: real local Mongo replica set, synthetic provider IO only."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import assembly_completion_delivery as delivery
import assembly_completion_v2_readback as readback
import fulfillment_v2_routes as fulfillment
from accounting_atomic import SessionDatabase
from accounting_write_control import AccountingDatabase
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING
from stock_component_consumption_service import PLANS, UNITS


class BlockedCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["v2_readback_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.addCleanup(self.mongo.close)
        async def cleanup():
            self.assertTrue(self.db.name.startswith("v2_readback_"))
            await self.mongo.drop_database(self.db.name)
        self.addAsyncCleanup(cleanup)
        self.order = {"id": "internal-1", "reference_id": "synthetic-1",
            "status": {"slug": "completed"}, "date": "2026-10-01T10:00:00Z",
            "updated_at": "2026-10-17T12:00:00Z", "items": [], "shipping": {"company": "iMile"}}
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "synthetic-1",
            "order_date": "2026-10-01T10:00:00Z", "order_status": "completed", "order_status_slug": "completed",
            "raw_by_source": {"salla_direct": deepcopy(self.order)},
            "g47_salla_snapshot": {"source_updated_at": "2026-10-17T12:00:00Z", "revision": 5,
                                   "component_pending": False, "requires_authoritative_refresh": False},
            CURRENT_SHIPPING: {"company_name": "iMile", "shipment_id": "shipment-current", "status": "created",
                "tracking_number": "SYNTHETIC-AWB", "label_url": "https://labels.test/current.pdf"}})
        await self.db.order_review_workflows.insert_one({"user_id": "owner", "order_number": "synthetic-1",
            "stage": "completed", "assembly_status": "completed", "revision": 4,
            "assembly_ready_piece_count": 2, "assembly_total_piece_count": 2, "items": [],
            "shipping_status_epoch": 2, "store_delivery_assignment_id": "assignment-current",
            "assembly_delivery": {"version": 2, "state": "requires_attention", "claim": None,
                "lease_until": "", "error_code": "assembly_canonical_status_blocked", "attempts": 3,
                "status_attempted": True, "order_confirmed": False}})
        await self.db[PLANS].insert_one({"user_id": "owner", "order_id": "synthetic-1", "state": "accepted",
            "source_version": {"kind": "sequence", "value": 3}, "lines": [{"quantity": 2}]})
        await self.db[fulfillment.COMPONENT_LIFECYCLES].insert_one({"user_id": "owner", "order_number": "synthetic-1",
            "state": "reserved", "accepted": True, "generation": 3, "cancelled": False, "retry_required": False,
            "source_updated_at": "2026-10-17T12:00:00Z"})
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {
            "component_lifecycle_starts_at": "2026-09-01T00:00:00Z"}})
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 7, "writes_paused": False, "control_revision": 0})
        # Protected effects remain visible in whole-database before/after checks.
        for collection in (UNITS, "warehouse_locations", "general_ledger", "order_review_events", "mezan_preparation_pieces_v1"):
            await self.db[collection].insert_one({"user_id": "owner", "sentinel": "unchanged", "quantity": 2})
        await self.reseal()
        self.provider = AsyncMock(side_effect=self.provider_read)
        for replacement in (
            patch.object(shipping, "call_salla", self.provider),
            patch.object(shipping, "_resolve_order", AsyncMock(side_effect=AssertionError("no keyword lookup"))),
            patch.object(shipping, "refresh_shipping_label", AsyncMock(side_effect=AssertionError("no refresh or issuance"))),
            patch.object(shipping, "_refresh_provider_shipping_label", AsyncMock(side_effect=AssertionError("no PDF"))),
            patch.object(shipping, "_best_effort_resync", AsyncMock(side_effect=AssertionError("no resync"))),
            patch.object(fulfillment, "allow_legacy_component_execution", AsyncMock(side_effect=AssertionError("no legacy writer"))),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    async def reseal(self):
        source = await self.db.unified_orders.find_one({})
        workflow = await self.db.order_review_workflows.find_one({})
        await self.db.order_review_workflows.update_one({}, {"$set": {
            "assembly_delivery.source_fingerprint": delivery.source_fingerprint(source),
            "assembly_delivery.workflow_fingerprint": delivery.workflow_fingerprint(workflow)}})

    async def provider_read(self, db, owner, method, path, **kwargs):
        self.assertIs(db.current() if isinstance(db, AccountingDatabase) else db, self.db)
        self.assertEqual((owner, method, path), ("owner", "GET", "/orders/internal-1"))
        self.assertEqual(kwargs, {})
        return {"data": deepcopy(self.order)}

    async def snapshot(self):
        return {name: await self.db[name].find({}).to_list(None)
                for name in await self.db.list_collection_names()}

    async def read(self):
        return await readback.read_blocked_completion(self.db, "owner", "synthetic-1")

    async def assert_no_write_read(self, *, expected_none=False):
        before = await self.snapshot()
        result = await self.read()
        self.assertEqual(await self.snapshot(), before)
        if expected_none:
            self.assertIsNone(result)
        return result

    async def test_blocked_v2_completed_returns_saved_label_one_get_zero_writes(self):
        result = await self.assert_no_write_read()
        self.assertTrue(result["assembly_completion_confirmed"])
        self.assertTrue(result["order_status_completed"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["order_completion_status"], "confirmed")
        self.assertEqual(result["label_status"], "available")
        self.assertEqual(result["label_url"], "https://labels.test/current.pdf")
        self.assertFalse(result["requires_attention"])
        self.assertIsNone(result["error_code"])
        self.provider.assert_awaited_once()

    async def test_source_refresh_code_can_recover_only_after_flags_resolved(self):
        await self.db.order_review_workflows.update_one({}, {"$set": {"assembly_delivery.error_code": "completion_source_refresh_pending"}})
        self.assertTrue((await self.assert_no_write_read())["ready"])
        await self.db.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        await self.reseal()
        await self.assert_no_write_read(expected_none=True)
        self.assertEqual(self.provider.await_count, 1)

    async def test_pending_courier_confirms_status_but_never_fabricates_document(self):
        self.order["shipping"] = {"company": "مندوب المتجر", "company_code": "0"}
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.shipping": deepcopy(self.order["shipping"]),
            f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر", f"{CURRENT_SHIPPING}.company_code": "0"}})
        await self.reseal()
        result = await self.assert_no_write_read()
        self.assertTrue(result["order_status_completed"])
        self.assertEqual(result["label_type"], "store_courier")
        self.assertFalse(result["ready"])
        self.assertEqual(result["label_status"], "pending")
        self.assertIsNone(result["label_url"])
        self.assertIsNone(result["print_data"])
        self.provider.assert_awaited_once()

    async def test_missing_label_preserves_confirmed_order_without_fetching_label(self):
        await self.db.unified_orders.update_one({}, {"$unset": {f"{CURRENT_SHIPPING}.label_url": ""}})
        result = await self.assert_no_write_read()
        self.assertTrue(result["order_status_completed"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["label_status"], "pending")
        self.provider.assert_awaited_once()

    async def test_ineligible_outbox_assembly_owner_or_claim_never_reads_provider(self):
        original = await self.db.order_review_workflows.find_one({})
        changes = [
            {"assembly_delivery": None}, {"assembly_delivery": {}}, {"assembly_delivery.version": 1},
            {"assembly_delivery.state": "pending"}, {"assembly_delivery.state": "confirmed"},
            {"assembly_delivery.error_code": "TimeoutError"}, {"assembly_delivery.error_code": "assembly_completion_evidence_changed"},
            {"assembly_delivery.legacy_readback_only": True}, {"assembly_delivery.claim": "active-worker"},
            {"assembly_delivery.lease_until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()},
            {"assembly_delivery.lease_until": "invalid"}, {"assembly_delivery.lease_until": "2026-10-17T10:00:00"},
            {"assembly_status": "in_progress"}, {"user_id": "other-owner"},
        ]
        for change in changes:
            with self.subTest(change=change):
                await self.db.order_review_workflows.replace_one({}, deepcopy(original))
                await self.db.order_review_workflows.update_one({}, {"$set": change})
                await self.assert_no_write_read(expected_none=True)
        self.provider.assert_not_awaited()

    async def test_expired_lease_without_claim_can_read(self):
        await self.db.order_review_workflows.update_one({}, {"$set": {
            "assembly_delivery.lease_until": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()}})
        self.assertTrue((await self.assert_no_write_read())["ready"])

    async def test_material_fingerprint_mismatch_or_missing_never_recovers(self):
        source = await self.db.unified_orders.find_one({})
        workflow = await self.db.order_review_workflows.find_one({})
        for collection, change in (
            ("unified_orders", {"raw_by_source.salla_direct.items": [{"quantity": 3}]}),
            ("order_review_workflows", {"items": [{"quantity": 3}]}),
            ("order_review_workflows", {"assembly_delivery.source_fingerprint": None}),
            ("order_review_workflows", {"assembly_delivery.workflow_fingerprint": None}),
        ):
            with self.subTest(change=change):
                await self.db.unified_orders.replace_one({}, deepcopy(source))
                await self.db.order_review_workflows.replace_one({}, deepcopy(workflow))
                await self.db[collection].update_one({}, {"$set": change})
                await self.assert_no_write_read(expected_none=True)
        self.provider.assert_not_awaited()

    async def test_component_lifecycle_plan_generation_and_watermark_fail_closed(self):
        originals = {name: await self.db[name].find_one({}) for name in
                     (PLANS, fulfillment.COMPONENT_LIFECYCLES, "unified_orders")}
        for collection, change in (
            (PLANS, {"state": "cancelled"}), (PLANS, {"source_version.value": 4}),
            (PLANS, {"reconciliation_required": True}),
            (fulfillment.COMPONENT_LIFECYCLES, {"accepted": False}),
            (fulfillment.COMPONENT_LIFECYCLES, {"state": "blocked"}),
            (fulfillment.COMPONENT_LIFECYCLES, {"cancelled": True}),
            (fulfillment.COMPONENT_LIFECYCLES, {"retry_required": True}),
            ("unified_orders", {"g47_salla_snapshot.component_pending": True}),
            ("unified_orders", {"g47_salla_snapshot.requires_authoritative_refresh": True}),
            ("unified_orders", {"g47_salla_snapshot.cancelled": True}),
        ):
            with self.subTest(change=change):
                for name, original in originals.items():
                    await self.db[name].replace_one({}, deepcopy(original))
                await self.db[collection].update_one({}, {"$set": change})
                await self.reseal()
                await self.assert_no_write_read(expected_none=True)
        self.provider.assert_not_awaited()

    async def test_existing_legacy_component_evidence_is_read_without_audit_writer(self):
        await self.db[PLANS].delete_many({})
        await self.db.settings.update_one({}, {"$unset": {"g47_inventory.component_lifecycle_starts_at": ""}})
        await self.db[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$set": {"state": fulfillment.LEGACY_COMPONENT_COHORT}})
        self.assertTrue((await self.assert_no_write_read())["ready"])
        await self.db[fulfillment.COMPONENT_LIFECYCLES].delete_many({})
        await self.assert_no_write_read(expected_none=True)
        self.assertEqual(self.provider.await_count, 1)

    async def test_exact_provider_identity_and_completed_status_required(self):
        original = deepcopy(self.order)
        before = await self.snapshot()
        for change in ({"id": "other"}, {"reference_id": "other"}, {"reference_id": None},
                       {"status": {"slug": "in_progress"}}, {"status": {"slug": "delivered"}},
                       {"status": None}, {"status_slug": "cancelled"}):
            with self.subTest(change=change):
                self.order = {**original, **change}
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()
                self.assertEqual(await self.snapshot(), before)

    async def test_provider_material_carrier_shipment_changes_reject_old_label(self):
        original = deepcopy(self.order)
        before = await self.snapshot()
        for change in ({"items": [{"quantity": 3}]}, {"shipping_address": {"city": "changed"}},
                       {"payment_method": "changed"}, {"shipping": {"company": "SMSA"}},
                       {"shipping": {"company": "iMile", "recipient": {"city": "changed"}}},
                       {"shipments": [{"id": "other", "courier_name": "iMile", "status": "created"}]}):
            with self.subTest(change=change):
                self.order = {**original, **change}
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()
                self.assertEqual(await self.snapshot(), before)

    async def test_local_changes_during_provider_get_reject_without_own_writes(self):
        collections = ("unified_orders", "order_review_workflows", PLANS, fulfillment.COMPONENT_LIFECYCLES, "settings")
        originals = {name: await self.db[name].find_one({}) for name in collections}
        for collection, change in (
            ("order_review_workflows", {"shipping_status_epoch": 3}),
            ("order_review_workflows", {"store_delivery_assignment_id": "changed"}),
            ("order_review_workflows", {"assembly_delivery.claim": "worker"}),
            ("order_review_workflows", {"assembly_delivery.attempts": 4}),
            ("order_review_workflows", {"items": [{"quantity": 3}]}),
            ("unified_orders", {f"{CURRENT_SHIPPING}.shipment_id": "changed"}),
            ("unified_orders", {"order_status": "delivered"}),
            ("unified_orders", {"g47_salla_snapshot.component_pending": True}),
            (PLANS, {"lines": [{"quantity": 3}]}),
            (fulfillment.COMPONENT_LIFECYCLES, {"retry_required": True}),
            ("settings", {"g47_inventory.component_lifecycle_starts_at": "2027-01-01T00:00:00Z"}),
        ):
            with self.subTest(change=change):
                for name, value in originals.items():
                    await self.db[name].replace_one({}, deepcopy(value))
                expected = None
                async def racing(*args, **kwargs):
                    nonlocal expected
                    await self.db[collection].update_one({}, {"$set": change})
                    expected = await self.snapshot()
                    return await self.provider_read(*args, **kwargs)
                self.provider.side_effect = racing
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()
                self.assertEqual(await self.snapshot(), expected)

    async def test_cancellation_receipt_race_blocks_old_label(self):
        async def racing(*args, **kwargs):
            await self.db.salla_shipping_label_inbox.insert_one({"user_id": "owner", "order_number": "synthetic-1",
                "state": "pending", "observation": {"shipment_id": "shipment-current", "status": "cancelled"}})
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = racing
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
        self.provider.side_effect = self.provider_read
        result = await self.assert_no_write_read()
        self.assertFalse(result["ready"])

    async def alias_conflict(self):
        await self.db.unified_orders.update_one({}, {"$set": {"order_status": "in_progress", "order_status_slug": "in_progress"}})

    async def test_versioned_alias_repair_changes_only_status_fields_and_owner_revision(self):
        await self.alias_conflict()
        before = await self.snapshot()
        result = await self.read()
        self.assertTrue(result["ready"])
        self.provider.assert_awaited_once()
        expected = deepcopy(before)
        expected["unified_orders"][0].update(order_status="completed", order_status_slug="completed")
        expected["unified_orders"][0]["raw_by_source"]["salla_direct"].update(
            status=deepcopy(self.order["status"]), status_slug="completed", updated_at="2026-10-17T12:00:00.000000+00:00")
        expected["mz2_atomic_owners"][0]["revision"] += 1
        self.assertEqual(await self.snapshot(), expected)
        self.assertEqual(expected["order_review_workflows"][0]["assembly_delivery"]["state"], "requires_attention")
        # It remains GET recovery, not delivery enrollment or a second POST.
        self.assertTrue((await self.assert_no_write_read())["ready"])
        self.assertEqual(self.provider.await_count, 2)

    async def test_repair_requires_known_fresh_at_least_latest_source_version(self):
        await self.alias_conflict()
        before = await self.snapshot()
        for value in (None, "invalid", "2026-10-17T11:59:59Z", "2026-10-17T12:00:00"):
            with self.subTest(value=value):
                self.order["updated_at"] = value
                await self.assert_no_write_read(expected_none=True)
                self.assertEqual(await self.snapshot(), before)
        self.order["updated_at"] = "2026-10-17T12:00:00Z"
        await self.db.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.source_updated_at": "2026-10-17T12:01:00Z"}})
        await self.assert_no_write_read(expected_none=True)

    async def test_repair_refuses_missing_or_invalid_stored_versions(self):
        await self.alias_conflict()
        await self.db.unified_orders.update_one({}, {"$unset": {
            "g47_salla_snapshot.source_updated_at": "", "raw_by_source.salla_direct.updated_at": ""}})
        await self.db[fulfillment.COMPONENT_LIFECYCLES].update_one({}, {"$unset": {"source_updated_at": ""}})
        await self.assert_no_write_read(expected_none=True)
        await self.db.unified_orders.update_one({}, {"$set": {"g47_salla_snapshot.source_updated_at": "invalid"}})
        await self.assert_no_write_read(expected_none=True)

    async def test_terminal_ambiguous_missing_status_and_pending_never_repaired(self):
        original = await self.db.unified_orders.find_one({})
        for change in ({"order_status": "cancelled"}, {"order_status": "delivered"},
                       {"order_status": "unknown"}, {"raw_by_source.salla_direct.status": None},
                       {"order_status": "in_progress", "g47_salla_snapshot.component_pending": True},
                       {"order_status": "in_progress", "g47_salla_snapshot.requires_authoritative_refresh": True}):
            with self.subTest(change=change):
                await self.db.unified_orders.replace_one({}, deepcopy(original))
                await self.db.unified_orders.update_one({}, {"$set": change})
                await self.reseal()
                await self.assert_no_write_read(expected_none=True)
        self.provider.assert_not_awaited()

    async def test_repair_rechecks_outbox_and_source_under_owner_fence(self):
        await self.alias_conflict()
        owner_boundary = readback.operational_owner
        expected = None
        async def race(db, owner, callback):
            nonlocal expected
            await self.db.order_review_workflows.update_one({}, {"$set": {"assembly_delivery.claim": "worker"}})
            expected = await self.snapshot()
            return await owner_boundary(db, owner, callback)
        with patch.object(readback, "operational_owner", race):
            with self.assertRaises(shipping.ShippingLabelError):
                await self.read()
        # The rejected owner transaction rolls back its own revision increment.
        self.assertEqual(await self.snapshot(), expected)

    async def test_repair_rollback_if_repaired_snapshot_cannot_be_proven(self):
        await self.alias_conflict()
        before = await self.snapshot()
        capture = readback._capture
        async def reject_after_write(db, owner, number):
            result = await capture(db, owner, number)
            if result and not result["repair_needed"]:
                return None
            return result
        with patch.object(readback, "_capture", reject_after_write):
            with self.assertRaises(shipping.ShippingLabelError):
                await self.read()
        self.assertEqual(await self.snapshot(), before)

    async def test_late_revocation_or_tenant_change_denies_repair_and_rolls_back(self):
        await self.alias_conflict()
        before = await self.snapshot()
        for reason in ("permission_revoked", "tenant_changed"):
            with self.subTest(reason=reason):
                async def denied(scoped):
                    current = await scoped.unified_orders.find_one({"user_id": "owner", "order_number": "synthetic-1"})
                    self.assertEqual(current["order_status"], "in_progress")
                    raise HTTPException(403, detail={"code": reason})
                with self.assertRaises(HTTPException) as error:
                    await readback.read_blocked_completion(self.db, "owner", "synthetic-1", authorize_repair=denied)
                self.assertEqual(error.exception.detail["code"], reason)
                self.assertEqual(await self.snapshot(), before)

    async def test_healthy_read_does_not_invoke_write_authorizer(self):
        callback = AsyncMock(side_effect=AssertionError("healthy read must not enter repair"))
        before = await self.snapshot()
        self.assertTrue((await readback.read_blocked_completion(
            self.db, "owner", "synthetic-1", authorize_repair=callback))["ready"])
        callback.assert_not_awaited()
        self.assertEqual(await self.snapshot(), before)

    async def test_provider_errors_timeout_and_repeated_get_never_dispatch_post(self):
        before = await self.snapshot()
        self.provider.side_effect = httpx.ConnectError("synthetic offline")
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "completion_readback_unavailable")
        async def slow(*args, **kwargs):
            await asyncio.sleep(0.2)
        self.provider.side_effect = slow
        with patch.object(readback, "READBACK_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await self.read()
        self.assertEqual(error.exception.code, "completion_readback_timeout")
        self.provider.side_effect = self.provider_read
        self.assertTrue((await self.read())["ready"])
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(self.provider.await_count, 3)

    async def test_no_cached_completion_authority_between_reads(self):
        self.assertTrue((await self.read())["ready"])
        self.order["status"] = {"slug": "delivered"}
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "order_status_not_completed")
        self.assertEqual(self.provider.await_count, 2)

    async def test_accounting_wrapper_and_existing_transaction_scope(self):
        wrapped = AccountingDatabase(self.db)
        self.assertTrue((await readback.read_blocked_completion(wrapped, "owner", "synthetic-1"))["ready"])
        async with await self.mongo.start_session() as session:
            scoped = SessionDatabase(self.db, session)
            scoped._owner = "owner"
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await readback.read_blocked_completion(scoped, "owner", "synthetic-1")
        self.assertEqual(error.exception.code, "completion_readback_scope_conflict")


if __name__ == "__main__":
    unittest.main()
