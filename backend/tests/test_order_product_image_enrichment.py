"""Tests for lightweight local product image enrichment."""
import asyncio

from order_engine.models import OrderItemDTO
from order_engine.product_image_enrichment import enrich_order_item_images


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def limit(self, _limit):
        return self

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row
        return iterate()


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, _query, _projection):
        return FakeCursor(self.rows)


class FakeDatabase:
    def __init__(self):
        self.collections = {
            "mezan_products_v2": FakeCollection([{
                "salla_product_id": "product-1",
                "sku": "AMS11351",
                "main_image": "https://cdn.example.com/main.jpg",
                "images": [{
                    "url": "https://cdn.example.com/second.jpg",
                }],
                "variants": [{
                    "id": "variant-1",
                    "sku": "AMS11351",
                    "image": "https://cdn.example.com/variant.jpg",
                }],
            }]),
            "mezan_product_image_profiles_v2": FakeCollection([{
                "salla_product_id": "product-1",
                "default_image_url": "https://cdn.example.com/default.jpg",
            }]),
            "salla_products": FakeCollection([]),
            "products": FakeCollection([]),
        }

    def __getitem__(self, name):
        return self.collections[name]


def item(**updates):
    values = {
        "order_item_id": "order-item-1",
        "product_id": "product-1",
        "variant_id": "variant-1",
        "sku": "AMS11351",
        "name": "منتج أطفال",
        "quantity": 1,
    }
    values.update(updates)
    return OrderItemDTO(**values)


def test_missing_image_is_recovered_from_v2_catalog_and_profile():
    enriched = asyncio.run(enrich_order_item_images(
        FakeDatabase(),
        user_id="owner-1",
        items=[item()],
    ))

    assert enriched[0].image_url == "https://cdn.example.com/default.jpg"
    assert enriched[0].image_urls == [
        "https://cdn.example.com/default.jpg",
        "https://cdn.example.com/main.jpg",
        "https://cdn.example.com/second.jpg",
        "https://cdn.example.com/variant.jpg",
    ]


def test_existing_order_image_keeps_catalog_images_as_browser_fallbacks():
    enriched = asyncio.run(enrich_order_item_images(
        FakeDatabase(),
        user_id="owner-1",
        items=[item(
            image_url="https://cdn.example.com/broken.jpg",
            image_urls=["https://cdn.example.com/broken.jpg"],
        )],
    ))

    assert enriched[0].image_url == "https://cdn.example.com/broken.jpg"
    assert "https://cdn.example.com/default.jpg" in enriched[0].image_urls
    assert "https://cdn.example.com/variant.jpg" in enriched[0].image_urls
