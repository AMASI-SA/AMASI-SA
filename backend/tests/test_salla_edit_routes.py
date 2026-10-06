"""ASGI contracts with a real Mongo edit fixture; only authenticated identity is synthetic."""
import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import salla_edit_routes as routes
from tests import test_salla_edit_application_mongo as fixtures


class SallaEditRoutesTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixtures.SallaEditApplicationMongoTests.cleanup
    replay = fixtures.SallaEditApplicationMongoTests.replay
    payload = fixtures.SallaEditApplicationMongoTests.payload
    state = fixtures.SallaEditApplicationMongoTests.state

    async def asyncSetUp(self):
        await fixtures.SallaEditApplicationMongoTests.asyncSetUp(self)

        async def user():
            return {"id": "owner", "name": "Synthetic manager"}

        async def context(db, actor):
            return self.context

        auth = patch.object(routes, "_actor_context", context)
        auth.start()
        self.addCleanup(auth.stop)
        app = FastAPI()
        app.include_router(routes.make_salla_edit_router(self.db, user))
        self.http = AsyncClient(transport=ASGITransport(app=app), base_url="http://local.test")
        self.addAsyncCleanup(self.http.aclose)
        self.path = "/order-change-edit-v1/orders/order-1"

    async def test_commercial_payload_and_invalid_fences_rejected(self):
        payload = await self.payload()
        before = await self.state()
        for delta in ({"options": {"color": "client-forged"}}, {"price": 100}, {"expected_revision": True},
                      {"expected_revision": -1}, {"expected_generation": "short"}, {"units": []}, {"employee_id": ""}):
            response = await self.http.post(self.path + "/apply", json={**payload, **delta})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(await self.state(), before)

    async def test_read_apply_retry_reopen_contract(self):
        response = await self.http.get(self.path + "/pending")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["changes"])
        payload = await self.payload()
        response = await self.http.post(self.path + "/apply", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "applied")
        retry = await self.http.post(self.path + "/apply", json=payload)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["idempotent_replay"])
        reopened = await self.http.get(self.path + "/pending")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertFalse(reopened.json()["changes"])

    async def test_unauthorized_actor_cannot_read_or_apply(self):
        payload = await self.payload()
        self.context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False, "permissions": set()}
        before = await self.state()
        for response in (await self.http.get(self.path + "/pending"), await self.http.post(self.path + "/apply", json=payload)):
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(await self.state(), before)

    async def test_disabled_contract_hides_actions_and_denies_application(self):
        payload = await self.payload()
        before = await self.state()
        with patch.dict(os.environ, {"ORDER_SALLA_EDIT_APPLICATION_ENABLED": "false"}):
            pending = await self.http.get(self.path + "/pending")
            self.assertEqual(pending.status_code, 200)
            self.assertFalse(pending.json()["enabled"])
            self.assertFalse(pending.json()["changes"])
            applied = await self.http.post(self.path + "/apply", json=payload)
            self.assertEqual(applied.status_code, 409, applied.text)
        self.assertEqual(await self.state(), before)
