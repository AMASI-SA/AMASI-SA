"""Durable freshness alarms and diagnostic-only persistence, using isolated fakes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
import pytest

import operational_balance_freshness as health
from operational_balance_store import STATES, digest


NOW = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
OWNER = "owner-a"


def at(minutes=0):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def header(as_of=None):
    return {"owner_id": OWNER, "status": "active", "revision": 8,
            "engine": {"as_of": at(30) if as_of is None else as_of}}


def sources():
    return [{"key": key, "label": key, "state": "current" if key == "payroll" else "inactive",
             "last_success_at": at(30) if key == "payroll" else None,
             "data_through": None, "expected_through": None, "message": "حالة المصدر"}
            for key in ("payroll", "recurring", "salla", "snapchat", "meta", "tiktok", "google_ads")]


def compose(*, state=None, record=None, lease=None, rows=None, now=NOW):
    return health.compose_freshness(OWNER, OWNER, state or header(), record or {}, lease or {},
                                    sources() if rows is None else rows, now=now)


def test_saved_calculation_clock_not_manual_revision_controls_alert():
    old = header(at(2880))
    old["revision"] = 10000  # A newer manual payment is not a recalculation.
    result = compose(state=old)
    assert result["freshness"]["state"] == "stale"
    assert result["as_of"] == at(2880)
    assert result["generated_at"] == NOW.isoformat()
    assert result["session_scope"] == digest([OWNER, OWNER])


@pytest.mark.parametrize("age,expected", [(119, "current"), (120, "stale"), (121, "stale")])
def test_two_hour_boundary(age, expected):
    assert compose(state=header(at(age)))["freshness"]["state"] == expected


def test_riyadh_midnight_expires_previous_day_before_two_hours():
    now = datetime(2026, 11, 23, 21, 1, tzinfo=timezone.utc)
    result = compose(state=header("2026-11-23T20:59:00+00:00"), now=now)
    assert result["freshness"]["state"] == "stale"


@pytest.mark.parametrize("value", ["", "invalid", "2026-11-23T13:00:00", at(-1), [], True])
def test_unverified_calculation_time_never_claims_current(value):
    assert compose(state=header(value))["freshness"]["state"] == "unknown"


def test_failed_attempt_persists_with_source_reason_and_without_exception_text():
    record = {"last_attempt": {"started_at": at(10), "finished_at": at(9),
        "outcome": "failed", "error_code": "operational_source_setup_incomplete"},
        "last_failure": {"code": "operational_source_setup_incomplete",
            "cause": "operational_source_read_timeout", "source_collection": "mezan_employees_v2",
            "message": "secret://credentials"}}
    result = compose(record=record)
    assert result["freshness"]["state"] == "failed"
    assert any("الموظفين" in issue["message"] for issue in result["issues"])
    assert "secret" not in str(result)


def test_running_attempt_needs_live_lease_and_expired_attempt_stays_unknown():
    record = {"last_attempt": {"started_at": at(1), "finished_at": None,
                               "outcome": "running", "error_code": None}}
    # Default BSON decoding produces naive UTC dates for this stored lease.
    lease = {"expires_at": (NOW + timedelta(seconds=60)).replace(tzinfo=None)}
    assert compose(record=record, lease=lease)["freshness"]["state"] == "updating"
    result = compose(record=record, lease={"expires_at": at(1)})
    assert result["freshness"]["state"] == "unknown"
    assert result["last_attempt"]["outcome"] == "unknown"


def test_later_successful_snapshot_supersedes_old_failure():
    record = {"last_attempt": {"started_at": at(60), "finished_at": at(59),
                               "outcome": "failed", "error_code": "operational_refresh_timeout"}}
    result = compose(record=record)
    assert result["freshness"]["state"] == "current"
    assert result["last_attempt"]["outcome"] == "success"


def test_lost_completion_metadata_cannot_leave_running_after_successful_cas():
    record = {"last_attempt": {"started_at": at(60), "finished_at": None,
                               "outcome": "running", "error_code": None}}
    result = compose(record=record)
    assert result["as_of"] == at(30)  # A newer financial snapshot really exists.
    assert result["last_attempt"]["outcome"] == "unknown"
    assert result["freshness"]["state"] == "unknown"
    assert any(row["code"] == "operational_refresh_outcome_unknown" for row in result["issues"])


def test_active_attempt_remains_updating_after_cas_until_lease_is_released():
    record = {"last_attempt": {"started_at": at(60), "finished_at": None,
                               "outcome": "running", "error_code": None}}
    result = compose(record=record, lease={"expires_at": NOW + timedelta(seconds=60)})
    assert result["last_attempt"]["outcome"] == "running"
    assert result["freshness"]["state"] == "updating"


def test_source_warning_survives_successful_aggregate_and_does_not_affect_other_sources():
    record = {"last_success_at": at(30), "source_warnings": [
        {"code": "salary_contract_missing", "key": "payroll", "message": "secret"}]}
    result = compose(record=record)
    assert result["freshness"]["state"] == "unknown"
    assert result["sources"][0]["state"] == "unknown"
    assert all(row["state"] == "inactive" for row in result["sources"][1:])
    assert "secret" not in str(result)


def test_unconfigured_sources_do_not_raise_false_failure():
    result = compose()
    assert result["freshness"]["state"] == "current"
    assert result["issues"] == []


def test_missing_source_rows_cannot_silently_claim_all_sources_healthy():
    assert compose(rows=[])["freshness"]["state"] == "unknown"


@pytest.mark.parametrize("record", [
    {"last_attempt": {"started_at": at(10), "finished_at": at(-1), "outcome": "success"}},
    {"last_attempt": {"started_at": at(10), "finished_at": None, "outcome": []}},
    {"last_attempt": []},
])
def test_corrupt_attempt_metadata_is_not_healthy(record):
    assert compose(record=record)["freshness"]["state"] == "unknown"


def test_unsafe_error_code_type_is_sanitized():
    result = compose(record={"last_attempt": {"started_at": at(10), "finished_at": at(9),
                    "outcome": "failed", "error_code": ["secret"]}})
    assert result["last_attempt"]["error_code"] is None
    assert "secret" not in str(result)


def test_collected_warnings_include_deferred_native_sources_and_known_issues_only():
    state = {"engine": {"issues": [{"code": "salary_source_changed", "amount": 999},
                                    {"code": "unknown-secret", "id": "private"}]}}
    native = {"courier": {"status": "completed", "outcomes": [{"status": "blocked", "order_id": "private"}]},
              "bank": {"status": "deferred", "reason": "secret"}}
    warnings = health.source_warnings(state, native)
    assert {row["key"] for row in warnings} == {"payroll", "shipping", "bank"}
    assert not any(term in str(warnings) for term in ("999", "private", "secret"))


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, part) for part in expected):
                return False
        elif isinstance(expected, dict):
            if "$exists" in expected and (key in row) != expected["$exists"]:
                return False
            if "$lte" in expected and (key not in row or row[key] > expected["$lte"]):
                return False
        elif row.get(key) != expected:
            return False
    return True


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection, **options):
        assert 0 < options["max_time_ms"] <= 1500
        assert projection and projection.get("_id") == 0
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection)))
        if self.name in self.db.broken:
            raise RuntimeError("secret://database")
        row = next((row for row in self.db.data.get(self.name, {}).values() if matches(row, query)), None)
        if row is None:
            return None
        result = {}
        for path, included in projection.items():
            if not included:
                continue
            original = row
            keys = path.split(".")
            for key in keys[:-1]:
                original = original.get(key, {}) if type(original) is dict else {}
            if type(original) is dict and keys[-1] in original:
                target = result
                for key in keys[:-1]:
                    target = target.setdefault(key, {})
                value = deepcopy(original[keys[-1]])
                target[keys[-1]] = value[:included['$slice']] if type(included) is dict else value
        return result

    async def update_one(self, query, update, *, upsert=False):
        self.db.writes.append((self.name, deepcopy(query), deepcopy(update), upsert))
        if self.name in self.db.broken:
            raise RuntimeError("secret://database")
        rows = self.db.data.setdefault(self.name, {})
        row = next((row for row in rows.values() if matches(row, query)), None)
        if row is None and upsert:
            if query["_id"] in rows:
                raise DuplicateKeyError("existing")
            row = rows[query["_id"]] = {"_id": query["_id"]}
        if row is not None:
            row.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=int(row is not None))


class DB:
    def __init__(self):
        self.data = {STATES: {OWNER: {"_id": OWNER, **header(), "snapshot": {"amount": 12345}}}}
        self.reads, self.writes, self.broken = [], [], set()

    def __getitem__(self, name):
        return Collection(self, name)


def test_attempt_writes_are_diagnostic_only_and_fenced_against_late_completion():
    async def scenario():
        db = DB()
        financial = deepcopy(db.data[STATES])
        first = await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=2))
        second = await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=1))
        await health.finish_attempt(db, OWNER, first, result={"status": "refreshed", "as_of": at(2), "revision": 8}, now=NOW)
        assert db.data[health.HEALTH][OWNER]["last_attempt"]["outcome"] == "running"
        await health.finish_attempt(db, OWNER, second, error=HTTPException(408, detail={"code": "operational_refresh_timeout"}), now=NOW)
        assert db.data[health.HEALTH][OWNER]["last_attempt"]["outcome"] == "failed"
        assert db.data[STATES] == financial
        assert {name for name, *_ in db.writes} == {health.HEALTH}
        assert all(not upsert for _, query, _, upsert in db.writes if "attempt_token" in query)
    asyncio.run(scenario())


def test_failed_refresh_persists_bounded_trace_and_reader_sanitizes_it():
    async def scenario():
        db = DB()
        attempt = await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=3))
        detail = {"code": "operational_refresh_timeout",
                  "stage_timings": [{"stage":"source_orders","duration_ms":81000,
                                     "outcome":"cancelled","customer_id":"secret-customer"}],
                  "primary_orders_reads": [],
                  "raw_error":"secret-file-path"}
        await health.finish_attempt(
            db, OWNER, attempt, error=HTTPException(408, detail=detail), now=NOW)
        saved = db.data[health.HEALTH][OWNER]
        assert saved["last_failure"]["code"] == "operational_refresh_timeout"
        assert saved["last_failure_trace"]["stage_timings"] == [
            {"stage":"source_orders","duration_ms":81000,"outcome":"cancelled"}]
        assert "secret" not in str(saved)
        output = compose(record=saved)
        assert output["last_attempt"]["diagnostics"]["stage_timings"] == [
            {"stage":"source_orders","duration_ms":81000,"outcome":"cancelled"}]
        assert "secret" not in str(output)
        assert {name for name, *_ in db.writes} == {health.HEALTH}
    asyncio.run(scenario())


def test_success_clears_failed_refresh_trace():
    async def scenario():
        db = DB()
        first = await health.begin_attempt(db, OWNER, now=NOW-timedelta(minutes=3))
        await health.finish_attempt(
            db, OWNER, first, error=HTTPException(408, detail={
                "code":"operational_refresh_timeout", "stage_timings":[{
                    "stage":"source_primary_reads","duration_ms":85000,"outcome":"failed"}]
            }), now=NOW-timedelta(minutes=2))
        second = await health.begin_attempt(db, OWNER, now=NOW-timedelta(minutes=1))
        await health.finish_attempt(
            db, OWNER, second,
            result={"status":"refreshed","revision":9,"as_of":at()},
            now=NOW)
        saved = db.data[health.HEALTH][OWNER]
        assert saved["last_failure_trace"] is None
        assert saved["last_attempt"]["outcome"] == "success"
        output = compose(record=saved)
        assert "diagnostics" not in output["last_attempt"]
    asyncio.run(scenario())


def test_late_older_start_cannot_overwrite_new_attempt():
    async def scenario():
        db = DB()
        newer = await health.begin_attempt(db, OWNER, now=NOW)
        assert await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=1)) is None
        assert db.data[health.HEALTH][OWNER]["attempt_token"] == newer["token"]
    asyncio.run(scenario())


@pytest.mark.parametrize("receipt", [
    {"status": "warning", "as_of": at(), "revision": 8},
    {"status": "refreshed", "as_of": at(-1), "revision": 8},
    {"status": "refreshed", "as_of": at(), "revision": True},
    {"status": "refreshed", "as_of": "invalid", "revision": 8},
])
def test_nonfinal_receipt_never_records_success(receipt):
    async def scenario():
        db = DB()
        attempt = await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=1))
        await health.finish_attempt(db, OWNER, attempt, result=receipt, now=NOW)
        assert db.data[health.HEALTH][OWNER]["last_attempt"]["outcome"] == "unknown"
        assert "last_success_at" not in db.data[health.HEALTH][OWNER]
    asyncio.run(scenario())


def test_cancellation_is_unknown_and_new_verified_success_clears_old_failure():
    async def scenario():
        db = DB()
        attempt = await health.begin_attempt(db, OWNER, now=NOW - timedelta(minutes=2))
        await health.finish_attempt(db, OWNER, attempt, error=asyncio.CancelledError(), now=NOW - timedelta(minutes=1))
        assert db.data[health.HEALTH][OWNER]["last_attempt"]["outcome"] == "unknown"
        attempt = await health.begin_attempt(db, OWNER, now=NOW - timedelta(seconds=30))
        await health.finish_attempt(db, OWNER, attempt, result={"status": "refreshed", "as_of": at(), "revision": 9}, now=NOW)
        assert db.data[health.HEALTH][OWNER]["last_attempt"]["outcome"] == "success"
        assert db.data[health.HEALTH][OWNER]["last_failure"] is None
    asyncio.run(scenario())


def test_diagnostic_failure_does_not_raise_or_write_finance():
    async def scenario():
        db = DB()
        db.broken.add(health.HEALTH)
        assert await health.begin_attempt(db, OWNER, now=NOW) is None
        await health.finish_attempt(db, OWNER, {"token": "a", "started_at": at()}, error=RuntimeError("secret"), now=NOW)
        assert {name for name, *_ in db.writes} == {health.HEALTH}
    asyncio.run(scenario())


def test_reader_projects_metadata_only_and_never_mutates(monkeypatch):
    async def scenario():
        import operational_source_freshness
        db = DB()
        before = deepcopy(db.data)
        async def read_sources(database, owner, *, as_of, now):
            assert database is db and owner == OWNER and as_of == at(30) and now == NOW
            return sources()
        monkeypatch.setattr(operational_source_freshness, "read_source_freshness", read_sources)
        result = await health.read_freshness(db, OWNER, OWNER, now=NOW)
        assert result["freshness"]["state"] == "current"
        assert result['entity_coverage']['state'] == 'unknown'
        assert db.data == before and db.writes == []
        assert "12345" not in str(result)
        assert all(not any(field in projection for field in ("snapshot", "movements", "openings")) for _, _, projection in db.reads)
    asyncio.run(scenario())


def test_saved_entity_day_gap_survives_a_successful_recent_clock():
    from operational_entity_freshness import build_coverage, FIELD
    state = header()
    state.update(started_at='2026-11-20T00:00:00+03:00', revision=7)
    catalog = {'schema_version': 1, 'owner_id': OWNER, 'complete': True, 'total_entities': 1,
        'rows': [{'entity_type': 'ad_account', 'entity_id': 'snap-sar', 'currency': 'SAR',
                  'timezone': 'Asia/Riyadh', 'label': 'سناب ريال', 'source_available': True}]}
    state['engine'][FIELD] = build_coverage(state, {'automatic_source_entities': catalog}, at(30))
    state['revision'] = 8
    result = compose(state=state)
    assert result['freshness']['state'] == 'stale'
    row = result['entity_coverage']['rows'][0]
    assert row['label'] == 'سناب ريال' and row['missing_days'] == ['2026-11-20', '2026-11-21', '2026-11-22']


def test_entity_metadata_is_bound_and_projected_with_an_explicit_row_cap(monkeypatch):
    async def scenario():
        import operational_source_freshness
        from operational_entity_freshness import FIELD, MAX_ENTITIES
        db = DB()
        db.data[STATES][OWNER]['engine'][FIELD] = {'schema_version': 1, 'owner_id': 'foreign',
            'as_of': at(30), 'calculation_revision': 8, 'complete': True, 'total_entities': 0, 'rows': [],
            'untrusted_private_payload': 'never-return-this'}
        async def read_sources(*args, **kwargs):
            return sources()
        monkeypatch.setattr(operational_source_freshness, 'read_source_freshness', read_sources)
        result = await health.read_freshness(db, OWNER, OWNER, now=NOW)
        assert result['freshness']['state'] == 'unknown' and result['entity_coverage']['rows'] == []
        assert 'never-return-this' not in str(result) and 'foreign' not in str(result)
        projections = [projection for name, _, projection in db.reads if name == STATES]
        assert len(projections) == 2
        assert all(projection['engine.'+FIELD+'.rows'] == {'$slice': MAX_ENTITIES+1} for projection in projections)
        assert all('engine' not in projection and 'engine.obligations' not in projection for projection in projections)
    asyncio.run(scenario())


def test_source_read_exception_preserves_saved_time_and_warns(monkeypatch):
    async def scenario():
        import operational_source_freshness
        async def broken(*args, **kwargs):
            raise RuntimeError("secret://provider")
        monkeypatch.setattr(operational_source_freshness, "read_source_freshness", broken)
        result = await health.read_freshness(DB(), OWNER, OWNER, now=NOW)
        assert result["freshness"]["state"] == "unknown"
        assert result["as_of"] == at(30)
        assert "secret" not in str(result)
    asyncio.run(scenario())


def test_concurrent_snapshot_changes_retry_once_then_warn(monkeypatch):
    async def scenario():
        import operational_source_freshness
        db, calls = DB(), []
        async def changed(*args, **kwargs):
            calls.append(True)
            db.data[STATES][OWNER]["revision"] += 1
            return sources()
        monkeypatch.setattr(operational_source_freshness, "read_source_freshness", changed)
        result = await health.read_freshness(db, OWNER, OWNER, now=NOW)
        assert len(calls) == 2 and db.writes == []
        assert result["freshness"]["state"] == "unknown"
    asyncio.run(scenario())


def test_metadata_failure_cancels_and_joins_sibling_reads(monkeypatch):
    async def scenario():
        original = Collection.find_one
        entered, ended = [], []
        ready = asyncio.Event()
        async def read(self, query, projection, **options):
            if self.name == health.HEALTH:
                await ready.wait()
                raise ValueError("invalid metadata")
            entered.append(self.name)
            if len(entered) == 4:
                ready.set()
            try:
                await asyncio.sleep(1)
                return await original(self, query, projection, **options)
            finally:
                ended.append(self.name)
        monkeypatch.setattr(Collection, "find_one", read)
        with pytest.raises(ValueError):
            await health.read_freshness(DB(), OWNER, OWNER, now=NOW)
        assert len(entered) == 4 and sorted(ended) == sorted(entered)
    asyncio.run(scenario())
