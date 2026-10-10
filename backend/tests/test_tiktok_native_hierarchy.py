from copy import deepcopy
from datetime import date
import json

import pytest

from integrations_control_center import tiktok_native_hierarchy as hierarchy
from integrations_control_center.tiktok_native_reporting import TIKTOK_REPORTING_COLLECTION, TikTokReportingError
from tests.test_tiktok_native_reporting import FakeDB

ACCOUNT = {"ad_account_id": "70001", "external_account_id": "70001", "display_name": "Test account",
           "currency": "SAR", "timezone": "Asia/Riyadh", "provider": "tiktok_ads",
           "user_id": "owner", "connection_status": "connected", "connection_provenance": "api_connection"}


def response(rows, *, page=1, pages=1, total=None):
    class Response:
        status_code = 200
        def json(self):
            return {"code": 0, "data": {"list": deepcopy(rows),
                "page_info": {"page": page, "total_page": pages, "total_number": len(rows) if total is None else total}}}
    return Response()


class Client:
    calls = []
    fail = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def __init__(self, **kwargs): pass
    async def get(self, url, *, headers, params):
        self.calls.append((url, deepcopy(params)))
        assert set(headers) == {"Access-Token"}
        if url == hierarchy.TIKTOK_REPORT_URL:
            kind = {value[2]: key for key, value in hierarchy.KINDS.items()}[params["data_level"]]
            if self.fail and kind == "adgroup":
                return response([], total=1)
            id_key = hierarchy.KINDS[kind][0]
            return response([{ "dimensions": {id_key: kind + "-1", "stat_time_day": "2026-10-03 00:00:00"},
                               "metrics": {"spend": "10.25", "impressions": "100", "clicks": "5", "conversion": "2"}}])
        kind = url.split("/")[-3]
        id_key, name_key, _ = hierarchy.KINDS[kind]
        return response([{id_key: kind + "-1", name_key: "Real " + kind, "campaign_id": "campaign-1",
                          "adgroup_id": "adgroup-1", "advertiser_id": "70001", "operation_status": "ENABLE"}])


@pytest.fixture
def db(monkeypatch):
    db = FakeDB()
    db.rows["mezan_integration_accounts_v2"] = [deepcopy(ACCOUNT)]
    async def credential(*args): return "test-token"
    monkeypatch.setattr(hierarchy, "_credential", credential)
    monkeypatch.setattr(hierarchy.httpx, "AsyncClient", Client)
    Client.calls, Client.fail = [], False
    return db


@pytest.mark.asyncio
async def test_real_hierarchy_ids_and_all_levels_are_persisted_without_provider_writes(db):
    result = await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="2026-10-04T00:00:00Z")
    assert result == {"status": "complete", "entity_counts": {"campaign": 1, "adgroup": 1, "ad": 1}, "errors": [], "errors_count": 0}
    assert len(Client.calls) == 6
    assert {row["entity_type"] for row in db.rows[hierarchy.ENTITY_COLLECTION]} == {"campaign", "adgroup", "ad"}
    for url, params in Client.calls:
        assert params["advertiser_id"] == "70001"
        assert json.loads(params["filtering"])
    assert all(name in {hierarchy.ENTITY_COLLECTION, hierarchy.DAILY_COLLECTION} for name, *_ in db.writes)


@pytest.mark.asyncio
async def test_repeat_sync_replaces_snapshots_without_doubling_metrics(db):
    for _ in range(2):
        await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="2026-10-04T00:00:00Z")
    assert len(db.rows[hierarchy.ENTITY_COLLECTION]) == 3
    assert len(db.rows[hierarchy.DAILY_COLLECTION]) == 3
    assert db.rows[hierarchy.DAILY_COLLECTION][0]["rows"][0]["spend_native"] == 10.25


