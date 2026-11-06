"""Independent regression cases for reusable product-option settings."""
import base64
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import AutoReconnect

import test_review_product_defaults as fixture
import order_review_product_defaults as defaults


class ProductDefaultRegressionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ProductDefaultsTests.asyncSetUp
    asyncTearDown = fixture.ProductDefaultsTests.asyncTearDown
    source_payload = fixture.ProductDefaultsTests.source_payload
    webhook = fixture.ProductDefaultsTests.webhook
    seed = fixture.ProductDefaultsTests.seed

    async def arabic_order(self, number, answer, customer):
        payload = self.source_payload(number=number)
        payload["items"][0]["options"] = [
            {"name": "هل تريد تطريز اسم على الشال", "value": answer},
            {"name": "الاسم", "value": customer},
        ]
        self.assertTrue((await self.webhook(payload))["synced"])
        result = await self.client.get(f"/order-reviews-v1/{number}?local_only=true")
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def test_arabic_personalization_yes_no_is_a_condition_without_saving_customer_text(self):
        first = await self.arabic_order("arabic-template", "نعم", "FIRST-CUSTOMER")
        created = await self.client.post("/order-reviews-v1/arabic-template/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic embroidery", "linked_spec_keys": ["هل تريد تطريز اسم على الشال", "الاسم"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        inactive = await self.arabic_order("arabic-no", "لا", "SECOND-CUSTOMER")
        self.assertEqual(inactive["operational_items"], [], "No must not inherit the Yes operational action")
        active = await self.arabic_order("arabic-yes", "نعم", "THIRD-CUSTOMER")
        self.assertEqual(len(active["operational_items"]), 1)
        self.assertEqual({row["key"]: row["value"] for row in active["operational_items"][0]["linked_specs"]}["الاسم"], "THIRD-CUSTOMER")
        setting = await self.db.order_review_product_option_defaults.find_one({"user_id": "owner"})
        self.assertNotIn("FIRST-CUSTOMER", str(setting))
        self.assertNotIn("SECOND-CUSTOMER", str(setting))
        self.assertNotIn("THIRD-CUSTOMER", str(setting))

    async def test_arabic_card_text_is_not_saved_as_a_global_condition_and_future_text_is_fresh(self):
        async def message_order(number, message):
            payload = self.source_payload(number=number)
            payload["items"][0]["options"] = [{"name": "gift box", "value": "yes"}, {"name": "نص الكرت", "value": message}]
            self.assertTrue((await self.webhook(payload))["synced"])
            response = await self.client.get(f"/order-reviews-v1/{number}?local_only=true")
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        first = await message_order("message-template", "PRIVATE-MESSAGE-FIRST")
        created = await self.client.post("/order-reviews-v1/message-template/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic card", "linked_spec_keys": ["gift box", "نص الكرت"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        settings = await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"})
        self.assertNotIn("PRIVATE-MESSAGE-FIRST", str(settings).upper())
        workflow = await self.db[fixture.review.WORKFLOWS].find_one({"order_number": "message-template"})
        self.assertNotIn("PRIVATE-MESSAGE-FIRST", str(workflow["default_actions"]).upper())
        future = await message_order("message-future", "PRIVATE-MESSAGE-SECOND")
        self.assertEqual(len(future["operational_items"]), 1)
        self.assertEqual({row["key"]: row["value"] for row in future["operational_items"][0]["linked_specs"]}["نص الكرت"], "PRIVATE-MESSAGE-SECOND")

    async def test_unhide_on_older_order_preserves_a_different_field_saved_from_another_order(self):
        first = await self.seed("hidden-old")
        first_path = f"/order-review-export-controls-v1/hidden-old/items/{first['items'][0]['order_item_id']}"
        self.assertEqual((await self.client.patch(first_path, json={"manual_hidden_spec_keys": ["الاسم"]})).status_code, 200)
        second = await self.seed("hidden-new")
        second_path = f"/order-review-export-controls-v1/hidden-new/items/{second['items'][0]['order_item_id']}"
        self.assertEqual((await self.client.patch(second_path, json={"manual_hidden_spec_keys": ["الاسم", "gift box"]})).status_code, 200)
        self.assertEqual((await self.client.patch(first_path, json={"manual_hidden_spec_keys": []})).status_code, 200)
        await self.seed("hidden-future")
        future = await self.client.get("/order-review-export-controls-v1/hidden-future")
        self.assertEqual(future.status_code, 200, future.text)
        self.assertEqual(future.json()["items"][0]["manual_hidden_spec_keys"], ["gift box"],
            "Cancelling Name on an older order must not cancel the independently saved gift-box field")

    async def test_editing_an_existing_auto_card_does_not_reactivate_a_revoked_rule(self):
        first = await self.seed("revoked-source")
        created = await self.client.post("/order-reviews-v1/revoked-source/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic box", "linked_spec_keys": ["gift box", "الاسم"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        original = created.json()["operational_items"][0]
        automatic = await self.seed("revoked-existing", personal="SECOND")
        existing = automatic["operational_items"][0]
        removed = await self.client.delete(f"/order-reviews-v1/revoked-source/operational-items/{original['operational_item_id']}",
            params={"expected_revision": created.json()["revision"]})
        self.assertEqual(removed.status_code, 200, removed.text)
        renamed = await self.client.patch(f"/order-reviews-v1/revoked-existing/operational-items/{existing['operational_item_id']}",
            json={"expected_revision": automatic["revision"], "name": "Changed only on this existing card"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual((await self.seed("revoked-after-rename"))["operational_items"], [])
        image = await self.client.post(f"/order-reviews-v1/revoked-existing/operational-items/{existing['operational_item_id']}/image", json={
            "expected_revision": renamed.json()["revision"], "operational_image": {
                "filename": "synthetic.png", "content_type": "image/png",
                "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()},
        })
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual((await self.seed("revoked-after-photo"))["operational_items"], [])

    async def test_pending_auto_card_uses_current_customer_value_after_a_verified_source_update(self):
        first = await self.seed("refresh-template")
        created = await self.client.post("/order-reviews-v1/refresh-template/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic box", "linked_spec_keys": ["gift box", "الاسم"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        before = await self.seed("refresh-pending", personal="BEFORE-UPDATE")
        old_card = before["operational_items"][0]
        payload = self.source_payload(number="refresh-pending", version="2026-09-26T13:00:00+00:00")
        payload["items"][0]["options"] = [
            {"name": "gift box", "value": "yes"}, {"name": "الاسم", "value": "AFTER-UPDATE"}]
        self.assertTrue((await self.webhook(payload, event="order.updated"))["synced"])
        updated = await self.client.get("/order-reviews-v1/refresh-pending?local_only=true")
        self.assertEqual(updated.status_code, 200, updated.text)
        cards = updated.json()["operational_items"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["operational_item_id"], old_card["operational_item_id"])
        self.assertEqual({row["key"]: row["value"] for row in cards[0]["linked_specs"]}["الاسم"], "AFTER-UPDATE",
            "The source field is hidden in its product PDF, so its auto-card must not retain an older customer's value")

    async def test_pending_inactive_option_removes_only_auto_card_and_keeps_manual_hidden_fields(self):
        first = await self.seed("inactive-template")
        created = await self.client.post("/order-reviews-v1/inactive-template/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic box", "linked_spec_keys": ["gift box"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        pending = await self.seed("inactive-pending", personal="CURRENT")
        hidden = await self.client.patch(f"/order-review-export-controls-v1/inactive-pending/items/{pending['items'][0]['order_item_id']}",
            json={"manual_hidden_spec_keys": ["الاسم"]})
        self.assertEqual(hidden.status_code, 200, hidden.text)
        payload = self.source_payload(number="inactive-pending", version="2026-09-26T13:00:00+00:00")
        payload["items"][0]["options"] = [{"name": "gift box", "value": "no"}, {"name": "الاسم", "value": "CURRENT"}]
        self.assertTrue((await self.webhook(payload, event="order.updated"))["synced"])
        updated = await self.client.get("/order-reviews-v1/inactive-pending?local_only=true")
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["operational_items"], [])
        control = await self.client.get("/order-review-export-controls-v1/inactive-pending")
        self.assertEqual(control.json()["items"][0]["manual_hidden_spec_keys"], ["الاسم"])
        self.assertEqual(control.json()["items"][0]["operational_hidden_spec_keys"], [])

    async def create_pending_with_failed_default_write(self, *, after_receipt=False):
        first = await self.seed("outbox-source", personal="PRIVATE-CUSTOMER")
        original = defaults._apply_action
        async def injected(*args, **kwargs):
            if after_receipt:
                await original(*args, **kwargs)
            raise AutoReconnect("Synthetic default-write ACK failure")
        with patch.object(defaults, "_apply_action", side_effect=injected):
            response = await self.client.post("/order-reviews-v1/outbox-source/operational-items", json={
                "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
                "name": "Synthetic box", "linked_spec_keys": ["gift box", "الاسم"],
            })
            self.assertEqual(response.status_code, 503, response.text)
            self.assertFalse(response.json()["detail"]["retry_post"])
            self.assertNotIn("approval_token", response.json())
            blocked = await self.client.get("/order-reviews-v1/outbox-source?local_only=true")
            self.assertEqual(blocked.status_code, 503, blocked.text)
            self.assertNotIn("approval_token", blocked.json())
        workflow = await self.db[fixture.review.WORKFLOWS].find_one({"order_number": "outbox-source"})
        self.assertEqual(len(workflow["operational_items"]), 1)
        self.assertEqual(len(workflow["default_actions"]), 1)
        self.assertEqual(workflow["default_actions"][0]["state"], "pending")
        self.assertNotIn("PRIVATE-CUSTOMER", str(workflow["default_actions"]))
        return workflow

    async def test_failed_default_write_after_workflow_commit_recovers_by_get_without_post_replay(self):
        workflow = await self.create_pending_with_failed_default_write()
        self.assertEqual(await self.db[defaults.DEFAULTS].count_documents({}), 0)
        result = await self.client.get("/order-reviews-v1/outbox-source?local_only=true")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["operational_items"][0]["operational_item_id"], workflow["operational_items"][0]["operational_item_id"])
        self.assertEqual(result.json()["revision"], workflow["revision"])
        self.assertEqual(len(result.json()["operational_items"]), 1)
        setting = await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"})
        self.assertEqual(len(setting["operational_templates"]), 1)
        self.assertEqual(len(setting["applied_actions"]), 1)
        for _ in range(2):
            self.assertEqual((await self.client.get("/order-reviews-v1/outbox-source?local_only=true")).status_code, 200)
        self.assertEqual(await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"}), setting)
        future = await self.seed("outbox-future", personal="FUTURE-CUSTOMER")
        self.assertEqual(len(future["operational_items"]), 1)
        self.assertEqual({row["key"]: row["value"] for row in future["operational_items"][0]["linked_specs"]}["الاسم"], "FUTURE-CUSTOMER")

    async def test_lost_default_receipt_ack_does_not_repeat_the_configuration_change(self):
        await self.create_pending_with_failed_default_write(after_receipt=True)
        before = await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"})
        self.assertEqual(len(before["applied_actions"]), 1)
        result = await self.client.get("/order-reviews-v1/outbox-source?local_only=true")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"}), before)
        workflow = await self.db[fixture.review.WORKFLOWS].find_one({"order_number": "outbox-source"})
        self.assertEqual(workflow["default_actions"][0]["state"], "applied")

    async def test_delayed_put_cannot_reactivate_a_newer_cancelled_setting(self):
        workflow = await self.create_pending_with_failed_default_write()
        put = workflow["default_actions"][0]
        cancel = await defaults.forget_action(self.db, user_id="owner", actor_id="owner", card=workflow["operational_items"][0])
        self.assertGreater(cancel["sequence"], put["sequence"])
        await defaults._apply_action(self.db, user_id="owner", action=cancel)
        await defaults._apply_action(self.db, user_id="owner", action=put)
        value = await self.db[defaults.DEFAULTS].find_one({"user_id": "owner"})
        self.assertEqual(value["operational_templates"], {})
        self.assertEqual(value["applied_actions"][put["id"]]["outcome"], "superseded")
        self.assertEqual(value["operational_action_stamps"][put["template"]["id"]], cancel["sequence"])

    async def test_parallel_hidden_changes_from_different_orders_preserve_both_fields(self):
        orders = [await self.seed(number) for number in ("concurrent-a", "concurrent-b")]
        responses = await asyncio.gather(*[
            self.client.patch(f"/order-review-export-controls-v1/{number}/items/{detail['items'][0]['order_item_id']}",
                json={"manual_hidden_spec_keys": [key]})
            for number, detail, key in zip(("concurrent-a", "concurrent-b"), orders, ("الاسم", "gift box"))])
        self.assertEqual([response.status_code for response in responses], [200, 200], [response.text for response in responses])
        await self.seed("concurrent-future")
        control = await self.client.get("/order-review-export-controls-v1/concurrent-future")
        self.assertEqual(set(control.json()["items"][0]["manual_hidden_spec_keys"]), {"الاسم", "gift box"})

    async def test_default_outbox_configuration_write_retains_the_real_acceptance_fence(self):
        import order_review_routes as routes
        import order_review_export_controls as controls
        from review_acceptance_config_guard import AcceptanceConfigDatabase, ensure_acceptance_config_storage, FENCES
        await ensure_acceptance_config_storage(self.db)
        app = FastAPI()
        wrapped = AcceptanceConfigDatabase(self.db)
        async def actor():
            return self.actor
        app.include_router(routes.make_order_review_router(wrapped, actor))
        app.include_router(controls.make_order_review_export_controls_router(wrapped, actor))
        self.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        first = await self.seed("fence-before")
        other = await self.seed("fence-change")
        response = await self.client.patch(f"/order-review-export-controls-v1/fence-change/items/{other['items'][0]['order_item_id']}",
            json={"manual_hidden_spec_keys": ["الاسم"]})
        self.assertEqual(response.status_code, 200, response.text)
        version = await self.db[FENCES].find_one({"_id": "owner"})
        self.assertGreater(version["version"], 0)
        rejected = await self.client.post("/order-reviews-v1/fence-before/complete", json={
            "expected_revision": first["revision"], "approval_token": first["approval_token"]})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(await self.db.order_review_completion_operations.count_documents({"order_number": "fence-before"}), 0)


if __name__ == "__main__":
    unittest.main()
