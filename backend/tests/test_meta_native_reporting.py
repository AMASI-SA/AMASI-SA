from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center import meta_account_selection as selection
from integrations_control_center import meta_campaign_reporting as campaign_reporting
from integrations_control_center import meta_native_reporting as reporting
from integrations_control_center import meta_native_reporting_routes as routes


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
        if isinstance(key, list):
            for sort_key, sort_direction in reversed(key):
                self.rows.sort(
                    key=lambda row: str(row.get(sort_key) or ""),
                    reverse=sort_direction < 0,
                )
        else:
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=direction < 0,
            )
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

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def delete_many(self, query):
        self.db.rows[self.name] = [row for row in self.rows if not _matches(row, query)]
        self.db.writes.append((self.name, "delete_many", deepcopy(query)))
        return object()

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
            "data": [
                {
                    "spend": "120.50",
                    "impressions": "15000",
                    "clicks": "510",
                    "account_currency": "USD",
                    "date_start": "2026-07-30",
                    "date_stop": "2026-07-30",
                    "actions": [
                        {"action_type": "omni_purchase", "value": "21"},
                        {
                            "action_type": "offsite_conversion.fb_pixel_purchase",
                            "value": "20",
                        },
                    ],
                    "action_values": [
                        {"action_type": "omni_purchase", "value": "620.40"},
                    ],
                }
            ]
        }


class FakeCampaignCatalogResponse:
    status_code = 200

    def json(self):
        return {
            "data": [
                {
                    "id": "campaign-1",
                    "name": "Meta Sales Campaign",
                    "objective": "OUTCOME_SALES",
                    "status": "ACTIVE",
                    "effective_status": "ACTIVE",
                    "daily_budget": "25000",
                    "lifetime_budget": "100000",
                    "start_time": "2026-07-01T00:00:00+0000",
                    "stop_time": "2026-08-31T23:59:59+0000",
                }
            ]
        }


class FakeCampaignInsightsResponse:
    status_code = 200

    def json(self):
        return {
            "data": [
                {
                    "campaign_id": "campaign-1",
                    "campaign_name": "Meta Sales Campaign",
                    "spend": "120.50",
                    "impressions": "15000",
                    "clicks": "510",
                    "account_currency": "USD",
                    "date_start": "2026-07-30",
                    "date_stop": "2026-07-30",
                    "actions": [
                        {"action_type": "omni_purchase", "value": "21"}
                    ],
                    "action_values": [
                        {"action_type": "omni_purchase", "value": "620.40"}
                    ],
                }
            ]
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
        if url.endswith("/campaigns"):
            return FakeCampaignCatalogResponse()
        if (kwargs.get("params") or {}).get("level") == "campaign":
            return FakeCampaignInsightsResponse()
        return FakeResponse()


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("META_BUSINESS_APP_ID", "meta-app-1")
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "meta-secret")
    monkeypatch.setenv("META_TOKEN_ENC_KEY", "test-key")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setenv(
        "META_BUSINESS_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/meta/callback",
    )
    monkeypatch.setenv("META_NATIVE_REPORTING_SYNC_ENABLED", "true")
    monkeypatch.setenv("META_USD_TO_SAR_RATE", "3.75")


def _seed_accounts(db):
    db.rows["mezan_integration_accounts_v2"] = [
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "external_account_id": "act_111",
            "ad_account_id": "act_111",
            "display_name": "Amasi Meta Main",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "business_id": "business-1",
            "business_name": "AMASI",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "external_account_id": "act_222",
            "ad_account_id": "act_222",
            "display_name": "Unused Meta Account",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": False,
        },
    ]


@pytest.mark.asyncio
async def test_account_selection_saves_only_known_accounts():
    db = FakeDB()
    _seed_accounts(db)
    result = await selection.save_meta_account_selection(
        db,
        "owner-1",
        selection.MetaAccountSelectionInput(account_ids=["111"]),
    )
    assert result["selected_count"] == 1
    selected = [row for row in db.rows["mezan_integration_accounts_v2"] if row.get("mezan_selected")]
    assert [row["external_account_id"] for row in selected] == ["act_111"]
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["summary"]["provider_write_reached"] is False
    assert run["summary"]["accounting_write_reached"] is False


