"""Real Mongo/ASGI parity for Web legacy shims and shared mobile controls."""
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import fulfillment_lifecycle as lifecycle
import fulfillment_lifecycle_routes as routes
import fulfillment_experiment_routes as legacy
import preparation_piece_operations as preparation
from order_engine import shipping_label_service as shipping
import test_fulfillment_lifecycle_mongo as fixture


class OrderChangeRoutesTests(unittest.IsolatedAsyncioTestCase):
    cleanup_database = fixture.LifecycleMongoTests.cleanup_database
    caps = fixture.LifecycleMongoTests.caps
    payload = fixture.LifecycleMongoTests.payload
    hold = fixture.LifecycleMongoTests.hold
    pieces = fixture.LifecycleMongoTests.pieces

    async def asyncSetUp(self):
        await fixture.LifecycleMongoTests.asyncSetUp(self)
        self.actor = {"id": "owner", "role": "owner", "name": "Synthetic operator"}

        async def current_user():
            return self.actor

        async def actor_context(*args):
            return self.context

        for module in (routes, legacy):
            auth = patch.object(module, "_actor_context", actor_context)
            auth.start()
            self.addCleanup(auth.stop)
        self.app = FastAPI()
        self.app.include_router(routes.make_fulfillment_lifecycle_router(self.db, current_user))
        self.app.include_router(legacy.make_fulfillment_experiment_router(self.db, current_user))
        self.client = AsyncClient(transport=ASGITransport(app=self.app, raise_app_exceptions=False),
                                  base_url="http://isolated-lifecycle-test")
        self.addAsyncCleanup(self.client.aclose)

    async def route_body(self):
        return {**await self.payload(), "target_id": None}

    @staticmethod
    def web_body(body):
        return {("note" if key == "reason" else key): value for key, value in body.items()}

    async def test_mobile_hold_web_retry_replays_one_shared_operation(self):
        body = await self.route_body()
        created = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        replay = await self.client.post("/fulfillment-experiments-v1/orders/order-1/holds", json=self.web_body(body))
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["hold"]["id"], created.json()["hold"]["id"])
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 1)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)

    async def test_web_hold_mobile_retry_replays_one_shared_operation(self):
        body = await self.route_body()
        created = await self.client.post("/fulfillment-experiments-v1/orders/order-1/holds", json=self.web_body(body))
        self.assertEqual(created.status_code, 200, created.text)
        replay = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["hold"]["id"], created.json()["hold"]["id"])

    async def test_web_release_requires_fences_and_replays_mobile_resume(self):
        created = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=await self.route_body())
        self.assertEqual(created.status_code, 200, created.text)
        hold_id = created.json()["hold"]["id"]
        denied = await self.client.post(f"/fulfillment-experiments-v1/holds/{hold_id}/release", json={"note": "No fences supplied"})
        self.assertEqual(denied.status_code, 422, denied.text)
        self.assertEqual((await self.caps())["active_holds"][0]["id"], hold_id)
        caps = await self.caps()
        body = {"reason": "Approved local resume", "idempotency_key": uuid4().hex,
                "expected_revision": caps["revision"], "expected_generation": caps["generation"]}
        released = await self.client.post(f"/fulfillment-experiments-v1/holds/{hold_id}/release", json=self.web_body(body))
        self.assertEqual(released.status_code, 200, released.text)
        replay = await self.client.post(f"/order-change-controls-v1/holds/{hold_id}/resume", json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 2)

    async def test_capabilities_and_audit_are_scoped_and_permission_checked(self):
        created = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=await self.route_body())
        self.assertEqual(created.status_code, 200, created.text)
        await self.db[lifecycle.AUDIT].insert_one({"user_id": "other-owner", "order_number": "order-1",
                                                  "reason": "Must not be disclosed"})
        result = await self.client.get("/order-change-controls-v1/orders/order-1/audit")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(result.json()["events"]), 1)
        self.assertEqual(result.json()["events"][0]["actor_id"], "owner")
        self.context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False, "permissions": set()}
        for endpoint in ("capabilities", "audit"):
            result = await self.client.get(f"/order-change-controls-v1/orders/order-1/{endpoint}")
            self.assertEqual(result.status_code, 403, result.text)
        self.context["permissions"] = {lifecycle.SELF_STOP}
        self.assertEqual((await self.client.get("/order-change-controls-v1/orders/order-1/capabilities")).status_code, 200)
        self.assertEqual((await self.client.get("/order-change-controls-v1/orders/order-1/audit")).status_code, 403)

    async def test_flag_disabled_keeps_commercial_denied_and_rejects_new_shared_hold(self):
        body = await self.route_body()
        with patch.dict(os.environ, {lifecycle.FLAG: "false"}):
            result = await self.client.get("/order-change-controls-v1/orders/order-1/capabilities")
            self.assertEqual(result.status_code, 200, result.text)
            for name in ("cancel_product", "edit_product", "add_product", "hold_order"):
                self.assertFalse(result.json()["actions"][name]["allowed"])
            denied = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=body)
            self.assertEqual(denied.status_code, 409, denied.text)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_real_worker_entrypoints_reject_hold_before_any_physical_or_provider_action(self):
        await self.hold()
        await self.db[lifecycle.PIECES].update_many({}, {"$set": {"batch_id": "batch-1"}})
        await self.db[preparation.BATCHES].insert_one({"id": "batch-1", "user_id": "owner",
            "lines": [{"order_number": "order-1", "order_item_id": "item-1"}]})
        before = await self.pieces()
        calls = [
            ("start_file", lambda: preparation._start_file_execution(self.db, user_id="owner",
                registry={"batch_id": "batch-1", "file_number": "file-1"}, actor=self.actor, note=None)),
            ("receive", lambda: preparation._receive_preparation_piece(self.db, user_id="owner",
                piece_id="piece-1", client_request_id="request-1", actor_id="owner", actor_name="Synthetic operator")),
            ("assembly", lambda: preparation._mark_assembly_piece_ready(self.db, user_id="owner",
                piece_id="piece-2", client_request_id="request-2", actor_id="owner", actor_name="Synthetic operator")),
            ("shipping", lambda: shipping.issue_shipping_label(self.db, "owner", "order-1")),
        ]
        # Only the external provider boundary is mocked; real decorators, service,
        # transactions, Mongo reads and shared worker bodies remain installed.
        with patch.object(shipping, "call_salla", AsyncMock(side_effect=AssertionError("Unexpected provider I/O"))) as provider:
            for name, call in calls:
                with self.subTest(worker=name), self.assertRaises(HTTPException) as caught:
                    await call()
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail["code"], "fulfillment_lifecycle_held")
            provider.assert_not_awaited()
        self.assertEqual(await self.pieces(), before)
        self.assertEqual(await self.db[lifecycle.EXECUTIONS].count_documents({"state": "active"}), 0)


if __name__ == "__main__":
    unittest.main()
