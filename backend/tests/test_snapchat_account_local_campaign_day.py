"""DST, source granularity, idempotency and account-local campaign spend."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from integrations_control_center.snapchat_native_account_day import (
    HOUR_COLLECTION, ACCOUNT_DAY_COLLECTION, local_day_utc_range,
    sync_windows, normalize_hour_point, summarize_hours,
    persist_account_local_campaign_facts,
)


def test_local_day_uses_23_25_and_24_hour_real_intervals():
    ny = ZoneInfo("America/New_York")
    riyadh = ZoneInfo("Asia/Riyadh")
    spring = local_day_utc_range(date(2026, 3, 8), ny)
    fall = local_day_utc_range(date(2026, 11, 1), ny)
    sa = local_day_utc_range(date(2026, 11, 1), riyadh)
    assert spring[1] - spring[0] == timedelta(hours=23)
    assert fall[1] - fall[0] == timedelta(hours=25)
    assert sa[1] - sa[0] == timedelta(hours=24)
    assert spring[0].isoformat() == "2026-03-08T05:00:00+00:00"
    assert fall[0].isoformat() == "2026-11-01T04:00:00+00:00"


def test_new_york_request_expands_existing_riyadh_window_without_gaps():
    windows = sync_windows(date(2026, 11, 13), date(2026, 11, 13),
                           ZoneInfo("America/New_York"))
    assert windows[0][0].isoformat() == "2026-11-12T21:00:00+00:00"
    assert windows[-1][1].isoformat() == "2026-11-14T05:00:00+00:00"
    assert all(timedelta(0) < b-a <= timedelta(days=7) for a,b in windows)
    assert all(windows[i][1] == windows[i+1][0] for i in range(len(windows)-1))


def test_genuine_hour_only_no_fake_day_and_repeated_hour_not_lost():
    day = date(2026, 11, 1)
    start, end = local_day_utc_range(day, ZoneInfo("America/New_York"))
    items = []
    for n in range(25):
        hour = start + timedelta(hours=n)
        row = {"external_id": "campaign-1",
               "start_time": hour.isoformat(),
               "end_time": (hour + timedelta(hours=1)).isoformat(),
               "metrics": {"spend": 1_000_000}}
        parsed = normalize_hour_point(row, window_start=start, window_end=end)
        assert parsed
        cid, st, en, native = parsed
        items.append({"campaign_id": cid, "start": st, "end": en, "spend_native": native})
    summary = summarize_hours(items, ZoneInfo("America/New_York"), start=start, end=end)
    assert summary[("campaign-1", day.isoformat())]["spend_native"] == 25
    assert summary[("campaign-1", day.isoformat())]["count"] == 25
    invalid = {"external_id": "campaign-1", "start_time": start.isoformat(),
               "end_time": end.isoformat(), "metrics": {"spend": 25_000_000}}
    assert normalize_hour_point(invalid, window_start=start, window_end=end) is None


class MemoryCollection:
    def __init__(self, rows=None):
        self.rows = [] if rows is None else rows
        self.calls = 0
    async def bulk_write(self, operations, ordered=False):
        self.calls += 1
        for op in operations:
            key = op._filter
            update = op._doc["$set"]
            matching = next((row for row in self.rows if all(row.get(k) == v for k,v in key.items())), None)
            if matching is None:
                self.rows.append(dict(update))
            else:
                matching.update(update)
    async def find_one(self, *args, **kwargs):
        return self.rows[0] if self.rows else None


class MemoryDB:
    def __init__(self, fx=None):
        self.coll = {
            HOUR_COLLECTION: MemoryCollection(),
            ACCOUNT_DAY_COLLECTION: MemoryCollection(),
            "ads_currency_settings": MemoryCollection(
                [{"usd_to_sar_rate": fx}] if fx is not None else []),
        }
    def __getitem__(self, name):
        return self.coll.setdefault(name, MemoryCollection())


class MemoryContext:
    user_id = "owner-1"
    def __init__(self, db):
        self.db = db
    def now_iso(self):
        return "2026-11-13T12:00:00+00:00"


@pytest.mark.asyncio
async def test_hourly_upsert_idempotent_and_verified_sar_account_day():
    ny = ZoneInfo("America/New_York")
    day = date(2026, 11, 13)
    start, end = local_day_utc_range(day, ny)
    # The API has actual hour-level evidence from midnight to local 1 AM.
    points = [{
        "external_id": "ny-campaign", "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=1)).isoformat(),
        "metrics": {"spend": 12_500_000},
    }]
    db = MemoryDB(fx=3.752)
    context = MemoryContext(db)
    account = {"ad_account_id": "ny-account", "timezone": "America/New_York",
               "currency": "USD"}
    a = await persist_account_local_campaign_facts(
        context, account, points, windows=[(start, end)])
    b = await persist_account_local_campaign_facts(
        context, account, points + points, windows=[(start, end)])
    assert a == b == 2
    hours = db[HOUR_COLLECTION].rows
    days = db[ACCOUNT_DAY_COLLECTION].rows
    assert len(hours) == len(days) == 1
    assert hours[0]["spend_native"] == 12.5
    assert days[0]["spend_sar"] == round(12.5 * 3.752, 2)
    assert days[0]["account_timezone"] == "America/New_York"
    assert days[0]["coverage_status"] == "provisional_hourly"


@pytest.mark.asyncio
async def test_missing_fx_remains_unknown_but_native_spend_stays_present():
    ny = ZoneInfo("America/New_York")
    start,end = local_day_utc_range(date(2026,11,13),ny)
    row = {"external_id":"c","start_time":start.isoformat(),
           "end_time":(start+timedelta(hours=1)).isoformat(),
           "metrics":{"spend":2_000_000}}
    db=MemoryDB()
    await persist_account_local_campaign_facts(
        MemoryContext(db),
        {"ad_account_id":"ny","timezone":"America/New_York","currency":"USD"},
        [row], windows=[(start,end)])
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_sar"] is None
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_native"] == 2


@pytest.mark.asyncio
async def test_partial_window_cannot_create_false_account_day_total():
    ny = ZoneInfo("America/New_York")
    start,end = local_day_utc_range(date(2026,11,13),ny)
    point={"external_id":"c","start_time":start.isoformat(),
           "end_time":(start+timedelta(hours=1)).isoformat(),
           "metrics":{"spend":1_000_000}}
    db=MemoryDB(fx=3.75)
    await persist_account_local_campaign_facts(
        MemoryContext(db),
        {"ad_account_id":"ny","timezone":"America/New_York","currency":"USD"},
        [point], windows=[(start, start+timedelta(hours=3))])
    assert len(db[HOUR_COLLECTION].rows) == 1
    assert db[ACCOUNT_DAY_COLLECTION].rows == []
