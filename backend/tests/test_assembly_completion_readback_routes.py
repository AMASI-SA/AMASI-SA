"""Actual completion GET dispatch, authorization and response contracts."""
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import preparation_piece_operations as pieces
import test_assembly_completion_v2_readback as fixture


class CompletionReadbackRouteTests(unittest.IsolatedAsyncioTestCase):
    reseal = fixture.BlockedCompletionTests.reseal
    provider_read = fixture.BlockedCompletionTests.provider_read
    snapshot = fixture.BlockedCompletionTests.snapshot

    async def asyncSetUp(self):
        await fixture.BlockedCompletionTests.asyncSetUp(self)
        # The print policy proves actual product units, not stored counters.
        self.order["items"] = [{"id": "line", "quantity": 2}]
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.items": self.order["items"]}})
        await self.db.mezan_preparation_pieces_v1.insert_many([
            {"user_id": "owner", "order_number": "synthetic-1", "piece_id": str(i),
             "order_item_id": "line", "unit_index": i + 1, "assembly_status": "ready"} for i in range(2)])
        await self.reseal()
        self.context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
                        "permissions": {"fulfillment.ready.read"}, "responsibilities": {"instant_ready"}}
        replacement = patch.object(pieces, "_actor_context", AsyncMock(side_effect=lambda *args: deepcopy(self.context)))
        replacement.start()
        self.addCleanup(replacement.stop)
        async def actor():
            return {"id": "employee", "role": "employee", "created_by": "owner"}
        app = FastAPI()
        app.include_router(pieces.make_preparation_piece_operations_router(self.db, actor))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def get(self):
        return await self.client.get("/preparation-work-v1/assembly/orders/synthetic-1/completion")

    async def test_blocked_v2_route_automatically_recovers_without_post_or_write(self):
        before = await self.snapshot()
        result = await self.get()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["ready"])
        self.assertFalse(result.json()["requires_attention"])
        self.assertEqual(self.provider.await_count, 1)
        self.assertEqual(await self.snapshot(), before)

    async def test_legacy_route_automatically_recovers_without_outbox_enrollment(self):
        await self.db.order_review_workflows.update_one({}, {"$unset": {"assembly_delivery": ""}})
        before = await self.snapshot()
        result = await self.get()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["ready"])
        self.assertEqual(self.provider.await_count, 1)
        self.assertEqual(await self.snapshot(), before)

    async def test_v2_alias_repair_denied_after_permission_revocation_keeps_source_unchanged(self):
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.status_slug": "in_progress"}})
        before = await self.snapshot()
        async def revoke(*args, **kwargs):
            self.context["permissions"].clear()
            self.context["responsibilities"].clear()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = revoke
        result = await self.get()
        self.assertEqual(result.status_code, 403, result.text)
        self.assertEqual(await self.snapshot(), before)
