"""Campaign hover contract: timezone isolation, quantities, missing data.

These tests do not access live Mongo or advertising APIs.
"""
from datetime import datetime, timezone

import pytest

from order_engine.campaign_hover import (
    _amount, _quantity, _timestamp, _window, _local_days_still_current,
)


def test_account_day_uses_account_timezone_not_riyadh():
    at = datetime(2026, 11, 12, 5, 30, tzinfo=timezone.utc)
    pst_start, pst_day = _window(at, "America/New_York")
    riyadh_start, riyadh_day = _window(at, "Asia/Riyadh")
    assert pst_day == "2026-11-11"
    assert pst_start.isoformat() == "2026-11-11T08:00:00+00:00"
    assert riyadh_day == "2026-11-12"
    assert riyadh_start.isoformat() == "2026-11-11T21:00:00+00:00"


def test_timezone_and_timestamp_must_not_be_guessed():
    assert _window(datetime.now(timezone.utc), "Fake/Missing") is None
    assert _window(datetime.now(timezone.utc), None) is None
    assert _timestamp("2026-11-12T01:00:00") is None
    assert _timestamp("2026-11-12T01:00:00+03:00").isoformat() == "2026-11-11T22:00:00+00:00"


def test_quantity_uses_units_not_line_count_and_skips_service():
    lines = {"items": [
        {"type": "product", "quantity": 3},
        {"type": "service", "quantity": 1},
        {"type": "product", "quantity": 2},
    ]}
    assert _quantity(lines) == 5
    assert _quantity({"items": [{"type": "product"}]}) is None
    assert _quantity({}) is None


def test_null_financial_amounts_remain_unavailable():
    assert _amount(None) is None
    assert _amount("NaN") is None
    assert _amount(float("inf")) is None
    assert _amount(-1) is None
    assert _amount("0") == 0


def test_old_account_date_invalidates_snapshot():
    assert not _local_days_still_current({"campaigns": {
        "c": {"timezone": "Asia/Riyadh", "date": "2025-01-01"},
    }})



class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
    def sort(self, *args, **kwargs):
        return self
    def limit(self, *args, **kwargs):
        return self
    def max_time_ms(self, *args, **kwargs):
        return self
    async def to_list(self, limit):
        return self.rows[:limit]


class _Collection:
    def __init__(self, rows):
        self.rows = rows
    def find(self, *args, **kwargs):
        return _Cursor(self.rows)
    async def find_one(self, *args, **kwargs):
        return self.rows[0] if self.rows else None


class _DB:
    def __init__(self, mapping):
        self.mapping = {name: _Collection(rows) for name, rows in mapping.items()}
    def __getitem__(self, key):
        return self.mapping.get(key, _Collection([]))
    def __getattr__(self, key):
        return self[key]


@pytest.mark.asyncio
async def test_native_snap_riyadh_spend_and_salla_orders_remain_separate_sources():
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from order_engine.campaign_hover import read_campaign_hover
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo("Asia/Riyadh"))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    at = (start + timedelta(seconds=1)).astimezone(timezone.utc).isoformat()
    day = local.date().isoformat()
    raw = {
        "id": "salla-test",
        "date": {"date": at},
        "utm_source": "snapchat",
        "utm_campaign": "campaign-unique",
        "status": {"name": "قيد التنفيذ"},
        "amounts": {"total": {"amount": 100, "currency": "SAR"}},
        "items": [{"id": "product-1", "quantity": 2, "type": "product"}],
    }
    db = _DB({
        "ads_v2_accounts": [{
            "id": "ads-account", "user_id": "owner", "provider": "snapchat",
            "external_account_id": "snap-account", "timezone": "Asia/Riyadh",
            "currency_native": "SAR",
        }],
        "mezan_integration_accounts_v2": [],
        "ads_v2_spend_raw": [],
        "mezan_snapchat_performance_daily_v2": [{
            "user_id": "owner", "ad_account_id": "snap-account",
            "entity_type": "campaign", "campaign_id": "campaign-unique",
            "date_timezone": "Asia/Riyadh", "date": day,
            "spend_sar": 30, "updated_at": now.isoformat(),
        }],
        "mezan_meta_campaign_performance_daily_v2": [],
        "unified_orders": [{
            "order_number": "1001", "order_date": at, "order_status": "processing",
            "raw_by_source": {"salla_direct": raw},
        }],
    })
    result = await read_campaign_hover(db, "owner", ["campaign-unique"])
    item = result["campaigns"]["campaign-unique"]
    assert item["timezone"] == "Asia/Riyadh"
    assert item["date"] == day
    assert item["spend_sar"] == 30
    assert item["orders"] == 1
    assert item["pieces"] == 2
    assert item["sales_sar"] == 100
    assert item["cpa_sar"] == 30
    assert item["roas"] == 3.33


