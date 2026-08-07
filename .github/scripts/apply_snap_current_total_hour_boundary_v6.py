from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:200]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


platform = "backend/integrations_control_center/snapchat_platform_source_integrity.py"

replace_once(
    platform,
    '''PLATFORM_TOTAL_SOURCE_MODE = (\n    f"{ADS_MANAGER_SOURCE_MODE}:account_spend_campaign_commercial_v4"\n)\n''',
    '''PLATFORM_TOTAL_SOURCE_MODE = (\n    f"{ADS_MANAGER_SOURCE_MODE}:account_spend_campaign_completed_hour_v6"\n)\nTOTAL_CURRENT_DAY_WINDOW_POLICY = "completed_account_local_hour"\n''',
)

replace_once(
    platform,
    '''    end = nominal_end if report_date < current.date() else min(\n        nominal_end,\n        current.replace(microsecond=0),\n    )\n    return (start, end) if end > start else None\n''',
    '''    if report_date < current.date():\n        end = nominal_end\n    else:\n        # Production evidence showed that Snapchat rejects account TOTAL\n        # requests ending at an open, second-level timestamp. Use only the\n        # latest fully completed account-local hour. The HOUR ingestion remains\n        # responsible for the live chart while TOTAL owns stable commercial\n        # campaign metrics.\n        completed_hour_end = current.replace(\n            minute=0,\n            second=0,\n            microsecond=0,\n        )\n        end = min(nominal_end, completed_hour_end)\n    return (start, end) if end > start else None\n''',
)

replace_once(
    platform,
    '''        "account_commercial_totals_source": "complete_campaign_breakdown_sum",\n        "request_windows": request_windows,\n''',
    '''        "account_commercial_totals_source": "complete_campaign_breakdown_sum",\n        "current_day_total_window_policy": TOTAL_CURRENT_DAY_WINDOW_POLICY,\n        "request_windows": request_windows,\n''',
)

replace_once(
    platform,
    '''        "account_commercial_totals_source": "complete_campaign_breakdown_sum",\n        "salla_metrics_applied_to_platform": False,\n''',
    '''        "account_commercial_totals_source": "complete_campaign_breakdown_sum",\n        "current_day_total_window_policy": TOTAL_CURRENT_DAY_WINDOW_POLICY,\n        "salla_metrics_applied_to_platform": False,\n''',
)

replace_once(
    platform,
    '''    "PLATFORM_TOTAL_SOURCE_MODE",\n    "REQUIRED_ACCOUNT_TOTAL_FIELDS",\n''',
    '''    "PLATFORM_TOTAL_SOURCE_MODE",\n    "REQUIRED_ACCOUNT_TOTAL_FIELDS",\n    "TOTAL_CURRENT_DAY_WINDOW_POLICY",\n''',
)

platform_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
replace_once(
    platform_test,
    '''def test_account_local_total_window_uses_account_midnight_and_current_second():\n    start, end = account_local_total_window(\n        date(2026, 8, 6),\n        timezone_name="America/Los_Angeles",\n        now=datetime(2026, 8, 6, 15, 30, 45, tzinfo=timezone.utc),\n    )\n    assert start.isoformat() == "2026-08-06T00:00:00-07:00"\n    assert end.isoformat() == "2026-08-06T08:30:45-07:00"\n\n\n''',
    '''def test_account_local_total_window_uses_latest_completed_account_hour():\n    start, end = account_local_total_window(\n        date(2026, 8, 6),\n        timezone_name="America/Los_Angeles",\n        now=datetime(2026, 8, 6, 15, 30, 45, tzinfo=timezone.utc),\n    )\n    assert start.isoformat() == "2026-08-06T00:00:00-07:00"\n    assert end.isoformat() == "2026-08-06T08:00:00-07:00"\n    assert end.minute == 0\n    assert end.second == 0\n    assert end.microsecond == 0\n\n\ndef test_account_local_total_window_uses_full_boundary_for_past_day():\n    start, end = account_local_total_window(\n        date(2026, 8, 5),\n        timezone_name="America/Los_Angeles",\n        now=datetime(2026, 8, 6, 15, 30, 45, tzinfo=timezone.utc),\n    )\n    assert start.isoformat() == "2026-08-05T00:00:00-07:00"\n    assert end.isoformat() == "2026-08-06T00:00:00-07:00"\n\n\ndef test_account_local_total_window_waits_until_first_hour_completes():\n    assert account_local_total_window(\n        date(2026, 8, 6),\n        timezone_name="America/Los_Angeles",\n        now=datetime(2026, 8, 6, 7, 30, 0, tzinfo=timezone.utc),\n    ) is None\n\n\n''',
)

if "test_total_source_mode_marks_completed_hour_policy" not in Path(platform_test).read_text(encoding="utf-8"):
    text = Path(platform_test).read_text(encoding="utf-8")
    marker = '''def test_refresh_dates_cover_account_days_touched_by_riyadh_window():\n'''
    addition = '''def test_total_source_mode_marks_completed_hour_policy():\n    assert PLATFORM_TOTAL_SOURCE_MODE.endswith(\n        "account_spend_campaign_completed_hour_v6"\n    )\n\n\n''' + marker
    if marker not in text:
        raise SystemExit("refresh dates marker missing")
    Path(platform_test).write_text(text.replace(marker, addition, 1), encoding="utf-8")

print("SNAP_CURRENT_TOTAL_HOUR_BOUNDARY_V6_APPLIED")
