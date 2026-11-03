"""Fail-closed historical bundle Salla evidence, without any database writes."""
from copy import deepcopy

import pytest

from bundle_historical_reporting_audit import (
    BundleHistoricalEvidenceUnavailable, inspect_historical_bundle_order,
)


ORDER = "297657209"
SKU = "AMS13281"
SOURCE_ITEM = "source-line-42"
PRODUCT = "735616589"


def fixtures():
    options = [
        {"id": "928241773",
         "option": {"id": "890565818", "name": "هل تريد باقة الورد مع الشال ؟"},
         "values": [{"id": "1493279869", "name": "نعم باقة ورد مع الشال"}]},
        {"id": "1952424308",
         "option": {"id": "116462523", "name": "هل تريد اضافة اسم على الشال ؟"},
         "values": [{"id": "720290686", "name": "نعم"}]},
        {"id": "1176813685",
         "option": {"id": "1356739204", "name": "التطريز على الشال"},
         "value": {"text": "اسم عميل خاص"}},
        {"id": "268562294",
         "option": {"id": "582701445", "name": "كرت اهداء"},
         "value": {"text": "رسالة هدية خاصة"}},
    ]
    source = {
        "reference_id": ORDER, "id": "180243420",
        "date": "2026-11-02T20:07:33Z",
        "items": [{"id": SOURCE_ITEM, "product_id": PRODUCT,
                   "quantity": 1, "options": deepcopy(options)}],
    }
    item = {
        "order_item_id": f"salla:{ORDER}:{SOURCE_ITEM}",
        "source_item_id": SOURCE_ITEM, "product_id": PRODUCT, "sku": SKU,
        "quantity": 1, "options_raw": deepcopy(options),
    }
    return source, item


def inspect(source, line):
    return inspect_historical_bundle_order(
        order_number=ORDER, raw_order=source,
        canonical_items=[line], target_line_id=line["order_item_id"],
    )


def test_exact_original_provider_choices_proven_without_borrowing_current_catalog():
    source, item = fixtures()
    result = inspect(source, item)
    assert result["original_source_verified"] is True
    assert result["historical_cost_verified"] is False
    assert result["ready_to_apply_cost"] is False
    assert result["source_choice_evidence_hash"]
    assert len(result["option_rows"]) == 4
    assert any(row["row_id_differs_from_option_id"] for row in result["option_rows"])
    assert result["option_rows"][0]["selected_value_id_present"]
    assert result["order_number"] == ORDER


def test_private_personalization_and_gift_message_are_never_exposed():
    source, item = fixtures()
    output = inspect(source, item)
    assert "اسم عميل خاص" not in str(output)
    assert "رسالة هدية خاصة" not in str(output)
    assert "نعم باقة ورد مع الشال" not in str(output)


@pytest.mark.parametrize("mutation,reason", [
    (lambda source, line: source.__setitem__("reference_id", "wrong"),
     "historical_source_order_mismatch"),
    (lambda source, line: source.__setitem__("items", []),
     "historical_source_line_not_unique"),
    (lambda source, line: source.pop("date"),
     "historical_source_date_missing"),
    (lambda source, line: line["options_raw"][0]["values"][0].__setitem__("name", "الشال فقط"),
     "historical_options_not_proven"),
    (lambda source, line: line.__setitem__("quantity", 2),
     "historical_source_quantity_mismatch"),
    (lambda source, line: source["items"].append(deepcopy(source["items"][0])),
     "historical_source_line_not_unique"),
    (lambda source, line: source["items"][0]["options"][0].__setitem__("option_id", "wrong"),
     "historical_option_identity_conflict"),
    (lambda source, line: source["items"][0]["options"][0].__setitem__("value_id", "wrong"),
     "historical_value_identity_conflict"),
])
def test_conflicting_or_missing_provenance_fails_closed(mutation, reason):
    source, line = fixtures()
    mutation(source, line)
    with pytest.raises(BundleHistoricalEvidenceUnavailable) as exc:
        inspect(source, line)
    assert exc.value.code == reason


def test_missing_canonical_original_options_cannot_reprice():
    source, line = fixtures()
    line["options_raw"] = []
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_options_not_proven"):
        inspect(source, line)


def test_same_sku_with_different_source_line_is_not_accepted():
    source, line = fixtures()
    line["source_item_id"] = "source-line-43"
    with pytest.raises(BundleHistoricalEvidenceUnavailable, match="historical_source_line_not_unique"):
        inspect(source, line)


def test_snapshot_digest_changes_on_original_choice_change_only_when_both_sides_agree():
    source, line = fixtures()
    before = inspect(source, line)
    source["items"][0]["options"][0]["values"][0]["name"] = "الشال فقط"
    line["options_raw"][0]["values"][0]["name"] = "الشال فقط"
    after = inspect(source, line)
    assert before["source_choice_evidence_hash"] != after["source_choice_evidence_hash"]
    # A real selected value ID remains authoritative; a different display
    # label must not silently rebind the stock/service identity.
    assert before["line_fingerprint"] == after["line_fingerprint"]


def test_no_cost_or_execution_is_inferred_from_a_verified_source():
    source, line = fixtures()
    result = inspect(source, line)
    assert "total_cost" not in result
    assert "supplier_id" not in result
    assert "stock" not in result
    assert result["next_gate"] == "sale_time_unit_cost_attestation_and_reviewed_manifest"
