from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import APIRouter

from snapchat_v2 import routes as snapchat_routes
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.connection import SNAPCHAT_CONNECTIONS_COLLECTION
from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from snapchat_v2.lease import SNAPCHAT_LEASE_COLLECTION
from snapchat_v2.read_indexes import ensure_snapchat_v2_read_indexes
from snapchat_v2.read_timing import (
    SnapchatV2ReadTimingMiddleware,
    timed_awaitable,
    timed_call,
)
from snapchat_v2.status import snapchat_v2_status
from snapchat_v2.sync_runs import LEVEL_STATUS_FIELDS, SNAPCHAT_SYNC_RUNS_COLLECTION


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = deepcopy(rows)

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return deepcopy(self.rows[:length])


class _ReadCollection:
    def __init__(
        self,
        db: "_StatusDB",
        name: str,
        *,
        row: dict[str, Any] | None = None,
        aggregate_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.db = db
        self.name = name
        self.row = deepcopy(row)
        self.aggregate_rows = deepcopy(aggregate_rows or [])
        self.find_one_calls = 0
        self.aggregate_calls = 0
        self.aggregate_pipelines: list[list[dict[str, Any]]] = []

    async def find_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        self.db.read_commands += 1
        self.find_one_calls += 1
        self.db.active_reads += 1
        self.db.max_active_reads = max(self.db.max_active_reads, self.db.active_reads)
        try:
            await asyncio.sleep(0)
            return deepcopy(self.row)
        finally:
            self.db.active_reads -= 1

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _Cursor:
        self.db.read_commands += 1
        self.aggregate_calls += 1
        self.aggregate_pipelines.append(deepcopy(pipeline))
        return _Cursor(self.aggregate_rows)

    async def insert_one(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GET status must not write")

    async def update_one(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GET status must not write")

    async def update_many(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GET status must not write")

    async def delete_many(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GET status must not write")


class _StatusDB:
    def __init__(self, *, now: datetime) -> None:
        self.read_commands = 0
        self.active_reads = 0
        self.max_active_reads = 0
        latest_run = {
            "sync_run_id": "run-latest",
            "status": "complete",
            "financial_sync_status": "complete",
            "campaign_sync_status": "complete",
            "ad_squad_sync_status": "complete",
            "ad_sync_status": "complete",
            "identity_sync_status": "complete",
            "last_error": None,
            "started_at": now - timedelta(minutes=8),
            "finished_at": now - timedelta(minutes=2),
        }
        last_success = {
            level: [
                {
                    "sync_run_id": f"run-{level}",
                    "started_at": now - timedelta(minutes=20),
                    "finished_at": now - timedelta(minutes=10),
                }
            ]
            for level in LEVEL_STATUS_FIELDS
        }
        snapshot = {"latest": [latest_run], **last_success}
        self.collections = {
            SNAPCHAT_ACCOUNTS_COLLECTION: _ReadCollection(
                self,
                SNAPCHAT_ACCOUNTS_COLLECTION,
                row={
                    "user_id": "user-1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "account-1",
                    "timezone": "America/Los_Angeles",
                    "currency": "USD",
                    "selected": True,
                    "active": True,
                },
            ),
            SNAPCHAT_CONNECTIONS_COLLECTION: _ReadCollection(
                self,
                SNAPCHAT_CONNECTIONS_COLLECTION,
                row={
                    "connection_status": "connected",
                    "next_due_at": now + timedelta(minutes=5),
                },
            ),
            SNAPCHAT_SYNC_RUNS_COLLECTION: _ReadCollection(
                self,
                SNAPCHAT_SYNC_RUNS_COLLECTION,
                aggregate_rows=[snapshot],
            ),
            SNAPCHAT_LEASE_COLLECTION: _ReadCollection(
                self,
                SNAPCHAT_LEASE_COLLECTION,
                row={
                    "status": "held",
                    "owner_id": "worker-1",
                    "heartbeat_at": now - timedelta(seconds=5),
                    "expires_at": now + timedelta(minutes=10),
                },
            ),
            SNAPCHAT_HOURLY_FACTS_COLLECTION: _ReadCollection(
                self,
                SNAPCHAT_HOURLY_FACTS_COLLECTION,
                row={
                    "updated_at": now - timedelta(minutes=5),
                    "hour_end_utc": now.replace(minute=0, second=0, microsecond=0),
                    "sync_run_id": "run-latest",
                },
            ),
        }

    def __getitem__(self, name: str) -> _ReadCollection:
        return self.collections[name]


@pytest.mark.asyncio
async def test_status_uses_one_run_aggregation_and_five_total_db_reads() -> None:
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    db = _StatusDB(now=now)

    result = await snapchat_v2_status(db, "user-1", now=now)

    assert db.read_commands == 5
    assert db.max_active_reads >= 2
    run_collection = db[SNAPCHAT_SYNC_RUNS_COLLECTION]
    assert run_collection.aggregate_calls == 1
    assert run_collection.find_one_calls == 0
    pipeline = run_collection.aggregate_pipelines[0]
    assert pipeline[0] == {
        "$match": {
            "user_id": "user-1",
            "provider": "snapchat_ads",
            "ad_account_id": "account-1",
        }
    }
    assert pipeline[1] == {"$sort": {"started_at": -1}}
    assert set(pipeline[2]["$facet"]) == {"latest", *LEVEL_STATUS_FIELDS}
    assert result["status"] == "healthy"
    assert result["last_run"]["sync_run_id"] == "run-latest"
    assert {
        level: row["sync_run_id"] if row else None
        for level, row in result["last_success"].items()
    } == {level: f"run-{level}" for level in LEVEL_STATUS_FIELDS}
    assert result["data"]["age_seconds"] == 300
    assert set(result) == {
        "provider",
        "account_id",
        "selected_account",
        "connection",
        "status",
        "financial_sync_status",
        "campaign_sync_status",
        "ad_squad_sync_status",
        "ad_sync_status",
        "identity_sync_status",
        "last_run",
        "last_success",
        "last_error",
        "lock",
        "data",
        "next_due_at",
        "checked_at",
    }


class _IndexCollection:
    def __init__(self, indexes: dict[str, dict[str, Any]] | None = None) -> None:
        self.indexes = deepcopy(indexes or {"_id_": {"key": [("_id", 1)]}})
        self.created: list[tuple[list[tuple[str, int]], str]] = []

    async def index_information(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.indexes)

    async def create_index(
        self,
        keys: list[tuple[str, int]],
        *,
        name: str,
    ) -> str:
        self.created.append((list(keys), name))
        self.indexes[name] = {"key": list(keys)}
        return name


class _IndexDB:
    def __init__(self, users: _IndexCollection, facts: _IndexCollection) -> None:
        self.users = users
        self.facts = facts

    def __getitem__(self, name: str) -> _IndexCollection:
        assert name == SNAPCHAT_HOURLY_FACTS_COLLECTION
        return self.facts


@pytest.mark.asyncio
async def test_read_indexes_are_shape_checked_and_idempotent() -> None:
    users = _IndexCollection(
        {"custom_users_id": {"key": [("id", 1)], "unique": True}}
    )
    facts = _IndexCollection(
        {
            "custom_latest_fact": {
                "key": [
                    ("user_id", 1),
                    ("provider", 1),
                    ("ad_account_id", 1),
                    ("updated_at", -1),
                    ("sync_run_id", 1),
                ]
            }
        }
    )
    existing = await ensure_snapchat_v2_read_indexes(_IndexDB(users, facts))
    assert existing == {
        "users_id_lookup_created": False,
        "latest_hourly_fact_created": False,
    }
    assert users.created == []
    assert facts.created == []

    missing_users = _IndexCollection()
    missing_facts = _IndexCollection()
    db = _IndexDB(missing_users, missing_facts)
    first = await ensure_snapchat_v2_read_indexes(db)
    second = await ensure_snapchat_v2_read_indexes(db)

    assert first == {
        "users_id_lookup_created": True,
        "latest_hourly_fact_created": True,
    }
    assert second == {
        "users_id_lookup_created": False,
        "latest_hourly_fact_created": False,
    }
    assert missing_users.created == [([("id", 1)], "users_id_lookup")]
    assert missing_facts.created == [
        (
            [
                ("user_id", 1),
                ("provider", 1),
                ("ad_account_id", 1),
                ("updated_at", -1),
            ],
            "snapchat_v2_hourly_account_updated_latest",
        )
    ]


@pytest.mark.asyncio
async def test_server_timing_contains_only_safe_stage_names_and_durations() -> None:
    async def app(_scope: dict[str, Any], _receive: Any, send: Any) -> None:
        await timed_awaitable("db-selected-account", asyncio.sleep(0))
        timed_call("headline-resolve", lambda: None)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = SnapchatV2ReadTimingMiddleware(app)
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(deepcopy(message))

    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations-v2/snapchat-v2/report",
        },
        receive,
        send,
    )

    start = next(row for row in messages if row["type"] == "http.response.start")
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    timing = headers["server-timing"]
    assert "db-selected-account;dur=" in timing
    assert "headline-resolve;dur=" in timing
    assert "app;dur=" in timing
    assert "user-1" not in timing
    assert "account-1" not in timing
    assert "token" not in timing.lower()


@pytest.mark.asyncio
async def test_report_and_hourly_keep_contract_and_never_call_provider_or_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = {
        "ad_account_id": "account-1",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
    }
    projection = {
        "report_date": "2026-08-29",
        "projection_timezone": "America/Los_Angeles",
        "account_timezone": "America/Los_Angeles",
        "action_report_time": "conversion",
        "base_spend_native": 10.25,
        "impressions": 100,
        "swipes": 5,
        "purchases": 1,
        "purchase_value_native": 30.0,
        "amount_complete": False,
        "hours": [{"hour": 0, "spend_native": 10.25, "status": "provisional"}],
        "coverage": {"status": "incomplete"},
    }
    provider_calls: list[str] = []

    async def selected_account(_db: Any, _user_id: str) -> dict[str, Any]:
        return deepcopy(account)

    async def daily_projections(_db: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [deepcopy(projection)]

    async def reconciliations(_db: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def forbidden_provider(*_args: Any, **_kwargs: Any) -> None:
        provider_calls.append("provider")
        raise AssertionError("GET must not call Snapchat or start a sync")

    monkeypatch.setattr(snapchat_routes, "get_selected_account", selected_account)
    monkeypatch.setattr(
        snapchat_routes,
        "list_daily_projections",
        daily_projections,
    )
    monkeypatch.setattr(snapchat_routes, "list_reconciliation", reconciliations)
    monkeypatch.setattr(
        "snapchat_v2.client.SnapchatV2Client._request_json",
        forbidden_provider,
    )
    monkeypatch.setattr(
        "snapchat_v2.sync_pipeline.SnapchatV2SyncPipeline.run",
        forbidden_provider,
    )
    monkeypatch.setattr(
        "snapchat_v2.provider_total.fetch_provider_total",
        forbidden_provider,
    )

    class NoWriteDB:
        def __getitem__(self, _name: str) -> Any:
            raise AssertionError("patched GET readers must not access another collection")

    router = APIRouter()
    snapchat_routes.attach_snapchat_v2_routes(
        router,
        NoWriteDB(),
        lambda: {"id": "user-1", "role": "owner"},
        lambda user: user,
    )
    report_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.name == "snapchat_v2_report_route"
    )
    hourly_endpoint = next(
        route.endpoint
        for route in router.routes
        if route.name == "snapchat_v2_hourly_route"
    )

    report = await report_endpoint(
        date_from=date(2026, 8, 29),
        date_to=date(2026, 8, 29),
        timezone="account",
        action_report_time="conversion",
        user={"id": "user-1", "role": "owner"},
    )
    hourly = await hourly_endpoint(
        report_date=date(2026, 8, 29),
        timezone="account",
        action_report_time="conversion",
        user={"id": "user-1", "role": "owner"},
    )

    assert provider_calls == []
    assert report["headline_spend_source"] == "hourly_facts"
    assert report["headline_spend_native"] == 10.25
    assert report["hourly_spend_native"] == 10.25
    assert report["unallocated_spend_native"] == 0.0
    assert report["days"] == [projection]
    assert set(report) == {
        "provider",
        "ad_account_id",
        "date_from",
        "date_to",
        "projection_timezone",
        "account_timezone",
        "currency",
        "action_report_time",
        "base_spend_native",
        "headline_spend_native",
        "hourly_spend_native",
        "unallocated_spend_native",
        "headline_spend_source",
        "hourly_breakdown_status",
        "hourly_breakdown_complete",
        "provider_total_checked_at",
        "impressions",
        "swipes",
        "video_views",
        "view_completion",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
        "purchases",
        "purchase_value_native",
        "amount_complete",
        "days",
        "reconciliation",
        "source_collection",
        "shadow_mode",
        "ui_enabled",
    }
    assert hourly == {
        "provider": "snapchat_ads",
        "ad_account_id": "account-1",
        "report_date": "2026-08-29",
        "projection_timezone": "America/Los_Angeles",
        "account_timezone": "America/Los_Angeles",
        "currency": "USD",
        "action_report_time": "conversion",
        "base_spend_native": 10.25,
        "amount_complete": False,
        "hours": projection["hours"],
        "coverage": projection["coverage"],
        "future_hours_are_zero": False,
        "source_collection": "mezan_snapchat_hourly_facts_v2",
    }
