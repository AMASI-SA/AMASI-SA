import campaign_ai_policy_v2 as policy
from integrations_control_center.snapchat_campaign_profitability import _finalize_campaign


def test_p0_2_campaign_profit_is_explicitly_mezan_contribution_not_net_profit():
    raw = {
        "orders": 2,
        "sales_sar": 500.0,
        "product_cost_sar": 200.0,
        "missing_cost_orders": 0,
        "products": {},
    }
    result = _finalize_campaign(raw, spend_sar=100.0)
    assert result["contribution_profit_sar"] == 200.0
    assert result["finance_authority"] == "mezan"
    assert result["profit_metric"] == "contribution_profit"
    assert result["contribution_profit_available"] is True
    assert result["net_profit_available"] is False
    assert result["net_profit_sar"] is None
    assert result["provider_sales_used_as_profit"] is False


def test_p0_2_missing_cost_never_becomes_profit_or_net_profit():
    raw = {
        "orders": 1,
        "sales_sar": 250.0,
        "product_cost_sar": 0.0,
        "missing_cost_orders": 1,
        "products": {},
    }
    result = _finalize_campaign(raw, spend_sar=50.0)
    assert result["contribution_profit_sar"] is None
    assert result["contribution_profit_available"] is False
    assert result["net_profit_sar"] is None


def test_p0_2_ai_context_marks_mezan_as_finance_authority():
    raw = {
        "orders": 2,
        "sales_sar": 500.0,
        "product_cost_sar": 200.0,
        "ad_spend_sar": 100.0,
        "contribution_profit_sar": 200.0,
        "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",
        "allocation_method": "test",
        "products": [],
    }
    result = policy._page_aligned_profitability(
        raw, {"orders": 2, "sales_sar": 500.0}
    )
    assert result["finance_authority"] == "mezan"
    assert result["commercial_outcomes_authority"] == "mezan_attribution"
    assert result["provider_finance_authority"] is False
    assert result["provider_sales_used_as_profit"] is False
    assert result["mezan_attributed_orders"] == 2
    assert result["mezan_attributed_sales_sar"] == 500.0
    assert result["contribution_profit_available"] is True
    assert result["net_profit_available"] is False
    assert result["net_profit_sar"] is None


def test_p0_2_attribution_mismatch_fails_closed_for_contribution_profit():
    raw = {
        "orders": 2,
        "sales_sar": 500.0,
        "contribution_profit_sar": 200.0,
        "products": [],
    }
    result = policy._page_aligned_profitability(
        raw, {"orders": 1, "sales_sar": 250.0}
    )
    assert result["verified_against_mezan_attribution"] is False
    assert result["contribution_profit_available"] is False
    assert result["contribution_profit_sar"] is None
    assert result["net_profit_sar"] is None
