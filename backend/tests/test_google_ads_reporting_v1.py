from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import google_ads_reporting as module


class FakeUpdateResult:
    modified_count = 1


class FakeCollection:
    def __init__(self):
        self.updates = []

    async def update_one(self, query, update, upsert=False):
        self.updates.append(
            {
                "query": deepcopy(query),
                "update": deepcopy(update),
                "upsert": upsert,
            }
        )
        return FakeUpdateResult()


class FakeDB:
    def __init__(self):
        self.collections = {}
        self.mezan_integration_accounts_v2 = FakeCollection()
        self.mezan_integrations_v2 = FakeCollection()

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.mark.asyncio
async def test_google_ads_reporting_persists_real_hourly_and_daily_cost(monkeypatch):
    db = FakeDB()
    hourly_rows = []

    async def ensure_indexes(_db):
        return None

    async def credential(_db, user_id, now):
        assert user_id == "owner-1"
        return "access-token"

    async def accounts(_db, user_id):
        return [
            {
                "ad_account_id": "1234567890",
                "display_name": "AMASI Google Ads",
                "currency": "USD",
                "timezone": "Asia/Riyadh",
            }
        ]

    async def metadata(client, *, access_token, account):
        return {
            "display_name": "AMASI Google Ads",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "manager": False,
        }

    async def search_stream(client, *, access_token, account, query):
        assert "segments.hour" in query
        assert "metrics.cost_micros" in query
        return [
            {
                "segments": {"date": "2026-08-05", "hour": 2},
                "metrics": {
                    "costMicros": 1_000_000,
                    "impressions": 100,
                    "clicks": 10,
                    "conversions": 2,
                    "conversionsValue": 20,
                },
            },
            {
                "segments": {"date": "2026-08-05", "hour": 3},
                "metrics": {
                    "costMicros": 500_000,
                    "impressions": 50,
                    "clicks": 5,
                    "conversions": 1,
                    "conversionsValue": 10,
                },
            },
        ]

    async def upsert_hour(_db, **kwargs):
        hourly_rows.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "google_oauth_configured", lambda: True)
    monkeypatch.setattr(module, "google_ads_reporting_enabled", lambda: True)
    monkeypatch.setattr(module, "ensure_google_ads_reporting_indexes", ensure_indexes)
    monkeypatch.setattr(module, "_credential", credential)
    monkeypatch.setattr(module, "_accounts", accounts)
    monkeypatch.setattr(module, "_account_metadata", metadata)
    monkeypatch.setattr(module, "_search_stream", search_stream)
    monkeypatch.setattr(module, "upsert_platform_hour", upsert_hour)

    result = await module.run_google_ads_reporting_sync(
        db,
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-05",
        now=lambda: datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["accounts_attempted"] == 1
    assert result["rows_saved"] == 1
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False

    assert len(hourly_rows) == 24
    hour_two = next(row for row in hourly_rows if row["hour_index"] == 2)
    hour_three = next(row for row in hourly_rows if row["hour_index"] == 3)
    assert hour_two["provider"] == "google"
    assert hour_two["spend_native"] == 1.0
    assert hour_two["spend_sar"] == 3.75
    assert hour_two["impressions"] == 100
    assert hour_three["spend_native"] == 0.5
    assert hour_three["spend_sar"] == 1.88

    daily_updates = db[module.GOOGLE_ADS_DAILY_COLLECTION].updates
    assert len(daily_updates) == 1
    daily = daily_updates[0]["update"]["$set"]
    assert daily["provider"] == "google_ads"
    assert daily["spend_native"] == 1.5
    assert daily["spend_sar"] == 5.62
    assert daily["impressions"] == 150
    assert daily["clicks"] == 15
    assert daily["conversions"] == 3.0
    assert daily["accounting_eligible"] is False
    assert daily["provider_write_reached"] is False
    assert daily["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_google_ads_manager_account_is_not_reported_as_spend(monkeypatch):
    db = FakeDB()

    async def noop(*args, **kwargs):
        return None

    async def credential(*args, **kwargs):
        return "access-token"

    async def accounts(*args, **kwargs):
        return [{"ad_account_id": "111", "currency": "SAR"}]

    async def metadata(*args, **kwargs):
        return {
            "display_name": "Manager",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "manager": True,
        }

    monkeypatch.setattr(module, "google_oauth_configured", lambda: True)
    monkeypatch.setattr(module, "google_ads_reporting_enabled", lambda: True)
    monkeypatch.setattr(module, "ensure_google_ads_reporting_indexes", noop)
    monkeypatch.setattr(module, "_credential", credential)
    monkeypatch.setattr(module, "_accounts", accounts)
    monkeypatch.setattr(module, "_account_metadata", metadata)

    result = await module.run_google_ads_reporting_sync(
        db,
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-05",
    )

    assert result["status"] == "failed"
    assert result["rows_saved"] == 0
    assert result["error_samples"][0]["code"] == (
        "google_ads_manager_account_requires_client_selection"
    )
    assert db[module.GOOGLE_ADS_DAILY_COLLECTION].updates == []
