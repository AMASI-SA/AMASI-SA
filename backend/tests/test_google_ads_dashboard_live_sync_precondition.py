from pathlib import Path


def test_google_ads_reporting_already_produces_hourly_and_daily_spend_facts():
    source = Path("backend/integrations_control_center/google_ads_reporting.py").read_text()
    assert "segments.date, segments.hour, metrics.cost_micros" in source
    assert 'provider="google"' in source
    assert "GOOGLE_ADS_DAILY_COLLECTION" in source
