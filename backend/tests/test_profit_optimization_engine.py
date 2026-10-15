from profit_optimization_engine import ProfitSignal, optimize_recommendation


def test_profit_recommendation_is_read_only():
    result = optimize_recommendation(
        ProfitSignal(revenue=100, product_cost=30, ad_cost=20)
    )
    assert result["net_profit"] == 50
    assert result["auto_execution"] is False
