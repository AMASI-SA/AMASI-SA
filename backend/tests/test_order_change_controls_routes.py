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
import order_tracking_notes_routes as tracking
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

        for module in (routes, legacy, tracking, preparation):
            auth = patch.object(module, "_actor_context", actor_context)
            auth.start()
            self.addCleanup(auth.stop)
        self.app = FastAPI()
        self.app.include_router(routes.make_fulfillment_lifecycle_router(self.db, current_user))
        self.app.include_router(legacy.make_fulfillment_experiment_router(self.db, current_user))
        self.app.include_router(tracking.make_order_tracking_notes_router(self.db, current_user))
        self.app.include_router(preparation.make_preparation_piece_operations_router(self.db, current_user))
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

    async def tracking_body(self):
        caps = await self.caps()
        return {"scope": "order", "action_type": "edit_order", "note": "Synthetic customer service hold",
                "target_stages": ["current_stage"], "priority": "normal", "required_action": "none",
                "idempotency_key": uuid4().hex, "expected_revision": caps["revision"],
                "expected_generation": caps["generation"]}

    async def test_tracking_customer_service_adapter_uses_atomic_hold_and_stable_stage_replay(self):
        body = await self.tracking_body()
        self.actor = {"id": "customer-service", "role": "customer_service", "name": "Synthetic service employee"}
        self.context = {"merchant_id": "owner", "actor_id": "customer-service", "is_owner": False, "permissions": set()}
        created = await self.client.post("/order-tracking-notes/orders/order-1/instructions", json=body)
        self.assertEqual(created.status_code, 201, created.text)
        first = created.json()
        self.assertEqual(first["instruction"]["target_stages"], ["assembly_labeling"])
        self.assertEqual(first["hold"]["created_by"], "customer-service")
        await self.db[lifecycle.PIECES].update_many({}, {"$set": {"status": "in_progress"}})
        replay = await self.client.post("/order-tracking-notes/orders/order-1/instructions", json=body)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["instruction"]["target_stages"], ["assembly_labeling"])
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)
        event = await self.db[lifecycle.AUDIT].find_one({})
        instruction_id = first["instruction"]["id"]
        denied = await self.client.post(f"/order-tracking-notes/instructions/{instruction_id}/complete", json={"note": "Missing fences"})
        self.assertEqual(denied.status_code, 422, denied.text)
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        released = await self.client.post(f"/order-tracking-notes/instructions/{instruction_id}/complete", json={
            "note": "Customer service verified resume", "idempotency_key": uuid4().hex,
            "expected_revision": caps["revision"], "expected_generation": caps["generation"]})
        self.assertEqual(released.status_code, 200, released.text)
        self.assertEqual(released.json()["instruction"]["status"], "completed")
        self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": event["_id"]}), event)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"id": first["hold"]["id"]}), 1)

    async def test_tracking_adapter_rejects_invalid_semantics_and_missing_fences(self):
        body = await self.tracking_body()
        for changes in ({"required_action": "not-an-action"}, {"priority": "not-a-priority"},
                        {"delivery_date": "2026-99-99"}, {"delivery_time": "29:99"},
                        {"expected_revision": None}, {"expected_generation": None}):
            with self.subTest(changes=changes):
                response = await self.client.post("/order-tracking-notes/orders/order-1/instructions", json={**body, **changes})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 0)

    async def test_reset_cannot_bypass_active_flag_or_persisted_owner_marker(self):
        before = await self.pieces()
        for flag in ("true", "false"):
            if flag == "false":
                await self.hold()
            with self.subTest(flag=flag), patch.dict(os.environ, {lifecycle.FLAG: flag}):
                response = await self.client.post("/fulfillment-experiments-v1/orders/order-1/reset",
                    json={"confirmation": "RESET order-1", "note": "Synthetic attempted reset"})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(await self.pieces(), before)

    async def test_real_receive_and_assembly_routes_reject_invalid_piece_even_after_ack(self):
        self.context.update(permissions={"fulfillment.pack.confirm", "fulfillment.ready.read"}, responsibilities=set())
        for state in ("cancelled", "replaced"):
            await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {
                "status": state, "acknowledged_by_ids": ["owner"], "notification_read": True,
                "last_acknowledged_at": "2026-10-06T12:00:00Z"}})
            piece = await self.db[lifecycle.PIECES].find_one({"piece_id": "piece-1"})
            for endpoint in ("receiving/pieces/piece-1/receive", "assembly/pieces/piece-1/ready"):
                with self.subTest(state=state, endpoint=endpoint):
                    response = await self.client.post("/preparation-work-v1/" + endpoint, json={
                        "client_request_id": uuid4().hex, "expected_revision": piece["revision"],
                        "expected_generation": lifecycle.piece_generation(piece)})
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertIn(response.json()["detail"]["code"], {"fulfillment_piece_inactive", "fulfillment_piece_not_current"})

    async def test_resume_audit_captures_current_units_options_without_rewriting_hold_history(self):
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
            {"$set": {"product_options_snapshot": {"color": "red"}}})
        held = await self.hold()
        original = await self.db[lifecycle.HOLDS].find_one({"id": held["hold"]["id"]})
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
            {"$set": {"generation": 3, "product_options_snapshot": {"color": "blue"}}})
        result = await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
            context=self.context, payload=await self.payload(reason="Resume current generation"))
        self.assertTrue(result["ok"])
        event = await self.db[lifecycle.AUDIT].find_one({"event_type": "fulfillment_hold_resumed"})
        self.assertIn({"key": "piece-1:color", "value": "blue"}, event["options_snapshot"])
        self.assertTrue(any(row["generation"] == 3 for row in event["unit_snapshot"]))
        current_hold = await self.db[lifecycle.HOLDS].find_one({"id": held["hold"]["id"]})
        self.assertEqual(current_hold["before_states"], original["before_states"])
        self.assertEqual(current_hold["options_snapshot"], original["options_snapshot"])

    async def test_worker_claim_keeps_exclusion_after_flag_disable(self):
        async with lifecycle.execution_scope(self.db, user_id="owner",
            targets=[{"order_number": "order-1"}], operation="synthetic-worker"):
            with patch.dict(os.environ, {lifecycle.FLAG: "false"}):
                caps = await self.caps()
                self.assertTrue(caps["execution_in_flight"])
                # A fresh context imitates a second worker, rather than a nested call.
                import contextvars
                import asyncio
                async def competing_worker():
                    async with lifecycle.execution_scope(self.db, user_id="owner",
                        targets=[{"order_number": "order-1"}], operation="competing-worker"):
                        self.fail("Claim was bypassed after feature flag disable")
                task = asyncio.create_task(competing_worker(), context=contextvars.Context())
                with self.assertRaises(HTTPException) as caught:
                    await task
                self.assertEqual(caught.exception.detail["code"], "fulfillment_execution_in_flight")

    async def test_resume_event_uses_current_assignee_and_preserves_creation_history(self):
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
            {"$set": {"responsible_employee_id": "original-employee"}})
        held = await self.hold(await self.payload(scope="piece", target_id="piece-1"))
        self.assertEqual(held["change_event"]["affected_employees"], ["original-employee"])
        original_event = await self.db[lifecycle.AUDIT].find_one({"event_type": "fulfillment_hold_created"})
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
            {"$set": {"responsible_employee_id": "replacement-employee"}})
        resumed = await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
            context=self.context, payload=await self.payload(reason="Resume reassigned work"))
        self.assertEqual(resumed["change_event"]["affected_employees"], ["replacement-employee"])
        self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": original_event["_id"]}), original_event)
        saved = await self.db[lifecycle.HOLDS].find_one({"id": held["hold"]["id"]})
        self.assertEqual(saved["employee_ids"], ["original-employee"])

    async def test_virtual_operational_and_direct_assembly_holds_and_source_cancellation(self):
        operational = {"operational_item_id": "virtual-operational", "source_order_item_id": "virtual-source",
            "name": "Synthetic card", "generation": 2, "revision": 1, "active": True, "current": True}
        direct = {"order_item_id": "virtual-direct-item", "preparation_route": "direct_assembly",
            "quantity": 1, "direct_assembly_piece_ids": ["virtual-direct"],
            "generation": 2, "revision": 1, "active": True, "current": True}
        await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {"operational_items": [operational],
            "items": [direct]}})
        for piece_id, source_field in (("virtual-operational", "operational_items.0"), ("virtual-direct", "items.0")):
            with self.subTest(piece=piece_id):
                current = await lifecycle.assert_piece_current(self.db, user_id="owner", piece_id=piece_id)
                self.assertEqual(current["generation"], 2)
                held = await self.hold(await self.payload(scope="piece", target_id=piece_id))
                self.assertIn(piece_id, held["hold"]["piece_ids"])
                with self.assertRaises(HTTPException) as blocked:
                    await preparation._mark_assembly_piece_ready(self.db, user_id="owner", piece_id=piece_id,
                        client_request_id=uuid4().hex, actor_id="owner", actor_name="Synthetic operator")
                self.assertEqual(blocked.exception.detail["code"], "fulfillment_lifecycle_held")
                await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
                    context=self.context, payload=await self.payload(reason="Resume virtual piece"))
                await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {source_field + ".cancelled": True}})
                with self.assertRaises(HTTPException) as invalid:
                    await lifecycle.assert_piece_current(self.db, user_id="owner", piece_id=piece_id)
                self.assertEqual(invalid.exception.detail["code"], "fulfillment_piece_not_current")
        self.assertEqual(await self.db[lifecycle.PIECES].count_documents({"piece_id": {"$in": ["virtual-operational", "virtual-direct"]}}), 0)

    async def execution_http_failure(self, status):
        held = await self.hold(await self.payload(scope="piece", target_id="piece-1"))
        with self.assertRaises(HTTPException) as caught:
            async with lifecycle.execution_scope(self.db, user_id="owner",
                targets=[{"order_number": "order-1", "order_item_id": "item-2", "piece_id": "piece-2"}],
                operation="synthetic-provider-failure"):
                raise HTTPException(status, detail="Synthetic boundary failure")
        self.assertEqual(caught.exception.status_code, status)
        claim = await self.db[lifecycle.EXECUTIONS].find_one({"order_number": "order-1"})
        if status >= 500:
            self.assertEqual(claim["state"], "uncertain")
            with self.assertRaises(HTTPException) as blocked:
                await self.hold()
            self.assertEqual(blocked.exception.detail["code"], "fulfillment_execution_in_flight")
            with self.assertRaises(HTTPException) as blocked:
                await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
                    context=self.context, payload=await self.payload(reason="Attempt resume after failure"))
            self.assertEqual(blocked.exception.detail["code"], "fulfillment_execution_in_flight")
        else:
            self.assertEqual(claim["state"], "rejected")
            self.assertTrue((await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
                context=self.context, payload=await self.payload(reason="Resume after business rejection")))["ok"])

    async def test_http_500_worker_failure_keeps_uncertain_claim(self):
        await self.execution_http_failure(500)

    async def test_http_503_worker_failure_keeps_uncertain_claim(self):
        await self.execution_http_failure(503)

    async def test_http_400_worker_failure_clears_execution_exclusion(self):
        await self.execution_http_failure(400)

    async def test_source_reconciliation_flags_block_execution_but_not_canonical_capture(self):
        from fulfillment_v2_routes import persist_component_source_snapshot
        source_query = {"user_id": "owner", "order_number": "order-1"}
        scenarios = [("watermark", {"cancelled": True}), ("watermark", {"component_pending": True}),
            ("watermark", {"requires_authoritative_refresh": True}), ("component", {"cancelled": True}),
            *(('component', {"state": state}) for state in ("blocked", "cancelled", "reconciliation_required")),
            ("source", {"order_status_slug": "canceled"})]
        for kind, fields in scenarios:
            with self.subTest(kind=kind, fields=fields):
                source = {**source_query, "g47_salla_snapshot": fields if kind == "watermark" else {}}
                if kind == "source":
                    source.update(fields)
                await self.db.unified_orders.replace_one(source_query, source, upsert=True)
                await self.db.mezan_component_order_lifecycle_v1.replace_one(source_query,
                    {**source_query, **(fields if kind == "component" else {})}, upsert=True)
                with self.assertRaises(HTTPException) as blocked:
                    async with lifecycle.execution_scope(self.db, user_id="owner",
                        targets=[{"order_number": "order-1"}], operation="synthetic-source-gated-callback"):
                        self.fail("Source-gated callback executed")
                self.assertEqual(blocked.exception.detail["code"], "fulfillment_source_reconciliation_required")
                self.assertEqual(await self.db[lifecycle.EXECUTIONS].count_documents({"state": "active"}), 0)
        # Capture must still commit authoritative facts while fulfillment waits.
        async def persist(scoped):
            await scoped.unified_orders.update_one(source_query, {"$set": {"order_status_slug": "cancelled"}})
            return {"synced": True}
        captured = await persist_component_source_snapshot(self.db, user_id="owner", order_number="order-1",
            payload={"id": 901, "status": {"slug": "cancelled"}, "updated_at": "2026-10-06T18:00:00+00:00"},
            persist=persist, authoritative_refresh=True)
        self.assertTrue(captured["synced"])
        snapshot = await self.db.unified_orders.find_one(source_query)
        self.assertTrue(snapshot["g47_salla_snapshot"]["cancelled"])
        self.assertEqual(await self.db[lifecycle.EXECUTIONS].count_documents({"state": "active"}), 0)

    async def test_tracking_only_actor_gets_adapter_capabilities_without_direct_control_access(self):
        body = await self.route_body()
        self.actor = {"id": "tracking-only", "role": "customer_service", "name": "Synthetic tracking employee"}
        self.context = {"merchant_id": "owner", "actor_id": "tracking-only", "is_owner": False, "permissions": set()}
        response = await self.client.get("/order-tracking-notes/orders/order-1/control-capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["generation"]), 64)
        self.assertEqual(response.json()["control_contract"], "tracking_instruction_adapter")
        self.assertTrue(response.json()["actions"]["hold_order"]["allowed"])
        direct_read = await self.client.get("/order-change-controls-v1/orders/order-1/capabilities")
        self.assertEqual(direct_read.status_code, 403, direct_read.text)
        direct_write = await self.client.post("/order-change-controls-v1/orders/order-1/holds", json=body)
        self.assertEqual(direct_write.status_code, 403, direct_write.text)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_mixed_physical_operational_unit_identity_dedup_preserves_piece_history(self):
        await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {"operational_items": [{
            "operational_item_id": "card-for-piece-1", "source_order_item_id": "item-1",
            "name": "Synthetic card", "generation": 2, "revision": 1, "active": True, "current": True}]}})
        held = await self.hold()
        self.assertEqual(set(held["hold"]["piece_ids"]), {"piece-1", "piece-2", "card-for-piece-1"})
        self.assertEqual(len(held["hold"]["before_states"]), 3)
        units = held["change_event"]["old_units"]
        self.assertEqual(len(units), 2)
        self.assertEqual(len({(row["order_item_id"], row["unit_index"], row["generation"]) for row in units}), 2)
        # A higher physical generation obsoletes the old physical unit, but the
        # independent operational card must not inherit that obsolete decision.
        await self.db[lifecycle.PIECES].insert_one({"piece_id": "new-physical", "user_id": "owner",
            "order_number": "order-1", "order_item_id": "item-1", "unit_index": 1,
            "generation": 3, "revision": 1, "status": "in_progress", "active": True, "current": True})
        with self.assertRaises(HTTPException) as old:
            await lifecycle.assert_piece_current(self.db, user_id="owner", piece_id="piece-1")
        self.assertEqual(old.exception.detail["code"], "fulfillment_piece_generation_conflict")
        card = await lifecycle.assert_piece_current(self.db, user_id="owner", piece_id="card-for-piece-1")
        self.assertEqual(card["virtual_kind"], "operational")
        self.assertEqual(card["generation"], 2)


if __name__ == "__main__":
    unittest.main()
