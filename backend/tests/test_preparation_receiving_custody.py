"""Receipt/custody identity contract through the real native adapter and Mongo.

Only a disposable unauthenticated loopback database is allowed. Authentication
starts at the authenticated user seam; permissions, tenant bridging, receipt
writes, source-employee grouping and custody queries execute their real code.
"""
from copy import deepcopy
from datetime import datetime, timezone
import os
import unittest
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI, Request
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

from mobile_app_permissions import MOBILE_APP_ACCESS, MOBILE_APP_ACCESS_OWNER_FIELD
from mobile_app_request_context import mobile_app_request_user
import preparation_piece_operations as operations


class SourceReadCounter(CommandListener):
    def __init__(self):
        self.finds = 0

    def started(self, event):
        if event.command_name == "find" and event.command.get("find") == "unified_orders":
            self.finds += 1

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class PreparationCustodyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("Explicit loopback MZ2_TEST_MONGO_URI required")
        parsed = urlsplit(uri)
        self.assertEqual((parsed.scheme, parsed.hostname), ("mongodb", "127.0.0.1"))
        self.assertTrue(parsed.port)
        self.assertFalse(parsed.username or parsed.password)
        self.assertIn(parsed.path, ("", "/"))
        self.source_reads = SourceReadCounter()
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True,
                                       event_listeners=[self.source_reads])
        self.addCleanup(self.mongo.close)
        self.db = self.mongo["preparation_custody_test_" + uuid4().hex]
        await self.db.command("ping")

        async def cleanup():
            self.assertTrue(self.db.name.startswith("preparation_custody_test_"))
            await self.mongo.drop_database(self.db.name)
        self.addAsyncCleanup(cleanup)

        async def no_external_http(*args, **kwargs):
            raise AssertionError("External HTTP is forbidden in custody tests")
        blocker = patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_external_http)
        blocker.start()
        self.addCleanup(blocker.stop)

        self.owner = {"id": "merchant-a", "role": "owner", "name": "Synthetic owner",
                      "_session_client": "amasi_mobile"}
        self.employee = {"id": "receiver-a", "role": "viewer", "name": "Synthetic receiver",
                         "created_by": "merchant-a", "_session_client": "amasi_mobile"}
        self.user = deepcopy(self.employee)
        await self.db.users.insert_one(deepcopy(self.owner))
        await self.db.users.insert_one(deepcopy(self.employee))
        # Stub only the provider transport; resolution, identity/status checks,
        # permissions and owner-serialized Mongo receipt remain real.
        async def salla_read(db, owner, method, path, **kwargs):
            self.assertEqual(method, "GET")
            number = path.removeprefix("/orders/")
            row = await self.db.unified_orders.find_one({"user_id": owner, "order_number": number}) or {}
            return {"data": {"id": number, "reference_id": number,
                "status": (row.get("raw_by_source", {}).get("salla_direct", {}).get("status"))}}
        provider = patch("order_engine.shipping_label_service.call_salla", side_effect=salla_read)
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        await self.db[MOBILE_APP_ACCESS].insert_one({
            MOBILE_APP_ACCESS_OWNER_FIELD: "merchant-a", "user_id": "receiver-a",
            "enabled": True, "permissions": ["app.page.preparation_receiving"],
        })

        async def authenticated_user(request: Request):
            return await mobile_app_request_user(self.db, deepcopy(self.user),
                path=request.url.path, method=request.method)

        app = FastAPI()
        app.include_router(operations.make_preparation_piece_operations_router(
            self.db, authenticated_user), prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def seed_piece(self, piece_id, source="prep-a", **values):
        row = {"id": piece_id, "piece_id": piece_id, "user_id": "merchant-a",
               "order_number": "synthetic-" + piece_id, "order_item_id": "item-" + piece_id,
               "product_name": "Synthetic product", "responsible_employee_id": source,
               "responsible_employee_name": "Synthetic " + source,
               "status": operations.PIECE_STATUS_IN_PROGRESS, "assembly_status": "pending",
               "assignment_status": "assigned", "services": [],
               "supplier_dispatch_status": "received", "execution_status": "received_from_supplier",
               "received_at": datetime(2026, 10, 18, tzinfo=timezone.utc), **values}
        await self.db[operations.PIECES].insert_one(row)
        await self.db.unified_orders.update_one(
            {"user_id": row["user_id"], "order_number": row["order_number"]},
            {"$setOnInsert": {
                "order_status": "in_progress", "order_status_slug": "in_progress",
                "raw_by_source": {"salla_direct": {"id": row["order_number"], "status": {"slug": "in_progress"}}},
            }}, upsert=True)

    async def seed_received(self, piece_id, source="prep-a", receiver="receiver-a", **values):
        await self.seed_piece(piece_id, source, **{
            "status": operations.PIECE_STATUS_READY_FOR_ASSEMBLY,
            "preparation_receipt_status": "received", "preparation_received_by": receiver,
            "preparation_received_at": datetime(2026, 10, 18, tzinfo=timezone.utc),
            "preparation_received_from_employee_id": source,
            "preparation_received_from_employee_name": "Synthetic " + source, **values})

    async def receive(self, piece_id, request_id="synthetic-receipt"):
        response = await self.client.post(
            f"/api/preparation-work-v1/receiving/pieces/{piece_id}/receive",
            json={"client_request_id": request_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def custody(self):
        response = await self.client.get("/api/preparation-work-v1/receiving/custody")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def source_status(self, number, status, owner="merchant-a"):
        await self.db.unified_orders.update_one({"user_id": owner, "order_number": number}, {
            "$set": {"order_status": status, "order_status_slug": status,
                     "raw_by_source.salla_direct.status": {"slug": status}}})

    async def snapshot(self):
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                for name in await self.db.list_collection_names()}

    async def test_native_receiver_sees_receipts_from_owner_and_multiple_preparers(self):
        # Owner receipts are deliberately present: the broken route shows this
        # card instead of the current employee's three newly received pieces.
        await self.seed_received("owners-own-piece", source="merchant-a", receiver="merchant-a")
        for index, source in enumerate(("merchant-a", "prep-a", "prep-b")):
            piece_id = f"received-{index}"
            await self.seed_piece(piece_id, source)
            result = await self.receive(piece_id, "request-" + piece_id)
            self.assertFalse(result["idempotent"])
            self.assertEqual(result["piece"]["preparation_received_by"], "receiver-a")
            self.assertEqual(result["piece"]["preparation_received_from_employee_id"], source)
        result = await self.custody()
        self.assertEqual(result["receiver_employee_id"], "receiver-a")
        self.assertEqual(result["total_pieces"], 3)
        self.assertEqual({group["source_employee_id"] for group in result["groups"]},
                         {"merchant-a", "prep-a", "prep-b"})
        self.assertEqual({piece["piece_id"] for group in result["groups"] for piece in group["pieces"]},
                         {"received-0", "received-1", "received-2"})

    async def test_owner_keeps_own_custody_and_source_employee_groups(self):
        self.user = deepcopy(self.owner)
        await self.seed_received("employee-custody")
        for source in ("merchant-a", "prep-a", "prep-b"):
            await self.seed_piece("owner-received-" + source, source)
            await self.receive("owner-received-" + source, "owner-request-" + source)
        result = await self.custody()
        self.assertEqual(result["receiver_employee_id"], "merchant-a")
        self.assertEqual(result["total_pieces"], 3)
        self.assertEqual({group["source_employee_id"] for group in result["groups"]},
                         {"merchant-a", "prep-a", "prep-b"})

    async def test_custody_excludes_other_receivers_tenants_and_finished_pieces(self):
        await self.seed_received("keep-a", "prep-a")
        await self.seed_received("keep-b", "prep-b")
        await self.seed_received("other-receiver", receiver="receiver-b")
        await self.seed_received("owner-receiver", receiver="merchant-a")
        await self.seed_received("other-store", user_id="merchant-b")
        await self.seed_received("assembled", assembly_status="ready")
        await self.seed_received("cancelled", status=operations.PIECE_STATUS_CANCELLED)
        await self.seed_received("archived", experiment_archived_at=datetime.now(timezone.utc))
        await self.seed_piece("not-received")
        before = await self.db[operations.PIECES].find({}).sort("piece_id", 1).to_list(None)
        for attempt in range(2):
            result = await self.custody()
            ids = {piece["piece_id"] for group in result["groups"] for piece in group["pieces"]}
            self.assertEqual(ids, {"keep-a", "keep-b"})
        after = await self.db[operations.PIECES].find({}).sort("piece_id", 1).to_list(None)
        self.assertEqual(after, before)

    async def test_receipt_retry_preserves_source_and_does_not_duplicate_custody(self):
        await self.seed_piece("retry", "prep-b")
        first = await self.receive("retry")
        again = await self.receive("retry")
        self.assertFalse(first["idempotent"])
        self.assertTrue(again["idempotent"])
        result = await self.custody()
        self.assertEqual(result["total_pieces"], 1)
        self.assertEqual(result["groups"][0]["source_employee_id"], "prep-b")
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({
            "event_type": "preparation_piece_received_for_assembly", "piece_id": "retry"}), 1)

    async def test_live_mobile_page_permission_remains_required(self):
        await self.seed_received("keep")
        access = {MOBILE_APP_ACCESS_OWNER_FIELD: "merchant-a", "user_id": "receiver-a"}
        for revoked in ({"permissions": []}, {"enabled": False}):
            with self.subTest(revoked=revoked):
                await self.db[MOBILE_APP_ACCESS].update_one(access, {"$set": {
                    "enabled": True, "permissions": ["app.page.preparation_receiving"]}})
                await self.db[MOBILE_APP_ACCESS].update_one(access, {"$set": revoked})
                response = await self.client.get("/api/preparation-work-v1/receiving/custody")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"]["code"], "mobile_app_page_permission_required")

    async def test_browser_owner_ignores_mobile_actor_metadata(self):
        self.user = {**self.owner, "_session_client": None, "_mobile_actor_id": "receiver-a"}
        await self.seed_received("owner", receiver="merchant-a")
        await self.seed_received("employee", receiver="receiver-a")
        result = await self.custody()
        self.assertEqual(result["receiver_employee_id"], "merchant-a")
        self.assertEqual(result["total_pieces"], 1)
        self.assertEqual(result["groups"][0]["pieces"][0]["piece_id"], "owner")

    async def test_external_order_status_changes_recount_and_remove_empty_cards(self):
        for actor in (self.owner, self.employee):
            with self.subTest(actor=actor["id"]):
                self.user = deepcopy(actor)
                prefix = actor["id"]
                for suffix, source in (("owner", "merchant-a"), ("a1", "prep-a"),
                                       ("a2", "prep-a"), ("b", "prep-b")):
                    await self.seed_received(prefix + suffix, source, receiver=actor["id"])
                self.assertEqual((await self.custody())["total_pieces"], 4)
                # External sync changes only canonical order status. The piece
                # and local workflow intentionally retain their older states.
                await self.db[operations.WORKFLOWS].insert_one({
                    "user_id": "merchant-a", "order_number": "synthetic-" + prefix + "a1",
                    "stage": "in_progress"})
                await self.source_status("synthetic-" + prefix + "a1", "completed")
                before = await self.snapshot()
                result = await self.custody()
                self.assertEqual(result["total_pieces"], 3)
                self.assertEqual({g["source_employee_id"]: g["piece_count"] for g in result["groups"]},
                                 {"merchant-a": 1, "prep-a": 1, "prep-b": 1})
                self.assertEqual(await self.snapshot(), before)
                await self.source_status("synthetic-" + prefix + "a2", "shipped")
                result = await self.custody()
                self.assertEqual({g["source_employee_id"] for g in result["groups"]},
                                 {"merchant-a", "prep-b"})
                await self.source_status("synthetic-" + prefix + "owner", "cancelled")
                await self.source_status("synthetic-" + prefix + "b", "delivered")
                result = await self.custody()
                self.assertEqual((result["total_pieces"], result["groups"]), (0, []))
                # Reopening in Salla reveals the original receipt, no rewrite.
                await self.source_status("synthetic-" + prefix + "b", "in_progress")
                before = await self.snapshot()
                self.assertEqual((await self.custody())["total_pieces"], 1)
                self.assertEqual(await self.snapshot(), before)

    async def test_only_unambiguous_current_salla_in_progress_is_visible(self):
        for status in ("in_progress", "قيد التنفيذ", "completed", "تم التنفيذ", "shipped",
                       "delivering", "delivered", "cancelled", "refunded", "under_review", ""):
            with self.subTest(status=status):
                await self.db[operations.PIECES].delete_many({})
                await self.seed_received("status-case")
                await self.source_status("synthetic-status-case", status)
                result = await self.custody()
                self.assertEqual(result["total_pieces"], int(status in {"in_progress", "قيد التنفيذ"}))
        for fields in (
            {"order_status": "completed", "raw_by_source.salla_direct.status": {"slug": "in_progress"}},
            {"order_status": "in_progress", "raw_by_source.salla_direct.status": {"slug": "completed"}},
            {"raw_by_source": {}},
        ):
            with self.subTest(fields=fields):
                await self.source_status("synthetic-status-case", "in_progress")
                await self.db.unified_orders.update_one({"user_id": "merchant-a",
                    "order_number": "synthetic-status-case"}, {"$set": fields})
                self.assertEqual((await self.custody())["total_pieces"], 0)
        await self.db.unified_orders.delete_many({"user_id": "merchant-a"})
        await self.db.unified_orders.insert_one({"user_id": "merchant-b",
            "order_number": "synthetic-status-case", "order_status": "in_progress",
            "raw_by_source": {"salla_direct": {"status": {"slug": "in_progress"}}}})
        self.assertEqual((await self.custody())["total_pieces"], 0)

    async def test_status_visibility_does_not_depend_on_local_workflow_or_component_progress(self):
        await self.seed_received("keep")
        await self.db[operations.WORKFLOWS].insert_one({"user_id": "merchant-a",
            "order_number": "synthetic-keep", "stage": "completed"})
        await self.db.unified_orders.update_one({"user_id": "merchant-a", "order_number": "synthetic-keep"},
            {"$set": {"g47_salla_snapshot.component_pending": True}})
        self.assertEqual((await self.custody())["total_pieces"], 1)
        await self.db.unified_orders.update_one({"user_id": "merchant-a", "order_number": "synthetic-keep"},
            {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
        self.assertEqual((await self.custody())["total_pieces"], 0)

    async def test_receiving_search_excludes_inactive_orders_by_number_and_barcode(self):
        piece_id = "a" * 32
        await self.seed_received(piece_id, order_number="synthetic-search")
        for query in ("synthetic-search", "MEZAN-PIECE:" + piece_id):
            response = await self.client.get("/api/preparation-work-v1/receiving/search", params={"q": query})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["pieces"]), 1)
        await self.source_status("synthetic-search", "completed")
        before = await self.snapshot()
        for query in ("synthetic-search", "MEZAN-PIECE:" + piece_id):
            response = await self.client.get("/api/preparation-work-v1/receiving/search", params={"q": query})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "preparation_receipt_order_not_in_progress")
            self.assertIn("قيد التنفيذ", response.json()["detail"]["message"])
        self.assertEqual(await self.snapshot(), before)

    async def test_statuses_are_read_once_for_many_pieces_and_orders(self):
        for index in range(120):
            await self.seed_received(f"bulk-{index}", "prep-" + str(index % 3),
                                     order_number="synthetic-bulk-" + str(index % 6))
        await self.source_status("synthetic-bulk-1", "completed")
        await self.source_status("synthetic-bulk-4", "shipped")
        self.source_reads.finds = 0
        result = await self.custody()
        self.assertEqual(result["total_pieces"], 80)
        self.assertEqual(sum(g["piece_count"] for g in result["groups"]), 80)
        self.assertEqual(self.source_reads.finds, 1)

    async def test_rejection_names_salla_status_for_search_and_local_receipt_without_writes(self):
        piece_id = "b" * 32
        number = "synthetic-status-explanation"
        await self.seed_piece(piece_id, order_number=number)
        await self.db[operations.WORKFLOWS].insert_one({"user_id": "merchant-a", "order_number": number,
            "stage": "in_progress", "completion_mode": operations.LOCAL_COMPLETION_MODE})
        # The existing route initializes the event collection/index on first
        # use. Compare business records against an already initialized ledger.
        await self.db.create_collection(operations.PIECE_EVENTS)
        for status, label in (("delivering", "جاري التوصيل"), ("shipped", "تم الشحن"),
                              ("completed", "تم التنفيذ"), ("cancelled", "ملغى")):
            with self.subTest(status=status):
                await self.source_status(number, status)
                before = await self.snapshot()
                for query in (number, "MEZAN-PIECE:" + piece_id):
                    response = await self.client.get("/api/preparation-work-v1/receiving/search", params={"q": query})
                    self.assertEqual(response.status_code, 409, response.text)
                    detail = response.json()["detail"]
                    self.assertEqual(detail["order_status"], status)
                    self.assertEqual(detail["order_status_label"], label)
                    self.assertIn(label, detail["message"])
                    self.assertIn("يلزم أن تكون", detail["message"])
                response = await self.client.post(f"/api/preparation-work-v1/receiving/pieces/{piece_id}/receive",
                                                 json={"client_request_id": "synthetic-blocked-receipt"})
                self.assertEqual(response.status_code, 409, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], "preparation_receipt_order_not_in_progress")
                self.assertEqual(detail["order_status_label"], label)
                self.assertEqual(await self.snapshot(), before)

    async def test_unknown_status_explanation_does_not_invent_a_delivery_state(self):
        from preparation_receipt_status_message import receipt_status_rejection
        await self.seed_piece("explanation", order_number="synthetic-uncertain")
        await self.db.unified_orders.update_one({"user_id": "merchant-a", "order_number": "synthetic-uncertain"},
            {"$set": {"order_status": "delivered"}})
        before = await self.snapshot()
        detail = await receipt_status_rejection(self.db, user_id="merchant-a", order_number="synthetic-uncertain",
                                                code="local_review_receipt_not_eligible")
        self.assertIsNone(detail["order_status"])
        self.assertIsNone(detail["order_status_label"])
        self.assertIn("تعذّر التأكد", detail["message"])
        self.assertEqual(await self.snapshot(), before)

    async def test_supplier_and_services_are_required_even_with_current_salla_status(self):
        for index, values in enumerate((
            {"supplier_dispatch_status": "sent"}, {"execution_status": "in_progress"},
            {"received_at": None},
            {"services": [{"service_id": "print", "status": "pending", "required_quantity": 2, "completed_quantity": 1}]},
        )):
            piece_id = f"missing-{index}"
            await self.seed_piece(piece_id, **values)
            response = await self.client.post(f"/api/preparation-work-v1/receiving/pieces/{piece_id}/receive",
                json={"client_request_id": "missing-proof"})
            self.assertEqual(response.status_code, 409, response.text)
            row = await self.db[operations.PIECES].find_one({"piece_id": piece_id})
            self.assertNotEqual(row.get("preparation_receipt_status"), "received")
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({}), 0)

    async def test_receipt_ignores_review_components_and_payment_without_promoting(self):
        for index, services in enumerate(([], [{"service_id": "print", "status": "completed"}],
                                          [{"service_id": "print", "required_quantity": 2, "completed_quantity": 2}])):
            piece_id = f"eligible-{index}"
            number = "synthetic-" + piece_id
            await self.seed_piece(piece_id, services=services, active_hold_id="historical",
                                  assignment_status="unassigned", responsible_employee_id=None)
            await self.db[operations.WORKFLOWS].insert_one({"user_id": "merchant-a", "order_number": number,
                "stage": "in_progress", "completion_mode": operations.LOCAL_COMPLETION_MODE,
                "component_pending": True, "review_status": "pending"})
            await self.db.unified_orders.update_one({"order_number": number}, {"$set": {
                "order_status": "under_review", "g47_salla_snapshot.requires_authoritative_refresh": True}})
            component_names = ("mezan_component_order_lifecycle_v1", "mezan_component_consumption_plans_v1",
                               "mezan_inventory_reservations_v2", "payment_transactions", "warehouse_locations")
            for name in component_names:
                await self.db[name].update_one({"user_id": "merchant-a", "order_number": number},
                    {"$set": {"state": "unavailable", "revision": 77}}, upsert=True)
            before = {name: await self.db[name].find({}).to_list(None) for name in component_names}
            with patch.object(operations, "load_local_assignment_workflows", side_effect=AssertionError("receipt must not load review")), \
                 patch.object(operations, "_current_assembly_order", side_effect=AssertionError("receipt must not load components")):
                result = await self.receive(piece_id)
            self.assertFalse(result["progress"]["order_ready_for_assembly"])
            workflow = await self.db[operations.WORKFLOWS].find_one({"order_number": number})
            self.assertEqual(workflow["stage"], "in_progress")
            self.assertTrue(workflow["component_pending"])
            self.assertEqual(workflow["preparation_receipt_status"], "completed")
            self.assertEqual({name: await self.db[name].find({}).to_list(None) for name in component_names}, before)

    async def test_status_change_during_receipt_rolls_back_custody_and_event(self):
        await self.seed_piece("status-race")
        original = self.provider.side_effect
        calls = 0
        async def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            response = await original(*args, **kwargs)
            if calls == 2:
                response["data"]["status"] = {"slug": "completed"}
            return response
        self.provider.side_effect = changed
        response = await self.client.post("/api/preparation-work-v1/receiving/pieces/status-race/receive",
            json={"client_request_id": "receipt-race"})
        self.assertEqual(response.status_code, 409, response.text)
        row = await self.db[operations.PIECES].find_one({"piece_id": "status-race"})
        self.assertNotEqual(row.get("preparation_receipt_status"), "received")
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({}), 0)

    async def test_permission_revoked_during_provider_read_rolls_back(self):
        await self.seed_piece("permission-race")
        original = self.provider.side_effect
        calls = 0
        async def revoke(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = await original(*args, **kwargs)
            if calls == 2:
                await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "receiver-a"}, {"$set": {"enabled": False}})
            return result
        self.provider.side_effect = revoke
        response = await self.client.post("/api/preparation-work-v1/receiving/pieces/permission-race/receive",
            json={"client_request_id": "receipt-race"})
        self.assertEqual(response.status_code, 403, response.text)
        row = await self.db[operations.PIECES].find_one({"piece_id": "permission-race"})
        self.assertNotEqual(row.get("preparation_receipt_status"), "received")
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({}), 0)

    async def test_concurrent_receipts_record_one_custody_event(self):
        import asyncio
        await self.seed_piece("concurrent")
        results = await asyncio.gather(self.receive("concurrent", "request-one"), self.receive("concurrent", "request-two"))
        self.assertEqual(sorted(row["idempotent"] for row in results), [False, True])
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({"piece_id": "concurrent"}), 1)

    async def test_wrong_provider_identity_and_other_tenant_fail_without_receipt(self):
        await self.seed_piece("identity")
        original = self.provider.side_effect
        async def wrong(*args, **kwargs):
            result = await original(*args, **kwargs)
            result["data"]["reference_id"] = "another-order"
            return result
        self.provider.side_effect = wrong
        response = await self.client.post("/api/preparation-work-v1/receiving/pieces/identity/receive",
            json={"client_request_id": "identity"})
        self.assertEqual(response.status_code, 409, response.text)
        await self.seed_piece("other-tenant", user_id="merchant-b")
        response = await self.client.post("/api/preparation-work-v1/receiving/pieces/other-tenant/receive",
            json={"client_request_id": "other-tenant"})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(await self.db[operations.PIECE_EVENTS].count_documents({}), 0)
