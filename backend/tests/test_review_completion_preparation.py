"""Recovered review to real preparation routes on an isolated Mongo replica set."""
import unittest
from unittest.mock import AsyncMock, patch

import order_review_completion as completion
import order_review_routes as review
import reviewed_preparation_batches as batches
import reviewed_products_catalog as catalog
import preparation_file_registry as registry
import preparation_piece_operations as pieces
import test_g47_component_lifecycle_integration as fixture


class ReviewCompletionPreparationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    order = fixture.ComponentRouteTests.order
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        self.actor["email"] = "synthetic@example.invalid"
        pieces.install_preparation_piece_operations()
        async def actor():
            return self.actor
        self.app.include_router(batches.make_reviewed_preparation_batches_router(self.db, actor))
        self.app.include_router(registry.make_preparation_file_registry_router(self.db, actor))

    async def test_recovered_review_repeated_preparation_request_has_one_file_and_unit_allocation(self):
        payload = self.source_payload(number="recovered-preparation")
        payload["items"][0]["options"] = [{"name": "Color", "value": "Gold"}]
        self.assertTrue((await self.webhook(payload))["synced"])
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        provider = AsyncMock(return_value=("sent", None))
        endpoint = "/order-reviews-v1/recovered-preparation/complete"
        with patch.object(review, "_sync_salla_reviewed", provider):
            failed = await self.client.post(endpoint, json={"expected_revision": 0})
            self.assertEqual(failed.status_code, 500, failed.text)
            self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
            operation = await self.db[completion.OPERATIONS].find_one({})
            self.assertEqual(operation["state"], "provider_confirmed")
            await self.db.command({"collMod": completion.EVENTS, "validator": {}})
            recovered = await self.client.post(endpoint, json={"expected_revision": 0})
            self.assertEqual(recovered.status_code, 200, recovered.text)
            repeated = await self.client.post(endpoint, json={"expected_revision": 0})
            self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        context = await catalog.load_reviewed_product_context(self.db, user_id="owner")
        products = context["catalog"]["products"]
        self.assertTrue(products, context["catalog"])
        selections = [{"group_key": row["group_key"], "quantity": int(row["remaining_quantity"]),
                       **({"revision": row["revision"]} if row.get("revision") else {})} for row in products]
        quantity = sum(row["quantity"] for row in selections)
        self.assertEqual(quantity, 2)
        request_id = "synthetic-recovered-preparation"
        draft_body = {"client_request_id": request_id, "file_title": "Synthetic recovery",
                      "responsible_employee_id": "owner", "expected_quantity": quantity,
                      "selected_product_count": len(products)}
        draft = await self.client.post("/preparation-file-registry-v1/drafts", json=draft_body)
        self.assertEqual(draft.status_code, 200, draft.text)
        # Rendering is an output boundary; planning, reservations, registry,
        # materialization and both idempotency paths below are real application code.
        with patch.object(batches, "generate_preparation_pdf", return_value=b"%PDF-1.4\nsynthetic\n%%EOF"):
            created = await self.client.post("/reviewed-preparation-batches-v1/batches",
                json={"client_request_id": request_id, "selections": selections})
            self.assertEqual(created.status_code, 200, created.text)
            repeated_batch = await self.client.post("/reviewed-preparation-batches-v1/batches",
                json={"client_request_id": request_id, "selections": selections})
            self.assertEqual(repeated_batch.status_code, 200, repeated_batch.text)
        repeated_draft = await self.client.post("/preparation-file-registry-v1/drafts", json=draft_body)
        self.assertEqual(repeated_draft.status_code, 200, repeated_draft.text)
        rows = await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].find({}).to_list(10)
        self.assertEqual(len(rows), quantity)
        self.assertEqual(len({(r["order_number"], r["order_item_id"], r["unit_index"]) for r in rows}), quantity)
        self.assertTrue(all(r["status"] == "committed" for r in rows))
        self.assertEqual(await self.db[batches.BATCHES].count_documents({}), 1)
        self.assertEqual(await self.db[registry.REGISTRY].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), quantity)
        saved = await self.db[registry.REGISTRY].find_one({})
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["piece_registry_status"], "ready")
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)


if __name__ == "__main__":
    unittest.main()
