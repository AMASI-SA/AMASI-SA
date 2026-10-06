"""Real Mongo detection feed: authenticated recipient and tenant boundaries."""
import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
import fulfillment_lifecycle as lc
from tests import test_salla_order_change_reconciliation_mongo as fixture
from salla_edit_notifications import employee_edit_notifications
from salla_edit_routes import make_salla_edit_router

class EditNotificationTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.SallaChangeMongoTests.cleanup
    replay = fixture.SallaChangeMongoTests.replay
    baseline = fixture.SallaChangeMongoTests.baseline
    changed = fixture.SallaChangeMongoTests.changed

    async def asyncSetUp(self):
        await fixture.SallaChangeMongoTests.asyncSetUp(self)
        flags = patch.dict(os.environ, {"ORDER_SALLA_EDIT_APPLICATION_ENABLED": "true"})
        flags.start(); self.addCleanup(flags.stop)
        await self.baseline()
        await self.replay(self.changed(options={"color": "black"}))
        self.user = {"id": "employee-1", "created_by": "owner", "role": "employee"}

    async def test_detection_before_apply_exposes_stop_action_and_source_options_read_only(self):
        before = await self.db[lc.AUDIT].find({}).sort("_id", 1).to_list(100)
        result = await employee_edit_notifications(self.db, user=self.user)
        self.assertEqual(len(result["notifications"]), 1)
        notice = result["notifications"][0]
        self.assertEqual(notice["old_options"], {"color": "red"})
        self.assertEqual(notice["new_options"], {"color": "black"})
        self.assertEqual(notice["required_action"], "stop_old_generation")
        self.assertEqual(notice["application_state"], "pending_application")
        self.assertEqual(notice["current_units"][0]["piece_id"], "piece-1")
        self.assertIsNone(notice["read_at"])
        self.assertEqual(await self.db[lc.AUDIT].find({}).sort("_id", 1).to_list(100), before)

    async def test_other_recipient_and_other_tenant_receive_nothing(self):
        for user in [{**self.user, "id": "employee-2"}, {**self.user, "created_by": "other-owner"}]:
            with self.subTest(user=user):
                self.assertEqual((await employee_edit_notifications(self.db, user=user))["notifications"], [])

    async def test_native_merchant_principal_uses_preserved_employee_identity(self):
        user = {"id": "owner", "role": "owner", "_session_client": "amasi_mobile", "_mobile_actor_id": "employee-1"}
        self.assertEqual(len((await employee_edit_notifications(self.db, user=user))["notifications"]), 1)
        user["_mobile_actor_id"] = "employee-2"
        self.assertEqual((await employee_edit_notifications(self.db, user=user))["notifications"], [])

    async def test_inactive_account_rejected(self):
        with self.assertRaises(HTTPException) as failure:
            await employee_edit_notifications(self.db, user={**self.user, "disabled": True})
        self.assertEqual(failure.exception.status_code, 403)

    async def test_route_requires_no_reviewer_right_and_cannot_select_another_recipient(self):
        async def authenticated(): return self.user
        app = FastAPI(); app.include_router(make_salla_edit_router(self.db, authenticated))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/order-change-edit-v1/my-notifications?employee_id=employee-2&user_id=other-owner")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json()["notifications"]), 1)
            self.user = {**self.user, "id": "employee-2"}
            response = await client.get("/order-change-edit-v1/my-notifications?employee_id=employee-1")
            self.assertEqual(response.json()["notifications"], [])
