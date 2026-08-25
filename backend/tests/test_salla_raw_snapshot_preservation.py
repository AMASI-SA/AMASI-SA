"""Regression tests for rich Salla order data surviving sparse syncs."""

from orders_db import _merge_salla_raw_snapshot
from salla_marketing_attribution import preserve_salla_raw_attribution


def _rich_order():
    return {
        "id": 901,
        "reference_id": "3001",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "items": [{
            "id": 71,
            "sku": "AMS13070",
            "name": "أناقة طفلك في احتفالات الوطن",
            "quantity": 1,
            "options": [
                {"name": "المقاس", "value": {"name": "5 سنوات"}},
                {"name": "اللون", "value": {"name": "أخضر"}},
            ],
            "custom_fields": [{"name": "الاسم المطلوب", "value": "سلمان"}],
        }],
    }


def test_light_order_without_items_cannot_erase_rich_order_items():
    merged = _merge_salla_raw_snapshot(
        _rich_order(),
        {"id": 901, "reference_id": "3001", "status": {"slug": "processing", "name": "قيد التنفيذ"}},
    )
    assert merged["status"]["slug"] == "processing"
    assert merged["items"] == _rich_order()["items"]


def test_sparse_item_update_keeps_customer_options_and_custom_fields():
    merged = _merge_salla_raw_snapshot(
        _rich_order(),
        {"id": 901, "items": [{"id": 71, "sku": "AMS13070", "quantity": 2, "options": []}]},
    )
    item = merged["items"][0]
    assert item["quantity"] == 2
    assert item["options"] == _rich_order()["items"][0]["options"]
    assert item["custom_fields"] == _rich_order()["items"][0]["custom_fields"]


def test_richer_item_update_can_update_customer_options():
    merged = _merge_salla_raw_snapshot(
        _rich_order(),
        {"id": 901, "items": [{
            "id": 71,
            "sku": "AMS13070",
            "options": [{"name": "المقاس", "value": {"name": "6 سنوات"}}],
        }]},
    )
    assert merged["items"][0]["options"] == [
        {"name": "المقاس", "value": {"name": "6 سنوات"}},
    ]


def test_new_item_identity_does_not_inherit_another_items_options_by_position():
    merged = _merge_salla_raw_snapshot(
        _rich_order(),
        {"id": 901, "items": [{"id": 99, "sku": "NEW-SKU", "quantity": 1}]},
    )
    assert "options" not in merged["items"][0]
    assert "custom_fields" not in merged["items"][0]


def test_item_preservation_composes_with_explicit_attribution_correction():
    existing = _rich_order() | {"source": "snapchat"}
    incoming = {"id": 901, "reference_id": "3001", "source": "facebook"}
    attributed = preserve_salla_raw_attribution(existing, incoming)
    merged = _merge_salla_raw_snapshot(existing, attributed)

    assert merged["source"] == "facebook"
    assert merged["items"] == _rich_order()["items"]
