"""Real local Mongo gallery caching, with only the read-only Salla boundary fake."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

import order_review_routes as review
from order_engine.mapper import map_salla_order


def order(number="gallery-1"):
    return map_salla_order({
        "id": "source-" + number, "reference_id": number,
        "date": "2026-11-06T00:00:00+00:00",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "amounts": {"total": {"amount": 10, "currency": "SAR"}},
        "items": [{"id": "line-" + number, "product_id": "product-one", "sku": "ONE",
                   "name": "Synthetic one-photo product", "quantity": 1}],
    })


class GalleryReadPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; never use production")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["review_gallery_" + uuid4().hex]
        self.addCleanup(self.mongo.close)
        await self.db.command("ping")
        self.when = datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.now = patch.object(review, "_now", lambda: self.when.isoformat())
        self.now.start(); self.addCleanup(self.now.stop)
        self.product = {"id": "product-one", "sku": "ONE", "name": "Synthetic product",
                        "images": [{"url": "https://images.example.test/one.png"}]}

    async def asyncTearDown(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("review_gallery_"))
            await self.mongo.drop_database(self.db.name)

    async def provider(self, db, owner, method, path, **kwargs):
        self.assertIs(db, self.db)
        self.assertEqual(method, "GET", "Gallery reads must never perform provider writes")
        if path == "/products/product-one":
            return {"data": dict(self.product)}
        self.assertEqual(path, "/products")
        self.assertEqual(kwargs["params"], {"keyword": "ONE", "per_page": 10})
        return {"data": [dict(self.product)]}

    async def read(self, number="gallery-1", owner="owner", **kwargs):
        return await review._review_item_identities(self.db, owner, order(number), **kwargs)

    async def test_confirmed_single_gallery_not_downloaded_on_each_order(self):
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            first = await self.read()
            self.assertEqual(provider.await_count, 2, "Keep detail plus exact-SKU completeness check")
            for index in range(20):
                current = await self.read("new-order-" + str(index))
                self.assertEqual(current[0].image_urls, first[0].image_urls)
            self.assertEqual(provider.await_count, 2, "Repeated reviews must reuse confirmed single-image gallery")

    async def test_legacy_single_image_is_not_reinterpreted_as_complete(self):
        await self.db.salla_products.insert_one({"user_id": "owner", "product_id": "product-one", "sku": "ONE",
            "gallery_refreshed_at": self.when.isoformat(), "images": list(self.product["images"])})
        self.product["images"] = [*self.product["images"], {"url": "https://images.example.test/two.png"}]
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            current = await self.read()
        self.assertEqual(provider.await_count, 1)
        self.assertIn("https://images.example.test/two.png", current[0].image_urls)

    async def test_confirmation_expires_and_reloads_new_full_gallery(self):
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            await self.read()
            self.when += timedelta(minutes=16)
            self.product["images"] = [*self.product["images"], {"url": "https://images.example.test/new.png"}]
            current = await self.read("after-expiry")
        self.assertEqual(provider.await_count, 3)
        self.assertIn("https://images.example.test/new.png", current[0].image_urls)

    async def test_partial_listing_never_proves_complete_single_gallery(self):
        async def partial(db, owner, method, path, **kwargs):
            result = await self.provider(db, owner, method, path, **kwargs)
            if path == "/products":
                result["data"] = [{"id": "product-one", "sku": "ONE", "main_image": "https://images.example.test/one.png"}]
            return result
        with patch.object(review, "call_salla", AsyncMock(side_effect=partial)) as provider:
            await self.read(); await self.read("next-order")
        self.assertEqual(provider.await_count, 4, "Partial listing must remain eligible for a later full refresh")

    async def test_unmatched_listing_never_proves_complete_single_gallery(self):
        async def wrong(db, owner, method, path, **kwargs):
            result = await self.provider(db, owner, method, path, **kwargs)
            if path == "/products":
                result["data"] = [{**self.product, "sku": "OTHER"}]
            return result
        with patch.object(review, "call_salla", AsyncMock(side_effect=wrong)) as provider:
            await self.read(); await self.read("next-order")
        self.assertEqual(provider.await_count, 4)

    async def test_failed_listing_never_proves_complete_single_gallery(self):
        async def failed(db, owner, method, path, **kwargs):
            if path == "/products":
                raise review.SallaError("synthetic provider unavailable", status_code=503)
            return await self.provider(db, owner, method, path, **kwargs)
        with patch.object(review, "call_salla", AsyncMock(side_effect=failed)) as provider:
            await self.read(); await self.read("next-order")
        self.assertEqual(provider.await_count, 6)
        self.assertEqual(await self.db.salla_products.count_documents({}), 0)

    async def test_future_or_malformed_proof_timestamp_is_not_fresh(self):
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            await self.read()
            for index, timestamp in enumerate(["invalid", (self.when + timedelta(hours=1)).isoformat(),
                                                self.when.replace(tzinfo=None).isoformat()]):
                await self.db.salla_products.update_one({"user_id": "owner", "product_id": "product-one"},
                    {"$set": {"review_gallery_confirmation_v1.checked_at": timestamp}})
                await self.read("timestamp-" + str(index))
            self.assertEqual(provider.await_count, 8, "Corrupt/future/naive timestamps cannot extend confirmation")

    async def test_changed_cache_content_cannot_reuse_old_confirmation(self):
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            await self.read()
            await self.db.salla_products.update_one({"user_id": "owner", "product_id": "product-one"},
                {"$set": {"images": [{"url": "https://images.example.test/partial-replacement.png"}]}})
            await self.read("changed-cache")
        self.assertEqual(provider.await_count, 4, "A partial product sync must invalidate the old image-content proof")

    async def test_confirmation_does_not_cross_merchants(self):
        with patch.object(review, "call_salla", AsyncMock(side_effect=self.provider)) as provider:
            await self.read(owner="another-owner")
            await self.read(owner="owner")
        self.assertEqual(provider.await_count, 4)

    async def test_local_only_never_fetches_or_writes_gallery(self):
        with patch.object(review, "call_salla", AsyncMock()) as provider:
            await self.read(local_only=True)
        provider.assert_not_awaited()
        self.assertEqual(await self.db.salla_products.count_documents({}), 0)
