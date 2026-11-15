"""Source refresh/Ready regressions using a disposable real Mongo replica set."""
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_g47_component_lifecycle_integration as fixture
from order_engine.repository import MongoOrderRepository
from order_engine.service import get_order
from order_review_business_snapshot import build_snapshot
from fulfillment_v2_routes import revalidate_unchanged_component_snapshot
import preparation_piece_operations as pieces
from stock_component_consumption_service import PLANS, UNITS, LOCATIONS


class OptionPriceCurrencyTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the real-ASGI/owner-transaction fixture without collecting its
    # unrelated inherited tests a second time.
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    refresh = fixture.ComponentRouteTests.refresh

    async def seed_currency_order(self):
        await self.db.settings.update_one({"user_id": "owner"}, {
            "$unset": {"g47_inventory": ""}, "$set": {
                "operational_flow.preparation_employee_receipt_mode": "optional",
                "operational_flow.supplier_assignment_mode": "required",
            },
        })
        payload = self.source_payload(status="in_progress")
        payload["currency"] = "KWD"
        payload["exchange_rate"] = {
            "base_currency": "SAR", "exchange_currency": "KWD", "rate": "12.07292044",
        }
        payload["amounts"] = {
            "total": {"amount": 37.5, "currency": "KWD"},
            "sub_total": {"amount": 36, "currency": "KWD"},
            "shipping_cost": {"amount": 1.5, "currency": "KWD"},
            "tax": {"percent": "0.00", "amount": {"amount": 0, "currency": "KWD"}},
            "discounts": [],
        }
        payload["items"] = []
        prices = ([0.828, 1.242, 0, 0, 0], [0.828, 0, 0], [0, 0])
        for index, amounts in enumerate(prices):
            payload["items"].append({
                "id": f"line-{index + 1}", "product_id": "p", "name": "Synthetic product",
                "sku": "SYN-P", "quantity": 1,
                "amounts": {"total": {"amount": [16, 12, 8][index], "currency": "KWD"}},
                "options": [{
                    "id": f"option-{index}-{position}", "name": f"Choice {position}",
                    "type": "radio", "value": {
                        "id": f"choice-{index}-{position}", "name": "Selected",
                        "price": {"amount": amount, "currency": "KWD"},
                    },
                } for position, amount in enumerate(amounts)],
            })
        self.assertTrue((await self.webhook(payload))["synced"])
        selector = {"user_id": "owner", "order_number": payload["reference_id"]}
        source = await self.db.unified_orders.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertIs(lifecycle.get("accepted"), True, lifecycle)
        self.assertEqual(lifecycle["state"], "legacy_operational_pre_g47")
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        self.assertEqual(await self.db[PLANS].count_documents({}), 0)
        order = await get_order(MongoOrderRepository(self.db), **selector)
        operation = {
            "_id": "approved-option-currency-order", **selector, "state": "completed",
            "completion_mode": "mezan_local_v1", "revision": 1,
            "workflow_fingerprint": "synthetic-approval",
        }
        identity = {k: operation[k] for k in (
            "_id", "user_id", "order_number", "revision", "workflow_fingerprint",
        )}
        operation["business_snapshot"] = build_snapshot(source, order, {}, identity=identity, schema_version=2)
        await self.db.order_review_completion_operations.insert_one(operation)
        await self.db.order_review_workflows.insert_one({
            **selector, "stage": "in_progress", "completion_mode": "mezan_local_v1",
            "review_completion_operation_id": operation["_id"], "reviewed_at": fixture.WHEN,
            "items": [{"order_item_id": item.order_item_id, "product_id": "p",
                       "quantity": 1, "review_status": "reviewed"} for item in order.items],
            "operational_items": [],
        })
        for index, item in enumerate(order.items):
            await self.db[pieces.PIECES].insert_one({
                **selector, "id": f"piece-{index}", "piece_id": f"piece-{index}",
                "order_item_id": item.order_item_id, "product_id": "p", "unit_index": 1,
                "status": pieces.PIECE_STATUS_RECEIVED if index < 2 else pieces.PIECE_STATUS_IN_PROGRESS,
                "execution_status": "received_from_supplier" if index < 2 else "in_progress",
                "supplier_dispatch_status": "received" if index < 2 else "sent",
                "received_at": fixture.WHEN if index < 2 else None,
                "responsible_employee_id": "synthetic-preparer", "assignment_status": "assigned",
                "services": [],
            })
        self.context["permissions"].add("fulfillment.ready.read")
        return selector, payload, operation

    def represented_in_sar(self, original, *, only_zero=False):
        result = deepcopy(original)
        result["updated_at"] = fixture.LATER
        for item in result["items"]:
            for option in item["options"]:
                price = option["value"]["price"]
                if only_zero and price["amount"]:
                    continue
                price.update(amount={0: 0, 0.828: 10, 1.242: 15}[price["amount"]], currency="SAR")
        return result

    async def frozen_business_state(self, selector):
        return {
            "workflow": await self.db.order_review_workflows.find_one(selector),
            "approval": await self.db.order_review_completion_operations.find_one(selector),
            "pieces": await self.db[pieces.PIECES].find(selector).sort("piece_id", 1).to_list(10),
            "stock": {name: await self.db[name].find({}).to_list(100) for name in (PLANS, UNITS, LOCATIONS)},
        }

    async def refresh_without_side_effects(self, payload):
        with patch("stock_component_consumption_service.reserve_component_stock",
                   AsyncMock(side_effect=AssertionError("must not reserve stock"))):
            return await self.refresh(payload, allow_auto_fulfillment=False,
                allow_delivery_observation=False, allow_billing_eligibility=False)

    async def test_refresh_proves_three_paid_seven_zero_options_and_preserves_business_state(self):
        selector, native, _ = await self.seed_currency_order()
        before = await self.frozen_business_state(selector)
        source_before = await self.db.unified_orders.find_one(selector)
        changed = self.represented_in_sar(native)
        await self.refresh_without_side_effects(changed)
        source = await self.db.unified_orders.find_one(selector)
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        self.assertEqual(source["raw_by_source"]["salla_direct"]["items"], changed["items"])
        for key in ("currency", "original_currency", "original_total_amount", "total_amount",
                    "total_amount_sar", "exchange_rate_to_sar"):
            self.assertEqual(source.get(key), source_before.get(key), key)
        self.assertEqual(await self.frozen_business_state(selector), before)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(lifecycle["generation"], 1)
        audit = lifecycle["option_price_currency_revalidation"]
        self.assertEqual(audit["reason"], "approved_order_option_currency_representation")
        self.assertEqual(audit["proof_kind"], "native_minor_unit_equivalence")
        self.assertEqual(audit["exchange_rate_to_sar"], "12.07292044")

    async def test_existing_pending_dry_run_is_read_only_then_only_received_pieces_become_eligible(self):
        selector, native, _ = await self.seed_currency_order()
        with patch("fulfillment_v2_routes.revalidate_unchanged_component_snapshot",
                   AsyncMock(return_value={"accepted": False, "reason": "component_reconciliation_required"})):
            await self.refresh_without_side_effects(self.represented_in_sar(native))
        source = await self.db.unified_orders.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        frozen = await self.frozen_business_state(selector)
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        search = await self.client.get("/preparation-work-v1/assembly/search?q=intake-order")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertFalse(any(row["can_mark_ready"] for row in search.json()["pieces"]))
        args = {**selector, "source_revision": source["g47_salla_snapshot"]["revision"]}
        preview = await revalidate_unchanged_component_snapshot(self.db, **args, dry_run=True)
        self.assertTrue(preview.get("eligible_for_revalidation"), preview)
        self.assertEqual(preview["reason"], "approved_order_option_currency_representation")
        self.assertEqual(await self.db.unified_orders.find_one(selector), source)
        self.assertEqual(await self.db.mezan_component_order_lifecycle_v1.find_one(selector), lifecycle)
        self.assertEqual(await self.frozen_business_state(selector), frozen)
        self.assertTrue((await revalidate_unchanged_component_snapshot(self.db, **args))["accepted"])
        search = await self.client.get("/preparation-work-v1/assembly/search?q=intake-order")
        self.assertEqual(search.status_code, 200, search.text)
        rows = {row["piece_id"]: row for row in search.json()["pieces"]}
        self.assertTrue(rows["piece-0"]["can_mark_ready"])
        self.assertTrue(rows["piece-1"]["can_mark_ready"])
        self.assertFalse(rows["piece-2"]["can_mark_ready"])
        self.assertEqual(rows["piece-2"]["assembly_blocker_code"], "assembly_piece_supplier_receipt_required")
        self.assertEqual(await self.frozen_business_state(selector), frozen)

    async def test_repeated_currency_switches_keep_original_approval_and_do_not_repeat_work(self):
        selector, native, operation = await self.seed_currency_order()
        initial = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        frozen = await self.frozen_business_state(selector)
        for payload in (self.represented_in_sar(native), native, self.represented_in_sar(native)):
            payload = deepcopy(payload)
            payload["updated_at"] = fixture.LATER
            await self.refresh_without_side_effects(payload)
            current = await self.db.unified_orders.find_one(selector)
            self.assertFalse(current["g47_salla_snapshot"]["component_pending"])
            lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
            self.assertEqual(lifecycle["generation"], initial["generation"])
            audit = lifecycle["option_price_currency_revalidation"]
            self.assertEqual(audit["approved_fingerprint"], initial["source_fingerprint"])
            self.assertEqual(audit["approval_integrity_hash"], operation["business_snapshot"]["integrity_hash"])
            self.assertEqual(await self.frozen_business_state(selector), frozen)

    async def test_existing_zero_currency_lineage_can_advance_to_paid_currency_proof(self):
        selector, native, _ = await self.seed_currency_order()
        initial = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        await self.refresh_without_side_effects(self.represented_in_sar(native, only_zero=True))
        self.assertIn("zero_price_currency_revalidation", await self.db.mezan_component_order_lifecycle_v1.find_one(selector))
        await self.refresh_without_side_effects(self.represented_in_sar(native))
        self.assertFalse((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(lifecycle["option_price_currency_revalidation"]["approved_fingerprint"], initial["source_fingerprint"])

    async def test_financial_choice_quantity_fx_or_metadata_change_stays_pending(self):
        selector, native, _ = await self.seed_currency_order()
        source = await self.db.unified_orders.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        for change in ("total", "line_total", "tax", "fx", "choice", "quantity", "metadata", "price"):
            with self.subTest(change=change):
                await self.db.unified_orders.replace_one(selector, deepcopy(source))
                await self.db.mezan_component_order_lifecycle_v1.replace_one(selector, deepcopy(lifecycle))
                payload = self.represented_in_sar(native)
                if change == "total": payload["amounts"]["total"]["amount"] += 1
                elif change == "line_total": payload["items"][0]["amounts"]["total"]["amount"] += 1
                elif change == "tax": payload["amounts"]["tax"]["percent"] = "1.00"
                elif change == "fx": payload["exchange_rate"]["rate"] = "12.0729"
                elif change == "choice": payload["items"][0]["options"][0]["value"]["id"] = "different"
                elif change == "quantity": payload["items"][0]["quantity"] = 2
                elif change == "metadata": payload["new_business_fact"] = True
                else: payload["items"][0]["options"][0]["value"]["price"]["amount"] = 10.01
                await self.refresh_without_side_effects(payload)
                current = await self.db.unified_orders.find_one(selector)
                self.assertTrue(current["g47_salla_snapshot"]["component_pending"], change)
                current_lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
                self.assertEqual(current_lifecycle["source_fingerprint"], lifecycle["source_fingerprint"])

    async def test_stale_revision_and_superseded_approval_cannot_clear_pending(self):
        selector, native, operation = await self.seed_currency_order()
        with patch("fulfillment_v2_routes.revalidate_unchanged_component_snapshot", AsyncMock(return_value={"accepted": False})):
            await self.refresh_without_side_effects(self.represented_in_sar(native))
        source = await self.db.unified_orders.find_one(selector)
        revision = source["g47_salla_snapshot"]["revision"]
        stale = await revalidate_unchanged_component_snapshot(self.db, **selector, source_revision=revision - 1)
        self.assertFalse(stale["accepted"])
        await self.db.order_review_completion_operations.update_one({"_id": operation["_id"]}, {"$set": {"superseded_by": "new"}})
        refused = await revalidate_unchanged_component_snapshot(self.db, **selector, source_revision=revision)
        self.assertFalse(refused["accepted"])
        self.assertEqual(await self.db.unified_orders.find_one(selector), source)

    async def test_cancel_or_authoritative_refresh_requirement_cannot_clear_pending(self):
        selector, native, _ = await self.seed_currency_order()
        with patch("fulfillment_v2_routes.revalidate_unchanged_component_snapshot", AsyncMock(return_value={"accepted": False})):
            await self.refresh_without_side_effects(self.represented_in_sar(native))
        source = await self.db.unified_orders.find_one(selector)
        revision = source["g47_salla_snapshot"]["revision"]
        for flag in ("cancelled", "requires_authoritative_refresh"):
            with self.subTest(flag=flag):
                await self.db.unified_orders.replace_one(selector, deepcopy(source))
                await self.db.unified_orders.update_one(selector, {"$set": {f"g47_salla_snapshot.{flag}": True}})
                refused = await revalidate_unchanged_component_snapshot(self.db, **selector, source_revision=revision)
                self.assertFalse(refused["accepted"])
                self.assertTrue((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])
