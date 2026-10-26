"""Fresh complete catalog parents trigger real bounded child reporting requests."""
from copy import deepcopy
from datetime import date
import json

import pytest

from snapchat_v2.client import SnapchatClientError, SnapchatV2Client
from snapchat_v2 import sync_pipeline as module
from tests.test_snapchat_reporting_v2_hourly_publish_heartbeat import (
    FakeDB, NOW, USER_ID, ACCOUNT_ID, _install_pipeline_fakes, _state,
)
from tests.test_snapchat_reporting_v2_entity_performance import FakeResponse, FakeTokenStore

DAY = date(2026, 8, 28)
EMPTY = {"status": "complete", "data_state": "confirmed_no_data",
         "expected_requests": 1, "completed_requests": 1, "rows_received": 0}
REAL_BREAKDOWN = module.SnapchatV2SyncPipeline._sync_breakdown_performance
REAL_IDENTITIES = module.SnapchatV2SyncPipeline._sync_identities


class Transport:
    def __init__(self, mode):
        self.mode, self.calls = mode, []
    def __call__(self, **kwargs): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def get(self, url, *, headers, params):
        self.calls.append((url, deepcopy(params)))
        if url.endswith("/campaigns"):
            if self.mode == "catalog403":
                response = FakeResponse({"request_status": "ERROR"}); response.status_code = 403
                return response
            return FakeResponse({"request_status": "SUCCESS", "campaigns": [
                {"campaign": {"id": f"catalog-{i}", "status": "PAUSED"}} for i in range(62)], "paging": {}})
        if url.endswith("/adsquads"):
            return FakeResponse({"request_status": "SUCCESS", "adsquads": [], "paging": {}})
        if url.endswith("/ads"):
            return FakeResponse({"request_status": "SUCCESS", "ads": [], "paging": {}})
        if "/adaccounts/" in url and url.endswith("/stats"):
            campaigns = []
            if self.mode == "usd_normal":
                campaigns = [{"id": "existing-day-parent", "timeseries": [{
                    "start_time": params["start_time"], "end_time": params["end_time"], "stats": {"spend": 0}}]}]
            return FakeResponse({"request_status": "SUCCESS", "timeseries_stats": [{
                "sub_request_status": "SUCCESS", "timeseries_stat": {
                    "granularity": "DAY", "breakdown_stats": {"campaign": campaigns}}}], "paging": {}})
        assert "/campaigns/" in url and url.endswith("/stats")
        if self.mode in {"child403", "rate_limit"}:
            response = FakeResponse({"request_status": "ERROR"}); response.status_code = 429 if self.mode == "rate_limit" else 403
            return response
        key = params["breakdown"]
        parent = url.split("/campaigns/")[1].split("/")[0]
        rows = [] if self.mode == "child_empty" else [{"id": f"{key}-{parent}", "stats": {"spend": 0}}]
        return FakeResponse({"request_status": "SUCCESS", "total_stats": [{"sub_request_status": "SUCCESS",
            "total_stat": {"granularity": "TOTAL", "breakdown_stats": {key: rows}}}], "paging": {}})


async def run_pipeline(monkeypatch, mode):
    state = _state(); db = FakeDB(state); _install_pipeline_fakes(monkeypatch, db, state)
    # This fixture tests reporting boundaries, independent of the shared test
    # host's live memory pressure; production governor calls remain intact.
    class HealthyGovernor:
        async def acquire(self, *args, **kwargs): return object(), None
        async def release(self, token): state["admission_released"] = True
        def safe_checkpoint(self): state["checkpoint_count"] = state.get("checkpoint_count", 0) + 1
    monkeypatch.setattr(module, "governor", HealthyGovernor())
    monkeypatch.setattr(module.SnapchatV2SyncPipeline, "_sync_breakdown_performance", REAL_BREAKDOWN)
    monkeypatch.setattr(module.SnapchatV2SyncPipeline, "_sync_identities", REAL_IDENTITIES)
    saved = []
    async def save(_db, rows, **kwargs):
        saved.extend(deepcopy(rows)); return {"rows_received": len(rows), "rows_saved": len(rows)}
    async def sync(_db, *, rows, **kwargs):
        return {"rows_received": len(rows), "rows_saved": len(rows), "rows_staled": 0}
    async def entities(*args, **kwargs): return []
    monkeypatch.setattr(module, "upsert_total_facts", save)
    monkeypatch.setattr(module, "sync_entities", sync)
    monkeypatch.setattr(module, "list_entities", entities)
    transport = Transport(mode)
    client = SnapchatV2Client(db, USER_ID, token_store=FakeTokenStore(), client_factory=transport)
    if mode == "budget": client.provider_calls = 380
    async def hourly(account, *, sync_run_id, **kwargs):
        return {"rows": [], "campaign_rows": [], "account_rows": [], "coverage": EMPTY, "request_windows": []}
    client.fetch_hourly_facts = hourly
    if mode in {"partial_catalog", "foreign_catalog"}:
        original = client.fetch_entities
        async def altered(account_id, level):
            result = await original(account_id, level)
            if level == "campaign":
                if mode == "partial_catalog": result["coverage"]["status"] = "partial"
                else: result["rows"][0]["ad_account_id"] = "foreign-account"
            return result
        client.fetch_entities = altered
    class Connection:
        async def ensure_indexes(self): pass
        async def validate_ready(self, user, *, ad_account_id=None):
            return {}, {"ad_account_id": ACCOUNT_ID,
                "currency": "USD" if mode == "usd_normal" else "SAR",
                "timezone": "America/Los_Angeles" if mode == "usd_normal" else "Asia/Riyadh"}
    pipeline = module.SnapchatV2SyncPipeline(db, now=lambda: NOW, connection_manager=Connection(),
                                            client_factory=lambda *args: client)
    result = await pipeline.run(USER_ID, ACCOUNT_ID, date_from=DAY, date_to=DAY)
    children = [(url, params) for url, params in transport.calls if "/campaigns/" in url and url.endswith("/stats")]
    return result, state, client, saved, children


