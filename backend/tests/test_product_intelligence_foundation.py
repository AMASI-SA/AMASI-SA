from product_intelligence_foundation import (
    ACTION_POLICY,
    PROTECTED_AUTOMATION_AREAS,
    SIGNAL_CONTRACT,
    action_candidates,
    product_intelligence_foundation,
    product_intelligence_readiness,
)
from ai_store_operations_foundation import make_ai_store_operations_router


def _complete_product():
    return {
        "name": "مريول مدرسي",
        "sku": "AMS-100",
        "description": "مريول مدرسي كحلي بقصة عملية.",
        "images": [{"url": "https://example.com/product.webp"}],
        "categories": [{"id": 10, "name": "مراييل مدرسية"}],
        "price": 150,
        "status": "sale",
        "options_count": 0,
        "seo": {
            "title": "مريول مدرسي كحلي",
            "description": "مريول مدرسي عملي بتفاصيل واضحة.",
        },
        "google_category": "Apparel & Accessories > Clothing",
    }


def _connected_sources():
    return {
        "ga4": True,
        "search_console": {"status": "healthy"},
        "salla_orders": {"status": "connected"},
        "customer_intelligence": {"status": "ready"},
        "merchant_center": True,
        "openai_product_feed": True,
    }


def test_foundation_runs_without_openai_and_disallows_every_write():
    foundation = product_intelligence_foundation()

    assert foundation["mode"] == "foundation_only"
    assert foundation["legacy_dependency"] is False
    assert foundation["openai_required"]["data_collection"] is False
    assert foundation["openai_required"]["rule_evaluation"] is False
    assert foundation["writes_allowed"] is False
    assert foundation["external_calls_allowed"] is False
    assert foundation["automatic_execution_allowed"] is False


def test_product_intelligence_routes_are_v2_read_only_get_routes():
    router = make_ai_store_operations_router(object(), lambda: {"id": "owner"})
    routes = {
        (route.path, method)
        for route in router.routes
        for method in (route.methods or set())
        if route.path.startswith("/ai-store-operations/product-intelligence")
    }

    assert routes == {
        ("/ai-store-operations/product-intelligence/foundation", "GET"),
        (
            "/ai-store-operations/product-intelligence/products/{product_id}",
            "GET",
        ),
    }


def test_all_action_policies_fail_closed_during_foundation_phase():
    assert ACTION_POLICY
    assert all(
        policy["execution_allowed"] is False
        for policy in ACTION_POLICY.values()
    )
    assert ACTION_POLICY["price_change"]["risk"] == "high"
    assert ACTION_POLICY["price_change"]["required_approval"] == "owner"
    assert ACTION_POLICY["inventory_change"]["risk"] == "critical"
    assert "price" in PROTECTED_AUTOMATION_AREAS
    assert "campaign_budget" in PROTECTED_AUTOMATION_AREAS
    assert "salla_publish" in PROTECTED_AUTOMATION_AREAS


def test_signal_contract_rejects_direct_customer_identifiers():
    forbidden = set(SIGNAL_CONTRACT["forbidden"])

    assert "customer_name" in forbidden
    assert "customer_phone" in forbidden
    assert "raw_customer_message" in forbidden
    assert "product_id" in SIGNAL_CONTRACT["required"]
    assert "evidence_ref" in SIGNAL_CONTRACT["required"]


def test_complete_product_and_connected_sources_are_rules_ready():
    result = product_intelligence_readiness(
        _complete_product(),
        {"base_cost": 50},
        _connected_sources(),
    )

    assert result["score"] == 100
    assert result["gaps"] == []
    assert result["critical_gaps"] == []
    assert result["rules_ready"] is True
    assert result["proposal_generation_ready"] is False
    assert result["automatic_execution_ready"] is False


def test_missing_cost_blocks_rules_even_when_content_is_complete():
    result = product_intelligence_readiness(
        _complete_product(),
        {},
        _connected_sources(),
    )

    assert result["rules_ready"] is False
    assert "economics.base_cost" in result["critical_gaps"]
    assert "economics.price_above_cost" in result["critical_gaps"]


def test_content_and_feed_gaps_create_candidates_but_never_execution():
    product = _complete_product()
    product["description"] = ""
    product["seo"] = {}
    result = product_intelligence_readiness(
        product,
        {"base_cost": 50},
        {},
    )

    candidates = action_candidates(result)
    by_action = {row["action"]: row for row in candidates}

    assert by_action["description_rewrite"]["risk"] == "medium"
    assert by_action["seo_metadata_draft"]["risk"] == "low"
    assert by_action["structured_product_feed_draft"]["status"] == "candidate_only"
    assert all(row["execution_allowed"] is False for row in candidates)
