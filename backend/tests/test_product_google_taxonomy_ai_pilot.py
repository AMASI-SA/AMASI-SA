from product_google_taxonomy_ai_pilot import (
    _candidate_rows,
    _decision_status,
    _fallback_search_terms,
    _input_revision,
    _product_evidence,
    _run_counters,
    _select_pilot_products,
)


def _evidence(name, *, categories=None, current=""):
    return {
        "product_id": f"p-{name}",
        "salla_product_id": "1",
        "name": name,
        "description": "",
        "short_description": "",
        "salla_categories": categories or [],
        "options": [],
        "product_type": "product",
        "brand": "",
        "sku": "",
        "gtin": "",
        "mpn": "",
        "current_google_category": current,
        "has_image": True,
    }


def test_abaya_alias_retrieves_traditional_clothing_candidate():
    taxonomy = [
        {"id": "5388", "name": "ملابس الاحتفالات والملابس التقليدية", "path": "ملابس وإكسسوارات > ملابس > ملابس الاحتفالات والملابس التقليدية", "depth": 2},
        {"id": "2271", "name": "فساتين", "path": "ملابس وإكسسوارات > ملابس > فساتين", "depth": 2},
    ]
    rows = _candidate_rows(_evidence("عباية صيفية مطرزة"), [], taxonomy, None)
    assert rows
    assert rows[0]["id"] == "5388"


def test_necklace_alias_retrieves_necklaces_candidate():
    taxonomy = [
        {"id": "196", "name": "قلادات", "path": "ملابس وإكسسوارات > حلي > قلادات", "depth": 2},
        {"id": "200", "name": "خواتم", "path": "ملابس وإكسسوارات > حلي > خواتم", "depth": 2},
    ]
    rows = _candidate_rows(_evidence("سلسال القطرة مرصع بالزركون"), [], taxonomy, None)
    assert rows[0]["id"] == "196"


def test_ai_search_terms_are_combined_with_deterministic_aliases():
    terms = _fallback_search_terms(_evidence("سلسال بالاسم"))
    assert any("قلادات" in term for term in terms)


def test_existing_category_never_batch_overwritten_when_ai_disagrees():
    assert _decision_status(current_id="5388", chosen_id="2271", confidence=99) == "review_required_existing_category"
    assert _decision_status(current_id="5388", chosen_id="5388", confidence=95) == "no_change"


def test_confidence_policy_matches_pilot_gate():
    assert _decision_status(current_id=None, chosen_id="196", confidence=90) == "high_confidence"
    assert _decision_status(current_id=None, chosen_id="196", confidence=89) == "review_required"
    assert _decision_status(current_id=None, chosen_id="196", confidence=69) == "low_confidence"
    assert _decision_status(current_id=None, chosen_id=None, confidence=99) == "low_confidence"


def test_classification_revision_changes_when_relevant_product_fact_changes():
    first = _evidence("سلسال فضي")
    second = {**first, "name": "سلسال ذهبي"}
    assert _input_revision(first) != _input_revision(second)


def test_product_evidence_is_allowlisted_and_does_not_leak_customer_fields():
    product = {
        "mezan_product_id": "mpv2_1",
        "salla_product_id": "1",
        "name": "عباية",
        "description": "وصف المنتج",
        "categories": [{"id": "10", "name": "عبايات"}],
        "raw_salla": {
            "customer_email": "secret@example.com",
            "customer_phone": "0500000000",
            "gtin": "123",
            "mpn": "ABC",
        },
    }
    result = _product_evidence(product)
    assert "customer_email" not in result
    assert "customer_phone" not in result
    assert result["gtin"] == "123"
    assert result["mpn"] == "ABC"


def test_pilot_selection_keeps_diversity_and_some_existing_categories():
    products = []
    for index in range(18):
        products.append({
            "mezan_product_id": f"missing-{index}",
            "name": f"منتج {index}",
            "product_type": "product",
            "categories": [{"name": f"قسم {index % 4}"}],
        })
    for index in range(4):
        products.append({
            "mezan_product_id": f"existing-{index}",
            "name": f"مصنف {index}",
            "product_type": "product",
            "google_category": "5388",
            "categories": [{"name": f"قسم موجود {index}"}],
        })
    selected = _select_pilot_products(products, 20)
    assert len(selected) == 20
    assert any(row.get("google_category") for row in selected)
    keys = {row["categories"][0]["name"] for row in selected if row.get("categories")}
    assert len(keys) >= 4


def test_visual_failures_are_counted_separately_from_ai_failures():
    counters = _run_counters([
        {
            "decision_status": "review_required",
            "visual_verification_status": "failed",
        },
        {
            "decision_status": "high_confidence",
            "visual_verification_status": "consistent",
        },
        {"decision_status": "ai_failed"},
    ], 3)
    assert counters["visual_checked"] == 2
    assert counters["visual_failed"] == 1
    assert counters["ai_failed"] == 1
