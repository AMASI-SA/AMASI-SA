"""Read-only route contract, provenance failures, isolation and bounded reads."""
import asyncio
import os
import uuid
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

from integrations_control_center import snapchat_readiness_provenance as diagnostic


START, END = date(2026, 10, 22), date(2026, 10, 24)
DATES = ["2026-10-22", "2026-10-23", "2026-10-24"]
SCOPE = {"user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "account-sar"}
FILTER = {
    "account_timezone": "Asia/Riyadh", "action_report_time": "conversion",
    "attribution_key": diagnostic.build_attribution_key("conversion", {"swipe": "28_DAY", "view": "7_DAY"}),
}


def nested(row, path):
    for part in path.split("."):
        if not isinstance(row, dict):
            return None
        row = row.get(part)
    return row


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, option) for option in expected):
                return False
            continue
        actual = nested(row, key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$gte" and (actual is None or actual < value):
                    return False
                if operator == "$lte" and (actual is None or actual > value):
                    return False
        elif actual != expected:
            return False
    return True


class Cursor:
    def __init__(self, database, name, rows, event):
        self.database, self.name, self.rows, self.event = database, name, rows, event

    def sort(self, fields):
        for field, direction in reversed(fields):
            self.rows.sort(key=lambda row: nested(row, field), reverse=direction < 0)
        return self

    def limit(self, limit):
        self.event["limit"] = limit
        self.rows = self.rows[:limit]
        return self

    def max_time_ms(self, timeout):
        self.event["max_time_ms"] = timeout
        return self

    async def to_list(self, *, length):
        if self.database.hang == self.name:
            await asyncio.Event().wait()
        if self.database.fail == self.name:
            raise OperationFailure("synthetic private connection details")
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, database, name):
        self.database, self.name = database, name

    def find(self, query, projection):
        event = {"kind": "find", "collection": self.name, "query": deepcopy(query), "projection": deepcopy(projection)}
        self.database.reads.append(event)
        return Cursor(self.database, self.name,
                      [deepcopy(row) for row in self.database.rows[self.name] if matches(row, query)], event)

    def aggregate(self, pipeline, *, maxTimeMS):
        event = {"kind": "aggregate", "collection": self.name, "pipeline": deepcopy(pipeline), "max_time_ms": maxTimeMS}
        self.database.reads.append(event)
        rows = deepcopy(self.database.rows[self.name])
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if matches(row, stage["$match"])]
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    rows.sort(key=lambda row: nested(row, field), reverse=direction < 0)
            elif "$limit" in stage:
                rows = rows[:stage["$limit"]]
            elif "$group" in stage:
                counts = Counter(row["entity_type"] for row in rows)
                rows = [{"_id": key, "count": count} for key, count in counts.items()]
            elif "$project" in stage:
                # Emulate the bounded storage seam; separate assertions below
                # inspect the actual generated Mongo projection contract.
                for row in rows:
                    row["rows"] = {level: [
                        {**{field: fact.get(field) for field in diagnostic.FACT_FIELDS},
                         "coverage": {"status": (fact.get("coverage") or {}).get("status")}}
                        for fact in (row.get("rows") or {}).get(level, [])[:diagnostic.MAX_FACT_ROWS + 1]
                    ] for level in diagnostic.LEVELS}
        return Cursor(self.database, self.name, rows, event)


class Database:
    def __init__(self):
        self.rows, self.reads = defaultdict(list), []
        self.hang = self.fail = None
        self.allowed = {
            diagnostic.INTEGRATION_ACCOUNTS, diagnostic.SNAPCHAT_ACCOUNTS_COLLECTION,
            diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION, diagnostic.COMMITTED_COLLECTION,
            diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION, diagnostic.SNAPCHAT_ENTITY_FACTS_COLLECTION,
        }

    def __getitem__(self, name):
        assert name in self.allowed, f"Unexpected collection read: {name}"
        return Collection(self, name)


def fact(level, day, run_id="producing", **changes):
    return {**SCOPE, **FILTER, "entity_type": level, "external_id": f"{level}-entity",
            "currency": "SAR", "report_date": day, "sync_run_id": run_id,
            "coverage": {"status": "complete"}, **changes}


