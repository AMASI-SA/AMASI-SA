"""Real Mongo regression for bounded campaign reads, with isolated test data."""
from copy import deepcopy
from datetime import date, timedelta
import gc
import json
import os
import subprocess
import time
import tracemalloc
import types
from urllib.parse import urlparse
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
import pytest
import pytest_asyncio

from integrations_control_center import tiktok_native_hierarchy as hierarchy
from integrations_control_center.tiktok_native_reporting import TIKTOK_REPORTING_COLLECTION, TikTokReportingError
from tests.test_tiktok_native_hierarchy import ACCOUNT, response


class ReadEvidence(CommandListener):
    def __init__(self):
        self.commands, self.fact_rows, self.catalogue_arrays = [], 0, 0
    def started(self, event):
        if event.command_name in {"find", "aggregate"}:
            self.commands.append((event.command_name, event.command.get(event.command_name)))
    def succeeded(self, event):
        cursor = event.reply.get("cursor") or {}
        for row in cursor.get("firstBatch", cursor.get("nextBatch", [])):
            self.fact_rows += len(row.get("rows") or [])
            self.catalogue_arrays += len(row.get("entities") or [])
    def failed(self, event):
        pass
    def clear(self):
        self.commands, self.fact_rows, self.catalogue_arrays = [], 0, 0


@pytest_asyncio.fixture
async def mongo_db():
    url = os.environ.get("TEST_TIKTOK_MONGO_URL")
    if not url:
        pytest.skip("TEST_TIKTOK_MONGO_URL must name an isolated local Mongo test server")
    parsed = urlparse(url)
    assert parsed.hostname in {"127.0.0.1", "localhost"}, "Only the isolated local test server is allowed"
    assert parsed.username is None and parsed.password is None
    listener = ReadEvidence()
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=3000, event_listeners=[listener])
    name = "tiktok_hierarchy_test_" + uuid4().hex
    db = client[name]
    await client.admin.command("ping")
    try:
        yield db, listener
    finally:
        await client.drop_database(name)
        client.close()


def measured_module():
    # A read-only baseline run can execute this same budget assertion against
    # the checkpoint before the performance change. CI exercises current code.
    baseline = os.environ.get("TIKTOK_HIERARCHY_BASELINE_REF")
    if not baseline:
        return hierarchy
    source = subprocess.run(["git", "show", baseline + ":backend/integrations_control_center/tiktok_native_hierarchy.py"],
                            capture_output=True, text=True, check=True).stdout
    module = types.ModuleType("integrations_control_center.tiktok_perf_baseline")
    module.__package__ = "integrations_control_center"
    exec(compile(source, "<reviewed-tiktok-baseline>", "exec"), module.__dict__)
    return module


