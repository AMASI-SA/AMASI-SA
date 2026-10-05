"""No network: exercise the real review provider helper against a fake transport."""
import unittest
import asyncio
from copy import deepcopy
from order_engine.mapper import map_salla_order
from unittest.mock import patch

import httpx
import order_review_routes as review
import order_review_completion as completion


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def run_sync(self, *, timeout=False, initial=False, failure=False, cancelled=False,
                       changed_before=False, changed_after=False, custom_cancel=False,
                       changed_spec=None, whole_call_timeout=False):
        data = {"id": 101, "reference_id": 1001, "date": "2026-09-26T12:00:00+00:00",
                "status": {"slug": "under_review", "name": "under_review"},
                "payment_method": "cod", "amounts": {"total": {"amount": 100, "currency": "SAR"}},
                "items": [{"id": "line-1", "product_id": "p", "name": "Synthetic", "quantity": 1}]}
        order = map_salla_order(data)
        if changed_before:
            data["items"][0]["quantity"] = 2
        if changed_spec:
            data["items"][0][changed_spec] = "Changed approved specification"
        state = {"reviewed": initial, "posts": 0, "reads": 0}
        async def transport(db, owner, method, path, **kwargs):
            self.assertEqual(owner, "synthetic-owner")
            if path == "/orders/items":
                return {"data": deepcopy(data["items"])}
            if path == "/orders/statuses":
                return {"data": [{"id": 12, "name": "تم المراجعة"}]}
            if method == "POST":
                self.assertEqual(path, "/orders/101/status")
                state["posts"] += 1
                if changed_after:
                    data["items"][0]["quantity"] = 2
                if not failure:
                    state["reviewed"] = True
                if whole_call_timeout:
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        state["transport_cancelled"] = True
                        raise
                if timeout:
                    raise httpx.ReadTimeout("synthetic response lost")
                return {"success": True}
            self.assertEqual(path, "/orders/101")
            state["reads"] += 1
            return {"data": {**deepcopy(data), "status": {
                "slug": "canceled" if cancelled else "under_review",
                "name": "ملغي" if cancelled else "بانتظار المراجعة",
                "customized": {"name": "ملغي" if custom_cancel else "تم المراجعة" if state["reviewed"] else "بانتظار المراجعة"},
            }}}

        with patch.object(review, "call_salla", transport), \
             patch.object(completion, "PROVIDER_CALL_TIMEOUT_SECONDS", 0.05 if whole_call_timeout else 45):
            first = await review._sync_salla_reviewed(object(), "synthetic-owner", order)
            second = await review._sync_salla_reviewed(object(), "synthetic-owner", order)
        return state, first, second

    async def test_successful_post_verified_retry_does_not_post_again(self):
        state, first, second = await self.run_sync()
        self.assertEqual(first, ("sent", None))
        self.assertEqual(second, ("sent", None))
        self.assertEqual(state["posts"], 1)

    async def test_lost_success_response_verified_no_duplicate_post(self):
        state, first, second = await self.run_sync(timeout=True)
        self.assertEqual((first, second), (("sent", None), ("sent", None)))
        self.assertEqual(state["posts"], 1)

    async def test_manual_reviewed_state_does_not_need_a_post(self):
        state, first, _ = await self.run_sync(initial=True)
        self.assertEqual(first, ("sent", None))
        self.assertEqual(state["posts"], 0)

    async def test_success_response_without_matching_readback_is_not_success(self):
        _, first, _ = await self.run_sync(failure=True)
        self.assertEqual(first[0], "pending")

    async def test_cancelled_provider_order_never_posts(self):
        state, first, _ = await self.run_sync(cancelled=True)
        self.assertEqual(first[0], "pending")
        self.assertEqual(state["posts"], 0)

    async def test_unavailable_readback_never_posts(self):
        async def transport(*args, **kwargs):
            self.assertEqual(args[2], "GET")
            raise httpx.ConnectError("synthetic unavailable")
        order = map_salla_order({"id": 101, "reference_id": 1001, "date": "2026-09-26T12:00:00+00:00"})
        with patch.object(review, "call_salla", transport):
            result = await review._sync_salla_reviewed(object(), "synthetic-owner", order)
        self.assertEqual(result[0], "pending")

    async def test_provider_product_change_before_post_blocks_write(self):
        state, first, _ = await self.run_sync(changed_before=True)
        self.assertEqual(first[0], "pending")
        self.assertEqual(state["posts"], 0)

    async def test_provider_product_change_after_post_is_not_approved(self):
        state, first, _ = await self.run_sync(changed_after=True)
        self.assertEqual(first[0], "pending")
        self.assertEqual(state["posts"], 1)

    async def test_custom_cancellation_blocks_even_with_under_review_base(self):
        state, first, _ = await self.run_sync(custom_cancel=True)
        self.assertEqual(first[0], "pending")
        self.assertEqual(state["posts"], 0)

    async def test_direct_product_specification_changes_block_without_webhook(self):
        for field in ("size", "color", "material"):
            with self.subTest(field=field):
                state, first, _ = await self.run_sync(changed_spec=field)
                self.assertEqual(first[0], "pending")
                self.assertEqual(state["posts"], 0)

    async def test_whole_provider_call_deadline_cancels_retries_and_verifies(self):
        state, first, second = await self.run_sync(whole_call_timeout=True)
        self.assertEqual((first, second), (("sent", None), ("sent", None)))
        self.assertTrue(state["transport_cancelled"])
        self.assertEqual(state["posts"], 1)


if __name__ == "__main__":
    unittest.main()
