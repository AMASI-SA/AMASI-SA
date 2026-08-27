from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from snapchat_v2.routes import _latest_level_status
from snapchat_v2.sync_pipeline import _current_hour_provider_total_fact

NOW = datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc)
ACCOUNT = {
    "ad_account_id": "snap-1",
    "timezone": "America/Los_Angeles",
    "currency": "USD",
}


def _provider_total(spend: float, *, granularity: str = "TOTAL") -> dict:
    return {
        "account_day_provider_spend_native": spend,
        "account_day_window_start_utc": NOW.replace(hour=7, minute=0),
        "account_day_window_end_utc": NOW.replace(hour=13, minute=0),
        "account_day_coverage": {
            "status": "complete",
            "provider_granularity": granularity,
            "data_state": "confirmed_data",
        },
    }


def _hour(hour: int, spend, **metrics) -> dict:
    start = NOW.replace(hour=hour, minute=0)
    return {
        "hour_start_utc": start,
        "hour_end_utc": start + timedelta(hours=1),
        "spend_native": spend,
        **metrics,
    }


def test_provider_total_residual_materializes_missing_current_hour():
    fact = _current_hour_provider_total_fact(
        user_id="owner-1",
        account=ACCOUNT,
        projection={
            "hours": [
                _hour(10, 100.0),
                _hour(11, 50.0),
                _hour(12, None),
            ]
        },
        provider_total=_provider_total(180.0),
        current=NOW,
        sync_run_id="run-live",
        action_report_time="conversion",
    )

    assert fact is not None
    assert fact["hour_start_utc"] == NOW.replace(minute=0)
    assert fact["spend_native"] == 30.0
    assert fact["provisional"] is True
    assert fact["source"]["materialization"] == (
        "current_hour_provider_total_residual_v1"
    )
    assert fact["source"]["account_day_provider_spend_native"] == 180.0
    assert fact["source"]["prior_hours_spend_native"] == 150.0


def test_provider_total_overlay_preserves_current_hour_conversion_metrics():
    fact = _current_hour_provider_total_fact(
        user_id="owner-1",
        account=ACCOUNT,
        projection={
            "hours": [
                _hour(10, 100.0),
                _hour(11, 50.0),
                _hour(
                    12,
                    10.0,
                    impressions=400,
                    swipes=22,
                    purchases=2,
                    purchase_value_native=75.0,
                ),
            ]
        },
        provider_total=_provider_total(200.0),
        current=NOW,
        sync_run_id="run-live",
        action_report_time="conversion",
    )

    assert fact is not None
    assert fact["spend_native"] == 50.0
    assert fact["impressions"] == 400
    assert fact["swipes"] == 22
    assert fact["purchases"] == 2
    assert fact["purchase_value_native"] == 75.0


def test_provider_total_overlay_rejects_hour_fallback_and_never_moves_backwards():
    projection = {
        "hours": [
            _hour(10, 100.0),
            _hour(11, 50.0),
            _hour(12, 20.0),
        ]
    }
    common = {
        "user_id": "owner-1",
        "account": ACCOUNT,
        "projection": projection,
        "current": NOW,
        "sync_run_id": "run-live",
        "action_report_time": "conversion",
    }

    assert (
        _current_hour_provider_total_fact(
            **common,
            provider_total=_provider_total(190.0, granularity="HOUR"),
        )
        is None
    )
    assert (
        _current_hour_provider_total_fact(
            **common,
            provider_total=_provider_total(165.0),
        )
        is None
    )


@pytest.mark.asyncio
async def test_spend_only_run_does_not_replace_campaign_total_proof():
    class Collection:
        async def find_one(self, query, projection, sort):
            self.query = query
            self.projection = projection
            self.sort = sort
            return {"campaign_sync_status": "complete"}

    collection = Collection()

    class DB:
        def __getitem__(self, name):
            assert name == "mezan_snapchat_sync_runs_v2"
            return collection

    status = await _latest_level_status(
        DB(),
        user_id="owner-1",
        ad_account_id="snap-1",
        entity_type="campaign",
    )

    assert status == "complete"
    assert collection.query["run_type"] == {
        "$ne": "dashboard_spend_refresh"
    }
