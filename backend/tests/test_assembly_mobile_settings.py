import os
import uuid
import unittest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from assembly_mobile_settings import make_assembly_mobile_settings_router, trusted_admin_url
from mobile_app_permissions import MOBILE_APP_ACCESS


class AssemblyMobileSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI")
        if not uri:
            self.skipTest("isolated MZ2_TEST_MONGO_URI required")
        self.mongo = AsyncIOMotorClient(uri)
        self.db = self.mongo["assembly_settings_test_" + uuid.uuid4().hex]
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner"},
            {"id": "manager", "role": "employee", "created_by": "owner"},
            {"id": "staff", "role": "employee", "created_by": "owner"},
            {"id": "other", "role": "owner"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_many([
            {"owner_user_id": "owner", "user_id": "manager", "enabled": True,
             "permissions": ["app.role.manager"]},
            {"owner_user_id": "owner", "user_id": "staff", "enabled": True,
             "permissions": ["app.page.assembly_shipping"]},
        ])
        self.user = {"id": "owner", "role": "owner"}
        async def current_user():
            return self.user
        app = FastAPI()
        app.include_router(make_assembly_mobile_settings_router(self.db, current_user))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated")

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    def actor(self, identity):
        # Native bridge intentionally looks like owner; real actor must decide.
        self.user = {"id": "owner", "role": "owner", "_mobile_actor_id": identity,
                     "_mobile_owner_id": "owner"}

    async def test_default_off_manager_change_shared_employee_read_and_scope(self):
        self.actor("staff")
        response = await self.client.get("/assembly/settings")
        self.assertEqual(response.json(), {"show_salla_order_link": False, "can_manage": False})
        self.assertEqual((await self.client.patch("/assembly/settings", json={"show_salla_order_link": True})).status_code, 403)
        self.actor("manager")
        self.assertEqual((await self.client.patch("/assembly/settings", json={"show_salla_order_link": True})).status_code, 200)
        self.actor("staff")
        self.assertTrue((await self.client.get("/assembly/settings")).json()["show_salla_order_link"])
        self.user = {"id": "other", "role": "owner"}
        self.assertFalse((await self.client.get("/assembly/settings")).json()["show_salla_order_link"])

    async def test_revoked_manager_and_disabled_actor_cannot_write(self):
        self.actor("manager")
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "manager"}, {"$set": {"permissions": ["app.page.assembly_shipping"]}})
        self.assertEqual((await self.client.patch("/assembly/settings", json={"show_salla_order_link": True})).status_code, 403)
        await self.db.users.update_one({"id": "manager"}, {"$set": {"disabled": True}})
        self.assertEqual((await self.client.get("/assembly/settings")).status_code, 403)
        self.assertIsNone(await self.db.settings.find_one({"user_id": "owner"}))

    async def test_link_is_current_tenant_bound_and_disabled_immediately(self):
        await self.db.settings.insert_one({"user_id": "owner", "assembly_shipping": {"show_salla_order_link": True}, "unrelated": 7})
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "123", "raw_by_source": {
            "salla_direct": {"id": 987, "reference_id": 123, "urls": {"admin": "https://s.salla.sa/orders/987"}}}})
        self.actor("staff")
        self.assertEqual((await self.client.get("/assembly/orders/123/salla-link")).json()["salla_admin_url"], "https://s.salla.sa/orders/987")
        self.user = {"id": "other", "role": "owner"}
        self.assertIsNone((await self.client.get("/assembly/orders/123/salla-link")).json()["salla_admin_url"])
        self.actor("manager")
        await self.client.patch("/assembly/settings", json={"show_salla_order_link": False})
        self.actor("staff")
        self.assertEqual((await self.client.get("/assembly/orders/123/salla-link")).json(), {"show_salla_order_link": False, "salla_admin_url": None})
        self.assertEqual((await self.db.settings.find_one({"user_id": "owner"}))["unrelated"], 7)

    async def test_patch_does_not_coerce_or_accept_owner_input(self):
        for payload in ({"show_salla_order_link": "true"}, {"show_salla_order_link": 1},
                        {"show_salla_order_link": True, "user_id": "other"}):
            self.assertEqual((await self.client.patch("/assembly/settings", json=payload)).status_code, 422)


def test_untrusted_admin_urls_are_rejected():
    for url in ("https://evil.test/orders/987", "javascript:alert(1)", "https://s.salla.sa@evil.test/orders/987",
                "https://s.salla.sa/orders/123", "https://s.salla.sa/orders/987?redirect=evil", "http://s.salla.sa/orders/987"):
        assert trusted_admin_url({"id": 987, "urls": {"admin": url}}) is None
