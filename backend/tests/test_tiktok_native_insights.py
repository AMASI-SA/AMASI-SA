"""AI boundary contracts; no real credentials, network or provider writes."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from integrations_control_center import tiktok_native_insights as insights
from integrations_control_center.tiktok_native_reporting import TikTokReportingError


PAYLOAD = {"account_id": "70001", "campaign_id": "campaign-1",
           "from_date": "2026-10-03", "to_date": "2026-10-09"}
RECOMMENDATION = {"focus": "tracking", "summary": "راجع تتبع الحملة.",
                  "next_step": "تحقق من ربط طلبات سلة قبل أي قرار مالي."}


class Client:
    def __init__(self, output=None, status="completed", started=None, finish=None):
        self.responses = self
        self.output = json.dumps(RECOMMENDATION if output is None else output)
        self.status, self.started, self.finish = status, started, finish
        self.calls, self.closed = [], False

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.started:
            self.started.set()
        if self.finish:
            await self.finish.wait()
        return SimpleNamespace(status=self.status, output_text=self.output)

    async def close(self):
        self.closed = True


@pytest.fixture
def boundary(monkeypatch):
    insights._cache.clear(); insights._recent.clear()
    monkeypatch.setattr(insights, "_ai_slot", asyncio.Semaphore(1))
    monkeypatch.setattr(insights, "_clock", lambda: 100.0)
    monkeypatch.setattr(insights.governor, "peek", lambda: ("normal", 0))
    monkeypatch.delenv("MEZAN_OPENAI_MODEL", raising=False)
    calls = []

    async def workspace(db, user_id, **kwargs):
        calls.append((user_id, kwargs))
        return {"entities": [{"account_id": kwargs["account_id"], "entity_id": kwargs["campaign_id"],
            "entity_name": "Verified campaign", "status": "ENABLE", "objective": "WEB_CONVERSIONS",
            "data_complete": True, "spend_sar": 57.4, "impressions": 5068, "clicks": 53,
            "conversions": 2, "ctr_pct": 1.05, "cpc_sar": 1.08, "cpm_sar": 11.33,
            "customer_phone": "PRIVATE_PHONE", "video_url": "PRIVATE_MEDIA",
            "raw": {"token": "PRIVATE_TOKEN"}}],
            "range": {"date_from": kwargs["from_date"], "date_to": kwargs["to_date"]}}

    async def ledger(*args):
        return {"exact_campaign_id_records": 2, "financial_orders": None, "sales_sar": None,
                "profit_sar": None, "financial_coverage": "not_verified",
                "record_coverage": "available_ledger_records_only"}

    monkeypatch.setattr(insights, "tiktok_workspace", workspace)
    monkeypatch.setattr(insights, "ledger_record_evidence", ledger)
    return calls


async def analyze(client, user="owner", **changes):
    return await insights.analyze_tiktok_campaign(object(), user,
        insights.TikTokCampaignAnalysisInput(**{**PAYLOAD, **changes}), client_factory=lambda: client)


@pytest.mark.asyncio
async def test_one_verified_campaign_with_limited_allowlisted_context(boundary):
    client = Client()
    result = await analyze(client)
    assert boundary == [("owner", {"from_date": "2026-10-03", "to_date": "2026-10-09",
        "entity_type": "campaign", "page": 1, "limit": 1,
        "campaign_id": "campaign-1", "account_id": "70001"})]
    request = client.calls[0]
    assert len(request["input"].encode("utf-8")) <= 8000
    assert not any(value in request["input"] for value in ["PRIVATE_PHONE", "PRIVATE_MEDIA", "PRIVATE_TOKEN"])
    assert request["store"] is False and "tools" not in request
    assert request["max_output_tokens"] == 1800
    assert result["context"]["salla_evidence"]["sales_sar"] is None
    assert result["context"]["salla_evidence"]["financial_orders"] is None
    assert result["policy"]["mutations_allowed"] is False
    assert result["recommendation"] == RECOMMENDATION and client.closed


@pytest.mark.asyncio
async def test_cache_checks_current_authority_and_never_crosses_tenant(boundary):
    first = Client()
    result = await analyze(first)
    result["context"]["metrics"]["spend_sar"] = 9999
    cached = await analyze(Client())
    assert cached["cached"] is True and cached["context"]["metrics"]["spend_sar"] == 57.4
    other = Client()
    assert (await analyze(other, user="other"))["cached"] is False
    assert [owner for owner, _ in boundary] == ["owner", "owner", "other"]
    assert len(first.calls) == len(other.calls) == 1


@pytest.mark.asyncio
async def test_cache_is_bounded_across_many_owners(boundary):
    for i in range(33):
        await analyze(Client(), user=f"tenant-{i}")
    assert len(insights._cache) <= 32 and len(insights._recent) <= 32


@pytest.mark.asyncio
async def test_overlapping_analysis_refuses_before_any_source_query(boundary):
    started, finish = asyncio.Event(), asyncio.Event()
    client = Client(started=started, finish=finish)
    first = asyncio.create_task(analyze(client))
    await asyncio.wait_for(started.wait(), timeout=1)
    try:
        with pytest.raises(TikTokReportingError) as error:
            await analyze(Client(), user="other")
        assert error.value.status_code == 503
        assert len(boundary) == 1
    finally:
        finish.set()
        await first
    assert len(client.calls) == 1 and not insights._ai_slot.locked()


@pytest.mark.asyncio
async def test_governor_pressure_refuses_before_database_and_model(boundary, monkeypatch):
    monkeypatch.setattr(insights.governor, "peek", lambda: ("blocked", 0))
    client = Client()
    with pytest.raises(TikTokReportingError) as error:
        await analyze(client)
    assert error.value.status_code == 503
    assert not boundary and not client.calls


@pytest.mark.asyncio
async def test_new_campaign_rate_is_limited_without_queue(boundary):
    await analyze(Client())
    client = Client()
    with pytest.raises(TikTokReportingError) as error:
        await analyze(client, campaign_id="campaign-2")
    assert error.value.status_code == 429 and not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("output,status", [
    ({**RECOMMENDATION, "focus": "scale"}, "completed"),
    ({**RECOMMENDATION, "budget": 500}, "completed"),
    ({**RECOMMENDATION, "summary": "x" * 701}, "completed"),
    (RECOMMENDATION, "incomplete"),
    ({**RECOMMENDATION, "next_step": ""}, "completed"),
])
async def test_invalid_model_response_is_safe_failure_not_cached(boundary, output, status):
    client = Client(output=output, status=status)
    with pytest.raises(TikTokReportingError) as error:
        await analyze(client)
    assert error.value.code == "tiktok_ai_generation_failed"
    assert error.value.status_code == 502
    assert not insights._cache and client.closed and not insights._ai_slot.locked()


@pytest.mark.asyncio
async def test_cancellation_releases_capacity_and_closes_client(boundary):
    started, finish = asyncio.Event(), asyncio.Event()
    client = Client(started=started, finish=finish)
    task = asyncio.create_task(analyze(client))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.closed and not insights._ai_slot.locked() and not insights._cache


@pytest.mark.asyncio
async def test_missing_key_reports_unavailable_without_network(boundary, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TikTokReportingError) as error:
        await insights.analyze_tiktok_campaign(object(), "owner", insights.TikTokCampaignAnalysisInput(**PAYLOAD))
    assert error.value.code == "tiktok_ai_not_configured" and error.value.status_code == 503


@pytest.mark.asyncio
async def test_disconnect_invalidates_cached_authority(boundary, monkeypatch):
    await analyze(Client())
    async def disconnected(*args, **kwargs):
        return {"entities": []}
    monkeypatch.setattr(insights, "tiktok_workspace", disconnected)
    client = Client()
    with pytest.raises(TikTokReportingError) as error:
        await analyze(client)
    assert error.value.status_code == 404 and not client.calls
