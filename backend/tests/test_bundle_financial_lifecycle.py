"""Financial-only bundle intake uses real, isolated lifecycle transactions."""
import asyncio
from copy import deepcopy
from datetime import datetime
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import bundle_catalog_v2 as catalog
import fulfillment_v2_routes as lifecycle
import order_cost_authority as authority
import reviewed_products_catalog as reviewed
import stock_component_consumption_service as stock
import test_bundle_fulfillment_v2 as fixtures


BLOCK = "bundle_component_policy_unresolved"
SERVICE = "fixture-external-embroidery"
BINDING = "fixture-name-binding"


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated loopback replica set")
class BundleFinancialLifecycleTests(unittest.IsolatedAsyncioTestCase):
    # Reuse only setup/helpers, without collecting the existing test class twice.
    asyncSetUp = fixtures.BundleMongoTests.asyncSetUp
    asyncTearDown = fixtures.BundleMongoTests.asyncTearDown
    order_dto = fixtures.BundleMongoTests.order_dto

    async def fixture(self, *, legacy=False, payment="paid", policy_at="2026-09-30T00:00:00+00:00"):
        options = [{"id": key, "name": key, "required": False,
                    "values": [{"id": "yes", "name": "Yes"}, {"id": "no", "name": "No"}]}
                   for key in ("rose-choice", "name-choice")]
        await self.db[stock.PRODUCTS].update_one({"id": "root"}, {"$set": {"options": options}})
        await self.db[catalog.COST_PROFILES].insert_many([
            {"user_id": "owner", "salla_product_id": "root", "base_cost": 15,
             "bundle_reporting_cost_policy": {"revision": 1, "include_parent_base": True,
                 "evidence_ref": "synthetic-owner-confirmed-base15", "updated_at": policy_at},
             "composition_components": {"option_resource:" + BINDING: {
                 "cost_treatment": "independent", "assembly_origin": "supplier",
                 "evidence_ref": "synthetic-external-service10"}}},
            {"user_id": "owner", "salla_product_id": "d", "base_cost": 40},
        ])
        await self.db[catalog.RESOURCES].insert_one({"user_id": "owner", "id": SERVICE,
            "name": "Fixture external embroidery", "kind": "service", "status": "active",
            "unit": "job", "unit_cost": 10, "track_inventory": False, "requires_preparation": True})
        await self.db[catalog.BINDINGS].insert_one({"user_id": "owner", "id": BINDING,
            "salla_product_id": "root", "option_id": "name-choice", "value_id": "yes",
            "mode": "resource", "resource_id": SERVICE, "quantity": 1})
        await catalog.save_bundle(self.db, "owner", "root", {
            "product_type": "composite", "schema_version": 2, "expected_revision": 0,
            "items": [{"kind": "product", "product_id": "d", "quantity": 1,
                       "condition": {"option_id": "rose-choice", "value_id": "yes"}}]}, "owner")
        order = self.order_dto(payment)
        order.items[0].quantity = 4
        order.items[0].options_raw = [
            {"option_id": key, "option_name": key, "value_id": "yes", "value_name": "Yes"}
            for key in ("rose-choice", "name-choice")]
        if legacy:
            order.created_at = datetime.fromisoformat("2026-08-01T00:00:00+00:00")
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order",
            "order_date": order.created_at.isoformat(), "order_status_slug": order.status,
            "raw_by_source": {"salla_direct": {"date": order.created_at.isoformat(),
                "id": "order", "reference_id": "order", "status": order.status}}})
        await self.db[lifecycle.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "order",
            "stage": "reviewed", "items": [{"order_item_id": "line", "product_id": "root",
                "quantity": 4, "supplier_export": True, "supplier_id": "supplier-a"}]})
        return order

    async def reconcile(self, order, *, minute=1, strict=False, source_updated_at=None):
        return await lifecycle.reconcile_component_order_lifecycle(self.db, user_id="owner", order=order,
            source_updated_at=source_updated_at or f"2026-10-01T00:{minute:02d}:00+00:00",
            decision={}, strict=strict)

    async def intent(self):
        return await self.db[lifecycle.COMPONENT_LIFECYCLES].find_one({"order_number": "order"})

    async def historical_cost(self, order):
        line = lifecycle._component_order_lines(order, {})[0]
        context = await authority.load_order_cost_authority(self.db, "owner", [
            {"order_number": "order", "products": [line]}])
        return authority.resolve_order_line_cost(line, order_number="order", authority=context,
            catalog_cost={"line_total": 99999, "calculation_cost_available": True})

    async def unrelated_records(self):
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                for name in sorted(await self.db.list_collection_names())
                if name not in {lifecycle.COMPONENT_LIFECYCLES, "mz2_atomic_owners"}}

    async def supplier_pairs(self, order):
        workflow = await self.db[lifecycle.WORKFLOWS].find_one({"order_number": "order"})
        # Only the DTO adapter is replaced; filtering and frozen-parent routing
        # use their real queries, including the newly persisted lifecycle.
        with patch.object(reviewed, "get_orders", AsyncMock(return_value={"order": order})):
            return await reviewed._load_catalog_pairs(self.db, user_id="owner", workflows=[workflow])

    async def test_real_mongo_salla_label_only_values_freeze_cost_without_supplier_change(self):
        from bundle_financial_selection_evidence import verify_selection_recovery

        order = await self.fixture()
        order.items[0].options_raw = [
            {"option_id": "rose-choice", "option_name": "rose-choice",
             "values": [{"name": "Yes"}]},
            {"option_id": "name-choice", "option_name": "name-choice",
             "values": [{"name": "Yes"}]},
        ]
        baseline = await self.unrelated_records()
        supplier_before = await self.supplier_pairs(order)
        result = await self.reconcile(order)
        self.assertEqual(result["error_code"], BLOCK)
        self.assertEqual(result["state"], "blocked")
        current = await self.intent()
        self.assertEqual(current["bundle_intake_snapshots"], {})
        self.assertEqual(current["bundle_financial_blocked"], {"line": BLOCK})
        record = current["reporting_cost_snapshots"]["line"]
        self.assertEqual(record["cost"]["line_total"], 260)
        self.assertTrue(verify_selection_recovery(record["bundle_snapshot"], record))
        self.assertEqual(record["bundle_snapshot"]["sale_quantity"], 4)
        self.assertEqual((await self.historical_cost(order))["line_total"], 260)
        self.assertEqual(await self.supplier_pairs(order), supplier_before)
        self.assertEqual(await self.unrelated_records(), baseline)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)

        frozen = deepcopy(current["reporting_cost_snapshots"])
        await self.db[catalog.COST_PROFILES].update_many({}, {"$set": {"base_cost": 999}})
        await self.db[catalog.RESOURCES].update_one({"id": SERVICE},
            {"$set": {"unit_cost": 777}})
        replay = await self.reconcile(order, minute=2)
        self.assertEqual(replay["error_code"], BLOCK)
        self.assertEqual((await self.intent())["reporting_cost_snapshots"], frozen)
        self.assertEqual((await self.historical_cost(order))["line_total"], 260)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)

    async def test_strict_block_keeps_260_financial_proof_and_unchanged_supplier_export(self):
        order = await self.fixture()
        before = await self.unrelated_records()
        supplier_before = await self.supplier_pairs(order)
        self.assertEqual(len(supplier_before[0]), 1)
        with self.assertRaises(HTTPException) as caught:
            await self.reconcile(order, strict=True)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], BLOCK)
        saved = await self.intent()
        self.assertIsNotNone(saved)  # Registration commits before acceptance rolls back.
        self.assertEqual(saved["state"], "blocked")
        self.assertFalse(saved["accepted"])
        self.assertTrue(saved["retry_required"])
        self.assertEqual(saved["bundle_financial_blocked"], {"line": BLOCK})
        self.assertNotIn("line", saved["bundle_intake_snapshots"])
        self.assertEqual(saved["reporting_cost_snapshots"]["line"]["cost"]["line_total"], 260)
        cost = await self.historical_cost(order)
        self.assertEqual(cost["line_total"], 260)
        self.assertEqual(cost["cost_stage"], "expected")
        self.assertTrue(cost["calculation_cost_available"])
        self.assertEqual(await self.supplier_pairs(order), supplier_before)
        self.assertTrue(supplier_before[0][0][1]["items"][0]["supplier_export"])
        self.assertEqual(await self.unrelated_records(), before)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)

    async def test_late_first_capture_cannot_certify_cost_from_policy_newer_than_sale(self):
        order = await self.fixture(policy_at="2026-11-02T00:00:00+00:00")
        before = await self.unrelated_records()
        result = await self.reconcile(order, source_updated_at="2026-11-02T01:00:00+00:00")
        self.assertEqual(result["state"], "blocked")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["error_code"], BLOCK)
        saved = await self.intent()
        self.assertNotIn("line", saved["bundle_intake_snapshots"])
        self.assertEqual(saved["bundle_financial_blocked"], {"line": BLOCK})
        record = saved["reporting_cost_snapshots"]["line"]
        self.assertIs(record["bundle_snapshot"].get("historical_cost_verified"), False)
        self.assertFalse(record["cost"]["calculation_cost_available"])
        self.assertFalse(record["cost"]["historical_cost_verified"])
        report = await self.historical_cost(order)
        self.assertFalse(report["calculation_cost_available"])
        self.assertFalse(report["historical_cost_verified"])
        self.assertIn("historical_bundle_cost_unproven", report["missing_cost_reasons"])
        self.assertEqual(await self.unrelated_records(), before)

        # A later catalog edit must not turn this first observation into proof
        # of the old sale, even when today's policy date is moved backwards.
        frozen = deepcopy(saved["reporting_cost_snapshots"])
        await self.db[catalog.COST_PROFILES].update_many({}, {"$set": {"base_cost": 999}})
        await self.db[catalog.COST_PROFILES].update_one({"salla_product_id": "root"},
            {"$set": {"bundle_reporting_cost_policy.updated_at": "2026-09-30T00:00:00+00:00"}})
        replay = await self.reconcile(order, source_updated_at="2026-11-02T02:00:00+00:00")
        self.assertEqual(replay["error_code"], BLOCK)
        self.assertEqual((await self.intent())["reporting_cost_snapshots"], frozen)
        self.assertFalse((await self.historical_cost(order))["calculation_cost_available"])
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)

    async def test_missing_sale_creation_date_never_certifies_current_policy_cost(self):
        order = await self.fixture()
        # DTO/order_date fallbacks cannot replace original provider evidence.
        await self.db.unified_orders.update_one({"order_number": "order"},
            {"$unset": {"raw_by_source.salla_direct.date": ""}})
        before = await self.unrelated_records()
        result = await self.reconcile(order, source_updated_at="2026-11-02T01:00:00+00:00")
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["error_code"], "component_source_created_at_required")
        self.assertFalse(result["accepted"])
        saved = await self.intent()
        self.assertNotIn("line", saved["bundle_intake_snapshots"])
        self.assertEqual(saved["reporting_cost_snapshots"], {})
        report = await self.historical_cost(order)
        self.assertFalse(report["calculation_cost_available"])
        self.assertFalse(report["historical_cost_verified"])
        self.assertEqual(await self.unrelated_records(), before)

    async def test_forged_dto_creation_cannot_override_canonical_sale_before_policy(self):
        order = await self.fixture(policy_at="2026-11-02T00:00:00+00:00")
        order.created_at = datetime.fromisoformat("2026-11-03T00:00:00+00:00")
        before = await self.unrelated_records()
        result = await self.reconcile(order, source_updated_at="2026-11-03T01:00:00+00:00")
        self.assertEqual(result["error_code"], BLOCK)
        self.assertFalse(result["accepted"])
        record = (await self.intent())["reporting_cost_snapshots"]["line"]
        self.assertIs(record["bundle_snapshot"].get("historical_cost_verified"), False)
        self.assertFalse(record["cost"]["calculation_cost_available"])
        report = await self.historical_cost(order)
        self.assertFalse(report["calculation_cost_available"])
        self.assertFalse(report["historical_cost_verified"])
        self.assertEqual(await self.unrelated_records(), before)

    async def test_webhook_and_later_source_replay_preserve_original_260_after_catalog_change(self):
        order = await self.fixture()
        self.assertEqual((await self.reconcile(order))["error_code"], BLOCK)
        original = await self.intent()
        frozen = deepcopy(original["reporting_cost_snapshots"])
        await self.db[catalog.COST_PROFILES].update_many({}, {
            "$set": {"base_cost": 999}, "$unset": {"bundle_reporting_cost_policy": ""}})
        await self.db[catalog.RESOURCES].update_one({"id": SERVICE}, {"$set": {"unit_cost": 777}})
        for minute, generation in ((1, original["generation"]), (2, original["generation"] + 1)):
            result = await self.reconcile(order, minute=minute)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["error_code"], BLOCK)
            saved = await self.intent()
            self.assertEqual(saved["generation"], generation)
            self.assertEqual(saved["reporting_cost_snapshots"], frozen)
            self.assertEqual(saved["bundle_financial_blocked"], {"line": BLOCK})
            self.assertNotIn("line", saved["bundle_intake_snapshots"])
            self.assertEqual((await self.historical_cost(order))["line_total"], 260)
        self.assertEqual(await self.db[lifecycle.COMPONENT_LIFECYCLES].count_documents({}), 1)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)
        self.assertTrue((await self.supplier_pairs(order))[0][0][1]["items"][0]["supplier_export"])

    async def test_pre_cutover_order_keeps_strict_legacy_intake_rejection_atomic(self):
        order = await self.fixture(legacy=True)
        before = await self.unrelated_records()
        with self.assertRaises(HTTPException) as caught:
            await self.reconcile(order)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["code"], BLOCK)
        # Pre-existing behavior: a rejected supplier intake never registers
        # a new lifecycle or changes an old operational cohort.
        self.assertIsNone(await self.intent())
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)
        self.assertEqual(await self.unrelated_records(), before)
        self.assertTrue(await lifecycle.legacy_component_cohort(
            self.db, user_id="owner", order_number="order"))

    async def test_existing_legacy_acceptance_survives_repeated_blocked_bundle_capture(self):
        order = await self.fixture(legacy=True)
        self.assertTrue(await lifecycle.allow_legacy_component_execution(
            self.db, user_id="owner", order_number="order"))
        original = await self.intent()
        before = await self.unrelated_records()
        self.assertEqual(original["state"], lifecycle.LEGACY_COMPONENT_COHORT)
        for minute in (1, 2):
            with self.assertRaises(HTTPException) as caught:
                await self.reconcile(order, minute=minute)
            self.assertEqual(caught.exception.detail["code"], BLOCK)
            self.assertEqual(await self.intent(), original)
            self.assertEqual(await self.unrelated_records(), before)
        # Legacy ready-piece, supplier export and piece-consumption checks
        # remain eligible; the financial-only opt-in did not revoke them.
        self.assertTrue(await lifecycle.legacy_component_cohort(
            self.db, user_id="owner", order_number="order"))
        self.assertTrue(await lifecycle.allow_legacy_component_execution(
            self.db, user_id="owner", order_number="order"))
        self.assertTrue((await self.supplier_pairs(order))[0][0][1]["items"][0]["supplier_export"])

    async def test_concurrent_replay_creates_one_lifecycle_and_keeps_first_financial_proof(self):
        order = await self.fixture()
        results = await asyncio.gather(*(self.reconcile(order) for _ in range(4)))
        self.assertTrue(all(row["state"] == "blocked" and row["error_code"] == BLOCK for row in results))
        original = await self.intent()
        self.assertEqual(original["generation"], 1)
        self.assertEqual(set(original["reporting_cost_snapshots"]), {"line"})
        self.assertEqual(original["reporting_cost_snapshots"]["line"]["cost"]["line_total"], 260)
        await self.db[catalog.COST_PROFILES].update_many({}, {"$set": {"base_cost": 999}})
        await self.db[catalog.RESOURCES].update_one({"id": SERVICE}, {"$set": {"unit_cost": 777}})
        replay = await asyncio.gather(*(self.reconcile(order) for _ in range(4)))
        self.assertTrue(all(row["state"] == "blocked" and not row["accepted"] for row in replay))
        saved = await self.intent()
        self.assertEqual(saved["generation"], 1)
        self.assertEqual(saved["reporting_cost_snapshots"], original["reporting_cost_snapshots"])
        self.assertEqual(saved["bundle_intake_snapshots"], {})
        self.assertEqual(saved["bundle_financial_blocked"], {"line": BLOCK})
        self.assertEqual((await self.historical_cost(order))["line_total"], 260)
        self.assertEqual(await self.db[lifecycle.COMPONENT_LIFECYCLES].count_documents({}), 1)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)

    async def test_pending_payment_still_records_financial_cost_without_accepting_components(self):
        order = await self.fixture(payment="pending")
        result = await self.reconcile(order)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["error_code"], BLOCK)
        self.assertFalse(result["accepted"])
        saved = await self.intent()
        self.assertFalse(saved["eligible"])
        self.assertEqual(saved["reporting_cost_snapshots"]["line"]["cost"]["line_total"], 260)
        self.assertEqual(await self.db[stock.PLANS].count_documents({}), 0)

    async def assert_cancellation_allowed(self, *, legacy):
        order = await self.fixture(legacy=legacy)
        if legacy:
            with self.assertRaises(HTTPException) as caught:
                await self.reconcile(order)
            self.assertEqual(caught.exception.detail["code"], BLOCK)
            self.assertIsNone(await self.intent())
            financial = {}
        else:
            await self.reconcile(order)
            financial = deepcopy((await self.intent())["reporting_cost_snapshots"])
        order.status = "cancelled"
        cancelled = await self.reconcile(order, minute=2)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["accepted"])
        # A persisted block must not stop a cancellation/release replay either.
        await self.db[lifecycle.COMPONENT_LIFECYCLES].update_one({"order_number": "order"},
            {"$set": {"bundle_financial_blocked": {"line": BLOCK}}})
        replay = await self.reconcile(order, minute=2)
        self.assertEqual(replay["state"], "cancelled")
        self.assertFalse(replay["accepted"])
        saved = await self.intent()
        self.assertFalse(saved["retry_required"])
        self.assertNotIn("error_code", saved)
        self.assertEqual(saved["reporting_cost_snapshots"], financial)
        self.assertEqual(await self.db[stock.UNITS].count_documents({}), 0)
        plans = await self.db[stock.PLANS].find({}).to_list(10)
        self.assertEqual(len(plans), 0 if legacy else 1)
        self.assertTrue(all(row["state"] == "cancelled" for row in plans))

    async def test_cancellation_and_release_remain_allowed_with_saved_financial_block(self):
        await self.assert_cancellation_allowed(legacy=False)

    async def test_legacy_cancellation_remains_allowed_without_component_plan(self):
        await self.assert_cancellation_allowed(legacy=True)