def run(run_id="producing", **changes):
    return {**SCOPE, "sync_run_id": run_id, "status": "complete", "financial_sync_status": "complete",
            "identity_sync_status": "complete", **{f"{level}_sync_status": "complete" for level in diagnostic.LEVELS},
            "started_at": datetime(2026, 10, 25, tzinfo=timezone.utc),
            "request_window": {"date_from": DATES[0], "date_to": DATES[-1], "action_report_time": "conversion"},
            **changes}


@pytest.fixture
def db():
    database = Database()
    account = {**SCOPE, "external_account_id": "account-sar", "currency": "SAR", "timezone": "Asia/Riyadh",
               "connection_status": "connected", "connection_provenance": "api_connection", "mezan_selected": True}
    database.rows[diagnostic.INTEGRATION_ACCOUNTS] = [account]
    database.rows[diagnostic.SNAPCHAT_ACCOUNTS_COLLECTION] = [{**account, "active": True}]
    database.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION] = [run()]
    database.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] = [fact(level, day) for level in diagnostic.LEVELS for day in DATES]
    return database


@pytest_asyncio.fixture
async def isolated_mongo():
    uri = os.environ.get("SNAPCHAT_PROVENANCE_TEST_MONGO_URL")
    if not uri:
        if os.environ.get("REQUIRE_SNAPCHAT_PROVENANCE_MONGO") == "1":
            pytest.fail("dedicated isolated provenance Mongo test URI is required")
        pytest.skip("dedicated isolated provenance Mongo test URI not configured")
    if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("provenance tests require loopback-only disposable Mongo")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    database = client["snap_provenance_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


async def diagnose(database, **changes):
    return await diagnostic.diagnose_snapchat_readiness_provenance(
        database, "owner", "account-sar", date_from=changes.get("date_from", START), date_to=changes.get("date_to", END),
    )


@pytest.mark.asyncio
async def test_complete_requires_all_dates_and_exact_producing_run_proof(db):
    result = await diagnose(db)
    assert result["status"] == "complete"
    assert result["writes_performed"] is False and result["provider_calls"] == 0
    assert result["decision_eligibility"]["eligible"] is False
    for level in diagnostic.LEVELS:
        evidence = result["levels"][level]
        assert evidence["period_complete"] is True
        assert evidence["fact_count"] == 3 and evidence["entity_count"] == 1
        assert evidence["producing_run_evidence"] == [{"sync_run_id": "producing", "fact_dates": DATES, "proven": True, "blockers": []}]


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [
    ({"status": "partial"}, "producing_run_not_complete"),
    ({"financial_sync_status": "partial"}, "producing_financial_not_complete"),
    ({"ad_sync_status": "partial"}, "producing_level_not_complete"),
    ({"request_window": {"date_from": DATES[1], "date_to": DATES[-1], "action_report_time": "conversion"}}, "producing_run_window_does_not_cover_facts"),
    ({"request_window": {"date_from": DATES[0], "date_to": DATES[-1], "action_report_time": "impression"}}, "producing_run_action_report_time_mismatch"),
])
async def test_reader_blockers_are_reported_without_latest_run_substitution(db, change, reason):
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION] = [run(**change), run("newer-unreferenced")]
    evidence = (await diagnose(db))["levels"]["ad"]
    assert evidence["period_complete"] is False and reason in evidence["blockers"]


@pytest.mark.asyncio
async def test_missing_children_and_complete_no_data_run_do_not_become_zero_or_complete(db):
    db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] = [row for row in db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] if row["entity_type"] == "campaign"]
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION][0]["coverage"] = {
        "ad": {"status": "complete", "data_state": "confirmed_no_data", "rows_received": 0},
    }
    result = await diagnose(db)
    assert result["levels"]["campaign"]["period_complete"] is True
    for level in ("ad_squad", "ad"):
        assert result["levels"][level]["period_complete"] is False
        assert result["levels"][level]["empty_level_proof"] == "unavailable_current_fact_contract"
        assert result["levels"][level]["missing_dates"] == DATES


