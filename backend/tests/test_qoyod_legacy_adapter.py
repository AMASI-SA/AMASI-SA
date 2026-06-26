"""Legacy-payload Adapter — comprehensive test suite.

Locks in (per user spec 2026-06-26):
  • Adapter detects flat Make.com shapes by smell (no `data` envelope +
    at least one legacy marker).
  • Adapter NEVER touches an already-canonical Salla payload.
  • Items resolution priority: items[] → packages[].items[] → "missing".
  • Pure function — no DB, no I/O, deterministic.
  • The downstream `validate()` accepts the adapter output.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.legacy_adapter import (
    adapt, is_legacy_shape, _adapt_item, _collect_items,
    _build_status, _split_name, _money,
)
from integrations.qoyod.normalizer import validate
from integrations.qoyod.state_machine import (
    NEEDS_ENRICHMENT, ALL_STAGES, can_transition,
    FAILURE_STAGES,
)


# ─── Reference payloads ─────────────────────────────────────────────
LEGACY_NO_ITEMS = {
    "event_type": "order_created",
    "order_number": "268500046",
    "order_id": "69664233",
    "created_at": "2026-06-26 07:00:16.000000",
    "total_amount": "139.51",
    "subtotal": "105",
    "shipping_cost": "22.61",
    "payment_method": "mada",
    "currency": "SAR",
    "customer_name": "عميل تجريبي",
    "customer_mobile": "+966500000000",
    "shipping_company": "iMile للتوصيل",
    "order_status": "بإنتظار المراجعة",
    "order_status_slug": "under_review",
    "source": "store",
    "utm_source": "snapchat",
    "received_from": "make",
}

LEGACY_WITH_FLAT_ITEMS = dict(LEGACY_NO_ITEMS, **{
    "order_status_slug": "completed",
    "order_status": "تم التنفيذ",
    "completed_at": "2026-06-26 14:30:00",
    "tax": "11.90",
    "items": [
        {"sku": "SKU-A", "name": "منتج 1", "quantity": 2,
         "price": {"amount": 50, "currency": "SAR"}},
        {"sku": "SKU-B", "name": "منتج 2", "quantity": 1,
         "price": {"amount": 5, "currency": "SAR"}},
    ],
})

LEGACY_WITH_PACKAGES = dict(LEGACY_NO_ITEMS, **{
    "order_status_slug": "completed",
    "order_status": "تم التنفيذ",
    "tax": "11.90",
    "packages": [
        {"id": "pkg1", "items": [
            {"sku": "SKU-A", "name": "منتج 1", "quantity": 2,
             "price": {"amount": 50, "currency": "SAR"},
             "options": [{"name": "Color", "value": "Red"}]},
        ]},
        {"id": "pkg2", "items": [
            {"sku": "SKU-B", "name": "منتج 2", "quantity": 1,
             "price": {"amount": 5, "currency": "SAR"}},
        ]},
    ],
})

CANONICAL = {
    "event": "order.completed",
    "data": {
        "id": 12345,
        "reference_id": "12345",
        "status": {"customized": {"name": "تم التنفيذ"}, "slug": "completed"},
        "items": [
            {"sku": "X", "name": "X", "quantity": 1,
             "amounts": {"price_without_tax": {"amount": 10, "currency": "SAR"}}}
        ],
        "amounts": {"total": {"amount": 10, "currency": "SAR"}},
        "customer": {"first_name": "A", "last_name": "B", "mobile": "+966500000000"},
    },
}


# ─── Detection ──────────────────────────────────────────────────────
class TestDetection:
    def test_canonical_payload_is_not_legacy(self):
        assert is_legacy_shape(CANONICAL) is False

    def test_flat_make_payload_is_legacy(self):
        assert is_legacy_shape(LEGACY_NO_ITEMS) is True
        assert is_legacy_shape(LEGACY_WITH_FLAT_ITEMS) is True
        assert is_legacy_shape(LEGACY_WITH_PACKAGES) is True

    def test_non_dict_is_not_legacy(self):
        assert is_legacy_shape(None) is False
        assert is_legacy_shape([]) is False
        assert is_legacy_shape("hello") is False

    def test_dict_without_markers_is_not_legacy(self):
        assert is_legacy_shape({"foo": "bar"}) is False


# ─── Helpers ────────────────────────────────────────────────────────
class TestHelpers:
    def test_split_name(self):
        assert _split_name("Ahmad Mohammed") == ("Ahmad", "Mohammed")
        assert _split_name("Ahmad") == ("Ahmad", "")
        assert _split_name("") == ("", "")
        assert _split_name(None) == ("", "")
        assert _split_name("  أحمد   محمد  السيد ") == ("أحمد", "محمد  السيد")

    def test_money_wraps_safely(self):
        assert _money(50) == {"amount": 50.0, "currency": "SAR"}
        assert _money("50.5") == {"amount": 50.5, "currency": "SAR"}
        assert _money(None) is None
        assert _money("") is None
        assert _money("abc") is None
        assert _money(50, "USD")["currency"] == "USD"


# ─── Item adaptation ────────────────────────────────────────────────
class TestItemAdaptation:
    def test_flat_price_object(self):
        out = _adapt_item(
            {"sku": "A", "name": "A", "quantity": 2,
             "price": {"amount": 50, "currency": "SAR"}}, "SAR")
        assert out["sku"] == "A"
        assert out["quantity"] == 2.0
        assert out["amounts"]["price_without_tax"]["amount"] == 50.0
        # total computed as price*qty when not explicit
        assert out["amounts"]["total"]["amount"] == 100.0

    def test_inline_price(self):
        out = _adapt_item(
            {"sku": "A", "name": "A", "quantity": 3, "price": 10}, "SAR")
        assert out["amounts"]["price_without_tax"]["amount"] == 10.0
        assert out["amounts"]["total"]["amount"] == 30.0

    def test_options_carried_through(self):
        out = _adapt_item(
            {"sku": "A", "name": "A", "quantity": 1,
             "price": {"amount": 1}, "options": [{"k": "v"}]}, "SAR")
        assert out["options"] == [{"k": "v"}]

    def test_product_nested_sku_and_name(self):
        out = _adapt_item(
            {"product": {"id": 999, "sku": "PSKU", "name": "PName"},
             "quantity": 1, "price": 1}, "SAR")
        assert out["sku"] == "PSKU"
        assert out["name"] == "PName"
        assert out["product_id"] == "999"

    def test_item_without_name_or_sku_drops(self):
        assert _adapt_item({"quantity": 1, "price": 1}, "SAR") is None

    def test_non_dict_item_drops(self):
        assert _adapt_item("not an item", "SAR") is None


# ─── Items collection priority ──────────────────────────────────────
class TestItemsCollection:
    def test_priority_items_over_packages(self):
        raw = {**LEGACY_WITH_FLAT_ITEMS, "packages": [{"items": [
            {"sku": "X-SHOULD-NOT-WIN", "name": "X", "quantity": 1,
             "price": 1}]}]}
        items, src = _collect_items(raw, "SAR")
        assert src == "items"
        assert all(i["sku"] != "X-SHOULD-NOT-WIN" for i in items)

    def test_packages_fallback_when_no_items(self):
        items, src = _collect_items(LEGACY_WITH_PACKAGES, "SAR")
        assert src == "packages"
        assert len(items) == 2
        assert items[0]["sku"] == "SKU-A"
        assert items[1]["sku"] == "SKU-B"

    def test_packages_flatten_across_multiple_packages(self):
        raw = {"packages": [
            {"items": [{"sku": "A", "name": "A", "quantity": 1, "price": 1}]},
            {"items": [{"sku": "B", "name": "B", "quantity": 1, "price": 1}]},
            {"items": [{"sku": "C", "name": "C", "quantity": 1, "price": 1}]},
        ]}
        items, src = _collect_items(raw, "SAR")
        assert src == "packages"
        assert [i["sku"] for i in items] == ["A", "B", "C"]

    def test_legacy_products_alias_accepted(self):
        """Older Salla webhooks use `products[]` instead of `items[]`."""
        raw = {"products": [{"sku": "P", "name": "P", "quantity": 1, "price": 1}]}
        items, src = _collect_items(raw, "SAR")
        assert src == "items"
        assert items[0]["sku"] == "P"

    def test_missing_when_both_empty(self):
        items, src = _collect_items(LEGACY_NO_ITEMS, "SAR")
        assert src == "missing"
        assert items == []

    def test_missing_when_items_all_invalid(self):
        raw = {"items": [{"quantity": 1}, "not_a_dict", {}]}
        items, src = _collect_items(raw, "SAR")
        # All items collapse → missing
        assert src == "missing"
        assert items == []


# ─── Status node ────────────────────────────────────────────────────
class TestStatus:
    def test_slug_plus_name_builds_customized_node(self):
        node = _build_status(LEGACY_WITH_FLAT_ITEMS)
        assert node["slug"] == "completed"
        assert node["name"] == "تم التنفيذ"
        assert node["customized"]["name"] == "تم التنفيذ"

    def test_slug_only(self):
        node = _build_status({"order_status_slug": "under_review"})
        assert node["slug"] == "under_review"
        assert node["customized"]["name"] == "under_review"

    def test_empty_returns_none(self):
        assert _build_status({}) is None


# ─── Public adapt() ─────────────────────────────────────────────────
class TestAdaptPublic:
    def test_canonical_passthrough(self):
        out, meta = adapt(CANONICAL)
        assert out is CANONICAL  # not modified
        assert meta["adapter_applied"] is False
        assert meta["items_source"] == "passthrough"

    def test_legacy_no_items_marks_missing(self):
        out, meta = adapt(LEGACY_NO_ITEMS)
        assert meta["adapter_applied"] is True
        assert meta["items_source"] == "missing"
        assert "data" in out
        assert out["data"]["reference_id"] == "268500046"
        assert out["data"]["id"] == "69664233"
        # status carried through
        assert out["data"]["status"]["slug"] == "under_review"
        assert out["data"]["customer"]["mobile"] == "+966500000000"
        # amounts mapped
        assert out["data"]["amounts"]["total"]["amount"] == 139.51
        assert out["data"]["amounts"]["sub_total"]["amount"] == 105.0
        assert out["data"]["amounts"]["shipping"]["amount"] == 22.61

    def test_legacy_with_flat_items(self):
        out, meta = adapt(LEGACY_WITH_FLAT_ITEMS)
        assert meta["items_source"] == "items"
        items = out["data"]["items"]
        assert len(items) == 2
        assert items[0]["sku"] == "SKU-A"
        assert items[0]["amounts"]["price_without_tax"]["amount"] == 50.0

    def test_legacy_with_packages(self):
        out, meta = adapt(LEGACY_WITH_PACKAGES)
        assert meta["items_source"] == "packages"
        items = out["data"]["items"]
        assert [i["sku"] for i in items] == ["SKU-A", "SKU-B"]
        assert items[0]["options"][0]["value"] == "Red"

    def test_legacy_extras_preserved(self):
        out, meta = adapt(LEGACY_NO_ITEMS)
        # utm_source / received_from / shipping_company are unknown → extras
        assert meta["legacy_extras"]["utm_source"] == "snapchat"
        assert meta["legacy_extras"]["received_from"] == "make"
        assert meta["legacy_extras"]["shipping_company"] == "iMile للتوصيل"

    def test_customer_name_split(self):
        out, _ = adapt({**LEGACY_NO_ITEMS, "customer_name": "Ahmad Mohammed"})
        cust = out["data"]["customer"]
        assert cust["first_name"] == "Ahmad"
        assert cust["last_name"] == "Mohammed"

    def test_completed_at_passed_through(self):
        out, _ = adapt(LEGACY_WITH_FLAT_ITEMS)
        assert out["data"]["completed_at"] == "2026-06-26 14:30:00"


# ─── Downstream contract: validate() accepts adapter output ─────────
class TestDownstreamContract:
    def test_adapted_with_flat_items_passes_validation(self):
        out, _ = adapt(LEGACY_WITH_FLAT_ITEMS)
        ok, err = validate(out)
        assert ok is True, f"validate rejected adapter output: {err}"

    def test_adapted_with_packages_passes_validation(self):
        out, _ = adapt(LEGACY_WITH_PACKAGES)
        ok, err = validate(out)
        assert ok is True, f"validate rejected adapter output: {err}"

    def test_adapted_without_items_still_passes_validate_shape_check(self):
        """validate() only checks structural items; an empty items array
        is rejected by `empty_items`. This is correct — the items-missing
        branch must NEVER reach validate() in production (the webhook
        handler short-circuits via _handle_missing_items)."""
        out, meta = adapt(LEGACY_NO_ITEMS)
        assert meta["items_source"] == "missing"
        ok, err = validate(out)
        assert ok is False
        assert err["code"] == "empty_items"


# ─── State machine additions ────────────────────────────────────────
class TestStateMachineEnrichmentStages:
    def test_states_registered(self):
        assert "NEEDS_ENRICHMENT" in ALL_STAGES
        assert "FAILED_ENRICHMENT" in FAILURE_STAGES

    def test_received_to_needs_enrichment_allowed(self):
        assert can_transition("RECEIVED", "NEEDS_ENRICHMENT") is True

    def test_needs_enrichment_to_validated(self):
        """Happy path: enricher succeeded, resume pipeline at VALIDATED."""
        assert can_transition("NEEDS_ENRICHMENT", "VALIDATED") is True

    def test_needs_enrichment_to_failed_enrichment(self):
        assert can_transition("NEEDS_ENRICHMENT", "FAILED_ENRICHMENT") is True

    def test_needs_enrichment_to_dead_letter(self):
        """Manual override remains available."""
        assert can_transition("NEEDS_ENRICHMENT", "DEAD_LETTER") is True

    def test_failed_enrichment_retry_resumes_from_received(self):
        from integrations.qoyod.state_machine import FAILURE_TO_RESUME
        assert FAILURE_TO_RESUME["FAILED_ENRICHMENT"] == "RECEIVED"

    def test_failed_enrichment_to_retrying(self):
        assert can_transition("FAILED_ENRICHMENT", "RETRYING") is True

    def test_retrying_to_received_resume_path(self):
        assert can_transition("RETRYING", "RECEIVED") is True

    def test_no_direct_failed_validation_to_needs_enrichment(self):
        """NEEDS_ENRICHMENT is only entered from RECEIVED."""
        assert can_transition("FAILED_VALIDATION", "NEEDS_ENRICHMENT") is False
