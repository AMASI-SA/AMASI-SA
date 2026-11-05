"""Source refresh regression, using real Mongo owner transactions and catalogue."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_g47_component_lifecycle_integration as fixture
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from order_review_business_snapshot import build_snapshot
from reviewed_products_catalog import load_reviewed_product_context
from fulfillment_v2_routes import revalidate_unchanged_component_snapshot
from component_zero_price_recovery import restore_zero_price_currencies


class ZeroPriceProofTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"items": [{"id": "line", "options": [{"id": "option",
            "value": {"id": "choice", "name": "selected", "price": {"amount": 0, "currency": "KWD"}}}]}]}
        self.op = {"_id": "proof", "user_id": "owner", "order_number": "order", "revision": 1,
                   "workflow_fingerprint": "review"}
        self.op["business_snapshot"] = build_snapshot({"raw_by_source": {"salla_direct": self.raw}}, {}, {},
                                                     identity=deepcopy(self.op), schema_version=2)
        self.current = deepcopy(self.raw)
        self.current["items"][0]["options"][0]["value"]["price"]["currency"] = "SAR"

    def test_proof_restores_only_zero_currency_without_mutating_input(self):
        before = deepcopy(self.current)
        self.assertEqual(restore_zero_price_currencies(self.current, self.op), self.raw)
        self.assertEqual(self.current, before)

    def test_missing_corrupt_or_rebound_evidence_is_rejected(self):
        for key, value in (("business_snapshot", {}), ("_id", "other"), ("user_id", "other")):
            with self.subTest(key=key):
                op = deepcopy(self.op); op[key] = value
                self.assertIsNone(restore_zero_price_currencies(self.current, op))

    def test_non_numeric_zero_and_invalid_currency_are_not_proof(self):
        for price in ({"amount": False, "currency": "SAR"}, {"amount": "0", "currency": "SAR"},
                      {"amount": 0, "currency": ""}, {"amount": 0, "currency": "sar"}):
            with self.subTest(price=price):
                current = deepcopy(self.current)
                current["items"][0]["options"][0]["value"]["price"] = price
                self.assertIsNone(restore_zero_price_currencies(current, self.op))


class ZeroPriceCurrencyTests(fixture.ComponentRouteTests):
    async def seed_currency_order(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload()
        payload["currency"] = "KWD"
        payload["items"][0]["options"] = [{
            "id": 123, "product_option_id": 456, "name": "Size", "type": "radio",
            "value": {"id": 789, "name": "Large", "option_value": "Large",
                      "price": {"amount": 0, "currency": "KWD"}},
        }]
        self.assertTrue((await self.webhook(payload))["synced"])
        selector = {"user_id": "owner", "order_number": payload["reference_id"]}
        source = await self.db.unified_orders.find_one(selector)
        order = await get_order(MongoOrderRepository(self.db), **selector)
        operation = {"_id": "approved-currency-order", **selector, "state": "completed",
                     "completion_mode": "mezan_local_v1", "revision": 1,
                     "workflow_fingerprint": "synthetic-approval"}
        identity = {k: operation[k] for k in ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
        operation["business_snapshot"] = build_snapshot(source, order, {}, identity=identity, schema_version=2)
        await self.db.order_review_completion_operations.insert_one(operation)
        await self.db.order_review_workflows.insert_one({
            **selector, "stage": "in_progress", "completion_mode": "mezan_local_v1",
            "review_completion_operation_id": operation["_id"], "reviewed_at": fixture.WHEN,
            "items": [{"order_item_id": order.items[0].order_item_id, "product_id": "p",
                       "quantity": 2, "review_status": "reviewed"}], "operational_items": [],
        })
        await self.db.mezan_preparation_unit_allocations_v2.insert_one({
            **selector, "order_item_id": order.items[0].order_item_id,
            "unit_index": 1, "status": "committed",
        })
        payload["updated_at"] = fixture.LATER
        payload["items"][0]["options"][0]["value"]["price"]["currency"] = "SAR"
        return selector, payload

    async def test_refresh_keeps_remaining_reviewed_unit_assignable(self):
        selector, payload = await self.seed_currency_order()
        before = await self.db.order_review_workflows.find_one(selector)
        with patch("stock_component_consumption_service.reserve_component_stock", AsyncMock(side_effect=AssertionError("no reservation"))):
            await self.refresh(payload, allow_auto_fulfillment=False,
                               allow_delivery_observation=False, allow_billing_eligibility=False)
        source = await self.db.unified_orders.find_one(selector)
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        self.assertEqual(source["raw_by_source"]["salla_direct"]["status"]["slug"], "under_review")
        self.assertEqual(await self.db.order_review_workflows.find_one(selector), before)
        context = await load_reviewed_product_context(self.db, **selector)
        self.assertEqual(context["catalog"]["summary"]["total_quantity"], 1)
        self.assertEqual(context["catalog"]["summary"]["reviewed_order_count"], 1)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(lifecycle["generation"], 1)
        self.assertEqual(lifecycle["zero_price_currency_revalidation"]["reason"], "zero_option_price_currency_only")
        # Repeated changes retain immutable approval lineage, including returning
        # to the original order currency.
        for currency in ("AED", "KWD"):
            payload["items"][0]["options"][0]["value"]["price"]["currency"] = currency
            await self.refresh(payload, allow_auto_fulfillment=False)
            self.assertFalse((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])

    async def test_business_change_stays_pending(self):
        selector, original = await self.seed_currency_order()
        source = await self.db.unified_orders.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        for change in ("quantity", "choice", "price", "metadata", "label"):
            with self.subTest(change=change):
                await self.db.unified_orders.replace_one(selector, deepcopy(source))
                await self.db.mezan_component_order_lifecycle_v1.replace_one(selector, deepcopy(lifecycle))
                payload = deepcopy(original)
                value = payload["items"][0]["options"][0]["value"]
                if change == "quantity":
                    payload["items"][0]["quantity"] = 3
                elif change == "choice":
                    value["id"] = 999
                elif change == "price":
                    value["price"]["amount"] = 1
                elif change == "metadata":
                    value["new_business_field"] = True
                else:
                    value["name"] = "Small"
                await self.refresh(payload, allow_auto_fulfillment=False)
                self.assertTrue((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])

    async def test_existing_pending_snapshot_dry_run_then_apply(self):
        selector, payload = await self.seed_currency_order()
        # Reproduce a snapshot persisted by the old code, then repair through
        # the same fenced revalidation used by the operational audit command.
        with patch("component_zero_price_recovery.prove_zero_price_currency_change", AsyncMock(return_value=None)):
            await self.refresh(payload, allow_auto_fulfillment=False)
        source = await self.db.unified_orders.find_one(selector)
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        args = {**selector, "source_revision": source["g47_salla_snapshot"]["revision"]}
        preview = await revalidate_unchanged_component_snapshot(self.db, **args, dry_run=True)
        self.assertTrue(preview["eligible_for_revalidation"])
        self.assertEqual(preview["reason"], "zero_option_price_currency_only")
        self.assertEqual(await self.db.unified_orders.find_one(selector), source)
        path = Path(__file__).resolve().parents[2] / "scripts" / "audit_pending_reviewed_products.py"
        spec = importlib.util.spec_from_file_location("pending_reviewed_audit", path)
        audit_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit_module)
        report = await audit_module.audit(self.db, "owner")
        self.assertEqual(report["proven_zero_price_currency_orders"], 1)
        self.assertEqual(report["proven_remaining_supplier_units"], 1)
        self.assertEqual(await self.db.unified_orders.find_one(selector), source)
        await self.db.order_review_completion_operations.update_one(selector, {"$set": {"superseded_by": "new"}})
        self.assertFalse((await revalidate_unchanged_component_snapshot(self.db, **args))["accepted"])
        await self.db.order_review_completion_operations.update_one(selector, {"$unset": {"superseded_by": ""}})
        self.assertTrue((await revalidate_unchanged_component_snapshot(self.db, **args))["accepted"])
