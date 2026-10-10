"""Local Review through assembly, using real Mongo/fulfillment/component writes."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
import order_review_completion as completion
import order_review_routes as review
import preparation_piece_operations as pieces
import reviewed_products_catalog as catalog
import reviewed_preparation_batches as batches
import preparation_file_registry as registry
import mobile_reviewed_preparation_routes as mobile_assignment
import test_g47_component_lifecycle_integration as fixture
from review_local_policy import LOCAL_COMPLETION_MODE, load_local_assignment_workflows
from order_engine.models import PaymentDTO
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from stock_component_consumption_service import LOCATIONS, UNITS, PLANS


class LocalAssemblyTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    mark_piece = fixture.ComponentRouteTests.mark_piece
    on_hand = fixture.ComponentRouteTests.on_hand

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        self.actor["email"] = "operator@example.test"
        self.context["permissions"].add("fulfillment.ready.read")
        await self.db[LOCATIONS].insert_one({
            "id": "base-products", "user_id": "owner", "warehouse_id": "wh", "state": "occupied",
            "occupancy": {"total_quantity": 10, "items": [{
                "product_id": "mp", "sku": "SYN-P", "quantity": 10,
                "source_type": "purchase_invoice", "preparation_state": "requires_preparation",
            }]},
        })
        self.assignment_status = "pending_review"
        self.assignment_posts = 0
        self.assignment_reads = 0
        self.assignment_uncertain = False
        self.external = AsyncMock(side_effect=AssertionError("local review/assembly called Salla"))
        for replacement in (
            patch.object(review, "call_salla", self.external),
            patch.object(review, "_sync_salla_reviewed", self.external),
            patch.object(review, "refresh_order_from_salla", self.external),
            patch.object(pieces, "call_salla", self.external),
            patch.object(batches, "refresh_order_from_salla", self.external),
        ):
            replacement.start()
            self.patches.append(replacement)

    async def complete_review(self, *, mixed=False, operational=False, direct=True):
        payload = self.source_payload(number="local-assembly")
        if mixed:
            payload["items"].append({**payload["items"][0], "id": "supplier-line"})
        self.assertTrue((await self.webhook(payload))["synced"])
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        self.direct_line_id = current.items[0].order_item_id
        self.supplier_line_id = current.items[1].order_item_id if mixed else None
        await self.db[completion.WORKFLOWS].update_one(
            {"user_id": "owner", "order_number": "local-assembly"},
            {"$set": {
                "stage": "pending_review", "revision": 0,
                "items": [{"order_item_id": self.direct_line_id, "preparation_route": "direct_assembly",
                           "product_id": "p", "quantity": 2}] if direct else [],
                "operational_items": ([{"operational_item_id": "operational-piece", "name": "Synthetic note",
                                        "blocks_order_completion": True}] if operational else []),
            }}, upsert=True,
        )
        response = await self.client.post(
            "/order-reviews-v1/local-assembly/complete", json={"expected_revision": 0},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["completion_mode"], LOCAL_COMPLETION_MODE)
        self.assertEqual(response.json()["stage"], "reviewed")
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        workflow = await self.workflow()
        self.assertEqual(workflow["stage"], "reviewed")
        self.external.assert_not_awaited()
        self.ids = workflow["items"][0].get("direct_assembly_piece_ids") or []
        self.assertEqual(len(self.ids), 2 if direct else 0)
        # Assembly now requires positive current Salla in_progress evidence.
        # This fixture models the provider update after review, not a write
        # performed by local review itself.
        await self.db.unified_orders.update_one(
            {"user_id": "owner", "order_number": "local-assembly"}, {"$set": {
                "order_status": "in_progress",
                "order_status_slug": "in_progress",
                "raw_by_source.salla_direct.status": {"slug": "in_progress", "name": "in_progress"},
            }})
        return workflow

    async def workflow(self):
        return await self.db[completion.WORKFLOWS].find_one({"user_id": "owner", "order_number": "local-assembly"})

    def mount_assignment(self):
        # Use the same materialisation hook as server startup, restored after
        # each test so focused suites do not leak monkeypatch state.
        for module, name in ((pieces, "_INSTALLED"), (pieces, "_ORIGINAL_FINALIZE"),
                             (pieces, "_ORIGINAL_RECONCILE_ORDER_STAGE"),
                             (registry, "_finalize_registry_row"), (batches, "_reconcile_order_stage")):
            replacement = patch.object(module, name, getattr(module, name))
            replacement.start()
            self.patches.append(replacement)
        pieces.install_preparation_piece_operations()
        async def actor():
            return self.actor
        self.app.include_router(catalog.make_reviewed_products_catalog_router(self.db, actor))
        self.app.include_router(batches.make_reviewed_preparation_batches_router(self.db, actor))
        self.app.include_router(registry.make_preparation_file_registry_router(self.db, actor))
        self.app.include_router(mobile_assignment.make_mobile_reviewed_preparation_router(self.db, actor))

    async def catalog_cards(self):
        response = await self.client.get("/reviewed-products-v1/catalog")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["products"]

    async def assignment_transport(self, db, user_id, method, path, **kwargs):
        # Only the assignment reconciliation seam may call this fake provider.
        self.assertEqual(user_id, "owner")
        if method == "GET" and path == "/orders/statuses":
            return {"data": [{"id": 7, "name": "قيد التنفيذ"}]}
        if method == "POST" and path.endswith("/status"):
            self.assignment_posts += 1
            if self.assignment_uncertain:
                raise TimeoutError("synthetic uncertain assignment update")
            self.assignment_status = "in_progress"
            return {"success": True}
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/orders/"))
        self.assignment_reads += 1
        return {"data": {"status": {"slug": self.assignment_status}}}

    async def assign_cards(self, cards, request_id, *, mobile=False):
        selections = [{"group_key": row["group_key"], "quantity": 1} for row in cards]
        if mobile:
            return await self.client.post("/mobile-reviewed-preparation-v1/files", json={
                "client_request_id": request_id, "file_title": "Synthetic mixed",
                "responsible_employee_id": "owner", "selections": selections,
                "required_completion_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            })
        draft = await self.client.post("/preparation-file-registry-v1/drafts", json={
            "client_request_id": request_id, "file_title": "Synthetic mixed",
            "responsible_employee_id": "owner", "expected_quantity": len(cards),
            "selected_product_count": len(cards),
        })
        self.assertEqual(draft.status_code, 200, draft.text)
        # Other suites reuse this fixture method without inheriting the class.
        transport = LocalAssemblyTests.assignment_transport.__get__(self)
        with patch.object(pieces, "call_salla", side_effect=transport):
            return await self.client.post("/reviewed-preparation-batches-v1/batches", json={
                "client_request_id": request_id, "selections": [
                    {**selection, "revision": row["revision"]} for row, selection in zip(cards, selections)
                ],
            })

    async def mark(self, piece_id, *, status=200):
        response = await self.mark_piece(piece_id)
        self.assertEqual(response.status_code, status, response.text)
        self.external.assert_not_awaited()
        return response.json()

    async def set_address(self, *, complete):
        address = {"city": "Synthetic city"}
        if complete:
            address["street"] = "Synthetic street"
        await self.db.unified_orders.update_one(
            {"user_id": "owner", "order_number": "local-assembly"},
            {"$set": {"raw_by_source.salla_direct.shipping_address": address}},
        )

    async def test_real_local_review_direct_assembly_starts_then_completes(self):
        await self.complete_review()
        board = await self.client.get("/preparation-work-v1/assembly/orders?state=in_progress")
        self.assertEqual(board.status_code, 200, board.text)
        self.assertEqual([row["order_number"] for row in board.json()["items"]], ["local-assembly"])
        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(all(row["can_mark_ready"] for row in search.json()["pieces"]))
        first = await self.mark(self.ids[0])
        self.assertFalse(first["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.on_hand(), 18)
        final = await self.mark(self.ids[1])
        self.assertTrue(final["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "completed")
        self.assertEqual(await self.on_hand(), 16)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 2)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)

    async def test_missing_address_retains_ready_work_and_retry_finishes_once(self):
        await self.complete_review()
        await self.set_address(complete=False)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
            self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(all(row["assembly_ready"] and row["can_mark_ready"] for row in search.json()["pieces"]))
        await self.set_address(complete=True)
        retried = await self.mark(self.ids[-1])
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["progress"]["order_completed"])
        await self.mark(self.ids[-1])
        self.assertEqual(await self.on_hand(), 16)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 2)

    async def test_mixed_order_requires_every_supplier_unit_before_completion(self):
        await self.complete_review(mixed=True)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
        self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        # A partial receipt must not turn absence of the other supplier unit
        # into evidence that the entire order finished preparation.
        supplier_piece = {
            "piece_id": "supplier-1", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.supplier_line_id, "unit_index": 1,
            "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "assembly_status": "pending",
            "supplier_dispatch_status": "received", "preparation_receipt_status": "received",
        }
        await self.db[pieces.PIECES].insert_one(dict(supplier_piece))
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"stage": "ready_to_ship"}})
        result = await self.mark("supplier-1")
        self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.db[pieces.PIECES].insert_one({**supplier_piece, "piece_id": "supplier-2", "unit_index": 2})
        result = await self.mark("supplier-2")
        self.assertTrue(result["progress"]["order_completed"])
        self.assertEqual(await self.on_hand(), 12)

    async def test_mixed_unassigned_units_survive_direct_start_and_supplier_assignment(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        before = await self.client.get("/reviewed-products-v1/catalog")
        self.assertEqual(len(before.json()["products"]), 4)
        await self.mark(self.ids[0])
        started = await self.workflow()
        self.assertEqual(started["stage"], "in_progress")
        stock = await self.on_hand()
        after = await self.client.get("/reviewed-products-v1/catalog")
        cards = after.json()["products"]
        self.assertEqual(len(cards), 3, after.text)
        self.assertEqual(await self.on_hand(), stock)
        self.assertEqual(await self.workflow(), started)
        self.assertEqual((await self.client.get("/reviewed-products-v1/catalog")).json(), after.json())
        supplier = [row for row in cards if row["source_lines"][0]["order_item_id"] == self.supplier_line_id]
        self.assertEqual(len(supplier), 2)
        self.assertEqual(len({row["group_key"] for row in supplier}), 2)
        response = await self.assign_cards(supplier[:1], "mixed-after-direct-start")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        remaining = (await self.client.get("/reviewed-products-v1/catalog")).json()["products"]
        self.assertEqual(len(remaining), 2)
        self.assertNotIn(supplier[0]["group_key"], {row["group_key"] for row in remaining})
        replay = await self.assign_cards(supplier[:1], "mixed-after-direct-start")
        self.assertEqual(replay.status_code, 200, replay.text)
        duplicate = await self.assign_cards(supplier[:1], "mixed-duplicate-request")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 1)
        next_assignment = await self.assign_cards(supplier[1:], "mixed-second-supplier")
        self.assertEqual(next_assignment.status_code, 200, next_assignment.text)
        self.assertEqual(len(await self.catalog_cards()), 1)
        self.assertEqual((await self.workflow())["items"], started["items"])
        # Allocation is not receipt/custody and cannot make assembly ready.
        for physical in await self.db[pieces.PIECES].find({}).to_list(10):
            await self.mark(physical["piece_id"], status=409)
        self.assertEqual(await self.on_hand(), stock)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.external.assert_not_awaited()

    async def test_mixed_mobile_assignment_retains_in_progress_and_assignment_metadata(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        before = await self.workflow()
        supplier = [row for row in await self.catalog_cards()
                    if row["source_lines"][0]["order_item_id"] == self.supplier_line_id]
        response = await self.assign_cards(supplier, "mixed-mobile-assignment", mobile=True)
        self.assertEqual(response.status_code, 200, response.text)
        workflow = await self.workflow()
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(workflow["items"], before["items"])
        self.assertEqual(len(workflow["preparation_assignments"]), 1)
        self.assertEqual(len(workflow["preparation_batch_ids"]), 1)
        self.assertEqual(workflow["revision"], before["revision"] + 1)
        self.assertEqual(len(await self.catalog_cards()), 1)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 2)
        self.external.assert_not_awaited()

    async def ready_for_receipt(self, piece):
        # A synthetic completed supplier/preparation stage; the receiving API,
        # its guards, custody, events and Mongo writes are exercised normally.
        await self.db[pieces.PIECES].update_one({"piece_id": piece["piece_id"]}, {"$set": {
            "status": pieces.PIECE_STATUS_READY_FOR_RECEIPT,
            "supplier_dispatch_status": "received",
            "services": [{**service, "status": "completed"} for service in piece.get("services", [])],
        }})

    async def receive(self, piece):
        return await self.client.post(
            f"/preparation-work-v1/receiving/pieces/{piece['piece_id']}/receive",
            json={"client_request_id": "receive-" + piece["piece_id"]},
        )

    async def test_partial_receipt_preserves_mixed_remaining_units_then_completes(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        supplier = [r for r in await self.catalog_cards()
                    if r["source_lines"][0]["order_item_id"] == self.supplier_line_id]
        self.assertEqual((await self.assign_cards(supplier[:1], "partial-receipt-first")).status_code, 200)
        first = await self.db[pieces.PIECES].find_one({})
        await self.ready_for_receipt(first)
        stock = await self.on_hand()
        received = await self.receive(first)
        self.assertEqual(received.status_code, 200, received.text)
        self.assertTrue(received.json()["progress"]["file_completed"])
        self.assertFalse(received.json()["progress"]["order_ready_for_assembly"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        remaining = await self.catalog_cards()
        self.assertEqual(len(remaining), 2)
        self.assertEqual(await self.catalog_cards(), remaining)
        self.assertEqual(await self.on_hand(), stock)
        replay = await self.receive(first)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent"])
        self.assertEqual((await self.assign_cards(supplier[1:], "partial-receipt-second")).status_code, 200)
        second = await self.db[pieces.PIECES].find_one({"piece_id": {"$ne": first["piece_id"]}})
        await self.ready_for_receipt(second)
        received = await self.receive(second)
        self.assertEqual(received.status_code, 200, received.text)
        self.assertFalse(received.json()["progress"]["order_ready_for_assembly"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        # Supplier receipt alone cannot stand in for the remaining direct work.
        await self.mark(self.ids[1])
        self.assertEqual((await self.workflow())["stage"], "ready_to_ship")
        self.assertEqual(await self.catalog_cards(), [])
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.mark(first["piece_id"])
        done = await self.mark(second["piece_id"])
        self.assertTrue(done["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "completed")
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 2)
        self.external.assert_not_awaited()

    async def test_direct_finished_before_partial_supplier_receipts(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        for identity in self.ids:
            await self.mark(identity)
        cards = await self.catalog_cards()
        for index, card in enumerate(cards):
            self.assertEqual((await self.assign_cards([card], f"direct-first-receipt-{index}")).status_code, 200)
            piece = await self.db[pieces.PIECES].find_one({"unit_index": index + 1})
            await self.ready_for_receipt(piece)
            received = await self.receive(piece)
            self.assertEqual(received.status_code, 200, received.text)
            self.assertEqual((await self.workflow())["stage"], "ready_to_ship" if index == 1 else "in_progress")
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)

    async def test_supplier_only_partial_quantity_receipt_and_duplicate(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        cards = await self.catalog_cards()
        for index, card in enumerate(cards):
            self.assertEqual((await self.assign_cards([card], f"supplier-receipt-{index}")).status_code, 200)
            piece = await self.db[pieces.PIECES].find_one({"unit_index": index + 1})
            await self.ready_for_receipt(piece)
            received = await self.receive(piece)
            self.assertEqual(received.status_code, 200, received.text)
            self.assertEqual(received.json()["progress"]["order_ready_for_assembly"], index == 1)
            self.assertEqual((await self.workflow())["stage"], "ready_to_ship" if index == 1 else "in_progress")
            self.assertEqual(len(await self.catalog_cards()), 0 if index == 1 else 1)
            self.assertEqual((await self.receive(piece)).status_code, 200)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 2)

    async def test_receiving_concurrently_with_remaining_assignment(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        supplier = [r for r in await self.catalog_cards()
                    if r["source_lines"][0]["order_item_id"] == self.supplier_line_id]
        self.assertEqual((await self.assign_cards(supplier[:1], "receipt-race-first")).status_code, 200)
        first = await self.db[pieces.PIECES].find_one({})
        await self.ready_for_receipt(first)
        original = pieces._refresh_preparation_receipt_progress
        entered, release = asyncio.Event(), asyncio.Event()
        async def interleave(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
            return await original(*args, **kwargs)
        with patch.object(pieces, "_refresh_preparation_receipt_progress", interleave):
            receiving = asyncio.create_task(self.receive(first))
            await asyncio.wait_for(entered.wait(), 10)
            try:
                assignment = await self.assign_cards(supplier[1:], "receipt-race-second", mobile=True)
            finally:
                release.set()
            received = await receiving
        self.assertEqual(assignment.status_code, 200, assignment.text)
        self.assertEqual(received.status_code, 200, received.text)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}), 2)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 2)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 1)
        self.assertEqual(len(await self.catalog_cards()), 1)

    async def test_concurrent_duplicate_receipt_commits_custody_and_event_once(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        self.assertEqual((await self.assign_cards((await self.catalog_cards())[:1], "double-receipt")).status_code, 200)
        piece = await self.db[pieces.PIECES].find_one({})
        await self.ready_for_receipt(piece)
        responses = await asyncio.gather(self.receive(piece), self.receive(piece))
        self.assertEqual([r.status_code for r in responses], [200, 200], [r.text for r in responses])
        self.assertEqual(sorted(r.json()["idempotent"] for r in responses), [False, True])
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 1)
        saved = await self.db[pieces.PIECES].find_one({})
        self.assertEqual(saved["preparation_employee_custody_status"], "handed_to_branch")
        self.assertEqual(saved["preparation_received_from_employee_id"], "owner")
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(len(await self.catalog_cards()), 1)

    async def test_local_receipt_rechecks_source_components_payment_and_cancellation(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        self.assertEqual((await self.assign_cards((await self.catalog_cards())[:1], "guarded-receipt")).status_code, 200)
        piece = await self.db[pieces.PIECES].find_one({})
        await self.ready_for_receipt(piece)
        before = await self.db[pieces.PIECES].find_one({})
        workflow = await self.workflow()
        mutations = [
            (fulfillment.COMPONENT_LIFECYCLES, {"accepted": False}),
            (fulfillment.COMPONENT_LIFECYCLES, {"generation": "changed"}),
            (fulfillment.COMPONENT_LIFECYCLES, {"retry_required": True}),
            ("unified_orders", {"g47_salla_snapshot.revision": 999}),
            ("unified_orders", {"g47_salla_snapshot.component_pending": True}),
            (completion.OPERATIONS, {"superseded_by": "later"}),
        ]
        for collection, change in mutations:
            with self.subTest(collection=collection, change=change):
                original = await self.db[collection].find_one({})
                await self.db[collection].update_one({"_id": original["_id"]}, {"$set": change})
                try:
                    result = await self.receive(piece)
                    self.assertEqual(result.status_code, 409, result.text)
                    self.assertEqual(await self.db[pieces.PIECES].find_one({}), before)
                    self.assertEqual(await self.workflow(), workflow)
                finally:
                    await self.db[collection].replace_one({"_id": original["_id"]}, original)
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        for order in (current.model_copy(update={"status": "cancelled", "status_native": "cancelled"}),
                      current.model_copy(update={"payment": PaymentDTO(method="card", status="pending", collection_status="unpaid")})):
            with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=order)):
                self.assertEqual((await self.receive(piece)).status_code, 409)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 0)
        self.assertEqual(await self.on_hand(), 20)

    async def test_receipt_transaction_failure_rolls_back_custody_and_progress(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        self.assertEqual((await self.assign_cards((await self.catalog_cards())[:1], "rollback-receipt")).status_code, 200)
        piece = await self.db[pieces.PIECES].find_one({})
        await self.ready_for_receipt(piece)
        before = {name: await self.db[name].find({}).to_list(100) for name in
                  (pieces.PIECES, completion.WORKFLOWS, pieces.REGISTRY, LOCATIONS, "mz2_atomic_owners")}
        await self.db.command({"collMod": pieces.PIECE_EVENTS,
            "validator": {"event_type": {"$ne": "preparation_piece_received_for_assembly"}}, "validationLevel": "strict"})
        result = await self.receive(piece)
        self.assertEqual(result.status_code, 500, result.text)
        for name, documents in before.items():
            self.assertEqual(await self.db[name].find({}).to_list(100), documents, name)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly"}), 0)

    async def test_receipt_waits_for_required_operational_work(self):
        self.mount_assignment()
        await self.complete_review(direct=False, operational=True)
        self.assertEqual((await self.assign_cards(await self.catalog_cards(), "operational-receipt")).status_code, 200)
        for piece in await self.db[pieces.PIECES].find({}).to_list(10):
            await self.ready_for_receipt(piece)
            self.assertEqual((await self.receive(piece)).status_code, 200)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        await self.mark("operational-piece")
        self.assertEqual((await self.workflow())["stage"], "ready_to_ship")
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)

    async def test_all_supplier_partial_then_complete_allocation_keeps_existing_semantics(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        cards = await self.catalog_cards()
        self.assertEqual(len(cards), 2)
        first = await self.assign_cards(cards[:1], "supplier-partial-first")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertEqual(self.assignment_posts, 0)
        remaining = await self.catalog_cards()
        self.assertEqual([row["group_key"] for row in remaining], [cards[1]["group_key"]])
        second = await self.assign_cards(remaining, "supplier-partial-last")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.assignment_posts, 1)
        self.assertGreaterEqual(self.assignment_reads, 2)
        self.assertEqual((await self.workflow())["salla_status_sync_state"], "sent")
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.catalog_cards(), [])
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 2)
        self.assertEqual(await self.on_hand(), 20)
        self.external.assert_not_awaited()

    async def test_mixed_concurrent_supplier_assignment_reserves_each_unit_once(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        supplier = [row for row in await self.catalog_cards()
                    if row["source_lines"][0]["order_item_id"] == self.supplier_line_id][:1]
        # Both requests must pass the same read, then race at the real unique
        # allocation index rather than accidentally exercising two serial reads.
        original = catalog.load_reviewed_product_context
        entered = 0
        release = asyncio.Event()
        async def simultaneous(*args, **kwargs):
            nonlocal entered
            context = await original(*args, **kwargs)
            entered += 1
            if entered == 2:
                release.set()
            await asyncio.wait_for(release.wait(), 10)
            return context
        with patch.object(catalog, "load_reviewed_product_context", simultaneous):
            results = await asyncio.gather(*[
                self.assign_cards(supplier, f"mixed-concurrent-{index}", mobile=True)
                for index in (1, 2)
            ])
        self.assertEqual(sorted(row.status_code for row in results), [200, 409], [row.text for row in results])
        rejected = next(row for row in results if row.status_code == 409)
        self.assertEqual(rejected.json()["detail"]["code"], "preparation_units_already_allocated")
        self.assertEqual(await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 1)
        self.assertEqual(len(await self.catalog_cards()), 2)

    async def test_mixed_catalog_and_assignment_recheck_local_proof_source_and_components(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        cards = await self.catalog_cards()
        cases = [
            (completion.OPERATIONS, {"state": "syncing"}),
            (completion.OPERATIONS, {"superseded_by": "new-operation"}),
            (completion.OPERATIONS, {"completion_mode": "future"}),
            (fulfillment.COMPONENT_LIFECYCLES, {"accepted": False}),
            (fulfillment.COMPONENT_LIFECYCLES, {"cancelled": True}),
            (fulfillment.COMPONENT_LIFECYCLES, {"retry_required": True}),
            (fulfillment.COMPONENT_LIFECYCLES, {"state": "blocked"}),
            (fulfillment.COMPONENT_LIFECYCLES, {"generation": "other-generation"}),
            (fulfillment.COMPONENT_LIFECYCLES, {"snapshot_revision": 999}),
            ("unified_orders", {"g47_salla_snapshot.component_pending": True}),
            ("unified_orders", {"g47_salla_snapshot.requires_authoritative_refresh": True}),
            ("unified_orders", {"g47_salla_snapshot.revision": 999}),
            (PLANS, {"source_version.value": "other-generation"}),
        ]
        for index, (collection, update) in enumerate(cases):
            with self.subTest(collection=collection, mutation=update):
                original = await self.db[collection].find_one({})
                await self.db[collection].update_one({"_id": original["_id"]}, {"$set": update})
                try:
                    self.assertEqual(await self.catalog_cards(), [])
                    response = await self.assign_cards(cards[:1], f"mixed-guard-{index}")
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}), 0)
                finally:
                    await self.db[collection].replace_one({"_id": original["_id"]}, original)
        self.assertEqual(len(await self.catalog_cards()), 3)
        self.assertEqual(await self.on_hand(), 18)

    async def test_mixed_assignment_rechecks_payment_cancellation_and_unknown_contract(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        cards = await self.catalog_cards()
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        for changed in (
            current.model_copy(update={"status": "cancelled", "status_native": "cancelled"}),
            current.model_copy(update={"payment": PaymentDTO(method="card", status="pending", collection_status="unpaid")}),
        ):
            with patch.object(catalog, "get_orders", AsyncMock(return_value={"local-assembly": changed})):
                self.assertEqual(await self.catalog_cards(), [])
                response = await self.assign_cards(cards[:1], "mixed-payment-cancel")
                self.assertEqual(response.status_code, 409, response.text)
        for mode in (None, "future", ""):
            await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"completion_mode": mode}})
            self.assertEqual(await self.catalog_cards(), [])
        self.assertEqual(await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].count_documents({}), 0)
        self.assertEqual(await self.on_hand(), 18)

    async def test_local_assignment_safety_reads_are_batched_and_projection_only(self):
        workflow = await self.complete_review(mixed=True)
        rows = [workflow]
        templates = {name: await self.db[name].find_one({}) for name in (
            completion.OPERATIONS, "unified_orders", fulfillment.COMPONENT_LIFECYCLES, PLANS,
        )}
        for index in range(1, 25):
            number, identity = f"synthetic-{index}", f"approved-{index}"
            rows.append({**workflow, "order_number": number, "review_completion_operation_id": identity})
            for name, template in templates.items():
                row = {**deepcopy(template), "_id": identity + name, "order_number": number}
                if name == completion.OPERATIONS:
                    row["_id"] = identity
                if name == PLANS:
                    row["order_id"] = number
                await self.db[name].insert_one(row)
        calls = []
        database = self.db
        class Collection:
            def __init__(self, name):
                self.name = name
            def find(self, query, projection):
                calls.append((self.name, query, projection))
                return database[self.name].find(query, projection)
        class ReadOnlyBatchDatabase:
            def __getitem__(self, name):
                return Collection(name)
            unified_orders = Collection("unified_orders")
        approved = await load_local_assignment_workflows(ReadOnlyBatchDatabase(), user_id="owner", workflows=rows)
        self.assertEqual(len(approved), 25)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len({name for name, _, _ in calls}), 4)
        for _, _, projection in calls:
            self.assertTrue(any(value == 1 for value in projection.values()))
            self.assertNotIn("raw_by_source", projection)
        self.external.assert_not_awaited()

    async def test_mixed_history_and_legacy_assignment_scope_remain_unchanged(self):
        self.mount_assignment()
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        workflow = await self.workflow()
        reviewed_date = str(workflow["reviewed_at"])[:10]
        history = await self.client.get("/reviewed-products-v1/catalog", params={"reviewed_date": reviewed_date})
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["summary"]["total_quantity"], 4)
        # Absent-mode legacy in_progress stays outside the new local branch;
        # legacy reviewed keeps its existing catalogue contract.
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$unset": {"completion_mode": ""}})
        self.assertEqual(await self.catalog_cards(), [])
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"stage": "reviewed"}})
        self.assertEqual(len(await self.catalog_cards()), 4)

    async def assert_ready_retry_instruction_guard(self, *, physical):
        await self.complete_review(mixed=physical)
        await self.set_address(complete=False)
        for piece_id in self.ids:
            await self.mark(piece_id)
        retry_id = self.ids[-1]
        if physical:
            for index in (1, 2):
                retry_id = f"supplier-{index}"
                await self.db[pieces.PIECES].insert_one({
                    "piece_id": retry_id, "user_id": "owner", "order_number": "local-assembly",
                    "order_item_id": self.supplier_line_id, "unit_index": index,
                    "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "assembly_status": "pending",
                    "supplier_dispatch_status": "received", "preparation_receipt_status": "received",
                })
                await self.mark(retry_id)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        if physical:
            # The existing preparation receipt lifecycle marks supplier work
            # ready_to_ship before its final assembly check.
            await self.db[completion.WORKFLOWS].update_one(
                {"order_number": "local-assembly"}, {"$set": {"stage": "ready_to_ship"}},
            )
        await self.set_address(complete=True)
        # A newly mandatory instruction on an already-ready *other* piece
        # still applies to the order-wide completion transition.
        instruction = {
            "id": "late-instruction", "user_id": "owner", "order_number": "local-assembly",
            "scope": "piece", "target_id": self.ids[0], "target_stages": ["assembly_labeling"],
            "status": "active", "enforcement": "completion_required",
        }
        await self.db[ORDER_TRACKING_INSTRUCTIONS].insert_one(instruction)
        before = await self.workflow()
        stock_before = await self.on_hand()
        for enforcement, state in (("completion_required", "active"),
                                   ("acknowledgement_required", "active"),
                                   ("notice", "waiting_customer_service_approval")):
            with self.subTest(enforcement=enforcement, state=state):
                await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one(
                    {"id": "late-instruction"}, {"$set": {"enforcement": enforcement, "status": state}},
                )
                result = await self.mark(retry_id, status=409)
                self.assertEqual(result["detail"]["code"], "customer_service_instruction_action_required")
                self.assertEqual(await self.workflow(), before)
                self.assertEqual(await self.on_hand(), stock_before)
                self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.db[ORDER_TRACKING_INSTRUCTIONS].update_one(
            {"id": "late-instruction"}, {"$set": {"status": "completed"}},
        )
        result = await self.mark(retry_id)
        self.assertTrue(result["idempotent"])
        self.assertTrue(result["progress"]["order_completed"])
        await self.mark(retry_id)
        self.assertEqual(await self.on_hand(), stock_before)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)

    async def test_virtual_ready_retry_preserves_new_mandatory_instructions(self):
        await self.assert_ready_retry_instruction_guard(physical=False)

    async def test_physical_ready_retry_preserves_new_mandatory_instructions(self):
        await self.assert_ready_retry_instruction_guard(physical=True)

    async def test_invalid_local_proofs_and_unknown_modes_block_both_write_paths(self):
        workflow = await self.complete_review()
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"stage": "in_progress"}})
        await self.db[pieces.PIECES].insert_one({
            "piece_id": "physical-proof", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.direct_line_id, "unit_index": 1, "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        })
        operation = await self.db[completion.OPERATIONS].find_one({"_id": workflow["review_completion_operation_id"]})
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        current = current.model_copy(update={"status": "in_progress", "status_native": "in_progress"})
        mutations = [{"state": "provider_confirmed"}, {"user_id": "other"}, {"order_number": "other"},
                     {"completion_mode": "future"}, {"superseded_by": "new-operation"}]
        with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=current)):
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    await self.db[completion.OPERATIONS].replace_one({"_id": operation["_id"]}, {**deepcopy(operation), **mutation})
                    for piece_id in (self.ids[0], "physical-proof"):
                        await self.mark(piece_id, status=409)
            await self.db[completion.OPERATIONS].replace_one({"_id": operation["_id"]}, operation)
            for mode in ("future", ""):
                await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"completion_mode": mode}})
                for piece_id in (self.ids[0], "physical-proof"):
                    result = await self.mark(piece_id, status=409)
                    self.assertEqual(result["detail"]["code"], "review_completion_mode_unknown")
        self.assertEqual(await self.on_hand(), 20)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 0)

    async def test_ready_retry_rechecks_proof_payment_cancellation_and_source_fence(self):
        workflow = await self.complete_review()
        await self.mark(self.ids[0])
        op_id = workflow["review_completion_operation_id"]
        await self.db[completion.OPERATIONS].update_one({"_id": op_id}, {"$set": {"superseded_by": "new-operation"}})
        await self.mark(self.ids[0], status=409)
        await self.db[completion.OPERATIONS].update_one({"_id": op_id}, {"$unset": {"superseded_by": ""}})
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        for changed in (
            current.model_copy(update={"status": "cancelled", "status_native": "cancelled"}),
            current.model_copy(update={"payment": PaymentDTO(method="card", status="pending", collection_status="unpaid")}),
        ):
            with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=changed)):
                await self.mark(self.ids[0], status=409)
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        result = await self.mark(self.ids[0], status=409)
        self.assertEqual(result["detail"]["code"], "component_execution_blocked")
        self.assertEqual(await self.on_hand(), 18)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 1)

    async def test_operational_last_piece_observes_source_fence(self):
        await self.complete_review(operational=True)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
            self.assertFalse(result["progress"]["order_completed"])
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        await self.mark("operational-piece", status=409)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": False}})
        result = await self.mark("operational-piece")
        self.assertTrue(result["progress"]["order_completed"])

    async def test_physical_reviewed_and_unreceived_pieces_remain_blocked(self):
        await self.complete_review(mixed=True)
        await self.db[pieces.PIECES].insert_one({
            "piece_id": "not-received", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.supplier_line_id, "unit_index": 1, "status": "assigned",
            "supplier_dispatch_status": "dispatched", "assembly_status": "pending",
        })
        await self.mark("not-received", status=409)
        await self.mark(self.ids[0])
        result = await self.mark("not-received", status=409)
        self.assertEqual(result["detail"]["code"], "assembly_piece_supplier_receipt_required")
        self.assertEqual(await self.on_hand(), 18)

    async def test_assignment_timeout_keeps_committed_pieces_and_reports_reconciliation(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        self.assignment_uncertain = True
        cards = await self.catalog_cards()
        response = await self.assign_cards(cards, "uncertain-assignment")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("local-assembly", response.json()["reconciliation_required"])
        self.assertEqual(self.assignment_posts, 1)
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 2)
        self.assertEqual(await self.on_hand(), 20)
        with patch.object(pieces, "call_salla", side_effect=self.assignment_transport):
            with self.assertRaises(RuntimeError):
                await pieces._assigned_reconcile_order_stage(self.db, user_id="owner",
                    order_number="local-assembly", batch_id="uncertain-assignment", actor=self.actor)
            self.assertEqual(self.assignment_posts, 1)
            self.assignment_status = "in_progress"
            await pieces._assigned_reconcile_order_stage(self.db, user_id="owner",
                order_number="local-assembly", batch_id="uncertain-assignment", actor=self.actor)
        self.assertEqual(self.assignment_posts, 1)
        self.assertEqual((await self.workflow())["salla_status_sync_state"], "sent")
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 2)
        self.assertEqual(await self.on_hand(), 20)
        self.external.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
