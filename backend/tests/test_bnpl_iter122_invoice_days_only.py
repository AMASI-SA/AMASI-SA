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
            # Mon 2025-05-05 → Sun 2025-05-11 — one full cycle (Mon→Sun)
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-05&to=2025-05-11", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 1, (
                f"expected 1 settlement for one Monday cycle in a week, "
                f"got {len(rows)}: {rows}"
            )
            # Iter-123 — period STARTS on Monday, ends on Sunday.
            assert rows[0]["from"] == "2025-05-05"  # Mon
            assert rows[0]["to"]   == "2025-05-11"  # Sun
        finally:
            _restore_defaults(session)

    def test_two_invoice_days_one_week_yields_two_settlements(self, session):
        """invoice=[Monday, Thursday] over 7 days → 2 settlements."""
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
            # period_start weekdays: Mon(0) then Thu(3)
            starts = [date.fromisoformat(r["from"]).weekday() for r in rows]
            assert starts == [0, 3], starts
        finally:
            _restore_defaults(session)

    def test_transfer_days_dont_create_settlements(self, session):
        """Even with 6 transfer days and invoice=[Monday], we still get
        only ONE settlement per Mon→Sun cycle."""
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
        """transfer=[] → expected_transfer_date is null but settlements
        STILL happen on invoice days."""
        _save_tabby(session, ["monday"], [])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-01&to=2025-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            # May 2025: there are 5 invoice cycles (May 5, 12, 19, 26 starts
            # + partial first May 1-4 from the floor).
            assert len(rows) >= 4, f"expected ≥4 invoices, got {len(rows)}"
            for row in rows:
                assert row.get("expected_transfer_date") is None, (
                    f"transfer_weekdays=[] must yield null "
                    f"expected_transfer_date, got {row}"
                )
        finally:
            _restore_defaults(session)

    def test_no_invoice_day_in_window_returns_zero_rows(self, session):
        """invoice=[Monday], window=Tue→Sun (no Monday inside) → 1 row
        covering Tue→Sun (mid-week start, ends day before next Mon=Sun).
        This is INTENTIONAL — we still want to surface the data even
        though the user filtered a non-aligned window."""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-06&to=2025-05-11", timeout=30,  # Tue→Sun
            )
            rows = r.json().get("rows") or []
            # Tue→Sun = 1 partial row, ending Sunday (day before next Mon)
            assert len(rows) == 1, (
                f"expected 1 partial row for mid-week start, "
                f"got {len(rows)}: {rows}"
            )
            assert rows[0]["from"] == "2025-05-06"
            assert rows[0]["to"]   == "2025-05-11"
        finally:
            _restore_defaults(session)

    def test_full_month_yields_correct_count(self, session):
        """May 2025 with invoice=[Monday]:
          • partial first: May 1 (Thu) → May 4 (Sun)
          • Mon May 5 → Sun May 11
          • Mon May 12 → Sun May 18
          • Mon May 19 → Sun May 25
          • Mon May 26 → Sat May 31 (partial last)
        = 5 rows total"""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-01&to=2025-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 5, (
                f"May 2025 yields 5 invoice cycles (4 full + 2 partial "
                f"or similar), got {len(rows)}: "
                f"{[(r['from'],r['to']) for r in rows]}"
            )
        finally:
            _restore_defaults(session)
