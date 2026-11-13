"""The sync orchestrator must not publish failed/truncated HOUR responses."""
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock

import pytest
from integrations_control_center import snapchat_native_performance_sync as sync

DAY = date(2026, 11, 13)
ACCOUNT = {"ad_account_id": "account", "timezone": "Asia/Riyadh", "currency": "SAR"}
NEXT = "https://adsapi.snapchat.com/v1/adaccounts/account/stats?cursor=next"


def payload(next_link=None):
    result = {"request_status": "SUCCESS", "timeseries_stats": [{
        "sub_request_status": "SUCCESS", "timeseries_stat": {
            "breakdown_stats": {"campaign": [{"id": "campaign", "timeseries": [{
                "start_time": "2026-11-13T00:00:00+03:00",
                "end_time": "2026-11-13T01:00:00+03:00",
                "stats": {"spend": 1_000_000},
            }]}]},
        },
    }]}
    if next_link:
        result["paging"] = {"next_link": next_link}
    return result


class Context:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
    async def get_json(self, *args, **kwargs):
        self.calls += 1
        return deepcopy(self.responses[min(self.calls - 1, len(self.responses) - 1)])


@pytest.fixture
def writers(monkeypatch):
    daily = AsyncMock()
    local = AsyncMock(return_value=0)
    monkeypatch.setattr(sync, "_upsert_performance", daily)
    monkeypatch.setattr(sync, "persist_account_local_campaign_facts", local)
    return daily, local


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["request", "subrequest", "malformed"])
async def test_unsuccessful_response_preserves_previous_daily_facts(writers, kind):
    data = payload()
    if kind == "request":
        data["request_status"] = "ERROR"
    elif kind == "subrequest":
        data["timeseries_stats"].append({"sub_request_status": "FAILURE"})
    else:
        data["timeseries_stats"].append(None)
    count, errors = await sync.sync_snapchat_performance(
        Context([data]), None, "test-only", ACCOUNT, start_date=DAY, end_date=DAY)
    assert count == 0 and errors
    writers[0].assert_not_awaited()
    writers[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_next_page_remaining_at_cap_never_publishes_partial_amount(monkeypatch, writers):
    monkeypatch.setattr(sync, "MAX_PAGES", 2)
    context = Context([payload(NEXT)])
    count, errors = await sync.sync_snapchat_performance(
        context, None, "test-only", ACCOUNT, start_date=DAY, end_date=DAY)
    assert context.calls == 2 and count == 0
    assert any(error["error"] == "stats_pagination_incomplete" for error in errors)
    writers[0].assert_not_awaited()
    writers[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_last_page_at_exact_cap_is_complete(monkeypatch, writers):
    monkeypatch.setattr(sync, "MAX_PAGES", 2)
    final = {"request_status": "SUCCESS", "timeseries_stats": []}
    context = Context([payload(NEXT), final])
    count, errors = await sync.sync_snapchat_performance(
        context, None, "test-only", ACCOUNT, start_date=DAY, end_date=DAY)
    assert context.calls == 2 and not errors and count == 2
    assert writers[0].await_count == 2
    writers[1].assert_awaited_once()
    assert writers[1].await_args.kwargs["source_complete"] is True
