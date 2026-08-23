from growth_intelligence_product_discovery import build_product_discovery_candidates


def test_unknown_economics_stay_unknown():
    result = build_product_discovery_candidates(
        candidates=[{"product_name": "Test Product"}],
        audience_evidence=[],
    )
    item = result["candidates"][0]
    assert item["unit_economics_known"] is False
    assert item["expected_margin_sar"] is None
    assert item["requires_owner_approval"] is True


def test_known_margin_is_calculated():
    result = build_product_discovery_candidates(
        candidates=[
            {
                "product_name": "Test Product",
                "landed_cost_sar": 50,
                "expected_price_sar": 150,
            }
        ],
        audience_evidence=[{"source": "mezan"}],
    )
    item = result["candidates"][0]
    assert item["unit_economics_known"] is True
    assert item["expected_margin_sar"] == 100


def test_engine_is_read_only():
    result = build_product_discovery_candidates(
        candidates=[],
        audience_evidence=[],
    )
    assert result["read_only"] is True
