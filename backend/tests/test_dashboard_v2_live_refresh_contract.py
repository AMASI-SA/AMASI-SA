from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_v2_runs_independent_read_phases_concurrently():
    source = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")

    assert "initial_results = await asyncio.gather(*initial_reads)" in source
    assert "product_cost, ads, recurring = await asyncio.gather(" in source
    assert "allow_self_heal=False" in source


def test_advanced_dashboard_refreshes_summary_from_live_orders_without_fake_zeros():
    source = (ROOT / "frontend/src/pages/AdvancedDashboard.jsx").read_text(encoding="utf-8")

    assert "DASHBOARD_AUTO_REFRESH_MS" in source
    assert "shouldRefreshDashboardForOrders" in source
    assert 'loadPeriod(filters, { background: true })' in source
    assert 'setData(null)' in source
    assert 'if (!background && requestSequence === requestSequenceRef.current) setData(null)' in source
