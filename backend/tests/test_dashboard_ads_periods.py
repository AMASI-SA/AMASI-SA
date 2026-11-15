"""Half-day reporting requires source time evidence and Riyadh boundaries."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from dashboard_ads_periods import (
    SALLA_ORDER_TIMESTAMP_PROJECTION,
    dashboard_day_segments,
    order_riyadh_timestamp,
)


DAY = "2026-11-15"


def native_order(value, **raw_changes):
    return {"raw_by_source": {"salla_direct": {"date": value, **raw_changes}}}


@pytest.mark.parametrize(("value", "expected"), [
    ({"date": "2026-11-15 11:59:59", "timezone": "Asia/Riyadh"}, "2026-11-15T11:59:59+03:00"),
    ("2026-11-15T09:00:00Z", "2026-11-15T12:00:00+03:00"),
    ("2026-11-14T22:15:00+00:00", "2026-11-15T01:15:00+03:00"),
    ({"value": "2026-11-15T00:00:00+03:00", "timezone": "UTC"}, "2026-11-15T00:00:00+03:00"),
    (datetime(2026, 11, 15, 9, tzinfo=timezone.utc), "2026-11-15T12:00:00+03:00"),
])
def test_source_timestamp_preserves_offsets_and_precise_midnight(value, expected):
    assert order_riyadh_timestamp(native_order(value)).isoformat() == expected


@pytest.mark.parametrize("value", [
    "2026-11-15",
    {"date": "2026-11-15", "timezone": "Asia/Riyadh"},
    date(2026, 11, 15),
    "2026-11-15 11:59:59",
    datetime(2026, 11, 15, 11, 59, 59),
    {"date": "2026-11-15 11:59:59", "timezone": "Invalid/Timezone"},
    "2026-11-15T25:00:00+03:00",
    "not-a-date",
    1794733200,
    None,
])
def test_date_only_naive_and_invalid_source_times_remain_unknown(value):
    assert order_riyadh_timestamp(native_order(value)) is None


def test_native_date_precedes_native_created_at_and_normalized_fallback():
    order = native_order(
        {"date": "2026-11-15 11:59:59", "timezone": "Asia/Riyadh"},
        created_at="2026-11-15T13:00:00+03:00",
    )
    order["order_date_raw"] = "2026-11-15T14:00:00+03:00"
    original = deepcopy(order)
    assert order_riyadh_timestamp(order).hour == 11
    assert order == original
    order["raw_by_source"]["salla_direct"]["date"] = "2026-11-15"
    assert order_riyadh_timestamp(order).hour == 13
    order["raw_by_source"]["salla_direct"]["created_at"] = "invalid"
    assert order_riyadh_timestamp(order).hour == 14


def test_normalized_fallback_requires_aware_time_and_ignores_generic_timestamps():
    order = {"order_date": DAY, "created_at": "2026-11-15T08:00:00Z",
             "updated_at": "2026-11-15T09:00:00Z", "order_date_raw": "2026-11-15 10:00:00"}
    assert order_riyadh_timestamp(order) is None
    order["order_date_raw"] = "2026-11-15T10:00:00+03:00"
    assert order_riyadh_timestamp(order).hour == 10


def test_projection_only_adds_the_two_source_timestamp_paths():
    assert SALLA_ORDER_TIMESTAMP_PROJECTION == {
        "raw_by_source.salla_direct.date": 1,
        "raw_by_source.salla_direct.created_at": 1,
    }


@pytest.mark.parametrize(("now", "statuses", "cutoffs"), [
    ("2026-11-15T00:00:00+03:00", ("not_started", "not_started"),
     ("2026-11-15T00:00:00+03:00", "2026-11-15T12:00:00+03:00")),
    ("2026-11-15T11:59:59+03:00", ("active", "not_started"),
     ("2026-11-15T11:59:59+03:00", "2026-11-15T12:00:00+03:00")),
    ("2026-11-15T09:00:00+00:00", ("completed", "not_started"),
     ("2026-11-15T12:00:00+03:00", "2026-11-15T12:00:00+03:00")),
    ("2026-11-15T18:40:00+03:00", ("completed", "active"),
     ("2026-11-15T12:00:00+03:00", "2026-11-15T18:40:00+03:00")),
    ("2026-11-16T00:00:00+03:00", ("completed", "completed"),
     ("2026-11-15T12:00:00+03:00", "2026-11-16T00:00:00+03:00")),
    ("2026-11-14T21:00:00+03:00", ("not_started", "not_started"),
     ("2026-11-15T00:00:00+03:00", "2026-11-15T12:00:00+03:00")),
])
def test_segment_clock_is_clamped_without_replacing_nominal_boundaries(now, statuses, cutoffs):
    rows = dashboard_day_segments(DAY, DAY, now=datetime.fromisoformat(now))
    assert [row["id"] for row in rows] == ["morning", "afternoon"]
    assert tuple(row["status"] for row in rows) == statuses
    assert tuple(row["cutoff_at"] for row in rows) == cutoffs
    assert [row["start_at"] for row in rows] == ["2026-11-15T00:00:00+03:00", "2026-11-15T12:00:00+03:00"]
    assert [row["end_at"] for row in rows] == ["2026-11-15T12:00:00+03:00", "2026-11-16T00:00:00+03:00"]
    for row in rows:
        assert row["date_from"] == row["date_to"] == DAY
        assert row["timezone"] == "Asia/Riyadh"
        assert row["reason_codes"] == []


@pytest.mark.parametrize(("start", "end", "reason"), [
    (None, DAY, "missing_date_range"),
    (DAY, "", "missing_date_range"),
    ("2026-02-30", "2026-02-30", "invalid_date_range"),
    ("20261115", "20261115", "invalid_date_range"),
    ("2026-11-16", DAY, "invalid_date_range"),
    ("2026-11-14", DAY, "single_day_required"),
])
def test_unusable_date_scopes_never_invent_half_day_ranges(start, end, reason):
    rows = dashboard_day_segments(start, end, now=datetime(2026, 11, 15, tzinfo=timezone.utc))
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "unavailable"
        assert row["reason_codes"] == [reason]
        assert row["start_at"] is row["end_at"] is row["cutoff_at"] is None


def test_naive_clock_is_unavailable_instead_of_assuming_a_timezone():
    rows = dashboard_day_segments(DAY, DAY, now=datetime(2026, 11, 15, 11))
    assert all(row["status"] == "unavailable" for row in rows)
    assert all(row["reason_codes"] == ["clock_timezone_missing"] for row in rows)
