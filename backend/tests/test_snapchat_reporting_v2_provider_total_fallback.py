from __future__ import annotations

from datetime import datetime, timezone

import pytest

from snapchat_v2.client import SnapchatClientError, SnapchatV2Client
from snapchat_v2.provider_total import _fetch_window_total, fetch_provider_total


class _HTTPContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return False


class _Client:
    def __init__(self):
        self.granularities: list[str] = []
        self.provider_calls = 0

    def client_factory(self, **_kwargs):
        return _HTTPContext()

    async def _pages(self, _http_client, _url, *, params):
        granularity = str(params["granularity"])
        self.granularities.append(granularity)
        self.provider_calls += 1
        if granularity == "TOTAL":
            raise SnapchatClientError(
                "snapchat_provider_http_400",
                "TOTAL is rejected for this ad account.",
            )
        return (
            [
                {
                    "request_status": "SUCCESS",
                    "timeseries_stats": [
                        {
                            "sub_request_status": "SUCCESS",
                            "timeseries_stat": {
                                "granularity": "HOUR",
                                "breakdown_stats": {
                                    "campaign": [
                                        {
                                            "id": "campaign-1",
                                            "timeseries": [
                                                {
                                                    "start_time": "2026-08-22T00:00:00-07:00",
                                                    "end_time": "2026-08-22T01:00:00-07:00",
                                                    "stats": {"spend": 1_500_000},
                                                },
                                                {
                                                    "start_time": "2026-08-22T01:00:00-07:00",
                                                    "end_time": "2026-08-22T02:00:00-07:00",
                                                    "stats": {"spend": 2_000_000},
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                        }
                    ],
                    "paging": {},
                }
            ],
            1,
        )

    def _extract_hour_rows(self, payload):
        return SnapchatV2Client._extract_hour_rows(payload)


@pytest.mark.asyncio
async def test_provider_total_falls_back_to_fresh_hour_read_on_total_http_400():
    client = _Client()
    result = await _fetch_window_total(
        client,
        {
            "ad_account_id": "account-1",
            "timezone": "America/Los_Angeles",
        },
        start_utc=datetime(2026, 8, 22, 7, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 23, 7, tzinfo=timezone.utc),
        action_report_time="conversion",
        swipe_attribution_window="28_DAY",
        view_attribution_window="1_DAY",
    )

    assert client.granularities == ["TOTAL", "HOUR"]
    assert result["provider_spend_native"] == 3.5
    assert result["provider_granularity"] == "HOUR"
    assert result["fallback_from"] == "snapchat_provider_http_400"
    assert result["coverage"]["status"] == "complete"
    assert result["coverage"]["provider_granularity"] == "HOUR"
    assert result["coverage"]["fallback_from"] == "snapchat_provider_http_400"


def test_provider_total_defaults_match_ads_manager_conversion_time_window():
    defaults = fetch_provider_total.__kwdefaults__
    assert defaults["action_report_time"] == "conversion"
    assert defaults["swipe_attribution_window"] == "28_DAY"
    assert defaults["view_attribution_window"] == "7_DAY"