@pytest.mark.asyncio
async def test_large_catalogue_30_days_reads_only_25_entities_and_their_facts(mongo_db):
    db, evidence = mongo_db
    await db.mezan_integration_accounts_v2.insert_many([deepcopy(ACCOUNT), {**ACCOUNT, "user_id": "other"}])
    entities = [{"entity_id": f"c{i:05d}", "entity_name": f"Campaign {i}", "campaign_id": f"c{i:05d}",
                 "adgroup_id": None, "status": "ENABLE", "delivery_status": None,
                 "objective": "TRAFFIC", "budget_native": 50, "budget_mode": "BUDGET_MODE_DAY"}
                for i in range(5000)]
    identity = {"user_id": "owner", "ad_account_id": "70001", "entity_type": "campaign"}
    await db[hierarchy.ENTITY_COLLECTION].insert_many([
        {**identity, "complete": True, "entity_count": 5000, "entities": entities},
        {**identity, "user_id": "other", "complete": True, "entity_count": 1,
         "entities": [{**entities[0], "entity_id": "private-other"}]}])
    await db[hierarchy.ENTITY_COLLECTION].create_index([
        ("user_id", 1), ("ad_account_id", 1), ("entity_type", 1)], unique=True)
    await db[hierarchy.DAILY_COLLECTION].create_index([
        ("user_id", 1), ("ad_account_id", 1), ("entity_type", 1), ("date", 1)], unique=True)
    start = date(2026, 10, 1)
    facts = [{"entity_id": row["entity_id"], "spend_native": 1, "impressions": 10,
              "clicks": 2, "conversions": 0} for row in entities]
    for offset in range(30):
        day = (start + timedelta(days=offset)).isoformat()
        await db[hierarchy.DAILY_COLLECTION].insert_one({**identity, "date": day, "complete": True, "rows": facts})
        await db[TIKTOK_REPORTING_COLLECTION].insert_one({"user_id": "owner", "ad_account_id": "70001",
            "date": day, "spend_native": 200, "spend_sar": 200, "impressions": 100, "clicks": 10, "conversions": 3})
    del entities, facts
    gc.collect()
    evidence.clear()
    target = measured_module()
    tracemalloc.start()
    started = time.perf_counter()
    report, problem = None, None
    try:
        report = await target.tiktok_workspace(db, "owner", from_date="2026-10-01", to_date="2026-10-30")
    except TikTokReportingError as exc:
        problem = exc.code
    finally:
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    payload_bytes = len(json.dumps(report, ensure_ascii=False).encode()) if report else 0
    print(json.dumps({"case": "5000 campaigns / 150000 daily facts / page 25",
        "baseline": bool(os.environ.get("TIKTOK_HIERARCHY_BASELINE_REF")),
        "source_error": problem,
        "python_peak_bytes": peak, "seconds": round(elapsed, 4), "response_bytes": payload_bytes,
        "transported_fact_rows": evidence.fact_rows, "transported_catalogue_arrays": evidence.catalogue_arrays}))
    assert problem is None
    assert peak < 16 * 1024 * 1024
    assert len(report["entities"]) == 25 and report["campaign_pagination"]["total"] == 5000
    assert report["entities"][0]["entity_id"] == "c04999"
    assert report["entities"][0]["spend_sar"] == 30
    assert report["totals"]["spend_sar"] == 6000  # Independent account report, never sum the hierarchy.
    assert report["totals"]["orders"] is None
    assert payload_bytes < 40000
    assert evidence.fact_rows == 25 * 30
    assert evidence.catalogue_arrays == 0
    assert elapsed < 6

    evidence.clear()
    overview = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-01",
                                               to_date="2026-10-30", entity_type="overview")
    assert overview["entities"] == [] and overview["totals"]["spend_sar"] == 6000
    assert not any(collection == hierarchy.DAILY_COLLECTION or command == "aggregate"
                   for command, collection in evidence.commands)
    assert evidence.catalogue_arrays == 0
    page_two = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-01",
                                               to_date="2026-10-30", page=2, account_id="70001")
    assert len(page_two["entities"]) == 25
    assert {row["entity_id"] for row in page_two["entities"]}.isdisjoint(
        {row["entity_id"] for row in report["entities"]})
    selected = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-01",
        to_date="2026-10-30", campaign_id="c00001", page=999)
    assert selected["campaign_pagination"]["page"] == 1
    assert [row["entity_id"] for row in selected["entities"]] == ["c00001"]
    literal = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-01",
        to_date="2026-10-30", query=".*")
    assert literal["entities"] == []
    missing_day = await hierarchy.tiktok_workspace(db, "owner", from_date="2026-10-01", to_date="2026-10-31")
    assert missing_day["entities"][0]["spend_sar"] is None
    assert missing_day["totals"]["spend_sar"] is None
    with pytest.raises(TikTokReportingError) as foreign:
        await hierarchy.tiktok_workspace(db, "owner", account_id="not-connected")
    assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_large_backfill_has_one_request_at_a_time_and_bounded_memory(mongo_db, monkeypatch):
    db, _ = mongo_db
    await db.mezan_integration_accounts_v2.insert_one(deepcopy(ACCOUNT))
    async def credential(*args): return "isolated-test-token"
    monkeypatch.setattr(hierarchy, "_credential", credential)
    calls = []
    class PagedProvider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, *, headers, params):
            calls.append(deepcopy(params))
            assert params["page_size"] == 500
            first = (params["page"] - 1) * 500
            if url == hierarchy.TIKTOK_REPORT_URL:
                assert params["start_date"] == params["end_date"]  # Large catalogues cannot allocate a month.
                kind = {value[2]: key for key, value in hierarchy.KINDS.items()}[params["data_level"]]
                rows = [{"dimensions": {hierarchy.KINDS[kind][0]: f"{kind}-{i}",
                                        "stat_time_day": params["start_date"]},
                         "metrics": {"spend": "1", "impressions": "10", "clicks": "2", "conversion": "0"}}
                        for i in range(first, first + 500)]
            else:
                kind = url.split("/")[-3]
                id_key, name_key, _ = hierarchy.KINDS[kind]
                fields = json.loads(params["fields"])
                assert len(fields) <= 9 and not any("video" in field or "target" in field for field in fields)
                rows = [{id_key: f"{kind}-{i}", name_key: f"Entity {i}", "advertiser_id": "70001",
                         "campaign_id": f"campaign-{i}", "adgroup_id": f"adgroup-{i}", "operation_status": "ENABLE"}
                        for i in range(first, first + 500)]
            return response(rows, page=params["page"], pages=10, total=5000)
    monkeypatch.setattr(hierarchy.httpx, "AsyncClient", PagedProvider)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = await hierarchy.sync_tiktok_hierarchy(db, "owner",
            [date(2026, 10, 9), date(2026, 10, 10)], observed_at="2026-10-10T18:00:00Z")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(json.dumps({"case": "5000 entities per level / 30000 facts backfill", "python_peak_bytes": peak,
                      "seconds": round(time.perf_counter() - started, 4), "provider_calls": len(calls)}))
    assert result["status"] == "complete"
    assert result["entity_counts"] == {"campaign": 5000, "adgroup": 5000, "ad": 5000}
    assert len(calls) == 90
    assert peak < 16 * 1024 * 1024
    assert await db[hierarchy.DAILY_COLLECTION].count_documents({"user_id": "owner"}) == 6
    index = await db[hierarchy.DAILY_COLLECTION].index_information()
    assert index["tiktok_entity_daily_retention"]["expireAfterSeconds"] == 0
