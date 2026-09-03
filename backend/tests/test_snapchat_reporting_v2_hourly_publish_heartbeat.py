from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from resource_governor import CooperativeCancellation
from snapchat_v2 import sync_pipeline as pipeline_module
from snapchat_v2.facts import (
    HOURLY_FACT_WRITE_BATCH_SIZE,
    SNAPCHAT_HOURLY_FACTS_COLLECTION,
)
from snapchat_v2.lease import heartbeat_lease
from snapchat_v2.sync_pipeline import SnapchatV2SyncPipeline
from snapchat_v2.sync_runs import heartbeat_sync_run

NOW = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)
USER_ID = "user-1"
ACCOUNT_ID = "account-1"


class HourlyFactCollection:
    def __init__(self, db: "FakeDB", *, fail_on_batch: int | None = None):
        self.db = db
        self.fail_on_batch = fail_on_batch
        self.rows: list[dict[str, Any]] = []
        self.bulk_calls: list[tuple[int, bool]] = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def bulk_write(self, operations, *, ordered):
        batch_number = len(self.bulk_calls) + 1
        self.bulk_calls.append((len(operations), ordered))
        self.db.publish_active = True
        try:
            # Hold the first bulk request until the independent heartbeat task
            # proves that it ran during publish. The event removes timer-based
            # scheduling assumptions from the regression.
            if not self.db.heartbeat_during_bulk.is_set():
                await asyncio.wait_for(
                    self.db.heartbeat_during_bulk.wait(),
                    timeout=1,
                )
            else:
                await asyncio.sleep(0)
            if self.fail_on_batch == batch_number:
                raise RuntimeError("simulated_hourly_fact_bulk_failure")

            inserted = matched = modified = 0
            for operation in operations:
                identity = deepcopy(operation._filter)
                update = deepcopy(operation._doc)
                existing = next(
                    (
                        row
                        for row in self.rows
                        if all(row.get(key) == value for key, value in identity.items())
                    ),
                    None,
                )
                if existing is None:
                    existing = deepcopy(identity)
                    existing.update(deepcopy(update.get("$setOnInsert") or {}))
                    self.rows.append(existing)
                    inserted += 1
                else:
                    matched += 1
                    modified += 1
                existing.update(deepcopy(update.get("$set") or {}))
            return SimpleNamespace(
                upserted_count=inserted,
                matched_count=matched,
                modified_count=modified,
            )
        finally:
            self.db.publish_active = False


class SyncRunCollection:
    def __init__(self, state: dict[str, Any]):
        self.state = state

    async def find_one(self, query, _projection=None):
        run = self.state.get("run")
        if run and run["sync_run_id"] == query.get("sync_run_id"):
            return deepcopy(run)
        return None

    async def update_one(self, query, update):
        run = self.state.get("run")
        if run and run["sync_run_id"] == query.get("sync_run_id"):
            run.update(deepcopy(update.get("$set") or {}))
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class ConnectionCollection:
    def __init__(self):
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def update_one(self, query, update, *, upsert=False):
        self.updates.append((deepcopy(query), deepcopy(update)))
        return SimpleNamespace(matched_count=1, modified_count=1)


class FakeDB:
    def __init__(self, state: dict[str, Any], *, fail_on_batch: int | None = None):
        self.publish_active = False
        self.heartbeat_during_bulk = asyncio.Event()
        self.facts = HourlyFactCollection(self, fail_on_batch=fail_on_batch)
        self.sync_runs = SyncRunCollection(state)
        self.connections = ConnectionCollection()

    def __getitem__(self, name):
        if name == SNAPCHAT_HOURLY_FACTS_COLLECTION:
            return self.facts
        if name == "mezan_snapchat_sync_runs_v2":
            return self.sync_runs
        if name == "mezan_snapchat_connections_v2":
            return self.connections
        raise AssertionError(f"unexpected collection: {name}")


class ConnectionManager:
    async def ensure_indexes(self):
        return None

    async def validate_ready(self, user_id, *, ad_account_id=None):
        assert user_id == USER_ID
        assert ad_account_id == ACCOUNT_ID
        return {}, {
            "ad_account_id": ACCOUNT_ID,
            "timezone": "America/Los_Angeles",
            "currency": "USD",
        }


