from salla_orders_v3.compatibility import (
    COMPATIBILITY_FIELDS,
    build_compatibility_order,
)
from salla_orders_v3.merge_policy import decide_shadow_merge
from salla_orders_v3.normalizer import normalize_order_items
from salla_orders_v3.parity import (
    compare_attribution_parity,
    compare_fulfillment_parity,
    compare_qoyod_parity,
)


def _base_order():
    return {
        "id": 901,
        "reference_id": "3001",
        "created_at": "2026-08-30T09:00:00+03:00",
        "updated_at": "2026-08-30T10:00:00+03:00",
        "status": {"name": "بانتظار المراجعة", "slug": "under_review"},
        "payment": {"status": "paid", "method": "credit_card"},
        "payment_method": "credit_card",
        "amounts": {
            "sub_total": {"amount": 100, "currency": "SAR"},
            "discounts": {"amount": 10, "currency": "SAR"},
            "tax": {"amount": 13.5, "currency": "SAR"},
            "total": {"amount": 103.5, "currency": "SAR"},
        },
        "campaign_id": "cmp-1",
        "utm_source": "snapchat",
    }


def test_adapter_preserves_current_contract_and_adds_auditable_items():
    raw_items = [{
        "id": 7,
        "product_id": 11,
        "variant": {"id": 12, "sku": "VAR-12"},
        "name": "منتج",
        "quantity": 2,
        "options": {"المقاس": "XL"},
        "custom_fields": {"الاسم": "نورة"},
        "amounts": {"price": {"amount": 50}, "total": {"amount": 100}},
    }]
    normalized = normalize_order_items(raw_items, order_number="3001")
    doc = build_compatibility_order(
        _base_order(),
        normalized_items=normalized,
        items_sync_status="succeeded",
        items_payload_valid=True,
    )

    assert COMPATIBILITY_FIELDS <= set(doc)
    assert doc["order_number"] == "3001"
    assert doc["order_id"] == "901"
    assert doc["order_status_slug"] == "under_review"
    assert doc["payment_status"] == "paid"
    assert doc["products"][0]["order_item_id"] == "7"
    assert doc["products"][0]["variant_id"] == "12"
    assert doc["products"][0]["sku"] == "VAR-12"
    assert doc["products"][0]["options"][0]["value"] == "XL"
    assert doc["products"][0]["custom_fields"][0]["value"] == "نورة"
    assert doc["products"][0]["raw_item"] == raw_items[0]
    assert doc["provider_created_at"] == _base_order()["created_at"]
    assert doc["provider_updated_at"] == _base_order()["updated_at"]
    assert doc["items_sync_status"] == "succeeded"
    assert doc["items_payload_valid"] is True
    assert doc["items_count"] == 1
    assert doc["campaign_id"] == "cmp-1"
    assert doc["utm_source"] == "snapchat"


def test_adapter_uses_stable_fallback_item_id_and_preserves_zero_quantity():
    normalized = normalize_order_items(
        [{"sku": "ZERO", "name": "منتج", "quantity": 0}],
        order_number="3001",
    )
    doc = build_compatibility_order(
        _base_order(),
        normalized_items=normalized,
        items_sync_status="succeeded",
        items_payload_valid=True,
    )

    product = doc["products"][0]
    assert product["order_item_id"].startswith("salla:3001:generated:")
    assert product["quantity"] == 0


def test_merge_policy_never_clears_rich_items_on_failure_or_unfetched_light_data():
    existing = {
        "provider_updated_at": "2026-08-30T10:00:00+00:00",
        "items_synced_at": "2026-08-30T10:01:00+00:00",
        "products": [{"order_item_id": "7", "options": [{"name": "المقاس", "value": "XL"}]}],
    }

    failed = decide_shadow_merge(
        existing,
        {"provider_updated_at": "2026-08-30T10:02:00+00:00", "products": []},
        items_sync_status="failed",
        items_payload_valid=False,
    )
    unfetched = decide_shadow_merge(
        existing,
        {"provider_updated_at": "2026-08-30T10:02:00+00:00", "products": []},
        items_sync_status="not_requested",
        items_payload_valid=False,
    )

    assert failed["products"] == existing["products"]
    assert unfetched["products"] == existing["products"]


