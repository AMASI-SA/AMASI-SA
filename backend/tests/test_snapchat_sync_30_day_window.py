"""The historical backfill is closed; sync only the latest 30 Riyadh dates."""
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    SnapchatNativeReadInput,
    enumerate_native_read_dates,
    enumerate_native_sync_dates,
)
from snapchat_v2.routes import SnapchatV2SyncInput, _read_days
from snapchat_v2.sync_pipeline import _date_range
from integrations_control_center.snapchat_campaign_report_routes import resolve_report_dates
from integrations_control_center.snapchat_account_timezone_manager import resolve_account_report_dates

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
TODAY = date(2026, 9, 17)


def test_exact_latest_30_dates_are_accepted_in_both_sync_paths():
    native = enumerate_native_sync_dates(SnapchatNativeSyncInput(days=30), today=TODAY)
    canonical = _date_range("2026-08-19", "2026-09-17", now=NOW)
    assert native == canonical
    assert len(native) == 30
    assert native[0] == date(2026, 8, 19)
    assert native[-1] == TODAY
    SnapchatV2SyncInput(date_from=native[0], date_to=native[-1])


@pytest.mark.parametrize("start,end", [
    ("2026-08-18", "2026-09-17"),  # 31 dates
    ("2026-08-01", "2026-09-17"),  # completed historical backfill
    ("2026-08-18", "2026-08-18"),  # short but outside rolling window
    ("2026-09-18", "2026-09-18"),  # future
])
def test_outside_window_is_rejected_before_sync(start, end):
    with pytest.raises(SnapchatNativeSyncError):
        enumerate_native_sync_dates(
            SnapchatNativeSyncInput(from_date=start, to_date=end), today=TODAY,
        )
    with pytest.raises(ValueError):
        _date_range(start, end, now=NOW)


def test_days_argument_and_canonical_input_reject_31():
    with pytest.raises(ValidationError):
        SnapchatNativeSyncInput(days=31)
    with pytest.raises(ValidationError):
        SnapchatV2SyncInput(date_from="2026-08-18", date_to="2026-09-17")


def test_riyadh_midnight_moves_the_window():
    just_before = datetime(2026, 9, 16, 20, 59, tzinfo=timezone.utc)
    midnight = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)
    assert len(_date_range("2026-08-18", "2026-09-16", now=just_before)) == 30
    with pytest.raises(ValueError):
        _date_range("2026-08-18", "2026-09-16", now=midnight)
    assert len(_date_range("2026-08-19", "2026-09-17", now=midnight)) == 30


def test_historical_saved_report_stays_readable():
    assert _read_days(date(2026, 8, 1), TODAY) == 48
    assert len(enumerate_native_read_dates(SnapchatNativeReadInput(days=48), today=TODAY)) == 48
    assert len(resolve_report_dates("2026-08-01", "2026-09-17", today=TODAY)) == 48
    assert len(resolve_account_report_dates("2026-08-01", "2026-09-17", timezone_name="America/Los_Angeles", now=NOW)) == 48
