from pathlib import Path


def test_google_ads_is_part_of_server_side_ads_scheduler():
    source = Path("backend/integrations_control_center/ads_auto_sync_scheduler.py").read_text()
    assert "GOOGLE_ADS_PROVIDER_ID" in source
    assert "run_google_ads_reporting_sync" in source
    assert "async def _refresh_google(" in source
    assert "if provider == GOOGLE_ADS_PROVIDER_ID:" in source
    assert source.count("GOOGLE_ADS_PROVIDER_ID") >= 8


# Automatic saved-spend refresh is exercised by the mounted card test in
# frontend/src/components/DashboardAdsSpendCard.test.jsx. The former source-
# string assertion referred to the pre-refactor AdvancedDashboard loader;
# current polling belongs to the native card and cleans up on unmount.


def test_google_reporting_keeps_daily_and_hourly_cost_projection():
    source = Path("backend/integrations_control_center/google_ads_reporting.py").read_text()
    assert "segments.date, segments.hour, metrics.cost_micros" in source
    assert 'provider="google"' in source
    assert "GOOGLE_ADS_DAILY_COLLECTION" in source