def test_merge_policy_rejects_stale_base_but_accepts_newer_successful_item_read():
    existing = {
        "provider_updated_at": "2026-08-30T11:00:00+00:00",
        "items_synced_at": "2026-08-30T11:01:00+00:00",
        "order_status_slug": "processing",
        "products": [{"order_item_id": "7", "quantity": 1}],
    }
    incoming = {
        "provider_updated_at": "2026-08-30T10:00:00+00:00",
        "items_synced_at": "2026-08-30T11:02:00+00:00",
        "order_status_slug": "under_review",
        "products": [{"order_item_id": "7", "quantity": 2}],
    }

    merged = decide_shadow_merge(
        existing,
        incoming,
        items_sync_status="succeeded",
        items_payload_valid=True,
    )

    assert merged["order_status_slug"] == "processing"
    assert merged["provider_updated_at"] == existing["provider_updated_at"]
    assert merged["products"][0]["quantity"] == 2
    assert merged["items_synced_at"] == incoming["items_synced_at"]


def test_stale_failed_attempt_cannot_downgrade_newer_success_metadata():
    existing = {
        "items_synced_at": "2026-08-30T11:02:00+00:00",
        "items_sync_status": "succeeded",
        "items_payload_valid": True,
        "products": [{"order_item_id": "7", "quantity": 2}],
        "sync_revision": 4,
    }
    merged = decide_shadow_merge(
        existing,
        {
            "items_synced_at": "2026-08-30T11:01:00+00:00",
            "items_sync_error": "TimeoutError",
            "products": [],
            "sync_revision": 3,
        },
        items_sync_status="failed",
        items_payload_valid=False,
    )

    assert merged["items_sync_status"] == "succeeded"
    assert merged["items_payload_valid"] is True
    assert merged.get("items_sync_error") is None
    assert merged["products"] == existing["products"]
    assert merged["sync_revision"] == 5


def test_failed_items_read_is_never_recorded_as_authoritative_empty():
    candidate = build_compatibility_order(
        _base_order(),
        normalized_items=[],
        items_sync_status="failed",
        items_payload_valid=False,
        items_sync_error="TimeoutError",
        items_attempted_at="2026-08-30T11:02:00+00:00",
    )
    merged = decide_shadow_merge(
        {},
        candidate,
        items_sync_status="failed",
        items_payload_valid=False,
    )

    assert merged["products"] == []
    assert merged["items_authoritative"] is False
    assert merged["items_authoritative_empty"] is False
    assert merged["needs_items_enrichment"] is True
    assert merged["items_sync_status"] == "failed"
    assert merged["items_sync_error"] == "TimeoutError"

    fulfillment = compare_fulfillment_parity(
        {"products": []},
        merged,
    )
    assert fulfillment["passed"] is False
    assert fulfillment["v3_items_authoritative"] is False


def test_not_called_items_endpoint_is_never_recorded_as_authoritative_empty():
    candidate = build_compatibility_order(
        _base_order(),
        normalized_items=[],
        items_sync_status="not_requested",
        items_payload_valid=False,
        items_attempted_at=None,
    )
    merged = decide_shadow_merge(
        {},
        candidate,
        items_sync_status="not_requested",
        items_payload_valid=False,
    )

    assert merged["items_authoritative"] is False
    assert merged["items_authoritative_empty"] is False
    assert merged["needs_items_enrichment"] is True


def test_successful_empty_items_read_is_explicitly_authoritative():
    candidate = build_compatibility_order(
        _base_order(),
        normalized_items=[],
        items_sync_status="succeeded",
        items_payload_valid=True,
        items_attempted_at="2026-08-30T11:02:00+00:00",
        items_synced_at="2026-08-30T11:02:00+00:00",
    )
    merged = decide_shadow_merge(
        {
            "products": [{"order_item_id": "old"}],
            "items_authoritative": True,
            "items_synced_at": "2026-08-30T11:01:00+00:00",
        },
        candidate,
        items_sync_status="succeeded",
        items_payload_valid=True,
    )

    assert merged["products"] == []
    assert merged["items_authoritative"] is True
    assert merged["items_authoritative_empty"] is True
    assert merged["needs_items_enrichment"] is False


