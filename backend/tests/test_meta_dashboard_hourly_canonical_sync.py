from pathlib import Path


def test_meta_scheduler_reuses_existing_dashboard_hourly_projection():
    scheduler = Path("backend/integrations_control_center/ads_auto_sync_scheduler.py").read_text()
    dashboard_refresh = Path("backend/integrations_control_center/dashboard_ads_platform_refresh.py").read_text()

    assert "from .dashboard_ads_platform_refresh import _refresh_meta_hourly" in scheduler
    assert "hourly = await _refresh_meta_hourly(db, user_id, end_date)" in scheduler
    assert "async def _refresh_meta_hourly(" in dashboard_refresh
    assert "hourly_stats_aggregated_by_advertiser_time_zone" in dashboard_refresh
    assert "upsert_platform_hour(" in dashboard_refresh


def test_meta_hourly_projection_stays_inside_existing_meta_sync_not_new_scheduler():
    scheduler = Path("backend/integrations_control_center/ads_auto_sync_scheduler.py").read_text()
    assert scheduler.count("async def auto_sync_loop(") == 1
    assert scheduler.count("async def _refresh_meta(") == 1
    assert 'result = {**result, "hourly": hourly}' in scheduler
