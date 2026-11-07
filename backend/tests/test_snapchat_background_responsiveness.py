"""Exercise real reporting parsing/folds with synthetic provider data only."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from integrations_control_center import snapchat_account_hourly_refresh as hourly


def payload(campaigns=200):
    start = datetime(2026, 11, 3, 21, tzinfo=timezone.utc)
    return {"request_status": "SUCCESS", "timeseries_stats": [{
        "sub_request_status": "SUCCESS", "timeseries_stat": {"granularity": "HOUR",
        "breakdown_stats": {"campaign": [{"id": f"synthetic-{i}", "timeseries": [{
            "start_time": (start + timedelta(hours=h)).isoformat(),
            "end_time": (start + timedelta(hours=h + 1)).isoformat(),
            "stats": {"spend": 1_000_000, "impressions": 100, "swipes": 10,
                "conversion_purchases": 1, "conversion_purchases_value": 10_000_000},
        } for h in range(72)]} for i in range(campaigns)]}},
    }]}


def proof():
    return {"status": "complete", "data_state": "confirmed_data", "expected_requests": 1, "completed_requests": 1}


@pytest.mark.asyncio
async def test_provider_page_processing_gives_an_unrelated_request_a_turn():
    served = []
    body = payload()

    class Context:
        async def get_json(self, *args, **kwargs):
            # The provider response has arrived; the callback must run during
            # CPU processing, rather than relying on a later network wait.
            asyncio.get_running_loop().call_soon(lambda: served.append(True))
            return body

    result = await hourly._fetch_account_hours(Context(), object(), "synthetic-token",
        account_id="synthetic-account", start_date=date(2026, 11, 4), end_date=date(2026, 11, 6))
    assert len(result.rows) == 14_400
    assert result.errors == []
    assert served, "Unrelated API work was blocked until the entire provider page finished"


@pytest.mark.asyncio
async def test_both_aggregation_passes_yield_before_fact_publishing_and_keep_exact_totals(monkeypatch):
    served = []

    class Rows(list):
        passes = 0

        def __iter__(self):
            self.passes += 1
            phase = self.passes
            asyncio.get_running_loop().call_soon(lambda: served.append(phase))
            return super().__iter__()

    rows = Rows(hourly.extract_account_hour_rows(payload())[0])
    writes = []

    async def fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(rows=rows, errors=[], coverage=proof())

    async def write(context, **kwargs):
        assert served == [1, 2], "Each fold must let other requests run before publishing facts"
        writes.append(kwargs)

    monkeypatch.setattr(hourly, "_fetch_account_hours", fetch)
    monkeypatch.setattr(hourly, "_upsert_performance", write)
    result = await hourly.refresh_snapchat_account_hours(SimpleNamespace(provider_calls=1),
        object(), "synthetic-token", {"ad_account_id": "synthetic-account", "timezone": "Asia/Riyadh", "currency": "SAR"},
        start_date=date(2026, 11, 4), end_date=date(2026, 11, 6), now=datetime(2026, 11, 7, tzinfo=timezone.utc))
    assert result["coverage"] == proof()
    assert result["rows_saved"] == 603
    assert result["campaign_rows_saved"] == 600
    account_facts = [row for row in writes if row["entity_type"] == "ad_account"]
    assert len(account_facts) == 3
    assert sum(row["metrics"]["spend"] for row in account_facts) == 14_400_000_000
    assert sum(row["metrics"]["conversion_purchases"] for row in account_facts) == 14_400
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_cancellation_during_aggregation_publishes_no_partial_facts(monkeypatch):
    class Rows(list):
        def __iter__(self):
            task = asyncio.current_task()
            asyncio.get_running_loop().call_soon(task.cancel)
            return super().__iter__()

    rows = Rows(hourly.extract_account_hour_rows(payload())[0])
    writes = []

    async def fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(rows=rows, errors=[], coverage=proof())

    async def write(context, **kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(hourly, "_fetch_account_hours", fetch)
    monkeypatch.setattr(hourly, "_upsert_performance", write)
    task = asyncio.create_task(hourly.refresh_snapchat_account_hours(SimpleNamespace(provider_calls=1),
        object(), "synthetic-token", {"ad_account_id": "synthetic-account", "timezone": "Asia/Riyadh", "currency": "SAR"},
        start_date=date(2026, 11, 4), end_date=date(2026, 11, 6), now=datetime(2026, 11, 7, tzinfo=timezone.utc)))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert writes == []
