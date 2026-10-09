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

    async def test_all_supplier_partial_then_complete_allocation_keeps_existing_semantics(self):
        self.mount_assignment()
        await self.complete_review(direct=False)
        cards = await self.catalog_cards()
        self.assertEqual(len(cards), 2)
        first = await self.assign_cards(cards[:1], "supplier-partial-first")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        remaining = await self.catalog_cards()
        self.assertEqual([row["group_key"] for row in remaining], [cards[1]["group_key"]])
        second = await self.assign_cards(remaining, "supplier-partial-last")
        self.assertEqual(second.status_code, 200, second.text)
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


if __name__ == "__main__":
    unittest.main()