def test_three_parity_comparators_are_strict_and_side_effect_free():
    order = build_compatibility_order(
        _base_order(),
        normalized_items=normalize_order_items(
            [{"id": 7, "sku": "A", "quantity": 2, "options": {"المقاس": "XL"}}],
            order_number="3001",
        ),
        items_sync_status="succeeded",
        items_payload_valid=True,
    )
    order.update({"signal_revision": 1, "items_success_signal_revision": 1})

    fulfillment = compare_fulfillment_parity(order, order)
    qoyod = compare_qoyod_parity(
        {
            "eligible": True,
            "payload": {"x": 1},
            "idempotency_key": "same",
            "provider_write_reached": False,
        },
        {
            "eligible": True,
            "payload": {"x": 1},
            "idempotency_key": "same",
            "provider_write_reached": False,
        },
    )
    attribution = compare_attribution_parity(
        [{
            "order_number": "3001",
            "campaign_id": "cmp-1",
            "utm_source": "snapchat",
            "revenue": 103.5,
        }],
        [{
            "order_number": "3001",
            "campaign_id": "cmp-1",
            "utm_source": "snapchat",
            "revenue": 103.5,
        }],
    )

    assert fulfillment["passed"] is True
    assert qoyod["passed"] is True
    assert qoyod["idempotency_key_unchanged"] is True
    assert attribution["passed"] is True
    assert attribution["duplicate_orders"] == {"legacy": [], "v3": []}


def test_attribution_semantic_values_keep_bool_and_int_types_distinct():
    result = compare_attribution_parity(
        [{
            "order_number": "3001",
            "campaign_id": "cmp-1",
            "utm_content": True,
            "revenue": 10,
        }],
        [{
            "order_number": "3001",
            "campaign_id": "cmp-1",
            "utm_content": 1,
            "revenue": 10,
        }],
    )

    assert result["passed"] is False


def test_qoyod_parity_fails_if_either_dry_run_reports_a_provider_write():
    result = compare_qoyod_parity(
        {
            "eligible": True,
            "payload": {"x": 1},
            "idempotency_key": "same",
            "provider_write_reached": False,
        },
        {
            "eligible": True,
            "payload": {"x": 1},
            "idempotency_key": "same",
            "provider_write_reached": True,
        },
    )

    assert result["passed"] is False
    assert result["provider_write_reached"] is True


def test_fulfillment_parity_uses_semantic_option_values_not_raw_provenance():
    legacy = {
        "products": [{
            "order_item_id": "7",
            "product_id": "11",
            "quantity": 1,
            "options": [{
                "name": "المقاس",
                "value": "M",
                "source": "legacy",
                "raw": {"provider_shape": 1},
            }],
            "custom_fields": [{"name": "الاسم", "value": "نورة"}],
        }],
    }
    v3 = {
        "products": [{
            "order_item_id": "7",
            "product_id": "11",
            "quantity": 1,
            "options": [{
                "name": "المقاس",
                "value": "M",
                "source": "customer_options",
                "raw": {"new_shape": True},
            }],
            "custom_fields": [{
                "name": "الاسم",
                "value": "نورة",
                "source": "questions",
            }],
        }],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 4,
        "items_success_signal_revision": 4,
    }

    assert compare_fulfillment_parity(legacy, v3)["passed"] is True
    v3["products"][0]["options"][0]["value"] = "L"
    assert compare_fulfillment_parity(legacy, v3)["passed"] is False


def test_fulfillment_parity_requires_success_for_the_current_signal_revision():
    legacy = {"products": []}
    v3 = {
        "products": [],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 8,
        "items_success_signal_revision": 7,
    }

    result = compare_fulfillment_parity(legacy, v3)

    assert result["passed"] is False
    assert result["latest_success_matches_signal"] is False

    v3["items_success_signal_revision"] = 8
    v3.pop("needs_items_enrichment")
    result = compare_fulfillment_parity(legacy, v3)
    assert result["passed"] is False
    assert result["v3_enrichment_complete"] is False
