"""Additional real Mongo guards and intake-path regression coverage."""
import unittest
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
import order_review_completion as completion
import order_review_routes as review
import product_fulfillment_rules as rules
import test_g47_component_lifecycle_integration as fixture
import test_review_completion_recovery as recovery


class ReviewCompletionExtraTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    order = fixture.ComponentRouteTests.order
    accept = fixture.ComponentRouteTests.accept
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    refresh = fixture.ComponentRouteTests.refresh
    assert_once = recovery.ReviewCompletionRecoveryTests.assert_once
    assert_no_completion = recovery.ReviewCompletionRecoveryTests.assert_no_completion

    async def test_status_only_refresh_during_complete_revalidates(self):
        await self.webhook(self.source_payload(number="order-1"))
        order = await review.get_order(review.MongoOrderRepository(self.db), user_id="owner", order_number="order-1")
        before = await self.db.unified_orders.find_one({"order_number": "order-1"})
        async def provider_call(*args):
            update = self.source_payload(number="order-1", version=fixture.LATER)
            update["status"]["customized"] = {"id": "synthetic-reviewed", "name": "تم المراجعة"}
            result = await self.refresh(update)
            self.assertTrue(result["ok"], result)
            return "sent", None
        response, provider = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        provider.assert_awaited_once()
        self.assertEqual(response.status_code, 200, response.text)
        after = await self.db.unified_orders.find_one({"order_number": "order-1"})
        self.assertGreater(after["g47_salla_snapshot"]["revision"], before["g47_salla_snapshot"]["revision"])
        await self.assert_once()

    async def test_product_changing_refresh_during_complete_rejects(self):
        await self.webhook(self.source_payload(number="order-1"))
        order = await review.get_order(review.MongoOrderRepository(self.db), user_id="owner", order_number="order-1")
        async def provider_call(*args):
            update = self.source_payload(number="order-1", version=fixture.LATER)
            update["items"][0]["quantity"] = 3
            result = await self.refresh(update)
            self.assertTrue(result["ok"], result)
            return "sent", None
        response, provider = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        provider.assert_awaited_once()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_completion_source_changed")
        await self.assert_no_completion()

    async def test_instant_auto_route_never_records_human_review_completion(self):
        await self.db[rules.PRODUCT_OPERATION_PROFILES].insert_one({
            "user_id": "owner", "salla_product_id": "p", "fulfillment_type": "instant",
            "inventory_policy": "finished_goods_inventory_not_tracked",
        })
        order = self.order(number="instant-order")
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": order.order_number,
            "raw_by_source": {"salla_direct": {"date": order.created_at.isoformat()}}})
        # Exercise the dormant explicit instant configuration without modifying
        # product rules in application source or any deployed configuration.
        with patch.object(rules, "PRODUCT_OPERATION_CHOICES_FROZEN", False):
            result = await fulfillment.auto_route_instant_order(self.db, user_id="owner", order=order,
                source_updated_at=fixture.WHEN)
            self.assertTrue(result["promoted"], result)
            retry = await fulfillment.auto_route_instant_order(self.db, user_id="owner", order=order,
                source_updated_at=fixture.WHEN)
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": order.order_number})
        self.assertEqual(workflow["stage"], "ready_to_ship")
        self.assertTrue(workflow["auto_routed_instant"])
        self.assertNotIn("review_completion_operation_id", workflow)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 1)

    async def test_final_acceptance_rejects_intervening_generation_or_state_change(self):
        owner_transaction = completion.operational_owner
        changes = (
            ("generation", {"$inc": {"generation": 1}}),
            ("blocked", {"$set": {"state": "blocked", "accepted": False}}),
        )
        for label, mutation in changes:
            with self.subTest(change=label):
                number = "final-guard-" + label
                injected = False
                async def inject_after_evaluation(db, owner, callback, **kwargs):
                    nonlocal injected
                    result = await owner_transaction(db, owner, callback, **kwargs)
                    operation = await self.db[completion.OPERATIONS].find_one({"order_number": number})
                    if (not injected and callback.__name__ == "evaluate_owned" and operation
                            and operation["state"] == "provider_confirmed"):
                        injected = True
                        await self.db[fulfillment.COMPONENT_LIFECYCLES].update_one(
                            {"user_id": "owner", "order_number": number}, mutation)
                    return result
                with patch.object(completion, "operational_owner", inject_after_evaluation):
                    response, provider = await self.accept(self.order(number=number))
                self.assertTrue(injected)
                provider.assert_awaited_once()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "component_acceptance_changed")
                await self.assert_no_completion()
                operation = await self.db[completion.OPERATIONS].find_one({"order_number": number})
                self.assertEqual(operation["state"], "provider_confirmed")


if __name__ == "__main__":
    unittest.main()
