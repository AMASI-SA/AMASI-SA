"""Real Mongo + ASGI coverage for the read-only native order tracking contract."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, Request
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

from mobile_app_permissions import MOBILE_APP_ACCESS, MOBILE_APP_ACCESS_OWNER_FIELD
from mobile_app_request_context import mobile_app_request_user, required_mobile_permissions
from mobile_order_tracking import make_mobile_order_tracking_router, TRACKING_PERMISSION


class Commands(CommandListener):
    def __init__(self):
        self.names = []

    def started(self, event):
        self.names.append(event.command_name)

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class TrackingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        self.assertTrue(uri, "An explicit isolated loopback Mongo URI is required")
        parsed = urlparse(uri)
        self.assertIn(parsed.hostname, {"127.0.0.1", "localhost"})
        self.assertTrue(parsed.port)
        self.assertFalse(parsed.username or parsed.password)
        self.assertIn(parsed.path, {"", "/"})
        self.commands = Commands()
        self.mongo = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000,
                                       event_listeners=[self.commands])
        self.addCleanup(self.mongo.close)
        self.db = self.mongo["mobile_tracking_test_" + uuid4().hex]
        await self.db.command("ping")

        async def cleanup():
            self.assertTrue(self.db.name.startswith("mobile_tracking_test_"))
            await self.mongo.drop_database(self.db.name)
        self.addAsyncCleanup(cleanup)

        async def no_external_http(*args, **kwargs):
            raise AssertionError("Tracking must not call an external provider")
        blocker = patch.object(httpx.AsyncHTTPTransport, "handle_async_request", no_external_http)
        blocker.start()
        self.addCleanup(blocker.stop)

        self.owner = {"id": "owner-a", "role": "owner", "name": "Synthetic owner"}
        self.employee = {"id": "employee-a", "role": "viewer", "name": "Synthetic employee",
                         "created_by": "owner-a"}
        await self.db.users.insert_many([deepcopy(self.owner), deepcopy(self.employee)])
        self.user = {**self.employee, "_session_client": "amasi_mobile"}
        await self.grant([TRACKING_PERMISSION])

        async def authenticated_user(request: Request):
            return await mobile_app_request_user(self.db, deepcopy(self.user),
                path=request.url.path, method=request.method)

        app = FastAPI()
        app.include_router(make_mobile_order_tracking_router(self.db, authenticated_user), prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def grant(self, permissions, enabled=True):
        await self.db[MOBILE_APP_ACCESS].update_one({
            MOBILE_APP_ACCESS_OWNER_FIELD: "owner-a", "user_id": "employee-a",
        }, {"$set": {"permissions": permissions, "enabled": enabled}}, upsert=True)

    async def source(self, number="10001", status="in_progress", quantity=1, owner="owner-a", **fields):
        raw = {"id": "source-" + number, "reference_id": number,
               "date": {"date": "2026-08-25 23:20:00", "timezone": "Asia/Riyadh"},
               "status": {"slug": status},
               "customer": {"phone": "do-not-return", "address": "do-not-return"},
               "items": [{"id": "line", "name": "Synthetic product", "sku": "SYN",
                          "quantity": quantity, "price": 999,
                          "product": {"id": "product", "thumbnail": "https://example.invalid/product.png"}}]}
        await self.db.unified_orders.insert_one({"user_id": owner, "order_number": number,
            "order_status": status, "order_date": "2026-08-25", "updated_at": datetime.now(timezone.utc),
            "raw_by_source": {"salla_direct": raw}, **fields})

    async def piece(self, piece_id="piece-a", number="10001", owner="owner-a", **fields):
        await self.db.mezan_preparation_pieces_v1.insert_one({
            "user_id": owner, "order_number": number, "piece_id": piece_id,
            "order_item_id": f"salla:{number}:line", "unit_index": 1,
            "product_name": "Synthetic product", "status": "assigned",
            "assignment_status": "assigned", "responsible_employee_id": "prep-a",
            "responsible_employee_name": "Synthetic preparer", "batch_id": "batch-a",
            "assigned_at": datetime(2026, 8, 25, 20, 30, tzinfo=timezone.utc), **fields})

    async def local_review(self, *, stage="reviewed", **operation_fields):
        at = "2026-08-26T00:00:00Z"
        identity = "synthetic-local-review"
        await self.db.order_review_workflows.insert_one({
            "user_id": "owner-a", "order_number": "10001", "stage": stage,
            "completion_mode": "mezan_local_v1", "review_completion_operation_id": identity,
            "reviewed_at": "2026-08-25T23:59:00Z", "reviewed_by": "employee-a"})
        await self.db.order_review_completion_operations.insert_one({
            "_id": identity, "user_id": "owner-a", "order_number": "10001",
            "state": "completed", "completion_mode": "mezan_local_v1", "completed_at": at,
            "actor_id": "employee-a", **operation_fields})
        await self.db.order_review_events.insert_one({
            "_id": identity + ":completed", "operation_id": identity,
            "user_id": "owner-a", "order_number": "10001", "event_type": "order_review_completed",
            "actor_id": "employee-a", "occurred_at": at})
        await self.db.unified_orders.update_one({"order_number": "10001"}, {"$set": {
            "raw_by_source.salla_direct.payment_method": "cod"}})

    async def get(self, number="10001", expected=200):
        before = {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                  for name in await self.db.list_collection_names()}
        self.commands.names.clear()
        response = await self.client.get("/api/mobile/order-tracking/" + number)
        commands = list(self.commands.names)
        self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse(set(commands) & {"insert", "update", "delete", "findAndModify", "createIndexes"})
        after = {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                 for name in await self.db.list_collection_names()}
        self.assertEqual(after, before)
        return response.json()

    async def test_delivery_history_is_order_scoped_and_preserves_driver_exceptions(self):
        await self.source(status="delivering", order_id="source-10001", shipping_company="سمسا")
        selector = {"user_id": "owner-a", "order_number": "10001", "order_id": "source-10001"}
        assignment = {**selector, "id": "assignment-a", "driver_id": "driver-a",
            "driver_name_snapshot": "الموصل الأول", "assigned_at": "2026-11-20T10:00:00Z",
            "out_for_delivery_at": "2026-11-20T11:00:00Z", "delivered_at": "2026-11-21T12:00:00Z"}
        await self.db.store_delivery_assignments.insert_many([assignment,
            {**assignment, "user_id": "owner-b", "id": "foreign", "driver_name_snapshot": "FOREIGN_DRIVER"},
            {**assignment, "order_id": "wrong-order", "id": "wrong", "driver_name_snapshot": "WRONG_ORDER"}])
        await self.db.store_delivery_events.insert_many([
            {**selector, "assignment_id": "assignment-a", "driver_id": "driver-a",
             "event_type": "store_delivery_customer_unreachable", "occurred_at": "2026-11-20T12:00:00Z",
             "note": "اتصلت بالعميل ولم يرد", "evidence_url": "PRIVATE_EVIDENCE"},
            {**selector, "assignment_id": "assignment-a", "driver_id": "driver-a",
             "event_type": "store_delivery_resumed", "occurred_at": "2026-11-21T09:00:00Z"},
            {**selector, "assignment_id": "assignment-a", "driver_id": "different",
             "event_type": "store_delivery_customer_requested_cancel", "occurred_at": "2026-11-20T13:00:00Z"}])
        body = await self.get()
        steps = body["order_route_steps"]
        self.assertEqual(body["carrier_name"], "سمسا")
        for label in ("أُسند الطلب إلى مندوب المتجر", "جاري توصيل الشحنة", "العميل لا يرد",
                      "استؤنف التوصيل", "تم توصيل الشحنة"):
            row = next(row for row in steps if row["label"] == label)
            self.assertEqual(row["actor_name"], "الموصل الأول")
        self.assertEqual(steps[0]["at"], "2026-11-21T12:00:00+00:00")
        exception = next(row for row in steps if row["label"] == "العميل لا يرد")
        self.assertEqual(exception["note"], "اتصلت بالعميل ولم يرد")
        for hidden in ("PRIVATE_EVIDENCE", "FOREIGN_DRIVER", "WRONG_ORDER", "العميل طلب إلغاء الطلب"):
            self.assertNotIn(hidden, str(body))

    async def test_explicit_return_status_is_not_inferred_from_cancel_request(self):
        await self.source(status="returned")
        await self.db.unified_orders.update_one({"user_id": "owner-a", "order_number": "10001"},
            {"$set": {"raw_by_source.salla_direct.status_changed_at": "2026-11-22T10:00:00Z"}})
        body = await self.get()
        self.assertEqual(body["status"], "returned")
        self.assertTrue(any(row["label"] == "تم استرجاع الشحنة" and row["source"] == "salla"
                            for row in body["order_route_steps"]))

    async def test_independent_grant_live_revoke_and_manager_owner_access(self):
        await self.source()
        self.assertEqual(required_mobile_permissions("/api/mobile/order-tracking/10001"),
                         frozenset({TRACKING_PERMISSION}))
        await self.get()
        await self.grant(["app.page.suppliers", "app.page.orders"])
        await self.get(expected=403)
        await self.grant([TRACKING_PERMISSION], enabled=False)
        await self.get(expected=403)
        await self.grant(["app.role.manager"])
        await self.get()
        # A fresh DB disable overrides the otherwise authenticated principal.
        await self.db.users.update_one({"id": "employee-a"}, {"$set": {"disabled": True}})
        await self.get(expected=403)
        self.user = {**self.owner, "_session_client": "amasi_mobile"}
        await self.get()

    async def test_barcode_lookup_returns_whole_order_and_matched_piece_without_writes(self):
        await self.source(quantity=2)
        identity = 'a' * 32
        await self.piece(identity)
        await self.piece('b' * 32, unit_index=2)
        self.commands.names.clear()
        response = await self.client.get('/api/mobile/order-tracking/lookup',
            params={'query': 'MEZAN-PIECE:' + identity.upper()})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['order_number'], '10001')
        self.assertEqual(data['matched_piece_id'], identity)
        self.assertEqual(len(data['items'][0]['pieces']), 2)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertFalse(set(self.commands.names) & {'insert', 'update', 'delete', 'findAndModify', 'createIndexes'})

    async def test_barcode_lookup_fails_closed_for_cross_tenant_archived_and_duplicate_identity(self):
        await self.source()
        identity = 'c' * 32
        await self.piece(identity, owner='owner-b')
        for query in [identity, 'MEZAN-PIECE:' + identity]:
            response = await self.client.get('/api/mobile/order-tracking/lookup', params={'query': query})
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()['detail']['code'], 'mobile_order_tracking_barcode_not_found')
        await self.piece(identity, experiment_archived_at=datetime.now(timezone.utc))
        response = await self.client.get('/api/mobile/order-tracking/lookup', params={'query': identity})
        self.assertEqual(response.status_code, 404, response.text)
        await self.piece(identity)
        await self.piece(identity, number='20002')
        response = await self.client.get('/api/mobile/order-tracking/lookup', params={'query': identity})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'mobile_order_tracking_barcode_ambiguous')

    async def test_barcode_lookup_requires_current_tracking_grant_and_valid_payload(self):
        await self.source()
        await self.piece('d' * 32)
        response = await self.client.get('/api/mobile/order-tracking/lookup', params={'query': 'SKU-not-a-piece'})
        self.assertEqual(response.status_code, 422, response.text)
        await self.grant(['app.page.assembly_shipping'])
        response = await self.client.get('/api/mobile/order-tracking/lookup', params={'query': 'd' * 32})
        self.assertEqual(response.status_code, 403, response.text)

    async def test_named_service_receipts_are_distinct_from_product_and_assignment(self):
        await self.source()
        at1, at2 = '2026-11-10T09:00:00Z', '2026-11-11T09:00:00Z'
        await self.piece(supplier_dispatch_status='received', supplier_id='service-supplier',
            supplier_name='مورد التطريز', sent_to_supplier_at='2026-11-09T09:00:00Z',
            supplier_dispatch_id='dispatch-a', received_at=at2, services=[
                {'service_id': 'embroidery', 'service_name': 'تطريز الاسم', 'status': 'completed',
                 'completed_quantity': 1, 'required_quantity': 1, 'completed_at': at2,
                 'completed_by_supplier_name': 'مورد التطريز', 'supplier_invoice_id': 'receipt-service'},
                {'service_id': 'packaging', 'service_name': 'تغليف هدية', 'status': 'pending'}],
            supplier_receiving_history=[
                {'invoice_id': 'receipt-product', 'supplier_id': 'product-supplier',
                 'supplier_name': 'محل أماسي الخليج', 'received_by_name': 'هلال', 'received_at': at1, 'services': []},
                {'invoice_id': 'receipt-service', 'supplier_id': 'service-supplier',
                 'supplier_name': 'مورد التطريز', 'received_by_name': 'هلال', 'received_at': at2,
                 'services': [{'service_id': 'embroidery', 'service_name': 'تطريز الاسم'}]}])
        await self.db.mezan_supplier_dispatches_v1.insert_one({
            'id': 'dispatch-a', 'user_id': 'owner-a', 'status': 'sent', 'sent_by_id': 'prep-a',
            'sent_by_name': 'هلال', 'supplier_id': 'product-supplier', 'supplier_name': 'محل أماسي الخليج',
            'source_files': [{'cards': [{'piece_id': 'piece-a'}]}]})
        data = await self.get()
        steps = data['items'][0]['pieces'][0]['route_steps']
        product = [row for row in steps if row.get('kind') == 'product_supplier_received']
        self.assertEqual(len(product), 1)
        self.assertEqual(product[0]['supplier_name'], 'محل أماسي الخليج')
        assigned = next(row for row in steps if row.get('kind') == 'product_supplier_assigned')
        self.assertEqual(assigned['supplier_name'], 'محل أماسي الخليج')
        received = [row for row in steps if row.get('kind') == 'service_supplier_received' and row['state'] == 'done']
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['service_name'], 'تطريز الاسم')
        self.assertEqual(received[0]['supplier_name'], 'مورد التطريز')
        self.assertEqual(received[0]['actor_name'], 'هلال')
        self.assertTrue(any(row.get('service_name') == 'تغليف هدية' and row['state'] == 'pending' for row in steps))
        self.assertFalse(any('إسناد خدمة' in row['label'] for row in steps))

    async def test_direct_supplier_assignment_is_one_event_but_older_dispatch_is_preserved(self):
        from supplier_receiving_direct_assignment import direct_assignment_patch
        await self.source()
        patch = direct_assignment_patch(piece={'status': 'assigned'},
            offer={'current_supplier_id': 'direct', 'current_supplier_name': 'محل أماسي الخليج'},
            session_id='draft-session', actor_id='prep-a', actor_name='هلال', at='2026-11-10T09:00:00Z')
        await self.piece(**patch)
        rows = (await self.get())['items'][0]['pieces'][0]['route_steps']
        assigned = [row for row in rows if row.get('kind') == 'product_supplier_assigned']
        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]['supplier_name'], 'محل أماسي الخليج')
        self.assertEqual(assigned[0]['actor_name'], 'هلال')
        self.assertFalse(any(row.get('kind') == 'product_supplier_received' and row['state'] == 'done' for row in rows))
        await self.db.mezan_preparation_pieces_v1.update_one({'piece_id': 'piece-a'},
            {'$set': {'sent_to_supplier_at': '2026-11-09T09:00:00Z'}})
        rows = (await self.get())['items'][0]['pieces'][0]['route_steps']
        self.assertEqual(len([row for row in rows if row.get('kind') == 'product_supplier_assigned']), 2)

    async def test_tracking_ready_flag_requires_persisted_approval_and_valid_current_piece(self):
        await self.source(quantity=2)
        await self.piece(assembly_status='pending')
        await self.piece('piece-b', unit_index=2, assembly_status='ready',
                         assembly_ready_at='2026-11-12T10:00:00Z')
        pieces = (await self.get())['items'][0]['pieces']
        self.assertEqual([row['assembly_ready'] for row in pieces], [False, True])
        await self.db.mezan_preparation_pieces_v1.update_one({'piece_id': 'piece-b'}, {'$set': {'unit_index': 1}})
        self.assertTrue(all(row['assembly_ready'] is False for row in (await self.get())['items'][0]['pieces']))

    async def test_draft_or_unproven_service_does_not_claim_supplier_receipt(self):
        await self.source()
        await self.piece(supplier_dispatch_status='sent', supplier_name='المورد الحالي',
            supplier_receiving_history=[{'received_at': '2026-11-11T09:00:00Z',
                'supplier_name': 'مورد المسودة', 'services': [{'service_id': 's', 'service_name': 'خدمة المسودة'}]}],
            services=[{'service_id': 's', 'service_name': 'خدمة المسودة', 'status': 'pending'},
                      {'service_id': 'unproven', 'service_name': 'خدمة بلا إثبات المورد', 'status': 'completed'}])
        rows = (await self.get())['items'][0]['pieces'][0]['route_steps']
        receipts = [row for row in rows if row.get('kind') in {'product_supplier_received', 'service_supplier_received'}]
        self.assertTrue(receipts)
        self.assertTrue(all(row['state'] == 'pending' for row in receipts))
        self.assertNotIn('مورد المسودة', str(receipts))

    async def test_supplier_changed_without_original_dispatch_does_not_relabel_old_assignment(self):
        await self.source()
        await self.piece(supplier_id='later', supplier_name='مورد لاحق',
            sent_to_supplier_at='2026-11-09T09:00:00Z', supplier_dispatch_status='partial_received',
            supplier_receiving_history=[{'invoice_id': 'first', 'supplier_id': 'original',
                'supplier_name': 'المورد الأصلي', 'received_at': '2026-11-10T09:00:00Z'}])
        rows = (await self.get())['items'][0]['pieces'][0]['route_steps']
        assignment = next(row for row in rows if row.get('kind') == 'product_supplier_assigned')
        self.assertIsNone(assignment['supplier_name'])
        self.assertNotIn('مورد لاحق', assignment['label'])
        receipt = next(row for row in rows if row.get('kind') == 'product_supplier_received')
        self.assertEqual(receipt['supplier_name'], 'المورد الأصلي')

    async def test_truncated_history_retains_original_product_receipt_and_reports_bound(self):
        await self.source()
        history = [{'invoice_id': f'invoice-{i}', 'supplier_id': f'supplier-{i}',
                    'supplier_name': f'Supplier {i}', 'received_at': f'2026-11-11T09:{i // 60:02}:{i % 60:02}Z'}
                   for i in range(105)]
        await self.piece(supplier_receiving_history=history, supplier_dispatch_status='received')
        data = await self.get()
        self.assertTrue(data['truncated'])
        receipts = [row for row in data['items'][0]['pieces'][0]['route_steps']
                    if row.get('kind') == 'product_supplier_received']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['supplier_name'], 'Supplier 0')

    async def test_service_history_output_is_bounded_without_losing_physical_receipt(self):
        await self.source()
        services = [{'service_id': f'service-{i}', 'service_name': f'Service {i}'} for i in range(100)]
        await self.piece(supplier_receiving_history=[
            {'invoice_id': f'invoice-{i}', 'supplier_name': f'Supplier {i}',
             'received_at': f'2026-11-11T09:0{i}:00Z', 'services': services}
            for i in range(3)])
        data = await self.get()
        self.assertTrue(data['truncated'])
        rows = data['items'][0]['pieces'][0]['route_steps']
        service_rows = [row for row in rows if row.get('kind') == 'service_supplier_received']
        self.assertEqual(len(service_rows), 100)
        self.assertTrue(all(row['supplier_name'] == 'Supplier 2' for row in service_rows))
        self.assertEqual(len([row for row in rows if row.get('kind') == 'product_supplier_received']), 1)

    async def test_same_order_number_is_tenant_scoped_and_browser_not_native(self):
        await self.source()
        await self.source(owner="owner-b")
        await self.piece()
        await self.piece("other-store-secret", owner="owner-b", responsible_employee_name="OTHER SECRET")
        data = await self.get()
        self.assertNotIn("OTHER SECRET", str(data))
        await self.source("20002", owner="owner-b")
        await self.get("20002", expected=404)
        self.user = dict(self.owner)
        await self.get(expected=403)

    async def test_waiting_is_not_in_progress_and_progress_day_uses_registry_date(self):
        await self.source(quantity=2)
        await self.piece()
        await self.piece("piece-b", unit_index=2, status="in_progress", supplier_dispatch_status="sent",
                         supplier_dispatch_id="dispatch-a", supplier_name="Synthetic supplier",
                         supplier_id="supplier-a", sent_to_supplier_by_id="prep-a")
        day = datetime(2026, 8, 25, 21, 30, tzinfo=timezone.utc)
        await self.db.mezan_preparation_file_registry_v2.insert_one({
            "user_id": "owner-a", "batch_id": "batch-a", "registered_at": day})
        await self.db.mezan_supplier_dispatches_v1.insert_one({
            "id": "dispatch-a", "user_id": "owner-a", "sent_by_id": "prep-a", "status": "sent",
            "source_files": [{"cards": [{"piece_id": "piece-b"}]}]})
        data = await self.get()
        pieces = {row["piece_id"]: row for row in data["items"][0]["pieces"]}
        self.assertEqual(pieces["piece-a"]["current_location"]["page"], "بانتظار الإسناد للمورد")
        self.assertEqual(pieces["piece-b"]["current_location"]["page"], "قيد التنفيذ")
        self.assertEqual(pieces["piece-b"]["current_location"]["at"], day.isoformat())
        self.assertEqual(data["items"][0]["coverage"], "complete")

    async def test_partial_supplier_receipt_uses_transferred_employee_not_first_assigner(self):
        await self.source()
        at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        await self.piece(status="in_progress", supplier_dispatch_status="partial_received",
                         responsible_employee_id="new-receiver", responsible_employee_name="Actual receiver",
                         reassigned_at=at, received_by_name="Actual receiver",
                         supplier_name="Supplier two", supplier_receiving_history=[{
                             "invoice_id": "synthetic-invoice", "supplier_name": "Supplier two",
                             "received_at": at, "received_by_name": "Actual receiver"}],
                         assignment_history=[{"reason": "supplier_receipt", "transferred_at": at,
                             "previous_responsible_employee_name": "First preparer",
                             "responsible_employee_name": "Actual receiver"}])
        piece = (await self.get())["items"][0]["pieces"][0]
        self.assertEqual(piece["current_location"]["page"], "تم الاستلام من المورد")
        self.assertEqual(piece["current_location"]["employee_name"], "Actual receiver")
        self.assertTrue(any(row["at"] == at.isoformat() and "المورد" in row["label"]
                            for row in piece["route_steps"]))
        self.assertTrue(any(row["actor_name"] == "First preparer" for row in piece["route_steps"]))

    async def test_preparation_receipt_uses_actual_receiver_and_ready_moves_to_assembly(self):
        await self.source()
        at = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
        await self.piece(status="ready_for_assembly", preparation_receipt_status="received",
                         preparation_received_by="receiving-employee", preparation_received_by_name="Receiver",
                         preparation_received_at=at, branch_handoff_at=at,
                         preparation_received_from_employee_name="Preparer", assembly_status="pending")
        piece = (await self.get())["items"][0]["pieces"][0]
        self.assertEqual(piece["current_location"]["employee_name"], "Receiver")
        self.assertEqual(piece["current_location"]["page"], "الاستلام من موظف التجهيز")
        await self.db.mezan_preparation_pieces_v1.update_one({"piece_id": "piece-a"}, {"$set": {
            "assembly_status": "ready", "assembly_ready_at": at, "assembly_ready_by_name": "Assembler"}})
        piece = (await self.get())["items"][0]["pieces"][0]
        self.assertEqual(piece["current_location"]["workspace"], "التجميع والعنونة")
        self.assertEqual(piece["current_location"]["employee_name"], "Assembler")

    async def test_external_terminal_and_conflicting_status_never_claim_active_employee_page(self):
        for index, status in enumerate(("completed", "shipped", "delivered", "cancelled", "refunded")):
            number = str(30000 + index)
            await self.source(number, status)
            await self.piece(number=number, piece_id=number)
            piece = (await self.get(number))["items"][0]["pieces"][0]
            self.assertFalse(piece["current_location"]["known"])
            self.assertIsNone(piece["current_location"]["employee_name"])
        await self.source()
        await self.piece()
        await self.db.unified_orders.update_one({"order_number": "10001"},
                                              {"$set": {"order_status": "completed"}})
        data = await self.get()
        self.assertEqual(data["status"], "unknown")
        self.assertFalse(data["items"][0]["pieces"][0]["current_location"]["known"])
        self.assertNotIn("تختلف", data["route_notice"])

    async def test_creation_and_status_dates_are_not_generic_updated_at(self):
        await self.source()
        data = await self.get()
        self.assertEqual(data["order_created_at"], "2026-08-25T20:20:00+00:00")
        self.assertIsNone(data["status_changed_at"])
        self.assertEqual(data["items"][0]["coverage"], "no_records")
        at = "2026-10-01T14:02:00+03:00"
        await self.db.unified_orders.update_one({"order_number": "10001"}, {"$set": {
            "raw_by_source.salla_direct.status.changed_at": at,
            "raw_by_source.salla_direct.status.name": "قيد التنفيذ"}})
        data = await self.get()
        self.assertEqual(data["status_changed_at"], "2026-10-01T11:02:00+00:00")
        self.assertEqual(data["status_label"], "قيد التنفيذ")
        self.assertFalse({"price", "phone", "address", "totals", "customer"} & set(data))
        self.assertNotIn("do-not-return", str(data))

    async def test_direct_assembly_has_per_unit_identity_and_no_supplier_route(self):
        await self.source(quantity=2)
        await self.db.order_review_workflows.insert_one({"user_id": "owner-a", "order_number": "10001",
            "stage": "in_progress", "items": [{"order_item_id": "salla:10001:line", "quantity": 2,
                "product_name": "Synthetic product", "preparation_route": "direct_assembly",
                "direct_assembly_piece_ids": ["direct-a", "direct-b"],
                "assembly_ready_piece_ids": ["direct-a"]}]})
        data = await self.get()
        self.assertEqual(data["items"][0]["coverage"], "complete")
        self.assertEqual({p["piece_id"] for p in data["items"][0]["pieces"]}, {"direct-a", "direct-b"})
        for piece in data["items"][0]["pieces"]:
            self.assertEqual(piece["current_location"]["workspace"], "التجميع والعنونة")
            self.assertFalse(any("المورد" in step["label"] for step in piece["route_steps"]))

    async def test_missing_piece_coverage_and_duplicate_identity_fail_closed(self):
        await self.source(quantity=3)
        await self.piece()
        self.assertEqual((await self.get())["items"][0]["coverage"], "partial")
        await self.piece(responsible_employee_name="Conflicting identity")
        data = await self.get()
        self.assertEqual(data["items"][0]["coverage"], "partial")
        self.assertFalse(data["items"][0]["pieces"][0]["current_location"]["known"])

    async def test_no_query_writes_large_bounded_response_and_input_validation(self):
        await self.source(quantity=1005)
        await self.db.mezan_preparation_pieces_v1.insert_many([{
            "user_id": "owner-a", "order_number": "10001", "order_item_id": "salla:10001:line",
            "piece_id": f"p-{index:04}", "unit_index": index + 1, "status": "assigned",
            "responsible_employee_id": "prep-a", "responsible_employee_name": "Preparer",
        } for index in range(1005)])
        data = await self.get()
        self.assertTrue(data["truncated"])
        self.assertLessEqual(len(data["items"][0]["pieces"]), 1000)
        self.assertEqual(data["items"][0]["coverage"], "partial")
        await self.get("not-an-order", expected=422)

    async def test_order_history_merges_salla_transitions_and_confirmed_mezan_reviews(self):
        await self.source(status="completed")
        events = [
            ("2026-08-25 23:30:00", "تغيرت الحالة إلى بانتظار المراجعة"),
            ("2026-08-26 01:00:00", "تم تغيير الحالة إلى قيد التنفيذ"),
            ("2026-08-26 02:00:00", "تم تغيير الحالة إلى بانتظار المراجعة"),
            ("2026-08-26 03:00:00", "تم تغيير الحالة إلى قيد التنفيذ"),
            ("2026-08-26 04:00:00", "تم تغيير الحالة إلى تم التنفيذ"),
        ]
        await self.db.order_activity_events_v2.insert_many([{
            "user_id": "owner-a", "order_number": "10001", "source": "salla_history",
            "event_type": "status_changed", "occurred_at": at, "title": title,
        } for at, title in events])
        # Duplicate imports cannot duplicate a milestone; recurring visits can.
        await self.db.order_activity_events_v2.insert_one({
            "user_id": "owner-a", "order_number": "10001", "source": "salla_history",
            "event_type": "status_changed", "occurred_at": events[0][0], "title": events[0][1]})
        await self.db.order_review_events.insert_many([{
            "user_id": "owner-a", "order_number": "10001", "event_type": "order_review_completed",
            "operation_id": "review-" + str(index), "actor_id": "employee-a", "occurred_at": at,
        } for index, at in enumerate(("2026-08-25T21:00:00Z", "2026-08-25T23:30:00Z"))])
        await self.db.order_review_events.insert_one({
            "user_id": "owner-b", "order_number": "10001", "event_type": "order_review_completed",
            "occurred_at": "2026-08-27T00:00:00Z", "actor_name": "OTHER SECRET"})
        await self.db.order_activity_events_v2.insert_one({
            "user_id": "owner-a", "order_number": "10001", "source": "salla_history",
            "event_type": "payment_updated", "occurred_at": "2026-08-29 00:00:00",
            "title": "payment-secret-do-not-return"})
        data = await self.get()
        route = data["order_route_steps"]
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["status_changed_at"], "2026-08-26T01:00:00+00:00")
        self.assertEqual(sum(row["label"] == "بانتظار المراجعة" for row in route), 2)
        self.assertEqual(sum(row["label"] == "قيد التنفيذ" for row in route), 2)
        local = [row for row in route if row["label"] == "تمت المراجعة — ميزان"]
        self.assertEqual(len(local), 2)
        self.assertTrue(all(row["source"] == "mezan" and row["actor_name"] == "Synthetic employee" for row in local))
        self.assertNotIn("SECRET", str(data))
        self.assertNotIn("payment-secret", str(data))
        self.assertEqual([row["at"] for row in route], sorted((row["at"] for row in route), reverse=True))

    async def test_terminal_deviation_preserves_mixed_piece_last_milestones(self):
        await self.source(status="completed", quantity=2)
        await self.piece(status="in_progress", supplier_dispatch_status="sent",
                         sent_to_supplier_at="2026-09-01T08:00:00Z", supplier_name="Supplier A")
        await self.piece("piece-b", unit_index=2, status="received", supplier_dispatch_status="received",
                         received_at="2026-09-01T12:00:00Z", responsible_employee_name="Actual receiver")
        data = await self.get()
        self.assertIsNotNone(data["route_notice"])
        first, second = data["items"][0]["pieces"]
        self.assertIn("إسناد المنتج إلى المورد", first["route_notice"])
        self.assertIn("تم الاستلام من المورد", second["route_notice"])
        self.assertIn("Actual receiver", second["route_notice"])
        for piece in (first, second):
            self.assertFalse(piece["current_location"]["known"])
            self.assertFalse(any(step["state"] == "done" and step["label"] == "جاهز من التجميع والعنونة"
                                 for step in piece["route_steps"]))
        for status in ("cancelled", "deleted", "under_review"):
            await self.db.unified_orders.update_one({"order_number": "10001"}, {"$set": {
                "order_status": status, "raw_by_source.salla_direct.status": {"slug": status}}})
            data = await self.get()
            self.assertIsNotNone(data["route_notice"])
            self.assertTrue(all(not p["current_location"]["known"] for p in data["items"][0]["pieces"]))

    async def test_completed_order_with_real_assembled_pieces_has_no_false_deviation(self):
        await self.source(status="completed")
        await self.piece(status="ready_for_assembly", assembly_status="ready",
                         assembly_ready_at="2026-09-01T12:00:00Z", assembly_ready_by_name="Assembler")
        await self.db.order_review_workflows.insert_one({"user_id": "owner-a", "order_number": "10001",
            "stage": "completed", "assembly_status": "completed", "completed_at": "2026-09-01T13:00:00Z"})
        data = await self.get()
        self.assertIsNone(data["route_notice"])
        self.assertIsNone(data["items"][0]["pieces"][0]["route_notice"])
        ready = [step for step in data["items"][0]["pieces"][0]["route_steps"]
                 if step["label"] == "جاهز من التجميع والعنونة"]
        self.assertEqual(ready[0]["state"], "done")

    async def test_unknown_status_title_and_generic_dates_do_not_invent_transition(self):
        await self.source()
        await self.db.order_activity_events_v2.insert_one({"user_id": "owner-a", "order_number": "10001",
            "source": "salla_history", "event_type": "status_changed", "title": "Unstructured private note",
            "occurred_at": "2026-09-01 15:00:00"})
        await self.db.order_review_workflows.insert_one({"user_id": "owner-a", "order_number": "10001",
            "completion_mode": "mezan_local_v1", "reviewed_at": "2026-09-01T12:00:00Z"})
        data = await self.get()
        self.assertIsNone(data["status_changed_at"])
        self.assertNotIn("private note", str(data))
        self.assertFalse(any(step["label"] == "تمت المراجعة — ميزان" for step in data["order_route_steps"]))

    async def test_inconsistent_line_and_piece_identity_remains_safe_for_consumer(self):
        await self.source()
        await self.piece(unit_index=-1)
        await self.piece("extra", unit_index=2)
        await self.piece("orphan-a", order_item_id="missing-line", unit_index=1)
        await self.piece("orphan-b", order_item_id="missing-line", unit_index=2)
        source = await self.db.unified_orders.find_one({"order_number": "10001"})
        raw_item = source["raw_by_source"]["salla_direct"]["items"][0]
        await self.db.unified_orders.update_one({"order_number": "10001"}, {"$push": {
            "raw_by_source.salla_direct.items": {**raw_item, "name": "Conflicting duplicate", "quantity": 4}}})
        data = await self.get()
        self.assertTrue(data["truncated"])
        self.assertEqual(len({item["order_item_id"] for item in data["items"]}), len(data["items"]))
        for item in data["items"]:
            self.assertLessEqual(len(item["pieces"]), item["quantity"])
            for piece in item["pieces"]:
                self.assertTrue(piece["unit_index"] is None or piece["unit_index"] >= 1)
                self.assertFalse(piece["current_location"]["known"])
            self.assertIn(item["coverage"], {"partial", "no_records"})
        orphan = next(item for item in data["items"] if item["order_item_id"] == "missing-line")
        self.assertEqual(orphan["quantity"], 2)
        self.assertEqual(len(orphan["pieces"]), 2)

    async def test_local_review_proof_keeps_salla_status_and_return_to_review_expires_authority(self):
        await self.source(status="under_review")
        await self.piece()
        await self.local_review()
        await self.db.order_activity_events_v2.insert_one({
            "user_id": "owner-a", "order_number": "10001", "source": "salla_history",
            "event_type": "status_changed", "to_status": "under_review", "occurred_at": "2026-08-25T22:00:00Z"})
        data = await self.get()
        self.assertEqual(data["status"], "under_review")
        self.assertEqual(data["status_changed_at"], "2026-08-25T22:00:00+00:00")
        self.assertIsNone(data["route_notice"])
        piece = data["items"][0]["pieces"][0]
        self.assertEqual(piece["current_location"]["page"], "بانتظار الإسناد للمورد")
        self.assertTrue(piece["current_location"]["known"])
        self.assertIsNone(piece["route_notice"])
        review = next(step for step in data["order_route_steps"] if step["source"] == "mezan")
        self.assertEqual(review["at"], "2026-08-26T00:00:00+00:00")
        fixture = os.environ.get("MZ_TRACKING_CONSUMER_FIXTURE")
        if fixture:
            Path(fixture).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # A new external return is evidence the old local review is stale.
        await self.db.order_activity_events_v2.insert_one({
            "user_id": "owner-a", "order_number": "10001", "source": "salla_history",
            "event_type": "status_changed", "to_status": "under_review", "occurred_at": "2026-08-27T01:00:00Z"})
        data = await self.get()
        self.assertEqual(data["status"], "under_review")
        self.assertFalse(data["items"][0]["pieces"][0]["current_location"]["known"])
        self.assertIsNotNone(data["route_notice"])
        self.assertEqual(sum(step["label"] == "تمت المراجعة — ميزان" for step in data["order_route_steps"]), 1)

    async def test_local_review_requires_matching_unsuperseded_completed_proof_and_payment(self):
        await self.source(status="under_review")
        await self.piece()
        await self.local_review()
        selector = {"_id": "synthetic-local-review"}
        for fields in ({"state": "processing"}, {"superseded_by": "new-attempt"},
                       {"user_id": "owner-b"}, {"order_number": "20002"}, {"completed_at": None}):
            operation = await self.db.order_review_completion_operations.find_one(selector)
            await self.db.order_review_completion_operations.update_one(selector, {"$set": fields})
            if "completed_at" in fields:
                await self.db.order_review_events.update_one({"operation_id": selector["_id"]},
                                                            {"$set": {"occurred_at": None}})
            self.assertFalse((await self.get())["items"][0]["pieces"][0]["current_location"]["known"])
            await self.db.order_review_completion_operations.replace_one(selector, operation)
        await self.db.order_review_events.update_one({"operation_id": selector["_id"]},
                                                    {"$set": {"occurred_at": "2026-08-26T00:00:00Z"}})
        await self.db.unified_orders.update_one({"order_number": "10001"}, {"$set": {
            "raw_by_source.salla_direct.payment_method": "bank", "raw_by_source.salla_direct.payment.status": "unpaid"}})
        self.assertFalse((await self.get())["items"][0]["pieces"][0]["current_location"]["known"])

    async def test_local_direct_assembly_and_v2_committed_proof(self):
        await self.source(status="under_review")
        binding = {"contract_version": 2, "client_request_id": str(uuid4()), "merchant_id": "owner-a",
                   "actor_id": "employee-a", "origin_session_ref": "s" * 43, "origin_session_epoch": 1,
                   "order_number": "10001", "approved_revision": 4, "approval_fingerprint": "a" * 64,
                   "approval_token_sha256": "b" * 64}
        await self.local_review(readback_binding=binding, revision=4, committed_revision=5,
                                result={"salla_status_sync": "not_requested"})
        await self.db.order_review_events.update_one({"operation_id": "synthetic-local-review"}, {"$set": {
            "readback_binding": binding, "committed_revision": 5}})
        await self.db.order_review_workflows.update_one({"order_number": "10001"}, {"$set": {
            "items": [{"order_item_id": "salla:10001:line", "quantity": 1, "preparation_route": "direct_assembly",
                       "direct_assembly_piece_ids": ["direct-a"]}]}})
        data = await self.get()
        self.assertEqual(data["status"], "under_review")
        self.assertTrue(data["items"][0]["pieces"][0]["current_location"]["known"])
        self.assertEqual(data["items"][0]["pieces"][0]["current_location"]["workspace"], "التجميع والعنونة")
        await self.db.order_review_events.update_one({"operation_id": "synthetic-local-review"},
                                                    {"$set": {"committed_revision": 99}})
        self.assertFalse((await self.get())["items"][0]["pieces"][0]["current_location"]["known"])

    async def test_unhashable_fractional_unit_indexes_do_not_break_response(self):
        await self.source(quantity=3)
        await self.piece(unit_index={"invalid": 1})
        await self.piece("piece-b", unit_index=1.5)
        await self.piece("piece-c", unit_index=True)
        data = await self.get()
        self.assertEqual(data["items"][0]["coverage"], "partial")
        self.assertTrue(all(piece["unit_index"] is None and not piece["current_location"]["known"]
                            for piece in data["items"][0]["pieces"]))

    async def test_missing_local_contract_proof_does_not_fall_back_to_salla_in_progress(self):
        await self.source()
        await self.piece()
        await self.local_review()
        self.assertTrue((await self.get())["items"][0]["pieces"][0]["current_location"]["known"])
        await self.db.order_review_completion_operations.update_one({"_id": "synthetic-local-review"},
                                                                   {"$set": {"superseded_by": "new-attempt"}})
        data = await self.get()
        self.assertEqual(data["status"], "in_progress")
        self.assertFalse(data["items"][0]["pieces"][0]["current_location"]["known"])
        self.assertIn("تعذّر تأكيد", data["route_notice"])


if __name__ == "__main__":
    unittest.main()
