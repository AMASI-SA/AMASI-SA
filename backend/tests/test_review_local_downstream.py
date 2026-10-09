"""Synthetic downstream contracts on explicitly configured loopback Mongo.

Only canonical order reads and provider transport are doubled. Durable review
proof, allocations, transitions and route-history writes use real collections.
"""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import preparation_piece_operations as pieces
import preparation_route_history as routes
import review_local_policy as local
import supplier_dispatch_waiting_policy as waiting
from order_engine import service as order_service
from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO


WHEN = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
OWNER = "synthetic-local-owner"
ACTOR = {"id": OWNER, "role": "owner", "name": "Synthetic operator"}


def order(number="A", *, status="under_review", paid=True, quantity=2):
    return OrderDTO(
        order_id="provider-" + number, order_number=number, created_at=WHEN,
        source=OrderSourceDTO(source_order_id="provider-" + number),
        status=status, status_native=status,
        payment=PaymentDTO(method="cod") if paid else PaymentDTO(method="card", status="pending"),
        items=[OrderItemDTO(order_item_id="line", product_id="product", name="Synthetic product",
                            quantity=quantity)],
    )


class LocalPolicySafetyTests(unittest.TestCase):
    def test_only_missing_or_exact_local_contract_modes_are_known(self):
        self.assertTrue(local.is_known_review_mode(None))
        self.assertTrue(local.is_known_review_mode(local.LOCAL_COMPLETION_MODE))
        for mode in ("", "unknown", "salla_reviewed_v1", 1, {}):
            with self.subTest(mode=mode):
                self.assertFalse(local.is_known_review_mode(mode))

    def test_local_stage_keeps_current_order_and_payment_guards(self):
        workflow = {"stage": "reviewed"}
        self.assertTrue(local.local_review_stage_eligible(order(), workflow))
        self.assertFalse(local.local_review_stage_eligible(order(paid=False), workflow))
        for status in ("cancelled", "refunded", "on_hold", "delivered", "shipping", "mystery", ""):
            with self.subTest(status=status):
                self.assertFalse(local.local_review_stage_eligible(order(status=status), workflow))
        conflict = order().model_copy(update={"status": "cancelled", "status_native": "reviewed"})
        self.assertFalse(local.local_review_stage_eligible(conflict, workflow))
        self.assertFalse(local.local_review_stage_eligible(order(), None))
        self.assertFalse(local.local_review_stage_eligible(order(), {"stage": "completed"}))

    def test_supported_pending_and_preparation_native_aliases(self):
        workflow = {"stage": "reviewed"}
        for native in ("under_review", "waiting_review", "pending_review", "بانتظار المراجعة",
                       "بإنتظار المراجعة", "تمت المراجعة", "reviewed", "processing", "in_progress", "قيد التنفيذ"):
            with self.subTest(native=native):
                current = order().model_copy(update={"status_native": native})
                self.assertTrue(local.local_review_stage_eligible(current, workflow))


class LocalDownstreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["local_review_downstream_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))

    async def asyncTearDown(self):
        if hasattr(self, "mongo"):
            self.assertTrue(self.db.name.startswith("local_review_downstream_"))
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    async def seed(self, number="A", *, stage="reviewed", mode=local.LOCAL_COMPLETION_MODE):
        workflow = {
            "user_id": OWNER, "order_number": number, "stage": stage, "revision": 1,
            "completion_mode": mode, "review_completion_operation_id": "operation-" + number,
            "items": [], "operational_items": [], "preparation_piece_count": 2,
        }
        await self.db[local.WORKFLOWS].insert_one(dict(workflow))
        await self.db[local.OPERATIONS].insert_one({
            "_id": "operation-" + number, "user_id": OWNER, "order_number": number,
            "state": "completed", "completion_mode": mode,
        })
        return workflow

    async def allocate(self, number="A", count=2):
        await self.db[pieces.PREPARATION_UNIT_ALLOCATIONS].insert_many([
            {"user_id": OWNER, "order_number": number, "order_item_id": "line",
             "unit_index": i, "status": "committed"} for i in range(1, count + 1)
        ])

    async def test_proof_requires_exact_tenant_order_mode_completed_operation(self):
        mutations = {
            "other-tenant": {"user_id": "other"},
            "other-order": {"order_number": "other"},
            "unfinished": {"state": "provider_confirmed"},
            "legacy-mode": {"completion_mode": "salla_reviewed_v1"},
            "superseded": {"superseded_by": "next-operation"},
        }
        await self.seed("valid")
        for number, mutation in mutations.items():
            await self.seed(number)
            await self.db[local.OPERATIONS].update_one({"_id": "operation-" + number}, {"$set": mutation})
        await self.seed("missing-operation")
        await self.db[local.OPERATIONS].delete_one({"_id": "operation-missing-operation"})
        await self.seed("wrong-workflow-mode", mode="salla_reviewed_v1")
        numbers = ["valid", *mutations, "missing-operation", "wrong-workflow-mode"]
        loaded = await local.load_local_review_workflows(self.db, user_id=OWNER, order_numbers=numbers)
        self.assertEqual(set(loaded), {"valid"})

    async def test_proof_lookup_is_batched_and_read_only(self):
        for index in range(12):
            await self.seed(str(index))
        reads = []
        raw = self.db

        class ReadOnlyDB:
            def __getitem__(self, name):
                def find(query, projection):
                    reads.append((name, query))
                    return raw[name].find(query, projection)
                return SimpleNamespace(find=find)

        loaded = await local.load_local_review_workflows(
            ReadOnlyDB(), user_id=OWNER, order_numbers=[str(i) for i in range(12)],
        )
        self.assertEqual(len(loaded), 12)
        self.assertEqual([name for name, _ in reads], [local.WORKFLOWS, local.OPERATIONS])
        self.assertEqual(len(reads[1][1]["_id"]["$in"]), 12)

    async def test_supplier_pending_needs_local_proof_and_preserves_external_status(self):
        await self.seed("A")
        candidates = [{"piece_id": "a", "order_number": "A"}, {"piece_id": "b", "order_number": "B"}]
        with patch.object(order_service, "get_orders", AsyncMock(return_value={"A": order("A"), "B": order("B")})):
            result = await waiting.annotate_waiting_pieces(self.db, user_id=OWNER, pieces=candidates)
            self.assertEqual([row["waiting_review_eligible"] for row in result], [True, False])
            self.assertEqual(result[0]["order_status"], "under_review")
            await waiting.require_current_under_review(self.db, user_id=OWNER, pieces=candidates[:1])
            with self.assertRaises(HTTPException):
                await waiting.require_current_under_review(self.db, user_id=OWNER, pieces=candidates)
        with patch.object(order_service, "get_orders", AsyncMock(return_value={"A": order(status="cancelled")})):
            with self.assertRaises(HTTPException):
                await waiting.require_current_under_review(self.db, user_id=OWNER, pieces=candidates[:1])
        self.assertTrue(waiting.order_waiting_fields(order(status="reviewed"))["waiting_review_eligible"])
        self.assertFalse(waiting.order_waiting_fields(order())["waiting_review_eligible"])

    async def test_unknown_and_unproven_local_modes_cannot_fall_back_to_external_reviewed(self):
        await self.seed("unknown", mode="future")
        await self.seed("broken")
        await self.db[local.OPERATIONS].delete_one({"_id": "operation-broken"})
        await self.seed("legacy", mode=None)
        candidates = [{"piece_id": number, "order_number": number} for number in ("unknown", "broken", "legacy")]
        orders = {number: order(number, status="reviewed") for number in ("unknown", "broken", "legacy")}
        with patch.object(order_service, "get_orders", AsyncMock(return_value=orders)):
            result = await waiting.annotate_waiting_pieces(self.db, user_id=OWNER, pieces=candidates)
        self.assertEqual([row["waiting_review_eligible"] for row in result], [False, False, True])
        for number in ("unknown", "broken", "legacy"):
            await self.db.unified_orders.insert_one({"user_id": OWNER, "order_number": number, "order_status": "reviewed"})
            await self.db[routes.PIECES].insert_one({"user_id": OWNER, "piece_id": number, "order_number": number,
                                                    "responsible_employee_id": OWNER, "status": "assigned"})
        with patch.object(order_service, "get_orders", AsyncMock(return_value=orders)):
            eligible, outside = await routes.reconcile_employee_workspace_route(
                self.db, user_id=OWNER, employee_id=OWNER, pieces=candidates,
            )
        self.assertEqual([row["order_number"] for row in eligible], ["legacy"])
        self.assertEqual({row["order_number"] for row in outside}, {"unknown", "broken"})

    async def test_employee_route_uses_proof_and_still_observes_cancellation(self):
        await self.seed()
        await self.db.unified_orders.insert_one({"user_id": OWNER, "order_number": "A", "order_status": "under_review"})
        piece = {"user_id": OWNER, "piece_id": "piece", "order_number": "A",
                 "responsible_employee_id": OWNER, "status": "assigned"}
        await self.db[routes.PIECES].insert_one(dict(piece))
        with patch.object(order_service, "get_orders", AsyncMock(return_value={"A": order()})):
            eligible, outside = await routes.reconcile_employee_workspace_route(
                self.db, user_id=OWNER, employee_id=OWNER, pieces=[piece],
            )
        self.assertEqual((len(eligible), len(outside)), (1, 0))
        event = await self.db[routes.ROUTE_EVENTS].find_one({})
        self.assertEqual(event["reason"], "local_review_eligible_for_employee_preparation")
        self.assertEqual(event["order_status"], "under_review")
        await self.db.unified_orders.update_one({"order_number": "A"}, {"$set": {"order_status": "cancelled"}})
        with patch.object(order_service, "get_orders", AsyncMock(return_value={"A": order(status="cancelled")})):
            eligible, outside = await routes.reconcile_employee_workspace_route(
                self.db, user_id=OWNER, employee_id=OWNER, pieces=[piece],
            )
        self.assertEqual((len(eligible), len(outside)), (0, 1))
        stored = await self.db[routes.PIECES].find_one({"piece_id": "piece"})
        self.assertTrue(stored["outside_preparation"])
        self.assertEqual(stored["status"], "assigned")

    async def reconcile(self, workflow, *, current=None):
        with patch.object(pieces, "load_reviewed_product_context", AsyncMock(return_value={
            "pairs": [(current or order(workflow["order_number"]), workflow)],
        })):
            return await pieces._assigned_reconcile_order_stage(
                self.db, user_id=OWNER, order_number=workflow["order_number"], batch_id="batch", actor=ACTOR,
            )

    async def test_partial_then_full_allocation_advances_locally_without_provider_io(self):
        workflow = await self.seed()
        await self.allocate(count=1)
        forbidden = AsyncMock(side_effect=AssertionError("local allocation must not call Salla"))
        with patch.object(pieces, "_sync_salla_in_progress", forbidden), patch.object(pieces, "call_salla", forbidden):
            self.assertEqual(await self.reconcile(workflow), (False, 1))
            stored = await self.db[local.WORKFLOWS].find_one({"order_number": "A"})
            self.assertEqual(stored["stage"], "reviewed")
            await self.db[pieces.PREPARATION_UNIT_ALLOCATIONS].insert_one({
                "user_id": OWNER, "order_number": "A", "order_item_id": "line", "unit_index": 2, "status": "committed",
            })
            self.assertEqual(await self.reconcile(stored), (True, 0))
        forbidden.assert_not_awaited()
        stored = await self.db[local.WORKFLOWS].find_one({"order_number": "A"})
        self.assertEqual(stored["stage"], "in_progress")
        self.assertEqual(stored["review_completion_operation_id"], "operation-A")
        self.assertNotIn("salla_status_synced_at", stored)
        event = await self.db[pieces.EVENTS].find_one({"event_type": "order_moved_to_in_progress"})
        self.assertTrue(event["mezan_only"])
        self.assertFalse(event["salla_updated"])

    async def test_invalid_local_contract_never_falls_back_to_provider_sync(self):
        workflow = await self.seed()
        await self.allocate()
        await self.db[local.OPERATIONS].update_one({"_id": "operation-A"}, {"$set": {"state": "syncing"}})
        provider = AsyncMock(side_effect=AssertionError("invalid local proof must not call Salla"))
        with patch.object(pieces, "_sync_salla_in_progress", provider):
            with self.assertRaises(HTTPException) as failure:
                await self.reconcile(workflow)
        self.assertEqual(failure.exception.detail["code"], "local_review_preparation_not_eligible")
        provider.assert_not_awaited()
        self.assertEqual((await self.db[local.WORKFLOWS].find_one({"order_number": "A"}))["stage"], "reviewed")

    async def test_legacy_allocation_retains_provider_contract(self):
        workflow = await self.seed(mode=None)
        await self.allocate()
        provider = AsyncMock(return_value=("sent", None))
        with patch.object(pieces, "_sync_salla_in_progress", provider):
            self.assertEqual(await self.reconcile(workflow, current=order(status="reviewed")), (True, 0))
        provider.assert_awaited_once()
        stored = await self.db[local.WORKFLOWS].find_one({"order_number": "A"})
        self.assertEqual(stored["salla_status_sync_state"], "sent")

    async def test_unknown_allocation_contract_never_calls_provider(self):
        workflow = await self.seed(mode="future")
        await self.allocate()
        provider = AsyncMock(side_effect=AssertionError("unknown mode must not call Salla"))
        with patch.object(pieces, "_sync_salla_in_progress", provider):
            with self.assertRaises(HTTPException) as failure:
                await self.reconcile(workflow, current=order(status="reviewed"))
        self.assertEqual(failure.exception.detail["code"], "review_completion_mode_unknown")
        provider.assert_not_awaited()

    async def test_start_keeps_local_in_progress_and_piece_execution_semantics(self):
        await self.seed(stage="in_progress")
        registry = {"user_id": OWNER, "file_number": "PF-SYN", "batch_id": "batch",
                    "status": "ready", "execution_status": "assigned", "responsible_employee_id": OWNER}
        await self.db[pieces.REGISTRY].insert_one(dict(registry))
        await self.db[pieces.BATCHES].insert_one({"user_id": OWNER, "id": "batch"})
        await self.db[pieces.PIECES].insert_one({
            "user_id": OWNER, "piece_id": "one", "order_number": "A", "order_item_id": "line",
            "batch_id": "batch", "status": pieces.PIECE_STATUS_ASSIGNED,
        })
        forbidden = AsyncMock(side_effect=AssertionError("starting local work must not call Salla"))
        with patch.object(pieces, "call_salla", forbidden), \
             patch.object(pieces, "enforce_stage_instructions", AsyncMock()):
            result = await pieces._start_file_execution(
                self.db, user_id=OWNER, registry=registry, actor=ACTOR, note=None,
            )
        forbidden.assert_not_awaited()
        self.assertEqual(result["execution_status"], "in_progress")
        self.assertEqual((await self.db[pieces.PIECES].find_one({"piece_id": "one"}))["status"], pieces.PIECE_STATUS_IN_PROGRESS)
        self.assertEqual((await self.db[local.WORKFLOWS].find_one({"order_number": "A"}))["stage"], "in_progress")
        event = await self.db[pieces.PIECE_EVENTS].find_one({"event_type": "preparation_file_started"})
        self.assertFalse(event["salla_updated"])

    async def test_assembly_boards_use_local_stage_and_keep_legacy_membership(self):
        stages = {"waiting": "reviewed", "working": "in_progress", "received": "ready_to_ship", "finished": "completed"}
        for number, stage in stages.items():
            await self.seed(number, stage=stage)
        await self.seed("legacy", stage="in_progress", mode=None)
        await self.seed("unknown", stage="in_progress", mode="future")
        await self.seed("broken", stage="in_progress")
        await self.db[local.OPERATIONS].delete_one({"_id": "operation-broken"})
        for number in [*stages, "legacy", "broken", "unknown"]:
            await self.db[pieces.PIECES].insert_one({"user_id": OWNER, "order_number": number, "piece_id": number})

        async def current(_repository, *, user_id, order_number):
            return order(order_number, status="in_progress" if order_number == "unknown" else "under_review")

        with patch.object(pieces, "get_order", side_effect=current):
            progress = await pieces._assembly_order_board(self.db, user_id=OWNER, state="in_progress", limit=50, offset=0)
            completed = await pieces._assembly_order_board(self.db, user_id=OWNER, state="completed", limit=50, offset=0)
        self.assertEqual({row["order_number"] for row in progress["items"]}, {"working", "received"})
        self.assertEqual({row["order_number"] for row in completed["items"]}, {"finished"})
        self.assertTrue(all(row["order_status"] == "under_review" for row in progress["items"]))
        with patch.object(pieces, "get_order", AsyncMock(return_value=order(status="cancelled"))):
            cancelled = await pieces._assembly_order_board(self.db, user_id=OWNER, state="in_progress", limit=50, offset=0)
        self.assertEqual(cancelled["items"], [])

    async def test_received_pieces_advance_to_ready_to_ship_only_when_all_received(self):
        await self.seed(stage="in_progress")
        first = {"user_id": OWNER, "piece_id": "one", "order_number": "A",
                 "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY}
        second = {**first, "piece_id": "two", "status": pieces.PIECE_STATUS_IN_PROGRESS}
        await self.db[pieces.PIECES].insert_many([dict(first), dict(second)])
        partial = await pieces._refresh_preparation_receipt_progress(
            self.db, user_id=OWNER, piece=first, actor_id=OWNER, actor_name=ACTOR["name"], now=WHEN,
        )
        self.assertFalse(partial["order_ready_for_assembly"])
        self.assertEqual((await self.db[local.WORKFLOWS].find_one({"order_number": "A"}))["stage"], "in_progress")
        await self.db[pieces.PIECES].update_one({"piece_id": "two"}, {"$set": {"status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY}})
        complete = await pieces._refresh_preparation_receipt_progress(
            self.db, user_id=OWNER, piece=first, actor_id=OWNER, actor_name=ACTOR["name"], now=WHEN,
        )
        self.assertTrue(complete["order_ready_for_assembly"])
        self.assertEqual((await self.db[local.WORKFLOWS].find_one({"order_number": "A"}))["stage"], "ready_to_ship")
