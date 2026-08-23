"""Regression tests for the Snapchat fail-closed publish contract.

Covers:
    * distributed lease atomicity across concurrent replica ticks
    * lease expiry / takeover
    * ``published_by_run_id`` binding in the dashboard reader
    * a running-elsewhere run must not invalidate a prior committed fact
    * a failed unrelated run must not invalidate a prior committed fact
    * a fact whose publish marker points to a failed run stays unknown
    * atomic publish semantics ("no read while running/failed on that fact")
    * 10-minute Snapchat cadence gate
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

import dashboard_snapchat_spend as reader_module
from integrations_control_center import snapchat_distributed_lease as lease_module
from integrations_control_center.snapchat_distributed_lease import (
    acquire_snapchat_lease,
    bind_run_to_lease,
    release_snapchat_lease,
    renew_snapchat_lease,
)
from integrations_control_center.snapchat_native_data_common import SNAPCHAT_PROVIDER_ID


SOURCE = reader_module.snapchat_hourly.ACCOUNT_REFRESH_SOURCE_MODE
DAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------
# FakeDB shared with test_dashboard_snapchat_spend_issue_727 – kept
# lightweight so lease tests can also exercise ``find_one_and_update``.
# --------------------------------------------------------------------

def _path(document, dotted):
    value = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document, query):
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, branch) for branch in condition):
                return False
            continue
        value, exists = _path(document, key)
        if not isinstance(condition, dict):
            if condition is None:
                if exists and value is not None:
                    return False
            elif not exists or value != condition:
                return False
            continue
        for operator, expected in condition.items():
            if operator == "$in":
                if not exists or value not in expected:
                    return False
            elif operator == "$gte":
                if not exists or value < expected:
                    return False
            elif operator == "$lte":
                if not exists or value > expected:
                    return False
            elif operator == "$lt":
                if not exists or not (value < expected):
                    return False
            elif operator == "$gt":
                if not exists or not (value > expected):
                    return False
            elif operator == "$ne":
                if exists and value == expected:
                    return False
            elif operator == "$exists":
                if exists is not bool(expected):
                    return False
            else:
                raise AssertionError(f"unsupported operator: {operator}")
    return True


def _apply_update(row: dict[str, Any], update: dict[str, Any]) -> None:
    if "$set" in update:
        for key, value in update["$set"].items():
            row[key] = value
    if "$setOnInsert" in update:
        for key, value in update["$setOnInsert"].items():
            row.setdefault(key, value)


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, order in reversed(key):
                self.rows.sort(
                    key=lambda row, f=field: _path(row, f)[0] or "",
                    reverse=order == -1,
                )
        else:
            field, order = key, direction
            self.rows.sort(
                key=lambda row: _path(row, field)[0] or "",
                reverse=order == -1,
            )
        return self

    def limit(self, length):
        self.rows = self.rows[:length]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self._lock = asyncio.Lock()

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None, *, sort=None):
        candidates = [row for row in self.rows if _matches(row, query)]
        if sort:
            for field, order in reversed(sort):
                candidates.sort(
                    key=lambda row, f=field: _path(row, f)[0] or "",
                    reverse=order == -1,
                )
        return deepcopy(candidates[0]) if candidates else None

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=False,
        projection=None,
    ):
        async with self._lock:
            for row in self.rows:
                if _matches(row, query):
                    _apply_update(row, update)
                    return deepcopy(row)
            if not upsert:
                return None
            new_row: dict[str, Any] = {}
            _apply_update(new_row, update)
            self.rows.append(new_row)
            return deepcopy(new_row)

    async def update_one(self, query, update, *, upsert=False):
        async with self._lock:
            for row in self.rows:
                if _matches(row, query):
                    _apply_update(row, update)
                    return type("Result", (), {"matched_count": 1, "modified_count": 1})()
            if upsert:
                new_row: dict[str, Any] = {}
                _apply_update(new_row, update)
                self.rows.append(new_row)
                return type("Result", (), {"matched_count": 0, "modified_count": 0, "upserted_id": True})()
            return type("Result", (), {"matched_count": 0, "modified_count": 0})()

    async def insert_one(self, document):
        async with self._lock:
            self.rows.append(deepcopy(document))
            return type("Result", (), {"inserted_id": True})()


class FakeDB:
    def __init__(self, collections=None):
        self.collections = {
            name: FakeCollection(rows) for name, rows in (collections or {}).items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]

    def get_collection(self, name):
        return self[name]


# --------------------------------------------------------------------
# Lease tests
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lease_acquires_when_none_held():
    db = FakeDB()
    handle = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert handle is not None
    assert handle.owner_token
    assert handle.lease_until > NOW


@pytest.mark.asyncio
async def test_lease_denies_second_concurrent_acquisition():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    second = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert second is None


@pytest.mark.asyncio
async def test_lease_takeover_after_expiry():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    future = NOW + lease_module.DEFAULT_LEASE_TTL + timedelta(minutes=1)
    second = await acquire_snapchat_lease(db, user_id="owner-1", now=future)
    assert second is not None
    assert second.owner_token != first.owner_token
    assert second.took_over_from == first.owner_token


@pytest.mark.asyncio
async def test_lease_release_and_reacquire():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    assert await release_snapchat_lease(db, first, final_status="complete", now=NOW) is True
    second = await acquire_snapchat_lease(
        db, user_id="owner-1", now=NOW + timedelta(seconds=1)
    )
    assert second is not None
    assert second.owner_token != first.owner_token


@pytest.mark.asyncio
async def test_lease_renew_extends_only_matching_owner():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    assert await renew_snapchat_lease(db, first, now=NOW + timedelta(minutes=5)) is True
    fake_handle = lease_module.SnapchatLeaseHandle(
        user_id="owner-1",
        provider=SNAPCHAT_PROVIDER_ID,
        owner_token="not-the-owner",
        worker_id="ghost",
        acquired_at=NOW,
        lease_until=NOW + lease_module.DEFAULT_LEASE_TTL,
    )
    assert await renew_snapchat_lease(db, fake_handle, now=NOW) is False


@pytest.mark.asyncio
async def test_lease_bind_run_id_is_atomic():
    db = FakeDB()
    handle = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert handle is not None
    assert await bind_run_to_lease(db, handle, run_id="run-42") is True
    row = await db[lease_module.LEASE_COLLECTION].find_one(
        {"user_id": "owner-1", "provider": SNAPCHAT_PROVIDER_ID}
    )
    assert row["run_id"] == "run-42"


@pytest.mark.asyncio
async def test_lease_concurrent_gather_only_one_winner():
    """Two replicas racing must not both acquire the lease."""

    db = FakeDB()
    results = await asyncio.gather(
        acquire_snapchat_lease(db, user_id="owner-1", now=NOW),
        acquire_snapchat_lease(db, user_id="owner-1", now=NOW),
        acquire_snapchat_lease(db, user_id="owner-1", now=NOW),
    )
    winners = [handle for handle in results if handle is not None]
    assert len(winners) == 1


@pytest.mark.asyncio
async def test_lease_reclaims_expired_bson_datetime():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    row = db[lease_module.LEASE_COLLECTION].rows[0]
    row["lease_until"] = NOW - timedelta(seconds=1)

    second = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)

    assert second is not None
    assert second.owner_token != first.owner_token
    assert second.took_over_from == first.owner_token


@pytest.mark.asyncio
async def test_lease_reclaims_after_absolute_max_age():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    row = db[lease_module.LEASE_COLLECTION].rows[0]
    row["acquired_at"] = lease_module._iso(
        NOW - lease_module.MAX_LEASE_TTL - timedelta(seconds=1)
    )
    row["lease_until"] = lease_module._iso(NOW + timedelta(hours=2))

    second = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)

    assert second is not None
    assert second.owner_token != first.owner_token
    assert await release_snapchat_lease(db, first, final_status="failed", now=NOW) is False


# --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lease_reclaims_legacy_far_future_without_acquired_at():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    row = db[lease_module.LEASE_COLLECTION].rows[0]
    row.pop("acquired_at", None)
    row["lease_until"] = lease_module._iso(
        NOW + lease_module.MAX_LEASE_TTL + timedelta(seconds=1)
    )
    second = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert second is not None
    assert second.owner_token != first.owner_token


@pytest.mark.asyncio
async def test_lease_preserves_legacy_current_window_without_acquired_at():
    db = FakeDB()
    first = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert first is not None
    row = db[lease_module.LEASE_COLLECTION].rows[0]
    row.pop("acquired_at", None)
    row["lease_until"] = lease_module._iso(NOW + lease_module.DEFAULT_LEASE_TTL)
    second = await acquire_snapchat_lease(db, user_id="owner-1", now=NOW)
    assert second is None


# Reader publish-marker binding tests
# --------------------------------------------------------------------

def _coverage(state="confirmed_data"):
    return {
        "status": "complete",
        "data_state": state,
        "expected_requests": 1,
        "completed_requests": 1,
    }


def _run(
    *,
    run_id="run-good",
    state="confirmed_data",
    status="complete",
    started="2026-08-21T08:00:00+00:00",
    finished="2026-08-21T08:05:00+00:00",
    account_id="snap-1",
):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "run_id": run_id,
        "status": status,
        "source_mode": SOURCE,
        "started_at": started,
        "finished_at": finished,
        "summary": {
            "date_from": DAY.isoformat(),
            "date_to": DAY.isoformat(),
            "coverage": _coverage(state),
            "accounts_attempted": 1,
            "accounts_complete": 1,
            "provider_calls": 2,
            "errors_count": 0,
            "account_provider_calls": [
                {"ad_account_id": account_id, "provider_calls": 2}
            ],
        },
    }


def _account(account_id="snap-1"):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "connection_status": "connected",
        "mezan_selected": True,
        "ad_account_id": account_id,
        "external_account_id": account_id,
        "mezan_integration_account_id": f"identity-{account_id}",
        "currency": "SAR",
        "source_mode": SOURCE,
        "data_quality": "complete",
        "coverage": _coverage(),
        "last_sync_at": "2026-08-21T08:04:00+00:00",
        "connection_provenance": "api_connection",
    }


def _integration():
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "connection_status": "connected",
        "source_mode": SOURCE,
        "data_quality": "complete",
        "coverage": _coverage(),
        "last_sync_at": "2026-08-21T08:04:00+00:00",
    }


def _fact(
    *,
    spend=10,
    currency="SAR",
    updated="2026-08-21T08:02:00+00:00",
    published_by_run_id=None,
):
    row = {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "ad_account_id": "snap-1",
        "entity_type": "ad_account",
        "external_id": "snap-1",
        "date": DAY.isoformat(),
        "attribution_model": reader_module.ATTRIBUTION_MODEL,
        "date_timezone": "Asia/Riyadh",
        "business_timezone": "Asia/Riyadh",
        "stored_granularity": "RIYADH_DAY",
        "provider_granularity": "HOUR",
        "provider_breakdown": "campaign",
        "currency": currency,
        "spend_native": spend,
        "spend_sar": 999999,
        "provider_window_start": "2026-08-19T21:00:00+00:00",
        "provider_window_end": "2026-08-20T21:00:00+00:00",
        "source_mode": SOURCE,
        "updated_at": updated,
    }
    if published_by_run_id:
        row["published_by_run_id"] = published_by_run_id
        row["published_at"] = updated
    return row


def _make_db(runs, facts):
    return FakeDB(
        {
            "mezan_integrations_v2": [_integration()],
            "mezan_integration_accounts_v2": [_account()],
            "mezan_integration_sync_runs_v2": runs,
            "mezan_snapchat_performance_daily_v2": facts,
            "mezan_ad_account_cost_settings_v2": [],
        }
    )


async def _load(db):
    return await reader_module.load_snapchat_dashboard_spend(
        db, "owner-1", start=DAY, end=DAY, now=NOW
    )


@pytest.mark.asyncio
async def test_reader_uses_published_by_run_id_across_newer_running_run():
    """A newer running run must not invalidate a previously-published fact."""

    good_run = _run(run_id="run-A", status="complete")
    newer_running = _run(
        run_id="run-B",
        status="running",
        started="2026-08-21T09:00:00+00:00",
        finished=None,
        state="unknown_incomplete",
    )
    facts = [_fact(published_by_run_id="run-A")]
    db = _make_db([good_run, newer_running], facts)
    result = await _load(db)
    assert result["quality"]["data_state"] == "confirmed_data"
    assert result["total_sar"] == 10.0


@pytest.mark.asyncio
async def test_reader_uses_published_by_run_id_across_newer_failed_run():
    """A newer failed unrelated run must not invalidate a previously-published fact."""

    good_run = _run(run_id="run-A", status="complete")
    newer_failed = _run(
        run_id="run-B",
        status="failed",
        started="2026-08-21T09:00:00+00:00",
        finished="2026-08-21T09:01:00+00:00",
        state="unknown_incomplete",
    )
    facts = [_fact(published_by_run_id="run-A")]
    db = _make_db([good_run, newer_failed], facts)
    result = await _load(db)
    assert result["quality"]["data_state"] == "confirmed_data"
    assert result["total_sar"] == 10.0


@pytest.mark.asyncio
async def test_reader_rejects_fact_pinned_to_failed_run():
    """A fact pinned to a failed run must stay unknown_incomplete."""

    failed = _run(run_id="run-X", status="failed", state="unknown_incomplete")
    facts = [_fact(published_by_run_id="run-X")]
    db = _make_db([failed], facts)
    result = await _load(db)
    assert result["quality"]["data_state"] == "unknown_incomplete"
    assert result["total_sar"] is None


@pytest.mark.asyncio
async def test_reader_rejects_fact_pinned_to_mismatched_run():
    """A fact pinned to run-A but only run-B exists → fail-closed."""

    other = _run(run_id="run-B", status="complete")
    facts = [_fact(published_by_run_id="run-A")]
    db = _make_db([other], facts)
    result = await _load(db)
    # Fact pin refers to run-A which is missing → fail-closed unknown.
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_reader_still_fail_closed_when_no_publish_marker_and_newer_failed():
    """Legacy contract: no publish marker + newer failed run → unknown."""

    good = _run(run_id="run-A", status="complete")
    newer_failed = _run(
        run_id="run-B",
        status="failed",
        started="2026-08-21T09:00:00+00:00",
        finished="2026-08-21T09:01:00+00:00",
        state="unknown_incomplete",
    )
    facts = [_fact()]  # no published_by_run_id
    db = _make_db([good, newer_failed], facts)
    result = await _load(db)
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_reader_confirmed_data_with_publish_marker_no_other_runs():
    """Baseline: single complete run + fact with publish marker → confirmed_data."""

    good = _run(run_id="run-A", status="complete")
    facts = [_fact(published_by_run_id="run-A")]
    db = _make_db([good], facts)
    result = await _load(db)
    assert result["quality"]["data_state"] == "confirmed_data"
    assert result["total_sar"] == 10.0


# --------------------------------------------------------------------
# Cadence gate test
# --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapchat_cadence_gate_defers_when_last_run_too_recent():
    from integrations_control_center import ads_auto_sync_scheduler as scheduler

    recent_started = (NOW - timedelta(minutes=3)).isoformat().replace("+00:00", "+00:00")
    db = FakeDB(
        {
            scheduler.RUNS_COLLECTION: [
                {
                    "user_id": "owner-1",
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "run_type": scheduler.SNAP_RUN_TYPE,
                    "status": "complete",
                    "started_at": recent_started,
                    "created_at": recent_started,
                }
            ]
        }
    )
    ready = await scheduler._snapchat_cadence_ready(
        db, user_id="owner-1", now=NOW
    )
    assert ready is False


@pytest.mark.asyncio
async def test_snapchat_cadence_gate_allows_when_last_run_older_than_10m():
    from integrations_control_center import ads_auto_sync_scheduler as scheduler

    older = (NOW - timedelta(minutes=15)).isoformat().replace("+00:00", "+00:00")
    db = FakeDB(
        {
            scheduler.RUNS_COLLECTION: [
                {
                    "user_id": "owner-1",
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "run_type": scheduler.SNAP_RUN_TYPE,
                    "status": "complete",
                    "started_at": older,
                    "created_at": older,
                }
            ]
        }
    )
    ready = await scheduler._snapchat_cadence_ready(
        db, user_id="owner-1", now=NOW
    )
    assert ready is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "queued"])
async def test_snapchat_cadence_gate_ignores_inflight_runs(status):
    from integrations_control_center import ads_auto_sync_scheduler as scheduler

    recent = NOW - timedelta(minutes=1)
    db = FakeDB(
        {
            scheduler.RUNS_COLLECTION: [
                {
                    "user_id": "owner-1",
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "run_type": scheduler.SNAP_RUN_TYPE,
                    "status": status,
                    "started_at": recent,
                    "created_at": recent,
                }
            ]
        }
    )

    ready = await scheduler._snapchat_cadence_ready(
        db, user_id="owner-1", now=NOW
    )

    assert ready is True


@pytest.mark.asyncio
async def test_snapchat_cadence_gate_allows_when_no_prior_run():
    from integrations_control_center import ads_auto_sync_scheduler as scheduler

    db = FakeDB()
    ready = await scheduler._snapchat_cadence_ready(
        db, user_id="owner-1", now=NOW
    )
    assert ready is True
