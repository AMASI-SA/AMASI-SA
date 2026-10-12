"""Displayed approval contract against isolated Mongo replica-set HTTP routes."""
import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import order_review_approval as approval
import order_review_completion as completion
import order_review_routes as routes
import test_review_local_completion as local


class DisplayedApprovalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = local.LocalCompletionTests.asyncSetUp
    asyncTearDown = local.LocalCompletionTests.asyncTearDown
    source_payload = local.LocalCompletionTests.source_payload
    webhook = local.LocalCompletionTests.webhook
    saved = local.LocalCompletionTests.saved
    assert_completed = local.LocalCompletionTests.assert_completed
    assert_no_completion = local.LocalCompletionTests.assert_no_completion

    async def display(self):
        response = await self.client.get("/order-reviews-v1/local-review?local_only=true")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def submit(self, detail):
        payload = {"expected_revision": detail["revision"]}
        if "approval_token" in detail:
            payload["approval_token"] = detail["approval_token"]
        return await self.client.post("/order-reviews-v1/local-review/complete", json=payload)

    async def state(self):
        names = (completion.OPERATIONS, completion.WORKFLOWS, completion.EVENTS,
                 local.fixture.PLANS, local.fixture.UNITS, local.fixture.LOCATIONS,
                 "mezan_fulfillment_decisions_v2")
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(1000)
                for name in names}

    async def assert_rejected_without_effects(self, detail, code="component_source_event_stale"):
        before = await self.state()
        response = await self.submit(detail)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], code)
        self.assertEqual(await self.state(), before)
        await self.assert_no_completion()

    async def test_displayed_token_survives_revision_only_update(self):
        detail = await self.display()
        await self.db.unified_orders.update_one({}, {"$inc": {"g47_salla_snapshot.revision": 1}})
        await self.assert_completed(await self.submit(detail))

    async def test_displayed_token_survives_same_facts_real_webhook(self):
        detail = await self.display()
        payload = deepcopy(self.payload)
        payload["updated_at"] = local.fixture.LATER
        self.assertTrue((await self.webhook(payload, "order.updated"))["synced"])
        await self.assert_completed(await self.submit(detail))

    async def test_changed_product_options_or_quantity_since_display_rejected(self):
        original = await self.db.unified_orders.find_one({})
        detail = await self.display()
        for path, value in (
            ("items.0.product_id", "different-but-same-name"),
            ("items.0.options", [{"id": "size", "name": "Size", "value": "60 inch"}]),
            ("items.0.quantity", 3),
        ):
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))
                await self.db.unified_orders.update_one({}, {"$set": {
                    "raw_by_source.salla_direct." + path: value}})
                await self.assert_rejected_without_effects(detail)

    async def test_component_recipe_changed_since_display_rejected(self):
        detail = await self.display()
        await self.db[local.fixture.PRODUCT_BINDINGS].update_one(
            {"id": "recipe-material"}, {"$set": {"quantity": 3}})
        await self.assert_rejected_without_effects(detail)

    async def test_missing_forged_expired_and_other_context_tokens_fail_closed(self):
        detail = await self.display()
        token = detail["approval_token"]
        forged = token[:-1] + ("0" if token[-1] != "0" else "1")
        with patch.object(approval.time, "time", return_value=0):
            expired = approval.issue_token(detail["approval_fingerprint"], user_id="owner",
                order_number="local-review", revision=detail["revision"])
        other_merchant = approval.issue_token(detail["approval_fingerprint"], user_id="other-owner",
            order_number="local-review", revision=detail["revision"])
        other_order = approval.issue_token(detail["approval_fingerprint"], user_id="owner",
            order_number="other-order", revision=detail["revision"])
        for name, candidate in (("forged", forged), ("expired", expired),
                                ("merchant", other_merchant), ("order", other_order)):
            with self.subTest(case=name):
                await self.assert_rejected_without_effects({**detail, "approval_token": candidate})
        missing = {key: value for key, value in detail.items() if key != "approval_token"}
        await self.assert_rejected_without_effects(missing, "review_approval_required")

    async def test_source_changes_between_route_read_and_claim_rejected(self):
        detail = await self.display()
        real_owner = completion.operational_owner
        injected = False

        async def before_claim(db, user_id, callback, **kwargs):
            nonlocal injected
            if callback.__name__ == "finish_local" and not injected:
                injected = True
                await self.db.unified_orders.update_one({}, {"$set": {
                    "raw_by_source.salla_direct.items.0.quantity": 3}})
            return await real_owner(db, user_id, callback, **kwargs)

        with patch.object(completion, "operational_owner", before_claim):
            await self.assert_rejected_without_effects(detail)
        self.assertTrue(injected)

    async def test_lost_response_retry_same_displayed_token_returns_same_operation(self):
        detail = await self.display()
        # Server commits; caller discards the response as a lost transport reply.
        await self.submit(detail)
        operation = await self.saved()
        self.assertIsNotNone(operation)
        before = await self.state()
        response = await self.submit(detail)
        self.assertTrue(response.json().get("already_reviewed"), response.text)
        self.assertEqual(await self.assert_completed(response), operation)
        self.assertEqual(await self.state(), before)

    async def test_route_enriched_identity_aba_cannot_freeze_unapproved_items(self):
        detail = await self.display()
        real_identities = routes._review_item_identities
        calls = 0

        async def transient_identity(*args, **kwargs):
            nonlocal calls
            calls += 1
            items = await real_identities(*args, **kwargs)
            if calls == 1:
                return [items[0].model_copy(update={"sku": "transient-unapproved-sku"}), *items[1:]]
            return items

        with patch.object(routes, "_review_item_identities", transient_identity):
            await self.assert_rejected_without_effects(detail)
        self.assertGreaterEqual(calls, 1)

    async def test_guarded_recipe_write_after_transaction_snapshot_fences_completion(self):
        from review_acceptance_config_guard import AcceptanceConfigDatabase, FENCES
        # Exercise row contention, not first-use concurrent collection creation.
        await self.db[FENCES].insert_one({"_id": "owner", "user_id": "owner", "version": 0, "fence": 0})
        detail = await self.display()
        real_acceptance = completion.acceptance_snapshot
        injected = False
        calls = 0
        before = await self.state()

        async def update_after_snapshot(scoped, **kwargs):
            nonlocal injected, calls
            calls += 1
            snapshot = await real_acceptance(scoped, **kwargs)
            if not injected:
                injected = True
                await AcceptanceConfigDatabase(self.db)[local.fixture.PRODUCT_BINDINGS].update_one(
                    {"user_id": "owner", "id": "recipe-material"}, {"$set": {"quantity": 3}})
            return snapshot

        with patch.object(completion, "acceptance_snapshot", update_after_snapshot):
            response = await self.submit(detail)
        self.assertTrue(injected)
        self.assertGreaterEqual(calls, 2)  # claim snapshot, then validation/fence path
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_source_event_stale")
        self.assertGreaterEqual((await self.db[FENCES].find_one({"_id": "owner"}))["version"], 1)
        self.assertEqual(await self.state(), before)
        await self.assert_no_completion()

    async def test_signed_evidence_contains_no_business_values_or_signing_secret(self):
        detail = await self.display()
        encoded, signature = detail["approval_token"].split(".")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(set(payload), {"v", "merchant", "order", "revision", "fingerprint", "expires"})
        self.assertEqual(payload["fingerprint"], detail["approval_fingerprint"])
        self.assertEqual(len(signature), 64)
        serialized = json.dumps(payload)
        self.assertNotIn("isolated-review-signing-test-only", detail["approval_token"])
        for forbidden in ("Synthetic product", "SYN-P", "recipe-material", "shipping_address", "JWT_SECRET"):
            self.assertNotIn(forbidden, serialized)

    async def test_get_renders_consistent_snapshot_when_source_changes_mid_read(self):
        before = await self.display()
        real_display = routes._display_detail
        injected = False

        async def change_after_transaction_order_read(db, user_id, order, **kwargs):
            nonlocal injected
            injected = True
            await self.db.unified_orders.update_one({}, {"$set": {
                "raw_by_source.salla_direct.items.0.quantity": 3}})
            return await real_display(db, user_id, order)

        with patch.object(routes, "_display_detail", change_after_transaction_order_read):
            consistent = await self.display()
        self.assertTrue(injected)
        self.assertEqual(consistent["approval_fingerprint"], before["approval_fingerprint"])
        self.assertEqual(consistent["items"], before["items"])
        await self.assert_rejected_without_effects(consistent)


if __name__ == "__main__":
    unittest.main()
