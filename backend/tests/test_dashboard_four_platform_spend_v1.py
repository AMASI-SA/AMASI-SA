from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import dashboard_ads_platform_spend_routes as module
from integrations_control_center.ads_platform_hourly import local_hour_start_utc


def _path_value(document, dotted):
    value = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document, query):
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        value, exists = _path_value(document, key)
        if not isinstance(condition, dict):
            if not exists or value != condition:
                return False
            continue
        for operator, expected in condition.items():
            if operator == "$in":
                if not exists or value not in expected:
                    return False
            elif operator == "$gte":
                if not exists or value < expected:
                    return False
            elif operator == "$lte":
                if not exists or value > expected:
                    return False
            elif operator == "$lt":
                if not exists or value >= expected:
                    return False
            elif operator == "$exists":
                if exists is not bool(expected):
                    return False
            elif operator == "$ne":
                if exists and value == expected:
                    return False
            else:
                raise AssertionError(f"unsupported operator: {operator}")
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    async def to_list(self, length):
        return deepcopy(self.rows[:length])

    def sort(self, key, direction=None):
        field, order = (key[0] if isinstance(key, list) else (key, direction))
        self.rows.sort(
            key=lambda row: _path_value(row, field)[0] or "",
            reverse=order == -1,
        )
        return self

    def limit(self, length):
        self.rows = self.rows[:length]
        return self


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None


class FakeDB:
    def __init__(self, collections):
        self.collections = {
            name: FakeCollection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_tiktok_make_ledger_fills_only_dates_without_native_reporting():
    db = FakeDB(
        {
            "counterparties": [
                {
                    "user_id": "owner-1",
                    "kind": "ad_account",
                    "ad_provider": "tiktok",
                    "id": "tiktok-account-1",
                }
            ],
            "ad_account_ledger": [
                {
                    "user_id": "owner-1",
                    "counterparty_id": "tiktok-account-1",
                    "type": "spend",
                    "date": "2026-08-24",
                    "amount": 99.0,
                },
                {
                    "user_id": "owner-1",
                    "counterparty_id": "tiktok-account-1",
                    "type": "spend",
                    "date": "2026-08-25",
                    "amount": 808.26,
                },
                {
                    "user_id": "owner-1",
                    "counterparty_id": "tiktok-account-1",
                    "type": "spend",
                    "date": "2026-08-25",
                    "amount": 284.45,
                },
            ],
            "mezan_tiktok_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "tiktok_ads",
                    "date": "2026-08-24",
                    "spend_sar": 50.0,
                }
            ],
        }
    )

    daily, facts = await module._daily_spend(
        db,
        "owner-1",
        date(2026, 8, 24),
        date(2026, 8, 25),
        {"daily_sar": {}, "quality": {}},
    )

    assert daily[0]["tiktok"] == 50.0
    assert daily[1]["tiktok"] == 1092.71
    assert facts["tiktok"] is True