@pytest.mark.asyncio
async def test_native_reporting_persists_only_selected_source_rows(configured, monkeypatch):
    db = FakeDB()
    db.rows["mezan_meta_oauth_credentials_v2"] = [
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "access_token_ciphertext": b"encrypted-token",
            "access_token_expires_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
        }
    ]
    _seed_accounts(db)
    monkeypatch.setattr(reporting, "decrypt_meta_token", lambda value: "meta-token")
    monkeypatch.setattr(reporting.httpx, "AsyncClient", FakeHttpClient)
    FakeHttpClient.calls = []

    result = await reporting.run_meta_reporting_sync(
        db,
        "owner-1",
        reporting.MetaReportingSyncInput(days=1),
        now=lambda: datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["accounts_attempted"] == 1
    assert result["accounts_complete"] == 1
    assert result["rows_saved"] == 1
    assert result["campaign_rows_saved"] == 1
    assert result["source_only"] is True
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False

    rows = db.rows[reporting.META_REPORTING_COLLECTION]
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_account_id"] == "act_111"
    assert row["date"] == "2026-07-30"
    assert row["spend_native"] == 120.50
    assert row["spend_sar"] == 451.88
    assert row["impressions"] == 15000
    assert row["clicks"] == 510
    assert row["purchases"] == 21
    assert row["purchase_value_native"] == 620.40
    assert row["purchase_value_sar"] == 2326.50
    assert row["purchase_action_type"] == "omni_purchase"
    assert row["source_only"] is True
    assert row["accounting_eligible"] is False

    written_collections = {name for name, _, _ in db.writes}
    assert "meta_connections" not in written_collections
    assert "meta_ads_daily" not in written_collections
    assert "ads_daily" not in written_collections
    assert "general_ledger" not in written_collections
    assert "qoyod_invoices" not in written_collections
    assert reporting.META_REPORTING_COLLECTION in written_collections
    assert campaign_reporting.META_CAMPAIGN_REPORTING_COLLECTION in written_collections

    campaign_rows = db.rows[
        campaign_reporting.META_CAMPAIGN_REPORTING_COLLECTION
    ]
    assert len(campaign_rows) == 1
    campaign = campaign_rows[0]
    assert campaign["campaign_id"] == "campaign-1"
    assert campaign["campaign_name"] == "Meta Sales Campaign"
    assert campaign["objective"] == "OUTCOME_SALES"
    assert campaign["effective_status"] == "ACTIVE"
    assert campaign["daily_budget_native"] == 250.0
    assert campaign["spend_sar"] == 451.88
    assert campaign["purchase_value_sar"] == 2326.50
    assert campaign["source_only"] is True
    assert campaign["accounting_eligible"] is False

    url, kwargs = next(
        (url, kwargs)
        for url, kwargs in FakeHttpClient.calls
        if (kwargs.get("params") or {}).get("level") == "account"
    )
    assert url.endswith("/act_111/insights")
    assert kwargs["params"]["access_token"] == "meta-token"
    assert kwargs["params"]["appsecret_proof"]
    assert kwargs["params"]["use_account_attribution_setting"] == "true"
    assert kwargs["params"]["use_unified_attribution_setting"] == "true"


@pytest.mark.asyncio
async def test_reporting_fails_closed_when_safety_flag_is_off(configured, monkeypatch):
    monkeypatch.setenv("META_NATIVE_REPORTING_SYNC_ENABLED", "false")
    with pytest.raises(reporting.MetaReportingError) as exc_info:
        await reporting.run_meta_reporting_sync(
            FakeDB(),
            "owner-1",
            reporting.MetaReportingSyncInput(days=1),
        )
    assert exc_info.value.code == "meta_reporting_disabled"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_expired_token_requires_reauthorization(configured, monkeypatch):
    db = FakeDB()
    db.rows["mezan_meta_oauth_credentials_v2"] = [
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "access_token_ciphertext": b"encrypted-token",
            "access_token_expires_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        }
    ]
    _seed_accounts(db)
    monkeypatch.setattr(reporting, "decrypt_meta_token", lambda value: "meta-token")
    with pytest.raises(reporting.MetaReportingError) as exc_info:
        await reporting.run_meta_reporting_sync(
            db,
            "owner-1",
            reporting.MetaReportingSyncInput(days=1),
            now=lambda: datetime(2026, 7, 30, tzinfo=timezone.utc),
        )
    assert exc_info.value.code == "meta_needs_reauth"


@pytest.mark.asyncio
async def test_async_job_persists_terminal_result(configured, monkeypatch):
    db = FakeDB()
    background = FakeBackgroundTasks()

    async def fake_sync(db_value, user_id, payload):
        assert db_value is db
        assert user_id == "owner-1"
        assert payload.days == 7
        return {
            "provider": "meta_ads",
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

    monkeypatch.setattr(routes, "run_meta_reporting_sync", fake_sync)
    result = await routes.start_meta_reporting_job(
        db,
        "owner-1",
        reporting.MetaReportingSyncInput(days=7),
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
            "provider": "meta_ads",
            "run_type": routes.META_REPORTING_RUN_TYPE,
            "status": "running",
            "created_at": now,
            "started_at": now,
            "finished_at": None,
            "summary": {"requested_days": 7},
            "error": None,
        }
    ]
    background = FakeBackgroundTasks()
    result = await routes.start_meta_reporting_job(
        db,
        "owner-1",
        reporting.MetaReportingSyncInput(days=7),
        background,
    )
    assert result["run_id"] == "existing-run"
    assert result["status"] == "running"
    assert background.tasks == []
