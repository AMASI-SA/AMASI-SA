"""ASGI boundaries using the real isolated Mongo ADD fixture; only auth is synthetic."""
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import salla_add_routes as routes
from tests import test_salla_add_application_mongo as fixtures


class SallaAddRoutesTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixtures.SallaAddApplicationMongoTests.cleanup
    replay = fixtures.SallaAddApplicationMongoTests.replay
    payload = fixtures.SallaAddApplicationMongoTests.payload
    state = fixtures.SallaAddApplicationMongoTests.state

    async def asyncSetUp(self):
        await fixtures.SallaAddApplicationMongoTests.asyncSetUp(self)

        async def user():
            return {"id": "owner", "name": "Synthetic manager"}

        async def context(db, actor):
            return self.context

        auth = patch.object(routes, "_actor_context", context)
        auth.start()
        self.addCleanup(auth.stop)
        app = FastAPI()
        app.include_router(routes.make_salla_add_router(self.db, user))
        self.http = AsyncClient(transport=ASGITransport(app=app), base_url="http://local.test")
        self.addAsyncCleanup(self.http.aclose)
        self.path = "/order-change-add-v1/orders/order-1"

    async def test_schema_rejects_untrusted_commercial_input_and_invalid_fences(self):
        payload = await self.payload()
        before = await self.state()
        for delta in ({"price": 10}, {"expected_revision": True}, {"expected_revision": -1},
                      {"expected_generation": "short"}, {"employee_id": ""}, {"reason": "x"}):
            response = await self.http.post(self.path + "/apply", json={**payload, **delta})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(await self.state(), before)

    async def test_unprivileged_actor_cannot_read_or_apply(self):
        payload = await self.payload()
        before = await self.state()
        self.context = {"merchant_id": "owner", "actor_id": "worker", "is_owner": False, "permissions": set()}
        for response in (await self.http.get(self.path + "/pending"),
                         await self.http.post(self.path + "/apply", json=payload)):
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["detail"]["code"], "salla_add_permission_required")
        self.assertEqual(await self.state(), before)

    async def test_flag_off_hides_pending_and_denies_mutation(self):
        payload = await self.payload()
        before = await self.state()
        with patch.dict(os.environ, {"ORDER_SALLA_ADD_APPLICATION_ENABLED": "false"}):
            response = await self.http.get(self.path + "/pending")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"enabled": False, "changes": [], "employees": []})
            response = await self.http.post(self.path + "/apply", json=payload)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "salla_add_disabled")
        self.assertEqual(await self.state(), before)


if __name__ == "__main__":
    unittest.main()