def _connected(provider):
    return {
        "user_id": "owner-1",
        "provider": provider,
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "data_quality": "complete",
        "last_sync_at": "2026-08-05T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_builds_selected_four_platform_daily_and_riyadh_hourly_spend(monkeypatch):
    async def canonical_snapchat(*_args, **_kwargs):
        return {
            "rows": [],
            "daily_sar": {"2026-08-05": 100.0},
            "daily_state": {"2026-08-05": "confirmed_data"},
            "hourly_sar": {
                "2026-08-05": [{
                    "hour_index": 0,
                    "hour": "00:00",
                    "spend_sar": 10.0,
                    "status": "confirmed",
                }],
            },
            "total_sar": 100.0,
            "bank_commissions": {},
            "quality": {
                "status": "complete",
                "data_state": "confirmed_data",
                "coverage_complete": True,
                "amount_complete": True,
                "complete": True,
                "connected": True,
                "reason_codes": [],
            },
        }

    monkeypatch.setattr(
        module,
        "load_unified_marketing_dashboard_spend",
        canonical_snapchat,
    )
    one_am = local_hour_start_utc(
        __import__("datetime").date(2026, 8, 5),
        1,
        "Asia/Riyadh",
    ).isoformat(timespec="seconds")

    db = FakeDB(
        {
            "mezan_integrations_v2": [
                _connected("snapchat_ads"),
                _connected("meta_ads"),
                _connected("tiktok_ads"),
                _connected("google_ads"),
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-selected",
                },
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "connection_status": "connected",
                    "mezan_selected": False,
                    "ad_account_id": "snap-unselected",
                },
            ],
            "mezan_snapchat_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-selected",
                    "entity_type": "ad_account",
                    "date": "2026-08-05",
                    "spend_sar": 100,
                },
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-selected",
                    "entity_type": "campaign",
                    "date": "2026-08-05",
                    "spend_sar": 500,
                },
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "snap-unselected",
                    "entity_type": "ad_account",
                    "date": "2026-08-05",
                    "spend_sar": 999,
                },
            ],
            "mezan_meta_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "meta_ads",
                    "date": "2026-08-05",
                    "spend_sar": 20,
                }
            ],
            "mezan_tiktok_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "tiktok_ads",
                    "date": "2026-08-05",
                    "spend_sar": 5,
                }
            ],
            "mezan_google_ads_performance_daily_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_ads",
                    "date": "2026-08-05",
                    "spend_sar": 7.5,
                }
            ],
            "mezan_ads_platform_hourly_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "meta",
                    "hour_start_utc": one_am,
                    "spend_sar": 2,
                },
                {
                    "user_id": "owner-1",
                    "provider": "tiktok",
                    "hour_start_utc": one_am,
                    "spend_sar": 1,
                },
                {
                    "user_id": "owner-1",
                    "provider": "google",
                    "hour_start_utc": one_am,
                    "spend_sar": 0.5,
                },
            ],
        }
    )

    result = await module.build_dashboard_platform_spend(
        db,
        "owner-1",
        date_from="2026-08-05",
        date_to="2026-08-05",
    )

    assert result["chart_granularity"] == "hour"
    assert result["daily_spend"] == [
        {
            "date": "2026-08-05",
            "snapchat": 100.0,
            "meta": 20.0,
            "tiktok": 5.0,
            "google": 7.5,
        }
    ]
    assert result["provider_totals_sar"] == {
        "snapchat": 100.0,
        "meta": 20.0,
        "tiktok": 5.0,
        "google": 7.5,
    }
    assert result["total_sar"] == 132.5
    assert result["hourly_spend"][0]["snapchat"] == 10.0
    assert result["hourly_spend"][0]["snapchat_status"] == "confirmed"
    assert result["hourly_spend"][1]["meta"] == 2.0
    assert result["hourly_spend"][1]["tiktok"] == 1.0
    assert result["hourly_spend"][1]["google"] == 0.5
    assert all(result["providers"][provider]["connected"] for provider in (
        "snapchat",
        "meta",
        "tiktok",
        "google",
    ))
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert "booked_ad_expense_sar" not in str(result)


@pytest.mark.asyncio
async def test_multi_day_read_supports_the_existing_ninety_day_dashboard_window():
    db = FakeDB(
        {
            "mezan_integrations_v2": [],
            "mezan_integration_accounts_v2": [],
        }
    )
    result = await module.build_dashboard_platform_spend(
        db,
        "owner-1",
        date_from="2026-05-08",
        date_to="2026-08-05",
    )

    assert result["chart_granularity"] == "day"
    assert len(result["daily_spend"]) == 90
    assert result["hourly_spend"] == []
    assert result["total_sar"] == 0


def test_account_local_hour_is_normalized_to_riyadh_reader_utc():
    point = local_hour_start_utc(
        __import__("datetime").date(2026, 8, 5),
        0,
        "Asia/Riyadh",
    )
    assert point == datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)