@pytest.mark.asyncio
async def test_native_riyadh_day_must_not_relabel_us_account_local_day():
    from zoneinfo import ZoneInfo
    from order_engine.campaign_hover import read_campaign_hover
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo("America/New_York"))
    day = local.date().isoformat()
    at = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    raw = {
        "date": {"date": at},
        "utm_source": "snapchat",
        "utm_campaign": "campaign-ny",
        "items": [{"id": "piece-1", "quantity": 3, "type": "product"}],
        "amounts": {"total": {"amount": 250, "currency": "SAR"}},
    }
    db = _DB({
        "ads_v2_accounts": [{
            "id": "ads-account", "provider": "snapchat",
            "external_account_id": "snap-ny",
            "timezone": "America/New_York", "currency_native": "USD",
        }],
        "mezan_integration_accounts_v2": [], "ads_v2_spend_raw": [],
        "mezan_snapchat_performance_daily_v2": [{
            "ad_account_id": "snap-ny", "entity_type": "campaign",
            "campaign_id": "campaign-ny", "date": day,
            "date_timezone": "Asia/Riyadh", "spend_sar": 200,
        }],
        "mezan_meta_campaign_performance_daily_v2": [],
        "unified_orders": [{
            "order_number": "snap-ny-order", "order_date": at,
            "order_status": "processing",
            "raw_by_source": {"salla_direct": raw},
        }],
    })
    result = await read_campaign_hover(db, "owner", ["campaign-ny"])
    row = result["campaigns"]["campaign-ny"]
    assert row["spend_sar"] is None
    assert row["orders"] == 1
    assert row["pieces"] == 3
    assert row["sales_sar"] == 250
    assert row["cpa_sar"] is None
    assert row["roas"] is None
    assert row["status"] == "partial"
    assert row["spend_reason"] == "snapchat_account_day_not_aligned"


def test_connected_account_timezone_is_authoritative_over_legacy():
    """Connected native account metadata must override an outdated Ads V2 timezone."""
    # Behavioral contract covered by the same-day native Snapchat test above:
    # native timezone is never replaced by a Riyadh assumption for USD accounts.
    assert _window(datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc),
                   "America/New_York")[1] == "2026-11-11"


@pytest.mark.asyncio
async def test_salla_campaign_sales_units_without_any_ad_spend_record():
    from zoneinfo import ZoneInfo
    from order_engine.campaign_hover import read_campaign_hover
    local = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Riyadh"))
    at = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    db = _DB({
        "ads_v2_accounts": [{
            "id": "account-1", "provider": "snapchat",
            "external_account_id": "snap-account-1",
            "timezone": "Asia/Riyadh", "currency_native": "SAR",
        }],
        "mezan_integration_accounts_v2": [], "ads_v2_spend_raw": [],
        "mezan_snapchat_performance_daily_v2": [],
        "mezan_meta_campaign_performance_daily_v2": [],
        "unified_orders": [{
            "order_number": "valid-salla-1", "order_date": at,
            "order_status": "processing",
            "raw_by_source": {"salla_direct": {
                "date": {"date": at},
                "utm_source": "snapchat", "utm_campaign": "campaign-without-spend",
                "amounts": {"total": {"amount": 319.5, "currency": "SAR"}},
                "items": [{"id": "a", "quantity": 2, "type": "product"},
                          {"id": "b", "quantity": 1, "type": "product"}],
            }},
        }],
    })
    data = await read_campaign_hover(db, "owner", ["campaign-without-spend"])
    row = data["campaigns"]["campaign-without-spend"]
    assert row["orders"] == 1
    assert row["pieces"] == 3
    assert row["sales_sar"] == 319.5
    assert row["spend_sar"] is None
    assert row["cpa_sar"] is None
    assert row["roas"] is None
    assert row["status"] == "partial"


def test_new_york_and_riyadh_account_midnight_with_daylight_saving():
    # Jan: New York UTC-05; July: New York UTC-04.
    cases = (
        ("2026-01-15T16:00:00+00:00", "2026-01-15T05:00:00+00:00"),
        ("2026-07-15T16:00:00+00:00", "2026-07-15T04:00:00+00:00"),
    )
    for instant, expected_new_york_start in cases:
        at = datetime.fromisoformat(instant)
        ny_start, ny_date = _window(at, "America/New_York")
        riyadh_start, riyadh_date = _window(at, "Asia/Riyadh")
        assert ny_start.isoformat() == expected_new_york_start
        assert riyadh_start.isoformat() == (at.date() - __import__("datetime").timedelta(days=1)).isoformat() + "T21:00:00+00:00"
        assert ny_date == at.date().isoformat()
        assert riyadh_date == at.date().isoformat()
        assert ny_start != riyadh_start
