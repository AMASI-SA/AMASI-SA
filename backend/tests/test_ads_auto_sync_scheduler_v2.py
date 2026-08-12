from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import inspect

from fastapi import APIRouter
import pytest

from integrations_control_center import ads_auto_sync_scheduler as scheduler


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, key, direction):
        self.rows.sort(
            key=lambda row: row.get(key) or "",
            reverse=direction < 0,
        )
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, projection=None):
        return next(
            (deepcopy(row) for row in self.rows if _matches(row, query)),
            None,
        )

    def find(self, query, projection=None):
        return _Cursor([row for row in self.rows if _matches(row, query)])


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        return _Collection(self.rows.setdefault(name, []))


def test_defaults_to_enabled_five_minutes_and_two_days(monkeypatch):
    monkeypatch.delenv(scheduler.ENABLED_ENV, raising=False)
    monkeypatch.delenv(scheduler.INTERVAL_ENV, raising=False)
    monkeypatch.delenv(scheduler.ROLLING_DAYS_ENV, raising=False)

    assert scheduler.auto_sync_enabled() is True
    assert scheduler.interval_seconds() == 300
    assert scheduler.rolling_days() == 2


def test_interval_cannot_be_faster_than_five_minutes(monkeypatch):
    monkeypatch.setenv(scheduler.INTERVAL_ENV, "30")
    assert scheduler.interval_seconds() == 300

    monkeypatch.setenv(scheduler.INTERVAL_ENV, "900")
    assert scheduler.interval_seconds() == 900


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "false")
    assert scheduler.auto_sync_enabled() is False


def test_rolling_window_uses_riyadh_calendar_day():
    # 21:30 UTC is 00:30 in Riyadh on Aug 1.
    start, end = scheduler.riyadh_date_range(
        datetime(2026, 7, 31, 21, 30, tzinfo=timezone.utc),
        2,
    )
    assert start.isoformat() == "2026-07-31"
    assert end.isoformat() == "2026-08-01"


def test_router_registers_status_and_backend_lifecycle(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "true")
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    def require_owner(user):
        return user

    before_startup = len(router.on_startup)
    before_shutdown = len(router.on_shutdown)
    scheduler.attach_ads_auto_sync_scheduler(
        router,
        object(),
        current_user,
        require_owner,
    )

    assert any(
        route.path == "/integrations-v2/ads-auto-sync/status"
        for route in router.routes
    )
    assert len(router.on_startup) == before_startup + 1
    assert len(router.on_shutdown) == before_shutdown + 1


def test_safe_summary_preserves_account_error_samples():
    summary = scheduler._safe_summary({
        "errors_count": 1,
        "error_samples": [{
            "error_id": "err-1",
            "ad_account_id": "account-1",
            "code": "snapchat_request_failed",
            "message": "provider rejected the range",
            "retryable": True,
            "secret": "must-not-leak",
        }],
    })

    assert summary["error_samples"] == [{
        "error_id": "err-1",
        "ad_account_id": "account-1",
        "code": "snapchat_request_failed",
        "message": "provider rejected the range",
        "retryable": True,
    }]


def test_snapchat_call_budget_is_isolated_per_selected_account():
    source = inspect.getsource(scheduler._refresh_snapchat)

    assert "token_context = SnapchatSyncContext" in source
    assert "account_context = SnapchatSyncContext" in source
    assert "provider_calls_total += int(account_context.provider_calls)" in source
    assert '"provider_call_budget_scope": "per_selected_account"' in source
    assert '"provider_calls": provider_calls_total' in source


def test_safe_summary_preserves_per_account_provider_calls():
    summary = scheduler._safe_summary({
        "provider_calls": 263,
        "provider_call_budget_scope": "per_selected_account",
        "campaign_rows_saved": 48,
        "campaign_facts_source_mode": (
            scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
        ),
        "campaign_facts_schema_version": 4,
        "account_provider_calls": [
            {"ad_account_id": "account-usd", "provider_calls": 132},
            {"ad_account_id": "account-sar", "provider_calls": 131},
        ],
    })

    assert summary["provider_calls"] == 263
    assert summary["provider_call_budget_scope"] == "per_selected_account"
    assert summary["account_provider_calls"] == [
        {"ad_account_id": "account-usd", "provider_calls": 132},
        {"ad_account_id": "account-sar", "provider_calls": 131},
    ]
    assert summary["campaign_rows_saved"] == 48
    assert summary["campaign_facts_source_mode"] == (
        scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
    )
    assert summary["campaign_facts_schema_version"] == 4


@pytest.mark.asyncio
async def test_status_never_exposes_global_results_from_another_tenant():
    db = _DB(
        {
            scheduler.SCHEDULER_COLLECTION: [
                {
                    "_id": scheduler.SCHEDULER_ID,
                    "status": "complete",
                    "last_started_at": "2026-08-12T12:00:00+00:00",
                    "last_finished_at": "2026-08-12T12:01:00+00:00",
                    "next_due_at": "2026-08-12T12:05:00+00:00",
                    "last_result": {
                        "status": "complete",
                        "started_at": "2026-08-12T12:00:00+00:00",
                        "finished_at": "2026-08-12T12:01:00+00:00",
                        "results": [
                            {
                                "run_id": "foreign-run-b",
                                "account_provider_calls": [
                                    {
                                        "ad_account_id": "foreign-account-b",
                                        "provider_calls": 7,
                                    }
                                ],
                                "error_samples": [
                                    {"error_id": "foreign-error-b"}
                                ],
                            }
                        ],
                    },
                    "last_error": {
                        "code": "ads_auto_sync_cycle_failed",
                        "message": "foreign-account-b failed",
                        "retryable": True,
                    },
                }
            ],
            scheduler.RUNS_COLLECTION: [
                {
                    "user_id": "tenant-a",
                    "trigger": scheduler.TRIGGER,
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "run_id": "tenant-a-run",
                    "started_at": "2026-08-12T12:00:00+00:00",
                },
                {
                    "user_id": "tenant-b",
                    "trigger": scheduler.TRIGGER,
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "run_id": "foreign-run-b",
                    "started_at": "2026-08-12T12:00:00+00:00",
                    "summary": {
                        "account_provider_calls": [
                            {"ad_account_id": "foreign-account-b"}
                        ]
                    },
                    "error": {"error_id": "foreign-error-b"},
                },
            ],
        }
    )

    result = await scheduler.auto_sync_status(db, "tenant-a")

    assert result["providers"][scheduler.SNAPCHAT_PROVIDER_ID]["run_id"] == (
        "tenant-a-run"
    )
    assert result["scheduler"]["last_result"] == {
        "status": "complete",
        "started_at": "2026-08-12T12:00:00+00:00",
        "finished_at": "2026-08-12T12:01:00+00:00",
    }
    assert result["scheduler"]["last_error"] == {
        "code": "ads_auto_sync_cycle_failed",
        "retryable": True,
    }
    serialized = repr(result)
    assert "foreign-run-b" not in serialized
    assert "foreign-account-b" not in serialized
    assert "foreign-error-b" not in serialized