@pytest.mark.asyncio
async def test_incomplete_provider_pages_preserve_previous_valid_snapshot(db):
    await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="first")
    previous = deepcopy([row for row in db.rows[hierarchy.DAILY_COLLECTION] if row["entity_type"] == "adgroup"])
    Client.fail = True
    result = await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="second")
    assert result["status"] == "partial" and result["errors_count"] == 1
    assert [row for row in db.rows[hierarchy.DAILY_COLLECTION] if row["entity_type"] == "adgroup"] == previous


@pytest.mark.asyncio
async def test_pagination_reads_every_page_and_rejects_changed_total():
    class Pages:
        async def get(self, url, headers, params):
            page = params["page"]
            return response([{"id": page}], page=page, pages=2, total=2)
    rows = await hierarchy._pages(Pages(), "test", "url", {}, limit=10)
    assert rows == [{"id": 1}, {"id": 2}]
    class Changed(Pages):
        async def get(self, url, headers, params):
            return response([{"id": params["page"]}], page=params["page"], pages=2, total=params["page"] + 1)
    with pytest.raises(TikTokReportingError, match="incomplete or invalid"):
        await hierarchy._pages(Changed(), "test", "url", {}, limit=10)


@pytest.mark.asyncio
async def test_workspace_keeps_account_total_independent_and_conversions_are_not_orders(db):
    await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="first")
    db.rows[TIKTOK_REPORTING_COLLECTION] = [{"user_id": "owner", "ad_account_id": "70001", "date": "2026-10-03",
        "spend_native": 20, "spend_sar": 20, "impressions": 300, "clicks": 15, "conversions": 7}]
    output = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-03", to_date="2026-10-03")
    assert output["totals"]["spend_sar"] == 20
    assert output["entities"][0]["spend_sar"] == 10.25
    assert output["totals"]["conversions"] == 7
    assert output["totals"]["orders"] is None and output["totals"]["sales_sar"] is None
    assert output["accounts"][0]["conversions"] == 7
    assert output["entities"][0]["entity_id"] == "campaign-1"
    assert output["ai_readiness"]["orders_ready"] is False


@pytest.mark.asyncio
async def test_missing_daily_snapshot_stays_unknown_and_foreign_tenant_does_not_leak(db):
    await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="first")
    foreign = deepcopy(db.rows[hierarchy.ENTITY_COLLECTION][0]); foreign["user_id"] = "other"
    foreign["entities"][0]["entity_id"] = "private-other"
    db.rows[hierarchy.ENTITY_COLLECTION].append(foreign)
    output = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-03", to_date="2026-10-04")
    assert output["entities"][0]["spend_sar"] is None
    assert output["totals"]["spend_sar"] is None
    assert all(row["entity_id"] != "private-other" for row in output["entities"])


@pytest.mark.asyncio
async def test_complete_empty_report_is_zero_but_metadata_is_not_a_fake_campaign(db):
    await hierarchy.sync_tiktok_hierarchy(db, "owner", [date(2026, 10, 3)], observed_at="first")
    db.rows[hierarchy.DAILY_COLLECTION][0]["rows"] = []
    result = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-03", to_date="2026-10-03")
    assert result["entities"][0]["spend_sar"] == 0
    assert result["entities"][0]["entity_id"] != "_default"
    assert result["entities"][0]["conversions"] == 0


@pytest.mark.parametrize("metric", [None, "bad", "NaN", "Infinity", -1])
def test_malformed_metric_cannot_replace_valid_snapshot(metric):
    rows = [{"dimensions": {"campaign_id": "1", "stat_time_day": "2026-10-03"},
             "metrics": {"spend": metric, "impressions": 1, "clicks": 1, "conversion": 1}}]
    with pytest.raises(TikTokReportingError): hierarchy._daily(rows, "campaign", ["2026-10-03"])


def test_duplicate_entity_and_wrong_advertiser_are_rejected():
    with pytest.raises(TikTokReportingError):
        hierarchy._entities([{"campaign_id": "1"}, {"campaign_id": "1"}], "70001", "campaign")
    with pytest.raises(TikTokReportingError):
        hierarchy._entities([{"campaign_id": "1", "advertiser_id": "other"}], "70001", "campaign")