@pytest.mark.asyncio
async def test_62_paused_catalog_parents_produce_real_zero_metric_child_rows(monkeypatch):
    result, state, client, saved, calls = await run_pipeline(monkeypatch, "normal")
    assert result["status"] == "complete"
    assert len(calls) == 124
    assert {url.split("/campaigns/")[1].split("/")[0] for url, _ in calls} == {f"catalog-{i}" for i in range(62)}
    assert len([row for row in saved if row["entity_type"] == "ad_squad"]) == 62
    assert len([row for row in saved if row["entity_type"] == "ad"]) == 62
    assert all(row["spend_native"] == 0 and row["currency"] == "SAR" for row in saved)
    assert all(params["start_time"] == "2026-08-28T00:00:00+03:00" and params["end_time"] == "2026-08-29T00:00:00+03:00" for _, params in calls)
    assert "_campaign_catalog_scope" not in json.dumps(result, default=str)
    assert "catalog-0" not in json.dumps(result["summary"], default=str)
    assert state["run"]["campaign_sync_status"] == "complete"
    assert state["checkpoint_count"] >= 2 and state["admission_released"] is True
    assert client.provider_calls == 128


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["catalog403", "partial_catalog", "foreign_catalog", "budget"])
async def test_unproven_catalog_or_both_level_budget_stops_children_without_hurting_campaign(monkeypatch, mode):
    result, state, client, saved, calls = await run_pipeline(monkeypatch, mode)
    assert result["status"] == "partial" and calls == []
    assert state["run"]["campaign_sync_status"] == state["run"]["financial_sync_status"] == "complete"
    assert state["run"]["ad_squad_sync_status"] == state["run"]["ad_sync_status"] == "partial"
    assert saved == [] and client.provider_calls <= 500
    if mode == "budget":
        assert {item["code"] for item in result["summary"]["warnings"]} == {"snapchat_child_catalog_scope_budget_exceeded"}
    assert "_campaign_catalog_scope" not in json.dumps(result, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["child403", "child_empty"])
async def test_actual_child_failure_or_no_rows_keeps_explicit_incomplete_evidence(monkeypatch, mode):
    result, state, client, saved, calls = await run_pipeline(monkeypatch, mode)
    assert result["status"] == "partial" and calls
    assert state["run"]["campaign_sync_status"] == "complete" and saved == []
    assert state["run"]["ad_squad_sync_status"] == state["run"]["ad_sync_status"] == "partial"
    if mode == "child_empty":
        assert len(calls) == 124
        assert all("zero_performance_proof_unavailable" in item["code"] for item in result["summary"]["warnings"])
    assert client.provider_calls <= 500


@pytest.mark.asyncio
async def test_existing_usd_day_scope_keeps_only_reported_parent_and_native_timezone(monkeypatch):
    result, state, client, saved, calls = await run_pipeline(monkeypatch, "usd_normal")
    assert result["status"] == "complete" and len(calls) == 2
    assert all("/campaigns/existing-day-parent/stats" in url for url, _ in calls)
    assert all(params["start_time"] == "2026-08-28T00:00:00-07:00" for _, params in calls)
    assert all(row["currency"] == "USD" for row in saved)


def test_joint_preflight_never_trims_seven_days_or_62_parents():
    client = SnapchatV2Client(None, "owner", token_store=FakeTokenStore())
    parents = [f"p{i}" for i in range(62)]
    with pytest.raises(SnapchatClientError, match="complete catalog") as error:
        client.preflight_child_total_scope(parents, [date(2026, 8, day) for day in range(22, 29)])
    assert error.value.coverage["expected_requests"] == 868
    assert client.provider_calls == 0


@pytest.mark.asyncio
async def test_preflight_never_bypasses_the_hard_budget_when_requests_retry(monkeypatch):
    transport = Transport("rate_limit")
    client = SnapchatV2Client(None, "owner", token_store=FakeTokenStore(), client_factory=transport)
    client.provider_calls = 498
    client.preflight_child_total_scope(["one-parent"], [DAY])
    async def no_wait(*args): pass
    monkeypatch.setattr("snapchat_v2.client.asyncio.sleep", no_wait)
    with pytest.raises(SnapchatClientError) as caught:
        await client.fetch_breakdown_daily_total_facts(
            {"ad_account_id": ACCOUNT_ID, "currency": "SAR", "timezone": "Asia/Riyadh"},
            campaign_ids=["one-parent"], entity_type="ad_squad", report_dates=[DAY], sync_run_id="test")
    assert caught.value.code == "snapchat_provider_call_budget_exceeded"
    assert len(transport.calls) == 2  # Only requests 499 and 500 reached HTTP.
