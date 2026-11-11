from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_v2_runs_independent_read_phases_concurrently():
    source = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")

    assert "initial_results = await _gather_dashboard_reads(*initial_reads)" in source
    assert "product_cost, ads, recurring = await _gather_dashboard_reads(" in source
    assert "allow_self_heal=False" in source


def test_advanced_dashboard_refreshes_summary_from_live_orders_without_fake_zeros():
    source = (ROOT / "frontend/src/pages/AdvancedDashboard.jsx").read_text(encoding="utf-8")

    assert "DASHBOARD_AUTO_REFRESH_MS" in source
    assert "shouldRefreshDashboardForOrders" in source
    assert 'loadPeriod(filters, { background: true })' in source
    # The independent cards retain a failed same-period snapshot. A changed
    # period must hide the old financial data synchronously, before effects or
    # network responses run. Behavioral race/failure coverage lives in the
    # AdvancedDashboard.cardRefresh and useDashboardCardSnapshot UI suites.
    assert 'const visibleData = dataKey === filterKey ? data : null' in source
    assert '<ProfitCard data={visibleData}' in source
    assert 'endpoint: "/dashboard-v2/cards/executive"' in source


def test_abandoned_cart_dashboard_reconciles_salla_and_never_fills_with_old_rows():
    source = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")

    assert "reconcile_recent_abandoned_carts(" in source
    assert "call_provider=call_salla" in source
    assert '"showing_latest_active_fallback": False' in source
    assert "latest_active_abandoned_carts" not in source
