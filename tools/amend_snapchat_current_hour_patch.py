from pathlib import Path

path = Path("tools/apply_snapchat_current_hour_sync.py")
source = path.read_text(encoding="utf-8")

old = '''    business_tz = _timezone(BUSINESS_TIMEZONE)\\n    completed_hour_end = current.astimezone(business_tz).replace(\\n        minute=0,\\n        second=0,\\n        microsecond=0,\\n    )\\n    return start, min(nominal_end, completed_hour_end)\\n\\n\\nasync def _fetch_account_hours(\\n'''
new = '''    business_tz = _timezone(BUSINESS_TIMEZONE)\\n    current_business = current.astimezone(business_tz)\\n    # Historical ranges are complete and must not be truncated by today's\\n    # clock. Clamp only when the requested range reaches the current Riyadh\\n    # business day.\\n    if end_date < current_business.date():\\n        return start, nominal_end\\n    completed_hour_end = current_business.replace(\\n        minute=0,\\n        second=0,\\n        microsecond=0,\\n    )\\n    return start, min(nominal_end, completed_hour_end)\\n\\n\\nasync def _fetch_account_hours(\\n'''
if source.count(old) != 1:
    raise RuntimeError(f"helper amendment expected one match, found {source.count(old)}")
source = source.replace(old, new, 1)

old_tests = '''    assert start.isoformat() == "2026-08-01T00:00:00+03:00"\\n    assert end.isoformat() == "2026-08-02T00:00:00+03:00"\\n'''\n'''
new_tests = '''    assert start.isoformat() == "2026-08-01T00:00:00+03:00"\\n    assert end.isoformat() == "2026-08-02T00:00:00+03:00"\\n\\n\\ndef test_historical_range_keeps_full_riyadh_days():\\n    start, end = snapchat_hourly_request_window(\\n        date(2026, 7, 30),\\n        date(2026, 7, 31),\\n        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),\\n    )\\n\\n    assert start.isoformat() == "2026-07-30T00:00:00+03:00"\\n    assert end.isoformat() == "2026-08-01T00:00:00+03:00"\\n'''\n'''
if source.count(old_tests) != 1:
    raise RuntimeError(f"historical test amendment expected one match, found {source.count(old_tests)}")
source = source.replace(old_tests, new_tests, 1)

path.write_text(source, encoding="utf-8")
print("SNAPCHAT_CURRENT_HOUR_PATCH_AMENDED")
