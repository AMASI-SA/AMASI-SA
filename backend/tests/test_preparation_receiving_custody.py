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

from mobile_app_permissions import MOBILE_APP_ACCESS, MOBILE_APP_ACCESS_OWNER_FIELD
from mobile_app_request_context import mobile_app_request_user
import preparation_piece_operations as operations


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
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
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
               "assignment_status": "assigned", "services": [], **values}
        await self.db[operations.PIECES].insert_one(row)

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