def _hourly_fact(sync_run_id: str, number: int) -> dict[str, Any]:
    return {
        "user_id": USER_ID,
        "ad_account_id": ACCOUNT_ID,
        "campaign_id": f"campaign-{number}",
        "hour_start_utc": datetime(2026, 8, 28, 10, tzinfo=timezone.utc),
        "hour_end_utc": datetime(2026, 8, 28, 11, tzinfo=timezone.utc),
        "account_timezone": "America/Los_Angeles",
        "currency": "USD",
        "action_report_time": "conversion",
        "attribution_windows": {"swipe": "28_DAY", "view": "7_DAY"},
        "spend_native": 1.0,
        "impressions": 1,
        "sync_run_id": sync_run_id,
    }


class Client:
    provider_calls = 1

    def __init__(self, row_count: int):
        self.row_count = row_count

    async def fetch_hourly_facts(self, _account, *, sync_run_id, **_kwargs):
        rows = [_hourly_fact(sync_run_id, number) for number in range(self.row_count)]
        return {
            "rows": rows,
            "campaign_rows": rows,
            "account_rows": [],
            "coverage": {
                "status": "complete",
                "data_state": "confirmed_data",
            },
            "request_windows": [],
        }


def _install_pipeline_fakes(
    monkeypatch: pytest.MonkeyPatch,
    db: FakeDB,
    state: dict[str, Any],
) -> None:
    async def no_indexes(_db):
        return None

    async def no_recovery(_db, **_kwargs):
        return 0

    async def acquire(_db, _user_id, _account_id, owner_id, **_kwargs):
        state["lease"] = {"status": "held", "owner_id": owner_id, "stale": False}
        return True

    async def release(_db, _user_id, _account_id, owner_id, *, outcome, **_kwargs):
        assert state["lease"]["owner_id"] == owner_id
        state["lease"].update(
            {"status": "released", "stale": False, "outcome": outcome}
        )
        return True

    async def create_run(_db, run):
        state["run"] = deepcopy(run)

    async def update_stage(_db, _run_id, stage, status=None, **_kwargs):
        run = state["run"]
        assert run["status"] == "running"
        run["stage"] = stage
        run["stage_status"] = status or "running"
        run["stage_history"].append({"stage": stage, "status": status or "running"})

    async def lease_heartbeat(_db, _user_id, _account_id, owner_id, **_kwargs):
        assert state["lease"] == {
            "status": "held",
            "owner_id": owner_id,
            "stale": False,
        }
        state["lease_heartbeats"] += 1
        if db.publish_active:
            state["heartbeats_during_bulk"] += 1
            db.heartbeat_during_bulk.set()
            if state.get("fail_heartbeat_during_bulk") and not state.get(
                "heartbeat_failure_observed"
            ):
                state["heartbeat_failure_observed"] = True
                raise RuntimeError("simulated_publish_heartbeat_failure")
        await asyncio.sleep(0)
        return NOW + timedelta(minutes=15)

    async def run_heartbeat(_db, run_id, *, owner_id=None, **_kwargs):
        run = state["run"]
        state["run_heartbeats"] += 1
        await asyncio.sleep(0)
        return (
            run["sync_run_id"] == run_id
            and run["owner_id"] == owner_id
            and run["status"] == "running"
        )

    async def set_level(_db, _run_id, level, status, *, coverage=None, **_kwargs):
        state["run"][f"{level}_sync_status"] = status
        if coverage is not None:
            state["run"].setdefault("coverage", {})[level] = deepcopy(coverage)

    async def complete_run(_db, _run_id, *, summary=None, **_kwargs):
        run = state["run"]
        statuses = [
            run.get(f"{level}_sync_status")
            for level in ("financial", "campaign", "ad_squad", "ad", "identity")
        ]
        run["status"] = (
            "complete"
            if all(value in {"complete", "not_requested"} for value in statuses)
            else "partial"
        )
        run["stage"] = "completed"
        run["stage_status"] = run["status"]
        run["summary"] = deepcopy(summary or {})
        run["stage_history"].append({"stage": "completed", "status": run["status"]})

    async def fail_run(_db, _run_id, error, *, stage=None, **_kwargs):
        run = state["run"]
        run["status"] = "failed"
        run["stage"] = stage or "failed"
        run["stage_status"] = "failed"
        run["last_error"] = {"code": type(error).__name__}
        run["stage_history"].append({"stage": run["stage"], "status": "failed"})

    async def breakdown(
        self,
        _client,
        *,
        sync_run_id,
        entity_type,
        **_kwargs,
    ):
        await set_level(self.db, sync_run_id, entity_type, "complete")
        return {"rows_saved": 0}, None

    async def identities(self, _client, *, sync_run_id, **_kwargs):
        await set_level(self.db, sync_run_id, "identity", "complete")
        return {}, []

    async def projections(_db, *, report_dates, account, **_kwargs):
        report_date = report_dates[0].isoformat()
        window_start = NOW - timedelta(hours=12)
        window_end = NOW + timedelta(hours=12)
        return [
            {
                "report_date": report_date,
                "projection_timezone": account["timezone"],
                "window_start_utc": window_start,
                "window_end_utc": window_end,
            },
            {
                "report_date": report_date,
                "projection_timezone": pipeline_module.RIYADH_TIMEZONE,
                "window_start_utc": window_start,
                "window_end_utc": window_end,
            },
        ]

    async def provider_total(*_args, **_kwargs):
        return {"spend_native": 0.0}

    async def reconciliation(*_args, **_kwargs):
        return {"reconciled": True}

    monkeypatch.setattr(pipeline_module, "HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(pipeline_module, "ensure_lease_indexes", no_indexes)
    monkeypatch.setattr(pipeline_module, "ensure_sync_run_indexes", no_indexes)
    monkeypatch.setattr(pipeline_module, "recover_expired_leases", no_recovery)
    monkeypatch.setattr(pipeline_module, "recover_abandoned_sync_runs", no_recovery)
    monkeypatch.setattr(pipeline_module, "acquire_lease", acquire)
    monkeypatch.setattr(pipeline_module, "release_lease", release)
    monkeypatch.setattr(pipeline_module, "create_sync_run", create_run)
    monkeypatch.setattr(pipeline_module, "update_sync_stage", update_stage)
    monkeypatch.setattr(pipeline_module, "heartbeat_lease", lease_heartbeat)
    monkeypatch.setattr(pipeline_module, "heartbeat_sync_run", run_heartbeat)
    monkeypatch.setattr(pipeline_module, "set_level_status", set_level)
    monkeypatch.setattr(pipeline_module, "complete_sync_run", complete_run)
    monkeypatch.setattr(pipeline_module, "fail_sync_run", fail_run)
    monkeypatch.setattr(
        SnapchatV2SyncPipeline,
        "_sync_breakdown_performance",
        breakdown,
    )
    monkeypatch.setattr(SnapchatV2SyncPipeline, "_sync_identities", identities)
    monkeypatch.setattr(
        pipeline_module,
        "build_and_persist_daily_projections",
        projections,
    )
    monkeypatch.setattr(pipeline_module, "fetch_provider_total", provider_total)
    monkeypatch.setattr(pipeline_module, "reconcile_day", reconciliation)


def _state() -> dict[str, Any]:
    return {
        "lease_heartbeats": 0,
        "run_heartbeats": 0,
        "heartbeats_during_bulk": 0,
    }


@pytest.mark.asyncio
async def test_memory_rise_during_fetch_stops_before_first_authoritative_write(
    monkeypatch,
):
    state = _state()
    db = FakeDB(state)
    _install_pipeline_fakes(monkeypatch, db, state)
    checkpoints = 0
    releases = 0

    class Governor:
        async def acquire(self, *args, **kwargs):
            return object(), None
        async def release(self, token):
            nonlocal releases
            releases += 1
        def safe_checkpoint(self):
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 2:
                raise CooperativeCancellation("resource_pressure")

    async def forbidden_downstream(*args, **kwargs):
        raise AssertionError("projection/reconciliation must not start")

    monkeypatch.setattr(pipeline_module, "governor", Governor())
    monkeypatch.setattr(
        pipeline_module, "build_and_persist_daily_projections", forbidden_downstream
    )
    monkeypatch.setattr(
        SnapchatV2SyncPipeline, "_sync_breakdown_performance", forbidden_downstream
    )
    monkeypatch.setattr(pipeline_module, "reconcile_day", forbidden_downstream)
    pipeline = SnapchatV2SyncPipeline(
        db, now=lambda: NOW, connection_manager=ConnectionManager(),
        client_factory=lambda *_args: Client(10),
    )
    result = await pipeline.run(
        USER_ID, ACCOUNT_ID,
        date_from=date(2026, 8, 28), date_to=date(2026, 8, 28),
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "resource_pressure"
    assert result["retryable"] is True
    assert checkpoints == 2
    assert db.facts.rows == []
    assert db.facts.bulk_calls == []
    assert releases == 1


@pytest.mark.asyncio
async def test_2001_hourly_rows_publish_in_bounded_batches_with_live_heartbeat(
    monkeypatch,
):
    state = _state()
    db = FakeDB(state)
    _install_pipeline_fakes(monkeypatch, db, state)
    client = Client(2001)
    pipeline = SnapchatV2SyncPipeline(
        db,
        now=lambda: NOW,
        connection_manager=ConnectionManager(),
        client_factory=lambda *_args: client,
    )

    result = await pipeline.run(
        USER_ID,
        ACCOUNT_ID,
        date_from=date(2026, 8, 28),
        date_to=date(2026, 8, 28),
    )

    assert result["status"] == "complete"
    assert state["run"]["status"] == "complete"
    assert state["run"]["financial_sync_status"] == "complete"
    assert state["lease"]["status"] == "released"
    assert state["lease"]["stale"] is False
    assert state["heartbeats_during_bulk"] > 0
    assert state["lease_heartbeats"] >= len(db.facts.bulk_calls)
    assert len(db.facts.rows) == 2001
    assert all(row["sync_run_id"] == result["sync_run_id"] for row in db.facts.rows)
    assert len(db.facts.bulk_calls) == 11
    assert [size for size, _ordered in db.facts.bulk_calls] == [
        *([HOURLY_FACT_WRITE_BATCH_SIZE] * 10),
        1,
    ]
    assert all(ordered is True for _size, ordered in db.facts.bulk_calls)
    assert result["summary"]["rows_saved"] == 2001


@pytest.mark.asyncio
async def test_financial_fast_lane_skips_campaign_ad_and_identity_work(monkeypatch):
    state = _state()
    db = FakeDB(state)
    _install_pipeline_fakes(monkeypatch, db, state)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("non-financial Snapchat work must not run")

    monkeypatch.setattr(SnapchatV2SyncPipeline, "_sync_identities", forbidden)
    monkeypatch.setattr(
        SnapchatV2SyncPipeline,
        "_sync_breakdown_performance",
        forbidden,
    )
    pipeline = SnapchatV2SyncPipeline(
        db,
        now=lambda: NOW,
        connection_manager=ConnectionManager(),
        client_factory=lambda *_args: Client(0),
    )

    result = await pipeline.run(
        USER_ID,
        ACCOUNT_ID,
        date_from=date(2026, 8, 28),
        date_to=date(2026, 8, 28),
        run_type="current_day_fast_lane",
        financial_only=True,
    )

    assert result["status"] == "complete"
    assert result["summary"]["financial_only"] is True
    assert result["summary"]["ui_switched"] is True
    assert state["run"]["financial_sync_status"] == "complete"
    assert state["run"]["campaign_sync_status"] == "not_requested"
    assert state["run"]["ad_squad_sync_status"] == "not_requested"
    assert state["run"]["ad_sync_status"] == "not_requested"
    assert state["run"]["identity_sync_status"] == "not_requested"


@pytest.mark.asyncio
async def test_parallel_refresh_is_rejected_by_the_distributed_account_lease(monkeypatch):
    state = _state()
    db = FakeDB(state)
    _install_pipeline_fakes(monkeypatch, db, state)
    releases = 0

    async def unavailable(*_args, **_kwargs):
        return False

    class Governor:
        async def acquire(self, *_args, **_kwargs):
            return object(), None

        async def release(self, _token):
            nonlocal releases
            releases += 1

        def safe_checkpoint(self):
            return None

    monkeypatch.setattr(pipeline_module, "acquire_lease", unavailable)
    monkeypatch.setattr(pipeline_module, "governor", Governor())
    pipeline = SnapchatV2SyncPipeline(
        db,
        now=lambda: NOW,
        connection_manager=ConnectionManager(),
        client_factory=lambda *_args: Client(0),
    )

    result = await pipeline.run(
        USER_ID,
        ACCOUNT_ID,
        date_from=date(2026, 8, 28),
        date_to=date(2026, 8, 28),
    )

    assert result == {
        "status": "skipped",
        "reason": "lease_unavailable",
        "ad_account_id": ACCOUNT_ID,
    }
    assert releases == 1
    assert "run" not in state
    assert db.facts.rows == []


@pytest.mark.asyncio
async def test_partial_bulk_publish_never_marks_financial_or_run_complete(monkeypatch):
    state = _state()
    db = FakeDB(state, fail_on_batch=2)
    _install_pipeline_fakes(monkeypatch, db, state)
    client = Client(401)
    pipeline = SnapchatV2SyncPipeline(
        db,
        now=lambda: NOW,
        connection_manager=ConnectionManager(),
        client_factory=lambda *_args: client,
    )

    result = await pipeline.run(
        USER_ID,
        ACCOUNT_ID,
        date_from=date(2026, 8, 28),
        date_to=date(2026, 8, 28),
    )

    assert result["status"] == "failed"
    assert state["run"]["status"] == "failed"
    assert state["run"]["financial_sync_status"] == "failed"
    assert state["run"]["stage_status"] == "failed"
    assert not any(
        entry["stage"] == "completed" for entry in state["run"]["stage_history"]
    )
    assert state["lease"]["status"] == "released"
    assert state["lease"]["stale"] is False
    assert len(db.facts.rows) == HOURLY_FACT_WRITE_BATCH_SIZE
    assert all(row["sync_run_id"] == result["sync_run_id"] for row in db.facts.rows)


@pytest.mark.asyncio
async def test_background_heartbeat_failure_is_observed_during_publish(monkeypatch):
    state = _state()
    state["fail_heartbeat_during_bulk"] = True
    db = FakeDB(state)
    _install_pipeline_fakes(monkeypatch, db, state)
    client = Client(401)
    pipeline = SnapchatV2SyncPipeline(
        db,
        now=lambda: NOW,
        connection_manager=ConnectionManager(),
        client_factory=lambda *_args: client,
    )

    result = await pipeline.run(
        USER_ID,
        ACCOUNT_ID,
        date_from=date(2026, 8, 28),
        date_to=date(2026, 8, 28),
    )

    assert state["heartbeat_failure_observed"] is True
    assert result["status"] == "failed"
    assert state["run"]["financial_sync_status"] == "failed"
    assert state["run"]["status"] == "failed"
    assert state["lease"]["status"] == "released"
    assert state["lease"]["stale"] is False
    assert len(db.facts.bulk_calls) == 1
    assert len(db.facts.rows) == HOURLY_FACT_WRITE_BATCH_SIZE


@pytest.mark.asyncio
async def test_same_millisecond_heartbeat_keeps_matching_lease_and_run_owned():
    class UnmodifiedOwnedCollection:
        async def update_one(self, query, _update):
            self.query = deepcopy(query)
            return SimpleNamespace(matched_count=1, modified_count=0)

    class OwnedDB:
        def __init__(self):
            self.collection = UnmodifiedOwnedCollection()

        def __getitem__(self, _name):
            return self.collection

    db = OwnedDB()

    expires_at = await heartbeat_lease(
        db,
        USER_ID,
        ACCOUNT_ID,
        "owner-1",
        now=lambda: NOW,
    )
    run_alive = await heartbeat_sync_run(
        db,
        "run-1",
        owner_id="owner-1",
        now=lambda: NOW,
    )

    assert expires_at == NOW + timedelta(minutes=15)
    assert run_alive is True
