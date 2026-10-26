"""Empty is a provider observation, never an interpretation of zero spend."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from tests.test_snapchat_reporting_v2_refresh_visibility import Database
from snapchat_v2.committed_totals import COLLECTION, publish_committed_totals
from snapchat_v2.empty_hierarchy import empty_hierarchy_proof, empty_period_complete
from snapchat_v2.total_facts import load_total_facts

DAYS = [date(2026, 10, 22), date(2026, 10, 23)]
ACCOUNT = {"ad_account_id": "saudi", "currency": "SAR", "timezone": "Asia/Riyadh"}
EMPTY = {"status": "complete", "data_state": "confirmed_no_data",
         "expected_requests": 1, "completed_requests": 1, "rows_received": 0}


def proof():
    return empty_hierarchy_proof(ACCOUNT, DAYS, "conversion", {"coverage": EMPTY},
                                {level: {"coverage": EMPTY} for level in ("campaign", "ad_squad", "ad")})


def run(run_id="closed", status="complete"):
    return {"user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "saudi",
            "sync_run_id": run_id, "status": status, "financial_sync_status": "complete",
            "identity_sync_status": "complete", "campaign_sync_status": "complete",
            "ad_squad_sync_status": "complete", "ad_sync_status": "complete",
            "finished_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
            "request_window": {"date_from": DAYS[0].isoformat(), "date_to": DAYS[-1].isoformat(),
                               "action_report_time": "conversion"},
            "summary": {"empty_hierarchy_proof": proof()}}


async def complete(db, **changes):
    return await empty_period_complete(db, **{"user_id": "owner", "account_id": "saudi",
        "currency": "SAR", "timezone_name": "Asia/Riyadh", "date_from": DAYS[0],
        "date_to": DAYS[-1], "action": "conversion", **changes})


@pytest.mark.parametrize("level", ["campaign", "ad_squad", "ad"])
@pytest.mark.parametrize("change", [
    {"status": "incomplete"}, {"data_state": "confirmed_data", "rows_received": 1},
    {"expected_requests": 0, "completed_requests": 0}, {"completed_requests": 0},
    {"expected_requests": True}, {"rows_received": False},
])
def test_failed_missing_403_or_nonempty_catalog_never_proves_empty(level, change):
    identities = {item: {"coverage": deepcopy(EMPTY)} for item in ("campaign", "ad_squad", "ad")}
    identities[level]["coverage"].update(change)
    assert empty_hierarchy_proof(ACCOUNT, DAYS, "conversion", {"coverage": EMPTY}, identities) is None


def test_empty_stats_alone_cannot_prove_empty_catalog():
    assert empty_hierarchy_proof(ACCOUNT, DAYS, "conversion", {"coverage": EMPTY}, {}) is None
    assert empty_hierarchy_proof(ACCOUNT, DAYS, "conversion", {},
        {level: {"coverage": EMPTY} for level in ("campaign", "ad_squad", "ad")}) is None


@pytest.mark.asyncio
async def test_commits_exact_days_without_synthetic_entities_and_keeps_failed_refresh(monkeypatch):
    db = Database()
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(run())
    assert await publish_committed_totals(db, "closed") == 2
    assert await complete(db)
    assert db.raw.mezan_snapchat_daily_total_facts_v2.count_documents({}) == 0
    for level in ("campaign", "ad_squad", "ad"):
        assert await load_total_facts(db, user_id="owner", ad_account_id="saudi", entity_type=level,
            date_from=DAYS[0], date_to=DAYS[-1], account_timezone="Asia/Riyadh") == []
    for status in ("running", "partial", "failed"):
        db.raw.mezan_snapchat_sync_runs_v2.insert_one(run(status, status))
        assert await publish_committed_totals(db, status) == 0
        assert await complete(db)
    from unified_marketing.readers import snapchat_v2 as reader
    async def account(*args, **kwargs):
        return ACCOUNT
    monkeypatch.setattr(reader, "resolve_account", account)
    for level in ("campaign", "ad_group", "ad"):
        result = await reader.load_snapchat_v2_entity_readiness_evidence(
            db, "owner", account_id="saudi", entity_level=level,
            date_from=DAYS[0], date_to=DAYS[-1], timezone_name="Asia/Riyadh")
        assert result["complete"] is True and result["empty_hierarchy_proven"] is True
        assert result["row_count"] == result["source_fact_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"user_id": "foreign"}, {"account_id": "usd"}, {"currency": "USD"},
    {"timezone_name": "America/Los_Angeles"}, {"action": "impression"},
    {"date_to": date(2026, 10, 24)},
])
async def test_proof_isolated_by_owner_account_currency_zone_action_and_days(change):
    db = Database(); db.raw.mezan_snapchat_sync_runs_v2.insert_one(run())
    await publish_committed_totals(db, "closed")
    assert not await complete(db, **change)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["run_missing", "run_partial", "snapshot_fact", "proof_tampered", "missing_day"])
async def test_committed_empty_requires_its_exact_complete_producing_run(mutation):
    db = Database(); db.raw.mezan_snapchat_sync_runs_v2.insert_one(run())
    await publish_committed_totals(db, "closed")
    if mutation == "run_missing": db.raw.mezan_snapchat_sync_runs_v2.delete_many({})
    if mutation == "run_partial": db.raw.mezan_snapchat_sync_runs_v2.update_one({}, {"$set": {"ad_sync_status": "partial"}})
    if mutation == "snapshot_fact": db.raw[COLLECTION].update_one({}, {"$set": {"rows.ad": [{"external_id": "unexpected"}]}})
    if mutation == "proof_tampered": db.raw[COLLECTION].update_one({}, {"$set": {"empty_hierarchy_proof.currency": "USD"}})
    if mutation == "missing_day": db.raw[COLLECTION].delete_one({})
    assert not await complete(db)


@pytest.mark.asyncio
async def test_empty_report_does_not_resurrect_old_catalog_identities(monkeypatch):
    db = Database(); db.raw.mezan_snapchat_sync_runs_v2.insert_one(run())
    await publish_committed_totals(db, "closed")
    import snapchat_v2.routes as routes
    async def hourly(*args, **kwargs): return []
    async def identities(*args, **kwargs): return [{"external_id": "stale", "name": "old"}]
    monkeypatch.setattr(routes, "load_hourly_facts", hourly)
    monkeypatch.setattr(routes, "list_entities", identities)
    result = await routes._entity_performance_report(db, user_id="owner", account=ACCOUNT,
        date_from=DAYS[0], date_to=DAYS[-1], timezone_name="Asia/Riyadh",
        action_report_time="conversion", entity_type="ad")
    assert result["empty_hierarchy_proven"] is True
    assert result["rows"] == [] and result["totals"]["source_fact_count"] == 0
    assert result["performance_sync_status"] == "complete"


@pytest.mark.asyncio
async def test_empty_commit_respects_document_limit_and_does_not_replace_previous(monkeypatch):
    db = Database(); db.raw.mezan_snapchat_sync_runs_v2.insert_one(run())
    await publish_committed_totals(db, "closed")
    previous = list(db.raw[COLLECTION].find({}, {"_id": 0}))
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(run("new"))
    import snapchat_v2.committed_totals as committed
    monkeypatch.setattr(committed, "MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(ValueError, match="document_limit"):
        await publish_committed_totals(db, "new")
    assert list(db.raw[COLLECTION].find({}, {"_id": 0})) == previous
    assert await complete(db)


@pytest.mark.asyncio
async def test_forged_proof_cannot_publish():
    db = Database(); source = run()
    source["summary"]["empty_hierarchy_proof"]["catalog_responses"]["campaign"]["rows_received"] = 1
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(source)
    with pytest.raises(ValueError, match="empty_proof_invalid"):
        await publish_committed_totals(db, "closed")
    assert db.raw[COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_same_proof_engine_accepts_usd_with_independent_native_settings():
    db = Database(); source = run()
    account = {"ad_account_id": "dollar", "currency": "USD", "timezone": "America/Los_Angeles"}
    source["ad_account_id"] = "dollar"
    source["summary"]["empty_hierarchy_proof"] = empty_hierarchy_proof(
        account, DAYS, "conversion", {"coverage": EMPTY},
        {level: {"coverage": EMPTY} for level in ("campaign", "ad_squad", "ad")})
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(source)
    assert await publish_committed_totals(db, "closed") == 2
    assert await complete(db, account_id="dollar", currency="USD", timezone_name="America/Los_Angeles")
    assert not await complete(db)

@pytest.mark.asyncio
async def test_empty_nonempty_empty_transition_replaces_proof_and_failed_refresh_keeps_it():
    db = Database()
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(run("first-empty"))
    assert await publish_committed_totals(db, "first-empty") == 2
    assert await complete(db)

    populated = run("populated")
    populated["summary"] = {}
    db.raw.mezan_snapchat_sync_runs_v2.insert_one(populated)
    for day in DAYS:
        for level in ("campaign", "ad_squad", "ad"):
            db.raw.mezan_snapchat_daily_total_facts_v2.insert_one({
                "user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "saudi",
                "sync_run_id": "populated", "report_date": day.isoformat(),
                "entity_type": level, "external_id": level + "-1", "currency": "SAR",
                "account_timezone": "Asia/Riyadh", "action_report_time": "conversion",
                "attribution_key": proof()["attribution_key"], "coverage": {"status": "complete"},
            })
    assert await publish_committed_totals(db, "populated") == 2
    snapshots = list(db.raw[COLLECTION].find({}, {"_id": 0}))
    assert all("empty_hierarchy_proof" not in row for row in snapshots)
    assert all(row["rows"][level] for row in snapshots for level in ("campaign", "ad_squad", "ad"))
    assert not await complete(db)

    db.raw.mezan_snapchat_sync_runs_v2.insert_one(run("last-empty"))
    assert await publish_committed_totals(db, "last-empty") == 2
    assert await complete(db)
    snapshots = list(db.raw[COLLECTION].find({}, {"_id": 0}))
    assert all(row["empty_hierarchy_proof"] == proof() for row in snapshots)
    assert all(row["rows"][level] == [] for row in snapshots for level in ("campaign", "ad_squad", "ad"))
    for status in ("running", "partial", "failed"):
        db.raw.mezan_snapchat_sync_runs_v2.insert_one(run("refresh-" + status, status))
        assert await publish_committed_totals(db, "refresh-" + status) == 0
        assert list(db.raw[COLLECTION].find({}, {"_id": 0})) == snapshots
        assert await complete(db)
