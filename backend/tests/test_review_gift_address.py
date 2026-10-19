"""Gift review gates use provider evidence, never buyer geography."""
import unittest

import test_review_local_completion as local


class GiftAddressTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = local.LocalCompletionTests.asyncSetUp
    asyncTearDown = local.LocalCompletionTests.asyncTearDown
    source_payload = local.LocalCompletionTests.source_payload
    webhook = local.LocalCompletionTests.webhook
    saved = local.LocalCompletionTests.saved
    assert_completed = local.LocalCompletionTests.assert_completed
    assert_no_completion = local.LocalCompletionTests.assert_no_completion
    post = local.LocalCompletionTests.post

    async def set_gift(self, incomplete):
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.source": "buy_as_gift",
            "raw_by_source.salla_direct.address_incomplete": incomplete,
        }})

    async def test_incomplete_gift_blocks_without_completion(self):
        await self.set_gift(True)
        detail = await self.client.get("/order-reviews-v1/local-review?local_only=true")
        self.assertTrue(detail.json()["gift_address_review"]["blocked"])
        result = await self.post()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"]["code"], "gift_recipient_address_incomplete")
        await self.assert_no_completion()

    async def test_missing_or_non_boolean_evidence_blocks(self):
        for value in (None, "false", 0):
            with self.subTest(value=value):
                await self.set_gift(value)
                result = await self.post()
                self.assertEqual(result.status_code, 409, result.text)
                await self.assert_no_completion()

    async def test_provider_complete_gift_can_complete(self):
        await self.set_gift(False)
        await self.assert_completed(await self.post())

    async def test_normal_order_unchanged_despite_incomplete_address(self):
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.source": "store",
            "raw_by_source.salla_direct.address_incomplete": True,
            "is_gift": True,
        }})
        await self.assert_completed(await self.post())

    async def test_fresh_provider_evidence_unlocks_review(self):
        await self.set_gift(True)
        self.assertEqual((await self.post()).status_code, 409)
        await self.assert_no_completion()
        await self.set_gift(False)
        await self.assert_completed(await self.post())
