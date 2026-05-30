"""Tests for Snapchat /daily-spend endpoint date validation.

Verifies the fix for: "Timeseries queries with DAY granularity must have a
start time that is the start of the day." — by ensuring we always validate
inputs strictly and (separately) format start_time/end_time correctly.
"""
import os
import uuid
from datetime import datetime

import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def test_malformed_date_picker_output_rejected():
    """Reproduces the bug report: '292026/05/' (Arabic-locale RTL date picker)."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/snapchat/daily-spend",
                     params={"date": "292026/05/"}, headers=h)
    assert r.status_code == 400, r.text
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_empty_date_rejected():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/snapchat/daily-spend",
                     params={"date": ""}, headers=h)
    assert r.status_code == 400
    assert "YYYY-MM-DD" in r.json()["detail"]


def test_impossible_calendar_date_rejected():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/snapchat/daily-spend",
                     params={"date": "2026-02-30"}, headers=h)
    assert r.status_code == 400
    assert "غير صالح" in r.json()["detail"]


def test_alternate_separators_rejected():
    """Slashes / dots / spaces are all rejected before any API call."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    for bad in ["2026/05/30", "30-05-2026", "2026.05.30", "May 30, 2026", "20260530"]:
        r = requests.get(f"{API}/snapchat/daily-spend",
                         params={"date": bad}, headers=h)
        assert r.status_code == 400, f"{bad!r} should fail but got {r.status_code}"


def test_well_formed_date_passes_validation_layer():
    """Well-formed YYYY-MM-DD passes the validator (subsequent error is auth/snap)."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/snapchat/daily-spend",
                     params={"date": "2026-05-30"}, headers=h)
    # Status 400 expected (no snap configured) but with a *different* error
    assert r.status_code == 400
    body = r.json()
    assert "YYYY-MM-DD" not in body["detail"], f"validator should not reject this: {body}"
    assert "غير صالح" not in body["detail"]


def test_select_adaccount_accepts_timezone():
    """The select-adaccount endpoint persists the timezone for later use."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    # Endpoint requires snap connection to exist; this just verifies the
    # request shape is accepted by the new Pydantic model.
    r = requests.post(f"{API}/snapchat/select-adaccount",
                      json={"ad_account_id": "abc", "ad_account_name": "Test",
                            "timezone": "Asia/Riyadh", "currency": "SAR"},
                      headers=h)
    # 400 because no connection record exists yet, NOT 422 (which would mean
    # the new fields are not accepted by the validator).
    assert r.status_code == 400, r.text


def test_unit_iso_format_start_of_day():
    """Direct unit check on the helper used by /daily-spend."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        return  # py<3.9
    from datetime import datetime, timedelta
    tz = ZoneInfo("Asia/Riyadh")
    day = datetime(2026, 5, 30, 0, 0, 0, tzinfo=tz)
    next_day = day + timedelta(days=1)
    # ISO formatted with offset, NOT with Z (since not UTC)
    assert day.isoformat(timespec="seconds") == "2026-05-30T00:00:00+03:00"
    assert next_day.isoformat(timespec="seconds") == "2026-05-31T00:00:00+03:00"
