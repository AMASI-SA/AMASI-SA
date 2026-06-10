"""Iter-122 — Strict separation between `invoice_weekdays` (which is
the SOLE basis for creating settlements) and `transfer_weekdays`
(which only computes the expected bank transfer date — never creates
a settlement).

The user's reported concern: the system should NOT create one
settlement per day of the week.  Settlements happen ONLY on the
configured invoice weekdays.
"""
from __future__ import annotations

import os
from datetime import date

import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


def _save_tabby(session, invoice_days, transfer_days):
    """Persist a custom weekday configuration for Tabby."""
    r = session.put(
        f"{BASE_URL}/api/bnpl/settings/tabby",
        json={
            "invoice_weekdays":  invoice_days,
            "transfer_weekdays": transfer_days,
        }, timeout=30,
    )
    assert r.status_code == 200, r.text[:300]


def _restore_defaults(session):
    _save_tabby(session, ["monday"], ["tuesday", "wednesday"])


class TestIssueDaysAreSoleBasis:

    def test_one_invoice_day_one_week_yields_one_settlement(self, session):
        """invoice=[Monday] over 7 days → EXACTLY 1 settlement (not 7)."""
        _save_tabby(session, ["monday"], ["tuesday", "wednesday"])
        try:
            # Mon 2025-05-05 → Sun 2025-05-11 — exactly one Monday inside
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-05&to=2025-05-11", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 1, (
                f"expected 1 settlement for one Monday in a week, "
                f"got {len(rows)}: {rows}"
            )
            assert date.fromisoformat(rows[0]["to"]).weekday() == 0  # Mon
        finally:
            _restore_defaults(session)

    def test_two_invoice_days_one_week_yields_two_settlements(self, session):
        """invoice=[Monday, Thursday] over 7 days → 2 settlements (not 7)."""
        _save_tabby(session, ["monday", "thursday"], ["wednesday", "friday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-05&to=2025-05-11", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 2, (
                f"expected 2 settlements for Mon+Thu in a week, "
                f"got {len(rows)}: {rows}"
            )
            weekdays = sorted(date.fromisoformat(r["to"]).weekday() for r in rows)
            assert weekdays == [0, 3], weekdays  # Mon=0, Thu=3
        finally:
            _restore_defaults(session)

    def test_transfer_days_dont_create_settlements(self, session):
        """Even with transfer=[Tue,Wed,Thu,Fri,Sat,Sun] (6 transfer days)
        and invoice=[Monday], we still get only ONE settlement per week."""
        _save_tabby(session, ["monday"],
                    ["tuesday", "wednesday", "thursday",
                     "friday", "saturday", "sunday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-05&to=2025-05-11", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 1, (
                "transfer_weekdays MUST NEVER create settlements. "
                f"Got {len(rows)} rows: {rows}"
            )
        finally:
            _restore_defaults(session)

    def test_empty_transfer_days_yields_null_expected_transfer(self, session):
        """transfer=[] (empty) → expected_transfer_date is null on every
        row, but settlements STILL happen on invoice days."""
        _save_tabby(session, ["monday"], [])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-01&to=2025-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            # 4 Mondays in May 2025 (5, 12, 19, 26)
            assert len(rows) == 4, f"expected 4 Monday invoices, got {len(rows)}"
            for row in rows:
                assert row.get("expected_transfer_date") is None, (
                    f"transfer_weekdays=[] must yield null "
                    f"expected_transfer_date, got {row}"
                )
        finally:
            _restore_defaults(session)

    def test_no_invoice_day_in_window_returns_zero_rows(self, session):
        """invoice=[Monday], window=Tue→Sun (no Monday inside) → 0 rows."""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-06&to=2025-05-11", timeout=30,  # Tue→Sun
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 0, (
                f"window with no Mondays should produce 0 rows, "
                f"got {len(rows)}: {rows}"
            )
        finally:
            _restore_defaults(session)

    def test_full_month_yields_correct_count(self, session):
        """May 2025 has 4 Mondays (5, 12, 19, 26) → 4 settlements."""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-01&to=2025-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 4, (
                f"May 2025 has 4 Mondays, got {len(rows)} settlements"
            )
        finally:
            _restore_defaults(session)
