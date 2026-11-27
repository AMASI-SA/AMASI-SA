"""Regression tests: new TikTok advertisers appear without code edits or double spend."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import tiktok_account_refresh as discovery
from integrations_control_center import tiktok_native_reporting as reporting
from operational_ad_selection import choices


def match(row, query):
    return all(row.get(key) == value for key, value in query.items())


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, field, direction=1):
        self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=direction < 0)
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows[:length] if length is not None else self.rows)


class Collection:
    def __init__(self, store, name):
        self.store, self.name = store, name

    @property
    def rows(self):
        return self.store.data.setdefault(self.name, [])

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def find_one(self, query, projection=None):
        row = next((item for item in self.rows if match(item, query)), None)
        return deepcopy(row) if row else None

    def find(self, query, projection=None):
        return Cursor([item for item in self.rows if match(item, query)])

    async def insert_one(self, doc):
        self.rows.append(deepcopy(doc))
        self.store.writes.append(self.name)

    async def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if match(item, query)), None)
        if row is None and upsert:
            row = {**query, **deepcopy(update.get("$setOnInsert") or {})}
            self.rows.append(row)
        if row is not None:
            row.update(deepcopy(update.get("$set") or {}))
        self.store.writes.append(self.name)


class DB:
    def __init__(self):
        self.data = {}
        self.writes = []

    def __getattr__(self, key):
        return Collection(self, key)

    def __getitem__(self, key):
        return Collection(self, key)


@pytest.mark.asyncio
async def test_authorized_account_api_reads_real_app_credentials(monkeypatch):
    monkeypatch.setenv("TIKTOK_MARKETING_APP_ID", "real-app-id")
    monkeypatch.setenv("TIKTOK_MARKETING_APP_SECRET", "configured-secret")
    requests = []

    class Response:
        status_code = 200

        def json(self):
            return {"code": 0, "data": {"list": [
                {"advertiser_id": "100"}, {"advertiser_id": "200"},
                {"advertiser_id": "100"}]}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()

    monkeypatch.setattr(discovery.httpx, "AsyncClient", Client)
    assert await discovery.authorized_advertiser_ids("oauth-access-token") == ["100", "200"]
    assert requests[0][1]["params"] == {
        "app_id": "real-app-id", "secret": "configured-secret"}
    assert requests[0][1]["headers"] == {"Access-Token": "oauth-access-token"}


@pytest.mark.asyncio
async def test_refresh_upserts_all_authorized_accounts_without_resetting_existing(monkeypatch):
    db = DB()
    db.data["mezan_integration_accounts_v2"] = [{
        "user_id": "owner", "provider": "tiktok_ads",
        "external_account_id": "100", "ad_account_id": "100",
        "mezan_integration_account_id": "original-id",
        "mezan_selected": False,
        "connection_status": "connected",
        "last_sync_at": "2026-11-26T12:00:00+00:00",
        "currency": "USD", "timezone": "Asia/Riyadh",
    }]
    db.data["mezan_tiktok_oauth_credentials_v2"] = [{
        "user_id": "owner", "provider": "tiktok_ads",
        "access_token_ciphertext": b"encrypted",
    }]

    async def authorized(*args):
        return ["100", "200"]

    async def detail(*args):
        return [
            {"external_account_id": "100", "display_name": "Existing",
             "currency": "USD", "timezone": "Asia/Riyadh"},
            {"external_account_id": "200", "display_name": "New",
             "currency": "SAR", "timezone": "Asia/Riyadh"},
        ]

    monkeypatch.setattr(discovery, "authorized_advertiser_ids", authorized)
    monkeypatch.setattr(discovery, "discover_tiktok_advertisers", detail)

    first = await discovery.refresh_tiktok_authorized_accounts(
        db, "owner", "token", "2026-11-27T15:00:00+00:00")
    second = await discovery.refresh_tiktok_authorized_accounts(
        db, "owner", "token", "2026-11-27T15:10:00+00:00")
    assert first["authorized_accounts"] == second["authorized_accounts"] == 2
    rows = db.data["mezan_integration_accounts_v2"]
    assert len(rows) == 2
    previous = next(row for row in rows if row["external_account_id"] == "100")
    new = next(row for row in rows if row["external_account_id"] == "200")
    assert previous["mezan_integration_account_id"] == "original-id"
    assert previous["mezan_selected"] is False
    assert previous["last_sync_at"] == "2026-11-26T12:00:00+00:00"
    assert new["mezan_integration_account_id"]
    assert new["connection_status"] == "connected"
    assert db.data["mezan_tiktok_oauth_credentials_v2"][0]["access_token_ciphertext"] == b"encrypted"
    assert not {"general_ledger", "ad_account_ledger", "mz2_ad_account_bindings_v2"}.intersection(db.writes)


@pytest.mark.asyncio
async def test_failed_authorization_does_not_delete_account_or_financial_identity(monkeypatch):
    db = DB()
    original = {"user_id": "owner", "provider": "tiktok_ads",
                "external_account_id": "old", "mezan_integration_account_id": "saved"}
    db.data["mezan_integration_accounts_v2"] = [deepcopy(original)]

    async def denied(*args):
        raise discovery.TikTokAccountRefreshError("provider_unavailable")

    monkeypatch.setattr(discovery, "authorized_advertiser_ids", denied)
    with pytest.raises(discovery.TikTokAccountRefreshError):
        await discovery.refresh_tiktok_authorized_accounts(db, "owner", "token", "time")
    assert db.data["mezan_integration_accounts_v2"] == [original]
    assert not db.writes


def test_operational_catalog_auto_includes_new_tiktok_but_respects_saved_exclusions():
    accounts = [
        {"provider": "tiktok_ads", "mezan_integration_account_id": "old",
         "external_account_id": "100", "connection_status": "connected"},
        {"provider": "tiktok_ads", "mezan_integration_account_id": "new",
         "external_account_id": "200", "connection_status": "connected"},
    ]
    state = {"ad_catalog_selection": {"version": 3, "account_ids": ["old"],
                                       "known_tiktok_account_ids": ["old"]}}
    initial = {item["id"]: item for item in choices(accounts, state)}
    assert initial["old"]["selected"] is True
    assert initial["new"]["selected"] is True  # newly discovered
    state["ad_catalog_selection"]["known_tiktok_account_ids"].append("new")
    later = {item["id"]: item for item in choices(accounts, state)}
    assert later["new"]["selected"] is False  # explicit owner exclusion


@pytest.mark.asyncio
async def test_dashboard_reports_missing_advertiser_days_without_inventing_zero(monkeypatch):
    from dashboard_tiktok_coverage import tiktok_reporting_coverage
    rows = [
        {"ad_account_id": "100", "date": "2026-11-27",
         "spend_native": 10.0, "spend_sar": 37.5,
         "currency_native": "USD", "account_timezone": "Asia/Riyadh",
         "source_close_proof": {
             "complete": True, "complete_response": True,
             "explicit_spend_present": True, "account_id": "100",
             "business_date": "2026-11-27"}},
        {"ad_account_id": "200", "date": "2026-11-27",
         "spend_native": 20.0, "spend_sar": 20.0,
         "currency_native": "SAR", "account_timezone": "Asia/Riyadh",
         "source_close_proof": {
             "complete": True, "complete_response": True,
             "explicit_spend_present": True, "account_id": "200",
             "business_date": "2026-11-27"}},
    ]
    complete = tiktok_reporting_coverage(["100", "200"],
                                               "2026-11-27", "2026-11-27", rows)
    assert complete["coverage_complete"] is True
    assert complete["accounts_expected"] == 2
    incomplete = tiktok_reporting_coverage(["100", "200"],
                                                 "2026-11-27", "2026-11-27", rows[:1])
    assert incomplete["coverage_complete"] is False
    assert incomplete["missing_account_ids"] == ["200"]
    assert incomplete["missing_account_days"] == 1
    # An account with one valid day but another missing day is incomplete.
    span = tiktok_reporting_coverage(
        ["100", "200"], "2026-11-26", "2026-11-27",
        [{**row, "date": "2026-11-26",
          "source_close_proof": {
              **row["source_close_proof"], "business_date": "2026-11-26"}}
         for row in rows] + [rows[0]],
    )
    assert span["missing_account_ids"] == ["200"]
    assert span["missing_account_days"] == 1
    duplicate = tiktok_reporting_coverage(["100", "200"],
                                                "2026-11-27", "2026-11-27", rows + rows[:1])
    assert duplicate["coverage_complete"] is False


@pytest.mark.asyncio
async def test_reporting_rejects_cross_account_spend():
    class Response:
        status_code = 200

        def json(self):
            return {"code": 0, "data": {"list": [
                {"dimensions": {"advertiser_id": "another"},
                 "metrics": {"spend": "999"}}]}}

    class Client:
        async def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(reporting.TikTokReportingError) as exc:
        await reporting._fetch_day(
            Client(), "token", {"ad_account_id": "100"},
            datetime(2026, 11, 27, tzinfo=timezone.utc).date())
    assert exc.value.code == "tiktok_reporting_advertiser_mismatch"


@pytest.mark.asyncio
async def test_reporting_saves_two_accounts_once_and_replay_is_idempotent(monkeypatch):
    db = DB()
    db.data["mezan_tiktok_oauth_credentials_v2"] = [{
        "user_id": "owner", "provider": "tiktok_ads",
        "access_token_ciphertext": b"encrypted",
    }]
    db.data["mezan_integration_accounts_v2"] = [
        {"user_id": "owner", "provider": "tiktok_ads",
         "external_account_id": "100", "ad_account_id": "100",
         "display_name": "USD", "currency": "USD", "timezone": "Asia/Riyadh",
         "connection_status": "connected", "connection_provenance": "api_connection"},
        {"user_id": "owner", "provider": "tiktok_ads",
         "external_account_id": "200", "ad_account_id": "200",
         "display_name": "SAR", "currency": "SAR", "timezone": "Asia/Riyadh",
         "connection_status": "connected", "connection_provenance": "api_connection"},
    ]
    monkeypatch.setenv("TIKTOK_USD_TO_SAR_RATE", "3.75")
    monkeypatch.setattr(reporting, "tiktok_oauth_configured", lambda: True)
    monkeypatch.setattr(reporting, "tiktok_reporting_enabled", lambda: True)
    monkeypatch.setattr(reporting, "decrypt_tiktok_token", lambda *_: "token")

    async def authorized(*args):
        return {"status": "complete", "authorized_accounts": 2, "account_ids": ["100", "200"]}

    monkeypatch.setattr(reporting, "refresh_tiktok_authorized_accounts", authorized)

    class Response:
        status_code = 200

        def __init__(self, account):
            self.account = account

        def json(self):
            return {"code": 0, "data": {"list": [{
                "dimensions": {"advertiser_id": self.account},
                "metrics": {"spend": "10" if self.account == "100" else "20",
                            "impressions": "100", "clicks": "10", "conversion": "1"}
            }]}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return Response(kwargs["params"]["advertiser_id"])

    monkeypatch.setattr(reporting.httpx, "AsyncClient", Client)
    async def run():
        return await reporting.run_tiktok_reporting_sync(
            db, "owner", reporting.TikTokReportingSyncInput(days=1),
            now=lambda: datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
        )
    first = await run()
    second = await run()
    assert first["status"] == second["status"] == "complete"
    assert first["accounts_attempted"] == 2
    assert first["rows_saved"] == 2
    rows = db.data[reporting.TIKTOK_REPORTING_COLLECTION]
    assert len(rows) == 2
    assert {row["ad_account_id"] for row in rows} == {"100", "200"}
    assert sum(row["spend_sar"] for row in rows) == pytest.approx(57.5)
    assert not {"general_ledger", "ad_account_ledger", "qoyod_invoices"}.intersection(db.writes)
