from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from snapchat_v2 import scheduler


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return self.rows[:length]


class Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []

    def find(self, *_args, **_kwargs):
        return Cursor(self.rows)

    async def find_one(self, *_args, **_kwargs):
        return None

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return SimpleNamespace(modified_count=1, matched_count=1)


class DB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


def test_shadow_scheduler_is_disabled_by_default_and_clamps_interval(monkeypatch):
    monkeypatch.delenv(scheduler.ENABLED_ENV, raising=False)
    monkeypatch.setenv(scheduler.INTERVAL_ENV, "1")
    assert scheduler.shadow_scheduler_enabled() is False
    assert scheduler.shadow_interval_seconds() == 300


@pytest.mark.asyncio
async def test_selected_accounts_falls_back_to_legacy_and_deduplicates():
    db = DB(
        {
            "mezan_snapchat_accounts_v2": Collection([]),
            "mezan_integration_accounts_v2": Collection(
                [
                    {
                        "user_id": "u1",
                        "ad_account_id": "a1",
                    },
                    {
                        "user_id": "u1",
                        "external_account_id": "a1",
                    },
                ]
            ),
        }
    )
    rows = await scheduler._selected_accounts(db)
    assert rows == [{"user_id": "u1", "ad_account_id": "a1"}]


@pytest.mark.asyncio
async def test_shadow_cycle_persists_source_only_result(monkeypatch):
    db = DB(
        {
            "mezan_snapchat_accounts_v2": Collection(
                [{"user_id": "u1", "ad_account_id": "a1"}]
            ),
            "mezan_snapchat_shadow_scheduler_v2": Collection(),
        }
    )

    class Pipeline:
        def __init__(self, _db):
            pass

        async def run(self, user_id, ad_account_id, **_kwargs):
            assert (user_id, ad_account_id) == ("u1", "a1")
            return {"status": "partial", "sync_run_id": "run-1"}

    monkeypatch.setattr(scheduler, "SnapchatV2SyncPipeline", Pipeline)
    fixed = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)
    result = await scheduler.run_shadow_cycle(db, now=lambda: fixed)
    assert result["status"] == "complete"
    assert result["completed"] == 1
    assert result["shadow_mode"] is True
    assert result["ui_enabled"] is False
    update = db["mezan_snapchat_shadow_scheduler_v2"].updates[0][1]
    assert update["$set"]["results"][0]["sync_run_id"] == "run-1"
