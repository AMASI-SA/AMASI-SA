"""Real coverage, New York DST, tenant isolation and saved FX contracts."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from integrations_control_center.snapchat_native_account_day import (
    HOUR_COLLECTION, ACCOUNT_DAY_COLLECTION, local_day_utc_range, sync_windows,
    normalize_hour_point, summarize_hours, required_local_date_range,
    persist_account_local_campaign_facts,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 13)
START, END = local_day_utc_range(DAY, NY)
NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)
ACCOUNT = {"ad_account_id": "ny-account", "timezone": "America/New_York", "currency": "USD"}


def point(hour, micro=0, campaign="ny-campaign"):
    start = START + timedelta(hours=hour)
    return {"external_id": campaign, "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(), "metrics": {"spend": micro}}


def dense_points():
    return [point(0, 12_500_000), *[point(h) for h in range(1, 7)]]


class MemoryCollection:
    def __init__(self, rows=None):
        self.rows = [] if rows is None else rows
        self.calls = 0
    async def bulk_write(self, operations, ordered=False):
        self.calls += 1
        for op in operations:
            key = op._filter
            value = op._doc["$set"]
            row = next((r for r in self.rows if all(r.get(k) == v for k, v in key.items())), None)
            if row is None:
                self.rows.append(dict(value))
            else:
                row.update(value)
    async def find_one(self, *args, **kwargs):
        return self.rows[0] if self.rows else None


class MemoryDB:
    def __init__(self, fx=3.752):
        self.coll = {"ads_currency_settings": MemoryCollection(
            [{"usd_to_sar_rate": fx}] if fx is not None else [])}
    def __getitem__(self, name):
        return self.coll.setdefault(name, MemoryCollection())


class MemoryContext:
    user_id = "owner-1"
    def __init__(self, db, now=NOW):
        self.db, self.now = db, now
    def now_iso(self):
        return self.now.isoformat()


async def save(points, db=None, **kwargs):
    db = db or MemoryDB()
    count = await persist_account_local_campaign_facts(MemoryContext(db), ACCOUNT, points,
                                                      windows=[(START, END)], **kwargs)
    return db, count


def test_new_york_dst_and_riyadh_utc_intervals():
    spring = local_day_utc_range(date(2026, 3, 8), NY)
    fall = local_day_utc_range(date(2026, 11, 1), NY)
    sa = local_day_utc_range(date(2026, 11, 1), ZoneInfo("Asia/Riyadh"))
    assert spring[1] - spring[0] == timedelta(hours=23)
    assert fall[1] - fall[0] == timedelta(hours=25)
    assert sa[1] - sa[0] == timedelta(hours=24)
    assert spring[0].isoformat() == "2026-03-08T05:00:00+00:00"
    assert fall[0].isoformat() == "2026-11-01T04:00:00+00:00"


def test_windows_cover_riyadh_and_new_york_and_never_exceed_seven_days():
    windows = sync_windows(DAY, DAY + timedelta(days=8), NY)
    assert windows[0][0].isoformat() == "2026-11-12T21:00:00+00:00"
    assert windows[-1][1].isoformat() == "2026-11-22T05:00:00+00:00"
    assert all(timedelta(0) < b-a <= timedelta(days=7) for a, b in windows)
    assert all(windows[i][1] == windows[i+1][0] for i in range(len(windows)-1))


def test_riyadh_midnight_retains_still_active_new_york_date():
    now = datetime(2026, 11, 12, 22, tzinfo=UTC)
    assert required_local_date_range(DAY, DAY, NY, now=now) == (date(2026, 11, 12), DAY)
    assert required_local_date_range(date(2026, 11, 1), date(2026, 11, 2), NY, now=now) == (date(2026, 11, 1), date(2026, 11, 2))


def test_fall_back_repeated_clock_hour_is_two_distinct_utc_hours():
    start, end = local_day_utc_range(date(2026, 11, 1), NY)
    rows = [{"campaign_id": "c", "start": start + timedelta(hours=h),
             "end": start + timedelta(hours=h+1), "spend_native": 1} for h in range(25)]
    row = summarize_hours(rows, NY, start=start, end=end, as_of=end)[("c", "2026-11-01")]
    assert row["spend_native"] == row["hours_expected"] == row["count"] == 25
    assert row["coverage_complete"] is True


def test_daily_or_naive_timestamp_is_not_an_hour():
    fake = point(0)
    fake["end_time"] = END.isoformat()
    assert normalize_hour_point(fake, window_start=START, window_end=END) is None
    fake = point(0)
    fake["start_time"] = "2026-11-13T05:00:00"
    assert normalize_hour_point(fake, window_start=START, window_end=END) is None


@pytest.mark.asyncio
async def test_dense_hours_are_idempotent_and_use_saved_fx_once():
    db, count = await save(dense_points())
    assert count == 8
    await save(dense_points() * 2, db)
    assert len(db[HOUR_COLLECTION].rows) == 7
    assert len(db[ACCOUNT_DAY_COLLECTION].rows) == 1
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["spend_native"] == 12.5
    assert row["spend_sar"] == 46.9
    assert row["coverage_complete"] is True
    assert row["spend_evidence_valid"] is True
    assert row["missing_hours"] == 0
    assert row["coverage_end_utc"] == NOW.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [0, 3, 6])
async def test_missing_first_middle_or_last_hour_keeps_observed_spend_with_warning(missing):
    db, _ = await save([p for i, p in enumerate(dense_points()) if i != missing])
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["coverage_complete"] is False
    assert row["missing_hours"] == 1
    assert row["spend_evidence_valid"] is True
    assert row["coverage_status"] == "incomplete_hourly"
    assert row["hours_observed"] == 6
    assert len(db[HOUR_COLLECTION].rows) == 6
    assert row["spend_native"] == (0 if missing == 0 else 12.5)
    assert row["spend_sar"] == (0 if missing == 0 else 46.9)


@pytest.mark.asyncio
async def test_single_valid_hour_is_usable_without_filling_missing_hours():
    db, _ = await save([point(0, 12_500_000)])
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["spend_native"] == 12.5 and row["spend_sar"] == 46.9
    assert row["missing_hours"] == 6 and row["hours_observed"] == 1
    assert row["coverage_complete"] is False and row["spend_evidence_valid"] is True
    assert len(db[HOUR_COLLECTION].rows) == 1
    assert row["accounting_eligible"] is False


@pytest.mark.asyncio
async def test_explicit_dense_zero_is_zero_not_unknown():
    db, _ = await save([point(h) for h in range(7)])
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["coverage_complete"] is True
    assert row["spend_sar"] == 0


@pytest.mark.asyncio
async def test_no_fx_retains_native_but_never_guesses_sar():
    db, _ = await save(dense_points(), MemoryDB(fx=None))
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["spend_native"] == 12.5
    assert row["spend_sar"] is None


@pytest.mark.asyncio
async def test_partial_hours_without_fx_keep_native_not_invented_sar():
    db, _ = await save([point(0, 12_500_000)], MemoryDB(fx=None))
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["spend_native"] == 12.5 and row["spend_sar"] is None
    assert row["coverage_complete"] is False and row["spend_evidence_valid"] is True


@pytest.mark.asyncio
async def test_future_points_do_not_inflate_todays_total():
    db, _ = await save([*dense_points(), point(8, 100_000_000)])
    assert len(db[HOUR_COLLECTION].rows) == 7
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_native"] == 12.5


@pytest.mark.asyncio
async def test_no_observed_hours_does_not_fabricate_a_zero_snapshot():
    db, count = await save([point(8, 100_000_000)])
    assert count == 0
    assert db[HOUR_COLLECTION].rows == db[ACCOUNT_DAY_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_conflicting_duplicate_and_invalid_spend_fail_closed():
    db, _ = await save([*dense_points(), point(0, 9_000_000)])
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_sar"] is None
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_evidence_valid"] is False
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["coverage_status"] == "invalid_hourly"
    bad = point(3)
    bad["metrics"]["spend"] = None
    db, _ = await save([*dense_points(), bad])
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_sar"] is None
    assert db[ACCOUNT_DAY_COLLECTION].rows[0]["spend_evidence_valid"] is False


@pytest.mark.asyncio
async def test_failed_provider_response_must_not_publish_projection():
    db, count = await save(dense_points(), source_complete=False)
    assert count == 0
    assert db[HOUR_COLLECTION].rows == db[ACCOUNT_DAY_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_requested_partial_window_or_gap_cannot_claim_account_day():
    db = MemoryDB()
    await persist_account_local_campaign_facts(MemoryContext(db), ACCOUNT, dense_points()[:3],
                                              windows=[(START, START + timedelta(hours=3))])
    assert len(db[HOUR_COLLECTION].rows) == 3
    assert db[ACCOUNT_DAY_COLLECTION].rows == []
    db = MemoryDB()
    count = await persist_account_local_campaign_facts(MemoryContext(db), ACCOUNT, dense_points(),
        windows=[(START, START + timedelta(hours=2)), (START + timedelta(hours=3), END)])
    assert count == 0


@pytest.mark.asyncio
async def test_partial_current_hour_is_labelled_provisional_and_not_future():
    now = NOW + timedelta(minutes=15)
    db = MemoryDB()
    await persist_account_local_campaign_facts(MemoryContext(db, now), ACCOUNT,
        [*dense_points(), point(7, 1_000_000)], windows=[(START, END)])
    row = db[ACCOUNT_DAY_COLLECTION].rows[0]
    assert row["coverage_complete"] is True
    assert row["coverage_end_utc"] == now.isoformat()
    assert row["coverage_status"] == "provisional_hourly"
    assert row["spend_native"] == 13.5
