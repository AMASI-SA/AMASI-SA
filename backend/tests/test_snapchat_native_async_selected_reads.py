from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import snapchat_native_async_routes as async_routes
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
)
from integrations_control_center.snapchat_native_selected_reads import (
    selected_snapchat_performance_summary,
)


class FakeResult:
    matched_count = 1
    modified_count = 1


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$nin" and actual in value:
                    return False
                if operator == "$gte" and (
                    actual is None or actual < value
                ):
                    return False
                if operator == "$lte" and (
                    actual is None or actual > value
                ):
                    return False
        elif actual != expected:
            return False
    return True


def _set_dotted(target, key, value):
    parts = key.split(".")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = deepcopy(value)


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    def sort(self, key_or_list, direction=None):
        specs = (
            key_or_list
            if isinstance(key_or_list, list)
            else [(key_or_list, direction)]
        )
        for key, order in reversed(specs):
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=(order or 1) < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self._iterator))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db

    @property
    def rows(self):
        return self.db.rows.setdefault(self.name, [])

    def find(self, query, projection=None):
        return FakeCursor(
            row for row in self.rows if _matches(row, query)
        )

    async def find_one(
        self,
        query,
        projection=None,
        sort=None,
    ):
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            rows = FakeCursor(rows).sort(sort).rows
        return deepcopy(rows[0]) if rows else None

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        return object()

    async def update_one(self, query, update, upsert=False):
        target = next(
            (row for row in self.rows if _matches(row, query)),
            None,
        )
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self.rows.append(target)
        if target is not None:
            for key, value in (
                update.get("$setOnInsert") or {}
            ).items():
                if key not in target:
                    _set_dotted(target, key, value)
            for key, value in (update.get("$set") or {}).items():
                _set_dotted(target, key, value)
        return FakeResult()


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return self[name]


def _account(account_id, *, selected, currency):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "mezan_integration_account_id": f"mezan-{account_id}",
        "external_account_id": account_id,
        "ad_account_id": account_id,
        "display_name": f"Snap {account_id}",
        "currency": currency,
        "timezone": (
            "America/Los_Angeles"
            if currency == "USD"
            else "Asia/Riyadh"
        ),
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "mezan_selected": selected,
    }


def _db():
    return FakeDB(
        {
            "mezan_integration_accounts_v2": [
                _account("usd-main", selected=True, currency="USD"),
                _account("sar-second", selected=True, currency="SAR"),
                _account("old-account", selected=False, currency="USD"),
            ],
            "mezan_integration_sync_runs_v2": [],
            SNAPCHAT_PERFORMANCE_COLLECTION: [],
        }
    )


