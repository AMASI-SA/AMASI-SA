from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import APIRouter

from integrations_control_center import ads_auto_sync as module


class FakeCollection:
    def __init__(self):
        self.rows = []
        self.indexes = []

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))
        return kwargs.get("name") or "index"

    async def find_one(self, query, projection=None, **kwargs):
        for row in reversed(self.rows):
            if all(row.get(key) == value for key, value in query.items()):
                if projection:
                    return {
                        key: value
                        for key, value in row.items()
                        if projection.get(key, 1) and key != "_id"
                    }
                return dict(row)
        return None

    async def update_one(self, query, update, upsert=False):
        row = await self.find_one(query)
        if row is None:
            row = dict(query)
            self.rows.append(row)
        row.update(update.get("$set") or {})
        # Replace the matching stored row when find_one returned a copy.
        for index, stored in enumerate(self.rows):
            if all(stored.get(key) == value for key, value in query.items()):
                self.rows[index] = dict(row)
                break
        return object()

    async def insert_one(self, document):
        self.rows.append(dict(document))
        return object()


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("5", 300), ("299", 300), ("300", 300), ("900", 900), ("99999", 3600)],
)
def test_interval_is_never_faster_than_five_minutes(monkeypatch, configured, expected):
    monkeypatch.setenv(module.AUTO_SYNC_INTERVAL_ENV, configured)
    assert module.ads_auto_sync_interval_seconds() == expected


def test_overall_status_isolated_by_provider():
    assert module._overall_status(
        {
            "snapchat": {"status": "complete"},
            "meta": {"status": "failed"},
        }
    ) == "partial"
    assert module._overall_status(
        {
            "snapchat": {"status": "disabled"},
            "meta": {"status": "not_configured"},
        }
    ) == "idle"


@pytest.mark.asyncio
async def test_user_cycle_keeps_provider_failures_isolated(monkeypatch):
    db = FakeDB()

    async def snap(*args, **kwargs):
        return module._safe_provider_result(
            "snapchat", status="complete", rows_saved=3
        )

    async def meta(*args, **kwargs):
        return module._safe_provider_result(
            "meta", status="failed", errors_count=1, message="token"
        )

    monkeypatch.setattr(module, "_sync_snapchat_today", snap)
    monkeypatch.setattr(module, "_sync_meta_today", meta)
    monkeypatch.setenv(module.AUTO_SYNC_INTERVAL_ENV, "300")

    result = await module.run_ads_auto_sync_for_user(
        db,
        "owner-1",
        now_value=datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
        bucket=123,
        interval_seconds=300,
    )

    assert result["status"] == "partial"
    assert result["providers"]["snapchat"]["rows_saved"] == 3
    assert result["providers"]["meta"]["status"] == "failed"
    assert result["providers"]["tiktok"]["status"] == "webhook"
    assert result["providers"]["snapchat"]["accounting_write_reached"] is False
    assert result["providers"]["meta"]["campaign_write_reached"] is False
    assert len(db[module.RUN_COLLECTION].rows) == 1


@pytest.mark.asyncio
async def test_status_contract_before_first_cycle(monkeypatch):
    db = FakeDB()
    monkeypatch.setenv(module.AUTO_SYNC_INTERVAL_ENV, "300")
    status = await module.get_ads_auto_sync_status(db, "owner-1")

    assert status["enabled"] is True
    assert status["interval_minutes"] == 5
    assert status["status"] == "waiting_for_first_cycle"
    assert status["providers"]["snapchat"]["mode"] == "native_pull"
    assert status["providers"]["meta"]["mode"] == "native_pull"
    assert status["providers"]["tiktok"]["mode"] == "event_driven"


def test_runtime_attaches_owner_status_route_and_lifecycle_handlers():
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    module.attach_ads_auto_sync_runtime(
        router,
        FakeDB(),
        current_user,
        lambda user: user,
    )

    paths = {route.path for route in router.routes}
    assert "/integrations-v2/ads-auto-sync/status" in paths
    assert len(router.on_startup) == 1
    assert len(router.on_shutdown) == 1


def test_runtime_module_has_no_campaign_or_accounting_mutation_surface():
    source = module.__file__
    text = open(source, encoding="utf-8").read()

    assert "general_ledger" not in text
    assert "daily_costs" not in text
    assert "post_txn_group" not in text
    assert "campaigns/" not in text
    assert "adsets/" not in text
    assert 'provider_write_reached": False' in text
    assert 'accounting_write_reached": False' in text
    assert 'qoyod_write_reached": False' in text