@pytest.mark.asyncio
async def test_committed_rows_win_over_in_progress_mutable_refresh(db):
    committed = []
    for day in DATES:
        committed.append({**SCOPE, **FILTER, "report_date": day, "sync_run_id": "producing",
                          "rows": {level: [fact(level, day)] for level in diagnostic.LEVELS}})
    db.rows[diagnostic.COMMITTED_COLLECTION] = committed
    db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] = [fact(level, day, "refresh", coverage={"status": "partial"})
                                                          for level in diagnostic.LEVELS for day in DATES]
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION].append(run("refresh", status="running"))
    result = await diagnose(db)
    assert result["status"] == "complete"
    assert [row["sync_run_id"] for row in result["producing_runs"]] == ["producing"]
    assert all(day["source"] == "committed" for day in result["levels"]["ad"]["days"])


@pytest.mark.asyncio
async def test_reader_filters_are_diagnosed_independently(db):
    db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] = [
        fact("ad", DATES[0], account_timezone="UTC"),
        fact("ad", DATES[1], action_report_time="impression"),
        fact("ad", DATES[2], attribution_key="other-window"),
    ]
    evidence = (await diagnose(db))["levels"]["ad"]
    assert evidence["raw_window_observed_rows"] == 3
    assert evidence["raw_reader_matching_observed_rows"] == 0
    assert evidence["raw_filter_mismatch_counts"] == {"account_timezone": 1, "action_report_time": 1, "attribution_key": 1}
    assert evidence["period_complete"] is False


@pytest.mark.asyncio
async def test_foreign_account_and_owner_runs_cannot_prove_coverage(db):
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION] = [run(user_id="another-owner"), run(ad_account_id="another-account")]
    db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] += [fact("ad", day, user_id="another-owner") for day in DATES]
    result = await diagnose(db)
    assert result["producing_runs"] == []
    assert result["levels"]["ad"]["fact_count"] == 3
    assert "producing_run_missing_in_account_scope" in result["levels"]["ad"]["blockers"]
    for read in db.reads:
        query = read.get("query") or read["pipeline"][0]["$match"]
        assert query["user_id"] == "owner" and query["provider"] == "snapchat_ads"
        if read["collection"] != diagnostic.INTEGRATION_ACCOUNTS:
            assert query["ad_account_id"] == "account-sar"


@pytest.mark.asyncio
async def test_malformed_committed_foreign_fact_is_unavailable_and_not_exposed(db):
    db.rows[diagnostic.COMMITTED_COLLECTION] = [{**SCOPE, **FILTER, "report_date": DATES[0], "rows": {
        "ad": [fact("ad", DATES[0], "foreign-private-run", ad_account_id="foreign-account")],
    }}]
    result = await diagnose(db)
    evidence = result["levels"]["ad"]
    assert evidence["period_complete"] is None and evidence["fact_count"] is None
    assert "committed_fact_identity_invalid" in evidence["blockers"]
    assert "foreign-private-run" not in str(result) and "foreign-account" not in str(result)


@pytest.mark.asyncio
async def test_truncated_reads_never_return_exact_or_complete_counts(db, monkeypatch):
    monkeypatch.setattr(diagnostic, "MAX_FACT_ROWS", 2)
    result = await diagnose(db)
    assert result["status"] == "incomplete"
    for evidence in result["levels"].values():
        assert evidence["period_complete"] is None and evidence["fact_count"] is None
        assert evidence["raw_window_count_exact"] is False
        assert evidence["missing_dates"] is None
    read = next(read for read in db.reads if read["collection"] == diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION)
    assert read["limit"] == 3