@pytest.mark.asyncio
async def test_selected_summary_excludes_unselected_historical_rows():
    db = _db()
    db.rows[SNAPCHAT_PERFORMANCE_COLLECTION] = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "usd-main",
            "entity_type": "ad_account",
            "date": "2026-07-30",
            "currency": "USD",
            "spend_native": 10,
            "spend_sar": 37.5,
            "purchase_value_native": 40,
            "purchase_value_sar": 150,
        },
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "sar-second",
            "entity_type": "ad_account",
            "date": "2026-07-30",
            "currency": "SAR",
            "spend_native": 20,
            "spend_sar": 20,
            "purchase_value_native": 80,
            "purchase_value_sar": 80,
        },
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "old-account",
            "entity_type": "ad_account",
            "date": "2026-07-30",
            "currency": "USD",
            "spend_native": 999,
            "spend_sar": 3746.25,
            "purchase_value_native": 999,
            "purchase_value_sar": 3746.25,
        },
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "usd-main",
            "entity_type": "campaign",
            "date": "2026-07-30",
            "currency": "USD",
            "spend_native": 10,
            "spend_sar": 37.5,
        },
    ]

    result = await selected_snapchat_performance_summary(
        db,
        "owner-1",
        from_date="2026-07-30",
        to_date="2026-07-30",
        now=lambda: datetime(
            2026, 7, 30, 20, 0, tzinfo=timezone.utc
        ),
    )

    assert result["selected_account_ids"] == [
        "sar-second",
        "usd-main",
    ]
    assert result["rows_included"] == 2
    assert result["unselected_rows_excluded"] == 1
    assert result["spend_sar"] == 57.5
    assert result["purchase_value_sar"] == 230.0
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_async_job_returns_queued_then_records_child_result(
    monkeypatch,
):
    db = _db()
    fixed_now = lambda: datetime(
        2026, 7, 30, 18, 0, tzinfo=timezone.utc
    )
    monkeypatch.setattr(
        async_routes,
        "snapchat_native_sync_enabled",
        lambda: True,
    )

    accepted = await async_routes.create_snapchat_native_sync_job(
        db,
        "owner-1",
        SnapchatNativeSyncInput(days=2),
        now=fixed_now,
    )

    assert accepted["status"] == "queued"
    assert accepted["selected_accounts"] == 2
    assert accepted["accounts_attempted"] == 0

    async def fake_execute(db_value, user_id, payload, *, now):
        assert db_value is db
        assert user_id == "owner-1"
        assert payload.days == 2
        return {
            "run_id": "child-run-1",
            "provider": "snapchat_ads",
            "status": "complete",
            "accounts_attempted": 2,
            "accounts_complete": 2,
            "rows_saved": 15604,
            "errors_count": 0,
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    monkeypatch.setattr(
        async_routes,
        "execute_snapchat_native_sync",
        fake_execute,
    )
    await async_routes.execute_snapchat_native_sync_job(
        db,
        "owner-1",
        accepted["run_id"],
        SnapchatNativeSyncInput(days=2).model_dump(),
        now=fixed_now,
    )
    final = await async_routes.get_snapchat_native_sync_job(
        db,
        "owner-1",
        accepted["run_id"],
        now=fixed_now,
    )

    assert final["status"] == "complete"
    assert final["accounts_attempted"] == 2
    assert final["accounts_complete"] == 2
    assert final["rows_saved"] == 15604
    assert final["child_run_id"] == "child-run-1"
    assert final["error"] is None


@pytest.mark.asyncio
async def test_async_job_rejects_a_second_active_sync(monkeypatch):
    db = _db()
    db.rows["mezan_integration_sync_runs_v2"].append(
        {
            "run_id": "already-running",
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "run_type": async_routes.ASYNC_SYNC_RUN_TYPE,
            "status": "running",
            "started_at": "2026-07-30T17:00:00+00:00",
            "lock_expires_at": "2026-07-30T22:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        async_routes,
        "snapchat_native_sync_enabled",
        lambda: True,
    )

    with pytest.raises(SnapchatNativeSyncError) as exc:
        await async_routes.create_snapchat_native_sync_job(
            db,
            "owner-1",
            SnapchatNativeSyncInput(days=1),
            now=lambda: datetime(
                2026, 7, 30, 18, 0, tzinfo=timezone.utc
            ),
        )

    assert exc.value.code == "snapchat_analytics_sync_in_progress"
    assert exc.value.status_code == 409
    assert exc.value.run_id == "already-running"


@pytest.mark.asyncio
async def test_manual_job_recovers_orphaned_scheduler_run(monkeypatch):
    db = _db()
    db.rows["mezan_integration_sync_runs_v2"].append(
        {
            "run_id": "orphaned-scheduler-run",
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "run_type": async_routes.SCHEDULER_SYNC_RUN_TYPE,
            "status": "running",
            "started_at": "2026-07-30T17:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        async_routes,
        "snapchat_native_sync_enabled",
        lambda: True,
    )

    accepted = await async_routes.create_snapchat_native_sync_job(
        db,
        "owner-1",
        SnapchatNativeSyncInput(days=1),
        now=lambda: datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc),
    )

    assert accepted["status"] == "queued"
    stale = next(
        row
        for row in db.rows["mezan_integration_sync_runs_v2"]
        if row["run_id"] == "orphaned-scheduler-run"
    )
    assert stale["status"] == "failed"
    assert stale["error"]["code"] == "snapchat_scheduler_sync_stale"


@pytest.mark.asyncio
async def test_manual_job_preserves_recent_scheduler_run(monkeypatch):
    db = _db()
    db.rows["mezan_integration_sync_runs_v2"].append(
        {
            "run_id": "live-scheduler-run",
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "run_type": async_routes.SCHEDULER_SYNC_RUN_TYPE,
            "status": "running",
            "started_at": "2026-07-30T17:50:00+00:00",
        }
    )
    monkeypatch.setattr(
        async_routes,
        "snapchat_native_sync_enabled",
        lambda: True,
    )

    with pytest.raises(SnapchatNativeSyncError) as exc:
        await async_routes.create_snapchat_native_sync_job(
            db,
            "owner-1",
            SnapchatNativeSyncInput(days=1),
            now=lambda: datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc),
        )

    assert exc.value.code == "snapchat_analytics_sync_in_progress"
    assert exc.value.run_id == "live-scheduler-run"
    assert db.rows["mezan_integration_sync_runs_v2"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_manual_job_recovers_far_future_scheduler_timestamp(monkeypatch):
    db = _db()
    db.rows["mezan_integration_sync_runs_v2"].append(
        {
            "run_id": "future-scheduler-run",
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "run_type": async_routes.SCHEDULER_SYNC_RUN_TYPE,
            "status": "running",
            "started_at": "2026-07-31T18:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        async_routes,
        "snapchat_native_sync_enabled",
        lambda: True,
    )

    accepted = await async_routes.create_snapchat_native_sync_job(
        db,
        "owner-1",
        SnapchatNativeSyncInput(days=1),
        now=lambda: datetime(2026, 7, 30, 18, 0, tzinfo=timezone.utc),
    )

    assert accepted["status"] == "queued"
    assert db.rows["mezan_integration_sync_runs_v2"][0]["status"] == "failed"
