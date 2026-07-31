import asyncio
from datetime import date

import httpx

from integrations_control_center.snapchat_account_hourly_refresh import (
    ACTION_REPORT_TIME,
    CONVERSION_SOURCE_TYPES,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _fetch_account_hours,
)


class FakeContext:
    def __init__(self):
        self.params = None

    async def get_json(self, client, url, *, headers, params=None):
        self.params = dict(params or {})
        return {"timeseries_stats": []}


def test_scheduler_requests_total_snapchat_purchases_with_explicit_attribution():
    async def run():
        context = FakeContext()
        async with httpx.AsyncClient() as client:
            rows, errors = await _fetch_account_hours(
                context,
                client,
                "test-token",
                account_id="snap-account-1",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 1),
            )
        return context.params, rows, errors

    params, rows, errors = asyncio.run(run())

    assert rows == []
    assert errors == []
    assert params["conversion_source_types"] == CONVERSION_SOURCE_TYPES == "total"
    assert params["action_report_time"] == ACTION_REPORT_TIME == "conversion"
    assert params["swipe_up_attribution_window"] == SWIPE_ATTRIBUTION_WINDOW == "28_DAY"
    assert params["view_attribution_window"] == VIEW_ATTRIBUTION_WINDOW == "1_DAY"
    assert "conversion_purchases" in params["fields"]
    assert "conversion_purchases_value" in params["fields"]
