from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import tiktok_native_reporting as reporting
from integrations_control_center import tiktok_native_reporting_routes as routes


class FakeResult:
    def __init__(self, modified_count=1):
        self.modified_count = modified_count


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, key, direction=1):
        reverse = direction < 0
        self.rows.sort(key=lambda row: str(row.get(key) or ""), reverse=reverse)
        return self

    def limit(self, size):
        self.rows = self.rows[:size]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows[:length] if length else self.rows)


class FakeCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db

    @property
    def rows(self):
        return self.db.rows.setdefault(self.name, [])

    async def create_index(self, *args, **kwargs):
        self.db.indexes.append((self.name, deepcopy(args), deepcopy(kwargs)))
        return kwargs.get("name")

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        self.db.writes.append((self.name, "insert_one", deepcopy(document)))
        return object()

    async def insert_many(self, documents):
        docs = deepcopy(list(documents))
        self.rows.extend(docs)
        self.db.writes.append((self.name, "insert_many", docs))
        return object()

    async def find_one(self, query, projection=None, sort=None):
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(
                    key=lambda row: str(row.get(key) or ""),
                    reverse=direction < 0,
                )
        return deepcopy(rows[0]) if rows else None

    def find(self, query, projection=None):
        return FakeCursor([row for row in self.rows if _matches(row, query)])

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            target.update(deepcopy(update.get("$setOnInsert") or {}))
            self.rows.append(target)
        if target is not None:
            target.update(deepcopy(update.get("$set") or {}))
        self.db.writes.append(
            (
                self.name,
                "update_one",
                {"query": deepcopy(query), "update": deepcopy(update)},
            )
        )
        return FakeResult(1 if target is not None else 0)


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.indexes = []

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return FakeCollection(name, self)


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "code": 0,
            "message": "OK",
            "request_id": "tt-request-1",
            "data": {
                "list": [
                    {
                        "dimensions": {"advertiser_id": "700000000001"},
                        "metrics": {
                            "spend": "100.223069",
                            "impressions": "12000",
                            "clicks": "420",
                            "conversion": "18",
                        },
                    }
                ]
            },
        }


class FakeHttpClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        return FakeResponse()


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("TIKTOK_MARKETING_APP_ID", "app-123")
    monkeypatch.setenv("TIKTOK_MARKETING_APP_SECRET", "secret-456")
    monkeypatch.setenv("TIKTOK_TOKEN_ENC_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setenv(
        "TIKTOK_MARKETING_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/tiktok/callback",
    )
    monkeypatch.setenv("TIKTOK_NATIVE_REPORTING_SYNC_ENABLED", "true")
    monkeypatch.setenv("TIKTOK_USD_TO_SAR_RATE", "3.75")


@pytest.mark.asyncio
async def test_native_reporting_persists_only_source_rows(configured, monkeypatch):
    db = FakeDB()
    db.rows["mezan_tiktok_oauth_credentials_v2"] = [
        {
            "user_id": "owner-1",
            "provider": "tiktok_ads",
            "access_token_ciphertext": b"encrypted-token",
        }
    ]
    db.rows["mezan_integration_accounts_v2"] = [
        {
            "user_id": "owner-1",
            "provider": "tiktok_ads",
            "external_account_id": "700000000001",
            "ad_account_id": "700000000001",
            "display_name": "Amasi TikTok",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        }
    ]
    monkeypatch.setattr(reporting, "decrypt_tiktok_token", lambda value: "token")
    monkeypatch.setattr(reporting.httpx, "AsyncClient", FakeHttpClient)
    FakeHttpClient.calls = []

    result = await reporting.run_tiktok_reporting_sync(
        db,
        "owner-1",
        reporting.TikTokReportingSyncInput(days=1),
        now=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["accounts_attempted"] == 1
    assert result["accounts_complete"] == 1
    assert result["rows_saved"] == 1
    assert result["source_only"] is True
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False

    row = db.rows[reporting.TIKTOK_REPORTING_COLLECTION][0]
    assert row["date"] == "2026-07-30"
    assert row["spend_native"] == pytest.approx(100.223069)
    assert row["spend_sar"] == 375.84
    assert row["impressions"] == 12000
    assert row["clicks"] == 420
    assert row["conversions"] == 18
    assert row["accounting_eligible"] is False
    assert row["source_only"] is True

    written_collections = {name for name, _, _ in db.writes}
    assert "tiktok_ads_daily" not in written_collections
    assert "ads_daily" not in written_collections
    assert "general_ledger" not in written_collections
    assert "qoyod_invoices" not in written_collections
    assert reporting.TIKTOK_REPORTING_COLLECTION in written_collections
    assert FakeHttpClient.calls[0][1]["headers"] == {"Access-Token": "token"}


@pytest.mark.asyncio
async def test_reporting_fails_closed_when_safety_flag_is_off(configured, monkeypatch):
    monkeypatch.setenv("TIKTOK_NATIVE_REPORTING_SYNC_ENABLED", "false")
    with pytest.raises(reporting.TikTokReportingError) as exc_info:
        await reporting.run_tiktok_reporting_sync(
            FakeDB(),
            "owner-1",
            reporting.TikTokReportingSyncInput(days=1),
        )
    assert exc_info.value.code == "tiktok_reporting_disabled"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_async_job_persists_terminal_result(configured, monkeypatch):
    db = FakeDB()
    background = FakeBackgroundTasks()

    async def fake_sync(db_value, user_id, payload):
        assert db_value is db
        assert user_id == "owner-1"
        assert payload.days == 7
        return {
            "provider": "tiktok_ads",
            "status": "complete",
            "date_from": "2026-07-24",
            "date_to": "2026-07-30",
            "accounts_attempted": 1,
            "accounts_complete": 1,
            "rows_saved": 7,
            "errors_count": 0,
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    monkeypatch.setattr(routes, "run_tiktok_reporting_sync", fake_sync)
    result = await routes.start_tiktok_reporting_job(
        db,
        "owner-1",
        reporting.TikTokReportingSyncInput(days=7),
        background,
    )
    assert result["status"] == "queued"
    assert result["source_only"] is True
    assert len(background.tasks) == 1

    func, args, kwargs = background.tasks[0]
    await func(*args, **kwargs)
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["status"] == "complete"
    assert run["summary"]["rows_saved"] == 7
    assert run["summary"]["accounting_write_reached"] is False
    assert run["summary"]["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_active_job_is_reused(configured):
    db = FakeDB()
    now = routes._iso()
    db.rows["mezan_integration_sync_runs_v2"] = [
        {
            "run_id": "existing-run",
            "user_id": "owner-1",
            "provider": "tiktok_ads",
            "run_type": routes.TIKTOK_REPORTING_RUN_TYPE,
            "status": "running",
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "summary": {"requested_days": 30},
            "error": None,
        }
    ]
    background = FakeBackgroundTasks()
    result = await routes.start_tiktok_reporting_job(
        db,
        "owner-1",
        reporting.TikTokReportingSyncInput(days=30),
        background,
    )
    assert result["run_id"] == "existing-run"
    assert result["status"] == "running"
    assert background.tasks == []
