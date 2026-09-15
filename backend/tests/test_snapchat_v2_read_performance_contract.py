from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.connection import SNAPCHAT_CONNECTIONS_COLLECTION
from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from snapchat_v2.lease import SNAPCHAT_LEASE_COLLECTION
from snapchat_v2.read_indexes import ensure_snapchat_v2_read_indexes
from snapchat_v2.read_timing import SnapchatV2ReadTimingMiddleware
from snapchat_v2.status import snapchat_v2_status
from snapchat_v2.sync_runs import LEVEL_STATUS_FIELDS, SNAPCHAT_SYNC_RUNS_COLLECTION


class IndexCollection:
    def __init__(self):
        self.indexes = {"_id_": {"key": [("_id", 1)]}}
        self.created = []

    async def index_information(self):
        return dict(self.indexes)

    async def create_index(self, keys, *, name):
        self.created.append(name)
        self.indexes[name] = {"key": list(keys)}
        return name


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, IndexCollection())


@pytest.mark.asyncio
async def test_read_indexes_are_idempotent_and_parent_page_indexes_are_present():
    db = DB()
    first = await ensure_snapchat_v2_read_indexes(db)
    created_once = sum(len(collection.created) for collection in db.collections.values())
    second = await ensure_snapchat_v2_read_indexes(db)

    assert all(first.values())
    assert not any(second.values())
    assert created_once == 6
    assert sum(len(collection.created) for collection in db.collections.values()) == created_once
    names = {
        name
        for collection in db.collections.values()
        for name in collection.created
    }
    assert "snapchat_v2_ad_squad_parent_page" in names
    assert "snapchat_v2_ad_parent_page" in names
    assert "snapchat_settings_visible_ids" in names


@pytest.mark.asyncio
async def test_read_timing_header_contains_only_fixed_metric_name(monkeypatch):
    monkeypatch.setenv("SNAPCHAT_V2_SLOW_GET_LOG_MS", "999999")

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    middleware = SnapchatV2ReadTimingMiddleware(app)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/integrations-v2/snapchat-v2/campaigns",
            "headers": [],
        },
        receive,
        send,
    )

    headers = dict(messages[0]["headers"])
    timing = headers[b"server-timing"].decode("ascii")
    assert timing.startswith("snapchat-v2-app;dur=")
    assert "campaign" not in timing
    assert "account" not in timing


class ReadCursor:
    def __init__(self, db, rows):
        self.db = db
        self.rows = list(rows)

    async def to_list(self, length=None):
        self.db.active_reads += 1
        self.db.max_active_reads = max(self.db.max_active_reads, self.db.active_reads)
        try:
            await asyncio.sleep(0)
            return list(self.rows[:length])
        finally:
            self.db.active_reads -= 1


class ReadCollection:
    def __init__(self, db, *, row=None, aggregate_rows=None):
        self.db = db
        self.row = row
        self.aggregate_rows = list(aggregate_rows or [])
        self.find_one_calls = 0
        self.aggregate_calls = 0

    async def find_one(self, *_args, **_kwargs):
        self.db.read_commands += 1
        self.find_one_calls += 1
        self.db.active_reads += 1
        self.db.max_active_reads = max(self.db.max_active_reads, self.db.active_reads)
        try:
            await asyncio.sleep(0)
            return dict(self.row) if self.row else None
        finally:
            self.db.active_reads -= 1

    def aggregate(self, _pipeline):
        self.db.read_commands += 1
        self.aggregate_calls += 1
        return ReadCursor(self.db, self.aggregate_rows)


class StatusDB:
    def __init__(self, now):
        self.read_commands = 0
        self.active_reads = 0
        self.max_active_reads = 0
        latest = {
            "sync_run_id": "run-latest",
            "financial_sync_status": "complete",
            "campaign_sync_status": "complete",
            "ad_squad_sync_status": "complete",
            "ad_sync_status": "complete",
            "identity_sync_status": "complete",
            "last_error": None,
        }
        snapshot = {
            "latest": [latest],
            **{
                level: [{"sync_run_id": f"run-{level}"}]
                for level in LEVEL_STATUS_FIELDS
            },
        }
        self.collections = {
            SNAPCHAT_ACCOUNTS_COLLECTION: ReadCollection(self, row={
                "ad_account_id": "account-1",
                "selected": True,
                "active": True,
            }),
            SNAPCHAT_CONNECTIONS_COLLECTION: ReadCollection(self, row={
                "connection_status": "connected",
            }),
            SNAPCHAT_SYNC_RUNS_COLLECTION: ReadCollection(
                self,
                aggregate_rows=[snapshot],
            ),
            SNAPCHAT_LEASE_COLLECTION: ReadCollection(self, row={
                "status": "held",
                "owner_id": "worker-1",
                "expires_at": now + timedelta(minutes=5),
            }),
            SNAPCHAT_HOURLY_FACTS_COLLECTION: ReadCollection(self, row={
                "updated_at": now - timedelta(minutes=5),
                "sync_run_id": "run-latest",
            }),
        }

    def __getitem__(self, name):
        return self.collections[name]


@pytest.mark.asyncio
async def test_status_collapses_run_n_plus_one_and_runs_independent_reads_concurrently():
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    db = StatusDB(now)

    result = await snapchat_v2_status(db, "owner-1", now=now)

    assert db.read_commands == 5
    assert db.max_active_reads >= 2
    assert db[SNAPCHAT_SYNC_RUNS_COLLECTION].aggregate_calls == 1
    assert db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one_calls == 0
    assert result["last_run"]["sync_run_id"] == "run-latest"
    assert result["last_success"] == {
        level: {"sync_run_id": f"run-{level}"}
        for level in LEVEL_STATUS_FIELDS
    }
    assert result["data"]["age_seconds"] == 300