@pytest.mark.asyncio
async def test_missing_fact_coverage_and_producing_id_remain_unproven(db):
    row = db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION][-1]
    row["coverage"], row["sync_run_id"] = {}, ""
    evidence = (await diagnose(db))["levels"]["ad"]
    assert evidence["period_complete"] is False
    assert "source_fact_coverage_not_complete" in evidence["blockers"]
    assert "source_fact_producing_run_missing" in evidence["blockers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("mezan_selected", False), ("connection_status", "needs_reauth"), ("connection_provenance", "manual"), ("user_id", "another-owner")])
async def test_selected_connected_owner_account_is_required_before_fact_reads(db, field, value):
    db.rows[diagnostic.INTEGRATION_ACCOUNTS][0][field] = value
    with pytest.raises(HTTPException) as caught:
        await diagnose(db)
    assert caught.value.status_code == 404
    assert len(db.reads) == 1


@pytest.mark.asyncio
async def test_missing_reporting_registry_is_explicitly_unavailable(db):
    db.rows[diagnostic.SNAPCHAT_ACCOUNTS_COLLECTION] = []
    result = await diagnose(db)
    assert result["status"] == "unavailable"
    assert result["blockers"] == ["reporting_account_registry_missing_or_ambiguous"]
    assert len(db.reads) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("start,end", [(END, START), (date(2026, 10, 1), END)])
async def test_period_bounds_reject_before_database_access(db, start, end):
    with pytest.raises(HTTPException) as caught:
        await diagnose(db, date_from=start, date_to=end)
    assert caught.value.detail["code"] == "snapchat_readiness_period_invalid"
    assert db.reads == []


@pytest.mark.asyncio
async def test_every_query_has_server_deadline_and_committed_arrays_are_sliced(db):
    await diagnose(db)
    assert all(read["max_time_ms"] == diagnostic.QUERY_TIMEOUT_MS for read in db.reads)
    for read in db.reads:
        if read["kind"] == "find":
            assert read["projection"]["_id"] == 0 and read["limit"] <= diagnostic.MAX_FACT_ROWS + 1
        else:
            assert not any("$merge" in stage or "$out" in stage for stage in read["pipeline"])
    committed = next(read for read in db.reads if read["collection"] == diagnostic.COMMITTED_COLLECTION)
    for level in diagnostic.LEVELS:
        mapping = committed["pipeline"][-1]["$project"]["rows"][level]["$map"]
        assert mapping["input"]["$slice"][1] == diagnostic.MAX_FACT_ROWS + 1
        assert set(mapping["in"]) == set(diagnostic.FACT_FIELDS) | {"coverage"}
        assert "raw" not in mapping["in"] and "spend_native" not in mapping["in"]


@pytest.mark.asyncio
async def test_overall_deadline_and_database_failures_are_sanitized(db, monkeypatch):
    db.hang = diagnostic.INTEGRATION_ACCOUNTS
    monkeypatch.setattr(diagnostic, "REQUEST_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HTTPException) as caught:
        await diagnose(db)
    assert caught.value.status_code == 504
    db.hang, db.fail = None, diagnostic.INTEGRATION_ACCOUNTS
    with pytest.raises(HTTPException) as caught:
        await diagnose(db)
    assert caught.value.status_code == 503
    assert "private" not in str(caught.value.detail)


def test_route_rejects_non_owner_before_database_access(db):
    router = APIRouter(prefix="/integrations-v2")
    async def current_user():
        return {"id": "staff", "role": "staff"}
    def require_owner(user):
        raise HTTPException(status_code=403)
    diagnostic.attach_snapchat_readiness_provenance_routes(router, db, current_user, require_owner)
    app = FastAPI(); app.include_router(router)
    response = TestClient(app).get("/integrations-v2/snapchat/readiness-provenance/account-sar?date_from=2026-10-22&date_to=2026-10-24")
    assert response.status_code == 403 and db.reads == []


@pytest.mark.asyncio
async def test_actual_mongo_committed_projection_and_account_isolation(isolated_mongo, db):
    # Dedicated explicit loopback-only disposable Mongo; mandatory CI fails
    # instead of skipping when this provenance-specific URI is absent.
    for collection, rows in db.rows.items():
        if rows:
            await isolated_mongo[collection].insert_many(deepcopy(rows))
    await isolated_mongo[diagnostic.COMMITTED_COLLECTION].insert_many([
        {**SCOPE, **FILTER, "report_date": day, "sync_run_id": "producing", "rows": {
            level: [{**fact(level, day), "raw": {"private": "never-export"}, "spend_native": 999}]
            for level in diagnostic.LEVELS
        }} for day in DATES
    ])
    await isolated_mongo[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION].delete_many(SCOPE)
    await isolated_mongo[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one(run("foreign-run", user_id="foreign-owner"))
    result = await diagnose(isolated_mongo)
    assert result["status"] == "complete"
    assert all(row["fact_count"] == 3 for row in result["levels"].values())
    assert all(day["source"] == "committed" for day in result["levels"]["ad"]["days"])
    assert "never-export" not in str(result) and "spend_native" not in str(result)
    assert "foreign-run" not in str(result)


@pytest.mark.asyncio
async def test_same_engine_isolates_usd_and_sar_with_overlapping_entity_ids(isolated_mongo):
    accounts = [
        {"account_id": "account-usd", "currency": "USD", "timezone": "America/Los_Angeles",
         "entity_ids": ["shared-entity"], "run_ids": ["usd-producing"]},
        {"account_id": "account-sar", "currency": "SAR", "timezone": "Asia/Riyadh",
         "entity_ids": ["shared-entity", "sar-extra-entity"],
         "run_ids": ["sar-producing-first", "sar-producing-last"]},
    ]
    for account in accounts:
        account_id = account["account_id"]
        scope = {**SCOPE, "ad_account_id": account_id}
        native = {**scope, "external_account_id": account_id, "currency": account["currency"],
                  "timezone": account["timezone"], "connection_status": "connected",
                  "connection_provenance": "api_connection", "mezan_selected": True}
        await isolated_mongo[diagnostic.INTEGRATION_ACCOUNTS].insert_one(native)
        await isolated_mongo[diagnostic.SNAPCHAT_ACCOUNTS_COLLECTION].insert_one({**native, "active": True})
        for index, run_id in enumerate(account["run_ids"]):
            covered_dates = DATES if len(account["run_ids"]) == 1 else DATES[:2] if index == 0 else DATES[2:]
            await isolated_mongo[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one(run(
                run_id, ad_account_id=account_id,
                request_window={"date_from": covered_dates[0], "date_to": covered_dates[-1],
                                "action_report_time": "conversion"},
            ))
        for day in DATES:
            run_id = account["run_ids"][-1] if day == DATES[-1] else account["run_ids"][0]
            await isolated_mongo[diagnostic.COMMITTED_COLLECTION].insert_one({
                **scope, **FILTER, "account_timezone": account["timezone"],
                "report_date": day, "sync_run_id": run_id,
                "rows": {level: [fact(
                    level, day, run_id, ad_account_id=account_id,
                    account_timezone=account["timezone"], currency=account["currency"],
                    external_id=f"{level}-{entity_id}",
                ) for entity_id in account["entity_ids"]] for level in diagnostic.LEVELS},
            })
        await isolated_mongo[diagnostic.SNAPCHAT_ENTITY_FACTS_COLLECTION].insert_many([
            {**scope, "entity_type": level, "external_id": f"{level}-{entity_id}", "active": True}
            for level in diagnostic.LEVELS for entity_id in account["entity_ids"]
        ])

    # Both currencies pass through the same public diagnostic function. The
    # different row/run cardinalities make a missing account fence observable.
    results = {}
    for account in accounts:
        result = await diagnostic.diagnose_snapchat_readiness_provenance(
            isolated_mongo, "owner", account["account_id"], date_from=START, date_to=END,
        )
        results[account["currency"]] = result
        assert result["status"] == "complete" and result["account_id"] == account["account_id"]
        assert result["account"] == {
            "currency": account["currency"], "timezone": account["timezone"],
            "selected_metadata_matches_reporting": True,
        }
        assert result["reader_filter"]["account_timezone"] == account["timezone"]
        assert {row["sync_run_id"] for row in result["producing_runs"]} == set(account["run_ids"])
        assert {row["sync_run_id"] for row in result["recent_overlapping_runs"]} == set(account["run_ids"])
        for level in diagnostic.LEVELS:
            evidence = result["levels"][level]
            assert evidence["period_complete"] is True and evidence["blockers"] == []
            assert evidence["fact_count"] == len(DATES) * len(account["entity_ids"])
            assert evidence["entity_count"] == len(account["entity_ids"])
            assert evidence["current_active_identity_count"] == len(account["entity_ids"])
            assert {row["sync_run_id"] for row in evidence["producing_run_evidence"]} == set(account["run_ids"])
            assert all(row["proven"] for row in evidence["producing_run_evidence"])
            assert all(row["source"] == "committed" and row["row_count"] == len(account["entity_ids"])
                       for row in evidence["days"])
    assert results["USD"]["levels"]["ad"]["fact_count"] == 3
    assert results["SAR"]["levels"]["ad"]["fact_count"] == 6
    assert len(results["USD"]["producing_runs"]) == 1
    assert len(results["SAR"]["producing_runs"]) == 2


def empty_snapshot_fixture(db):
    from snapchat_v2.empty_hierarchy import empty_hierarchy_proof
    empty = {"status": "complete", "data_state": "confirmed_no_data",
             "expected_requests": 1, "completed_requests": 1, "rows_received": 0}
    proof = empty_hierarchy_proof(
        {"ad_account_id": "account-sar", "currency": "SAR", "timezone": "Asia/Riyadh"},
        [date.fromisoformat(day) for day in DATES], "conversion", {"coverage": empty},
        {level: {"coverage": empty} for level in diagnostic.LEVELS})
    db.rows[diagnostic.SNAPCHAT_TOTAL_FACTS_COLLECTION] = []
    db.rows[diagnostic.COMMITTED_COLLECTION] = [
        {**SCOPE, **FILTER, "report_date": day, "sync_run_id": "empty-producing",
         "rows": {level: [] for level in diagnostic.LEVELS}, "empty_hierarchy_proof": deepcopy(proof),
         # The lightweight database does not interpret Mongo expressions.
         # Real-Mongo tests below calculate these from actual stored bytes.
         "empty_rows_explicit": True, "empty_proof_shape_valid": True}
        for day in DATES]
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION] = [run(
        "empty-producing", summary={"empty_hierarchy_proof": deepcopy(proof)}, empty_proof_shape_valid=True)]
    return proof


@pytest.mark.asyncio
async def test_provider_proven_empty_is_complete_without_fake_facts_or_recent_run(db):
    empty_snapshot_fixture(db)
    db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION].append(run("unrelated-newer"))
    result = await diagnose(db)
    assert result["status"] == "complete" and result["provider_calls"] == 0
    assert result["writes_performed"] is False and result["decision_eligibility"]["eligible"] is False
    assert [row["sync_run_id"] for row in result["producing_runs"]] == ["empty-producing"]
    assert "campaign_response" not in str(result) and "catalog_responses" not in str(result)
    for evidence in result["levels"].values():
        assert evidence["period_complete"] is True and evidence["empty_hierarchy_proven"] is True
        assert evidence["fact_count"] == evidence["observed_fact_count"] == evidence["entity_count"] == 0
        assert evidence["missing_dates"] == [] and evidence["dates_without_observed_facts"] == DATES
        assert evidence["empty_level_proof"] == "provider_confirmed_empty_hierarchy"
        assert evidence["producing_run_evidence"] == [{"sync_run_id": "empty-producing", "fact_dates": [],
            "empty_proof_dates": DATES, "proven": True, "blockers": []}]
        assert all(day["source"] == "committed_empty" and day["row_count"] == 0 for day in evidence["days"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["run_missing", "run_foreign", "run_partial", "identity_partial", "run_proof_mismatch",
    "currency", "timezone", "account", "date_missing", "snapshot_nonempty", "snapshot_rows_not_explicit",
    "snapshot_shape_invalid", "run_shape_invalid", "catalog_nonempty", "catalog_403", "truncated"])
async def test_empty_provenance_requires_same_exact_committed_proof_as_reader(db, monkeypatch, mutation):
    empty_snapshot_fixture(db)
    snapshot = db.rows[diagnostic.COMMITTED_COLLECTION][0]
    source = db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION][0]
    if mutation == "run_missing": db.rows[diagnostic.SNAPCHAT_SYNC_RUNS_COLLECTION] = [run("other")]
    if mutation == "run_foreign": source["ad_account_id"] = "other"
    if mutation == "run_partial": source["ad_sync_status"] = "partial"
    if mutation == "identity_partial": source["identity_sync_status"] = "partial"
    if mutation == "run_proof_mismatch": source["summary"]["empty_hierarchy_proof"]["report_dates"] = DATES[:1]
    if mutation == "currency": snapshot["empty_hierarchy_proof"]["currency"] = "USD"
    if mutation == "timezone": snapshot["empty_hierarchy_proof"]["account_timezone"] = "UTC"
    if mutation == "account": snapshot["empty_hierarchy_proof"]["ad_account_id"] = "other"
    if mutation == "date_missing": db.rows[diagnostic.COMMITTED_COLLECTION].pop()
    if mutation == "snapshot_nonempty": snapshot["rows"]["campaign"] = [fact("campaign", DATES[0], "empty-producing")]
    if mutation == "snapshot_rows_not_explicit": snapshot["empty_rows_explicit"] = False
    if mutation == "snapshot_shape_invalid": snapshot["empty_proof_shape_valid"] = False
    if mutation == "run_shape_invalid": source["empty_proof_shape_valid"] = False
    if mutation == "catalog_nonempty": snapshot["empty_hierarchy_proof"]["catalog_responses"]["ad"]["rows_received"] = 1
    if mutation == "catalog_403": snapshot["empty_hierarchy_proof"]["catalog_responses"]["ad"]["status"] = "incomplete"
    if mutation == "truncated": monkeypatch.setattr(diagnostic, "MAX_PRODUCING_RUNS", 0)
    result = await diagnose(db)
    assert result["status"] == "incomplete"
    assert all(evidence["empty_hierarchy_proven"] is False for evidence in result["levels"].values())
    assert not any(evidence["period_complete"] is True for evidence in result["levels"].values())


@pytest.mark.asyncio
async def test_actual_mongo_empty_projection_matches_shared_reader_and_hides_proof(isolated_mongo, db):
    empty_snapshot_fixture(db)
    for collection, rows in db.rows.items():
        if rows:
            clean_rows = deepcopy(rows)
            # Markers must be computed by Mongo, never trusted as input data.
            for row in clean_rows:
                row["empty_proof_shape_valid"] = False
                row["empty_rows_explicit"] = False
            await isolated_mongo[collection].insert_many(clean_rows)
    result = await diagnose(isolated_mongo)
    assert result["status"] == "complete"
    from snapchat_v2.empty_hierarchy import empty_period_complete
    assert await empty_period_complete(isolated_mongo, user_id="owner", account_id="account-sar",
        currency="SAR", timezone_name="Asia/Riyadh", date_from=START, date_to=END, action="conversion")
    assert all(row["empty_hierarchy_proven"] is True and row["fact_count"] == 0 for row in result["levels"].values())
    assert "campaign_response" not in str(result)
    # Extra source keys must not disappear under an allowlist and make
    # differently stored snapshot/run proofs compare equal.
    await isolated_mongo[diagnostic.COMMITTED_COLLECTION].update_one(
        {"report_date": DATES[0]}, {"$set": {"empty_hierarchy_proof.unknown_key": "private-proof-field"}})
    invalid = await diagnose(isolated_mongo)
    assert invalid["status"] == "incomplete" and "private-proof-field" not in str(invalid)
    await isolated_mongo[diagnostic.COMMITTED_COLLECTION].update_one(
        {"report_date": DATES[0]}, {"$unset": {"empty_hierarchy_proof.unknown_key": "", "rows.ad": ""}})
    invalid = await diagnose(isolated_mongo)
    assert invalid["status"] == "incomplete"
