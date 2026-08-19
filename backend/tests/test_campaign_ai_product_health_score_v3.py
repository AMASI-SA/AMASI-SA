from __future__ import annotations

from campaign_ai_product_health_score_v3 import enrich_product_health_scores, score_product


def test_product_health_score_uses_only_observed_checks():
    product = {
        "product_name": "Product",
        "visibility": "public_status_expected",
        "price": 120,
        "description": "Description",
        "main_image": "https://cdn.example.com/product.jpg",
        "page_probe": {
            "status": "PRODUCT_URL_OK",
            "page_title": "Product",
            "add_to_cart_marker_present": True,
        },
        "inventory": {"status": "in_stock"},
        "variants": [{"quantity": 4, "unlimited_quantity": False}],
    }
    result = score_product(product)
    assert result["score"] == 100.0
    assert result["coverage_pct"] == 100.0
    assert all(row["state"] == "pass" for row in result["components"].values())
    assert "No product_health_score threshold" in result["contract"]


def test_unknown_checks_do_not_become_failures():
    result = score_product({
        "product_name": "Known title",
        "page_probe": {"status": "PRODUCT_URL_UNKNOWN"},
        "inventory": {"status": "unknown"},
        "variants": [],
    })
    assert result["score"] == 100.0
    assert 0 < result["coverage_pct"] < 100
    assert result["components"]["url_health"]["state"] == "unknown"
    assert result["components"]["inventory"]["state"] == "unknown"
    assert result["components"]["variant_availability"]["state"] == "unknown"


def test_known_operational_failures_reduce_score_without_selecting_action():
    result = score_product({
        "product_name": "Product",
        "visibility": "not_public_or_inactive",
        "price": 100,
        "description": "Description",
        "main_image": "https://cdn.example.com/product.jpg",
        "page_probe": {
            "status": "PRODUCT_URL_BROKEN",
            "page_title": "Product",
            "add_to_cart_marker_present": False,
        },
        "inventory": {"status": "out_of_stock"},
        "variants": [{"quantity": 0, "unlimited_quantity": False}],
    })
    assert result["score"] < 50
    assert result["coverage_pct"] == 100.0
    assert "pause" in result["contract"].lower()
    assert "scale" in result["contract"].lower()


def test_enrichment_attaches_score_to_each_verified_product():
    pack = {
        "product_intelligence": {
            "entities": {
                "snapchat|campaign|acct|c1": {
                    "products": [
                        {
                            "product_id": "p1",
                            "product_name": "Product",
                            "visibility": "public_status_expected",
                            "price": 100,
                            "page_probe": {"status": "PRODUCT_URL_OK"},
                            "inventory": {"status": "in_stock"},
                            "variants": [],
                        }
                    ]
                }
            }
        }
    }
    enriched = enrich_product_health_scores(pack)
    product = enriched["product_intelligence"]["entities"]["snapchat|campaign|acct|c1"]["products"][0]
    assert "product_health_score" in product
    assert product["product_health_score"]["score"] is not None
