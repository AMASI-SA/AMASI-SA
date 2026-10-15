"""Local review membership against real Mongo; no provider or production fallback."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import fulfillment_v2_routes as fulfillment
import order_review_routes as review
from order_engine.repository import MongoOrderRepository


class LocalReviewQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["review_local_queues_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.repository = MongoOrderRepository(self.db)
        self.completed_numbers = [f"{number:03d}" for number in range(64, 9, -1)]
        self.pending_numbers = [f"{number:03d}" for number in range(9, 0, -1)]
        # More than a full legacy fetch window still says pending in Salla.
        # Local review, not the provider status, removes these 55 candidates.
        orders = [self.order(number) for number in self.completed_numbers + self.pending_numbers]
        orders.append(self.order("900", status="canceled"))
        orders.append(self.order("901", user_id="another-owner"))
        await self.db.unified_orders.insert_many(orders)
        await self.db[review.WORKFLOWS].insert_many([
            {
                "user_id": "owner", "order_number": number, "stage": "reviewed",
                "revision": 1, "reviewed_at": "2026-10-09T09:00:00+00:00",
                "completion_mode": "mezan_local_v1",
                "review_completion_operation_id": "local-" + number,
                "items": [], "operational_items": [],
            }
            for number in self.completed_numbers
        ] + [{
            # A same-number review owned by another tenant cannot hide ours.
            "user_id": "another-owner", "order_number": "009", "stage": "reviewed",
        }])

        async def actor():
            return {"id": "owner", "role": "owner"}

        app = FastAPI()
        app.include_router(review.make_order_review_router(self.db, actor))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.sync_patch = patch.object(review, "schedule_salla_auto_sync")
        self.sync_patch.start()

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            self.sync_patch.stop()
            await self.client.aclose()
        if hasattr(self, "mongo"):
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    @staticmethod
    def order(number, *, status="under_review", user_id="owner"):
        date = "2026-10-08" if number == "001" else "2026-10-09"
        name = "بانتظار المراجعة" if status == "under_review" else "ملغي"
        return {
            "user_id": user_id, "order_number": number, "order_date": date,
            "order_status": name, "order_status_slug": status,
            "raw_by_source": {"salla_direct": {
                "id": "provider-" + number, "reference_id": number,
                "date": date + "T08:00:00+00:00",
                "status": {"slug": status, "name": name},
                "customer": {"full_name": "Synthetic customer"},
                "payment_method": "credit_card",
                "amounts": {"total": {"amount": 100, "currency": "SAR"}},
                "items": [{"id": "item-" + number, "product_id": "product-1",
                           "name": "Synthetic product", "quantity": 1}],
            }},
        }

    async def test_numbered_queue_excludes_local_completion_before_slice_and_count(self):
        for page, expected in ((1, ["009", "008", "007"]), (2, ["006", "005", "004"]),
                               (3, ["003", "002", "001"]), (4, [])):
            numbers, total = await self.repository.numbered_pending_review_order_numbers(
                user_id="owner", page=page, limit=3, workflow_collection=review.WORKFLOWS,
                completed_stages=review.REVIEW_COMPLETED_STAGES,
            )
            self.assertEqual(numbers, expected)
            self.assertEqual(total, 9)

    async def test_cursor_queue_and_numbered_queue_have_same_membership_and_total(self):
        cursor = {}
        observed = []
        for expected in (["009", "008", "007"], ["006", "005", "004"],
                         ["003", "002", "001"], []):
            rows, total = await self.repository.cursor_pending_review_order_numbers(
                user_id="owner", limit=3, workflow_collection=review.WORKFLOWS,
                completed_stages=review.REVIEW_COMPLETED_STAGES, **cursor,
            )
            numbers = [row["order_number"] for row in rows]
            self.assertEqual(numbers, expected)
            self.assertEqual(total, 9)
            observed.extend(numbers)
            if rows:
                cursor = {"before_order_date": rows[-1]["order_date"],
                          "before_order_number": rows[-1]["order_number"]}
        self.assertEqual(observed, self.pending_numbers)
        self.assertEqual(len(observed), len(set(observed)))

    async def test_pending_route_does_not_return_empty_pages_behind_local_reviews(self):
        cursor = None
        observed = []
        for expected in (["009", "008", "007"], ["006", "005", "004"], ["003", "002", "001"]):
            params = {"limit": 3}
            if cursor:
                params["cursor"] = cursor
            response = await self.client.get("/order-reviews-v1", params=params)
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            numbers = [row["order_number"] for row in payload["items"]]
            self.assertEqual(numbers, expected)
            self.assertEqual(payload["total_count"], 9)
            observed.extend(numbers)
            cursor = payload["next_cursor"]
        self.assertIsNone(cursor)
        self.assertEqual(observed, self.pending_numbers)

    async def test_reviewed_list_keeps_local_reviews_while_provider_stays_pending(self):
        response = await self.client.get("/order-reviews-v1/reviewed", params={"limit": 100})
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()["items"]
        self.assertEqual({row["order_number"] for row in rows}, set(self.completed_numbers))
        self.assertTrue(all(row["stage"] == "reviewed" for row in rows))
        self.assertEqual(await self.db.unified_orders.count_documents({
            "user_id": "owner", "raw_by_source.salla_direct.status.slug": "under_review",
        }), 64)


class LocalReviewAutoRouteTests(unittest.IsolatedAsyncioTestCase):
    async def revert(self, *, mode="mezan_local_v1", operation="local-001", previous="pending_review"):
        workflows = SimpleNamespace(update_one=AsyncMock())
        workflow = {"stage": "ready_to_ship", "auto_routed_instant": True,
                    "auto_route_previous_stage": previous, "completion_mode": mode,
                    "review_completion_operation_id": operation}
        result = await fulfillment._apply_auto_route_decision(
            {fulfillment.WORKFLOWS: workflows}, user_id="owner",
            order=SimpleNamespace(order_number="001"), workflow=workflow,
            current_stage="ready_to_ship", decision={"ready_to_ship": False},
        )
        return result, workflows.update_one.call_args

    async def test_local_completion_cannot_revert_below_reviewed(self):
        for previous in ("pending_review", "customer_waiting", ""):
            result, write = await self.revert(previous=previous)
            self.assertEqual(result["stage"], "reviewed")
            self.assertEqual(write.args[1]["$set"]["stage"], "reviewed")
            self.assertEqual(write.args[0]["stage"], "ready_to_ship")
            self.assertTrue(write.args[0]["auto_routed_instant"])
            self.assertIn("$or", write.args[0])  # Existing claim fence remains.

    async def test_local_completion_keeps_later_saved_stages(self):
        for previous in ("reviewed", "in_progress", "assembly"):
            result, _ = await self.revert(previous=previous)
            self.assertEqual(result["stage"], previous)

    async def test_legacy_auto_routing_does_not_infer_local_approval(self):
        for mode, operation in ((None, "local-001"), ("mezan_local_v1", "")):
            result, _ = await self.revert(mode=mode, operation=operation)
            self.assertEqual(result["stage"], "pending_review")
