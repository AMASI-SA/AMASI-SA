import json
from types import SimpleNamespace

import pytest

from ai_operational_context_v2 import (
    MAX_ORDER_SAMPLE_LIMIT,
    build_orders_v2_operational_context,
)


class Dumpable:
    def __init__(self, payload):
        self.payload = payload
        self.order_number = payload.get("order_number")
        self.items = payload.get("items", [])

    def model_dump(self, mode="json"):
        assert mode == "json"
        return self.payload


class FakeItemService:
    def __init__(self, items):
        self.items = items

    async def get_items_for_order(self, *, user_id, order_number):
        assert user_id == "owner-1"
        assert order_number == "275190001"
        return self.items


@pytest.mark.asyncio
async def test_orders_v2_context_preserves_commerce_and_removes_customer_pii():
    order = Dumpable(
        {
            "schema_version": 1,
            "order_id": "source-order-secret",
            "order_number": "275190001",
            "created_at": "2026-07-31T00:00:00Z",
            "status": "completed",
            "status_native": "تم التنفيذ",
            "is_new": False,
            "is_gift": False,
            "customer": {
                "name": "Arafat Private",
                "mobile": "+966500000000",
                "email": "private@example.com",
                "shipping_address": {"street": "Private Street"},
            },
            "source": {
                "provider": "salla",
                "channel": "online",
                "utm_source": "snapchat",
                "campaign_id": "campaign-1",
            },
            "payment": {
                "method": "mada",
                "status": "paid",
                "paid_amount": 208.83,
                "remaining_amount": 0,
                "transaction_reference": "payment-secret",
                "card_last_four": "9894",
            },
            "shipping": {
                "company": "SMSA",
                "method": "delivery",
                "status": "delivered",
                "tracking_number": "tracking-secret",
                "address": {"city": "Riyadh", "street": "Private Street"},
            },
            "totals": {
                "currency": "SAR",
                "subtotal": 198.0,
                "shipping": 24.07,
                "discount": 28.71,
                "tax_percent": 8.0,
                "tax_reported_by_source": 15.47,
                "total": 208.83,
            },
            "customer_notes": "call private phone",
            "items": [],
        }
    )
    item = Dumpable(
        {
            "schema_version": 1,
            "order_item_id": "item-secret",
            "order_id": "source-order-secret",
            "order_number": "275190001",
            "line_index": 0,
            "product_id": "product-1",
            "variant_id": "variant-1",
            "sku": "AMS13029",
            "name": "مق هاف مليون الصيفي",
            "quantity": 1,
            "currency": "SAR",
            "unit_price": 99.0,
            "discount": 14.36,
            "tax_reported_by_source": 6.77,
            "total": 91.41,
            "color": "السماوي",
            "options": [{"name": "إضافة اسم", "value": "Khalid Private"}],
            "custom_fields": [{"name": "اسم العميل", "value": "Khalid Private"}],
        }
    )

    async def list_loader(repository, **kwargs):
        del repository
        assert kwargs["limit"] == 1
        return SimpleNamespace(items=[order], skipped_invalid=0)

    async def detail_loader(repository, **kwargs):
        del repository
        assert kwargs["order_number"] == "275190001"
        return order

    context = await build_orders_v2_operational_context(
        object(),
        user_id="owner-1",
        sample_limit=1,
        repository_factory=lambda db: object(),
        item_service_factory=lambda db: FakeItemService([item]),
        list_loader=list_loader,
        detail_loader=detail_loader,
    )

    encoded = json.dumps(context, ensure_ascii=False)
    assert context["source"] == "mezan_orders_v2_canonical"
    assert context["discovery_mode"] is True
    assert context["precomputed_business_conclusions"] is False
    assert context["sample_count"] == 1
    assert context["orders"][0]["sample_id"] == "order_sample_01"
    assert context["orders"][0]["totals"]["tax_reported_by_source"] == 15.47
    assert context["orders"][0]["items"][0]["tax_reported_by_source"] == 6.77
    assert "orders[].totals.tax_reported_by_source<number>" in context["observed_paths"]
    assert "Arafat Private" not in encoded
    assert "+966500000000" not in encoded
    assert "private@example.com" not in encoded
    assert "Private Street" not in encoded
    assert "275190001" not in encoded
    assert "payment-secret" not in encoded
    assert "tracking-secret" not in encoded
    assert "Khalid Private" not in encoded
    assert context["orders"][0]["items"][0]["option_names"] == ["إضافة اسم"]
    assert context["orders"][0]["items"][0]["custom_field_names"] == ["اسم العميل"]


@pytest.mark.asyncio
async def test_orders_v2_context_caps_sample_limit():
    captured = {}

    async def list_loader(repository, **kwargs):
        del repository
        captured.update(kwargs)
        return SimpleNamespace(items=[], skipped_invalid=0)

    context = await build_orders_v2_operational_context(
        object(),
        user_id="owner-1",
        sample_limit=500,
        repository_factory=lambda db: object(),
        item_service_factory=lambda db: FakeItemService([]),
        list_loader=list_loader,
        detail_loader=lambda *args, **kwargs: None,
    )

    assert captured["limit"] == MAX_ORDER_SAMPLE_LIMIT
    assert context["requested_sample_limit"] == MAX_ORDER_SAMPLE_LIMIT
    assert context["sample_count"] == 0
