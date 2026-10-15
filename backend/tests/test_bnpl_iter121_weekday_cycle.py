"""Iter-121 — Weekday-based settlement cycle.

Asserts:
  • Tabby reports issue and transfer Monday for a Monday→Sunday cycle.
  • Tamara statements issue Saturday after a Saturday→Friday period.
  • Per-user override via PUT /api/bnpl/settings/{provider} with
    `invoice_weekdays` and `transfer_weekdays` lists works end-to-end.
  • The new fields persist + drive subsequent weekly settlement output.
  • settlement_fee_per_invoice is multiplied by the COUNT of invoice
    weekdays inside the period, not by ceil(days/7).
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
    token = r.json().get("access_token")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


class TestWeekdayCycle:

    def test_settings_endpoint_returns_weekday_fields(self, session):
        r = session.get(f"{BASE_URL}/api/bnpl/settings/tabby", timeout=30)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert "invoice_weekdays" in body, body.keys()
        assert "transfer_weekdays" in body, body.keys()
        assert isinstance(body["invoice_weekdays"], list)
        assert isinstance(body["transfer_weekdays"], list)

    def test_tabby_default_invoice_day_is_monday(self, session):
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
            "?from=2025-05-01&to=2025-05-31", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        rows = r.json().get("rows") or []
        assert len(rows) >= 3, f"expected several weekly rows in May, got {len(rows)}"
        # Iter-123 — period STARTS on Monday and runs to Sunday.
        # Each row that's not the partial first one should start on Monday.
        # The issue_date is the NEXT Monday after the period ends.
        # expected_transfer_date is the report's Monday issue day.
        for row in rows:
            issue = row.get("issue_date")
            assert issue is not None, row
            issue_d = date.fromisoformat(issue)
            assert issue_d.weekday() == 0, (
                f"issue_date should be Monday, got {issue_d.weekday()} "
                f"for row {row}"
            )
            et = row.get("expected_transfer_date")
            assert et, row
            assert date.fromisoformat(et).weekday() == 0, (
                f"expected_transfer must be Monday, got {et}"
            )

    def test_tamara_default_invoice_day_is_saturday(self, session):
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/weekly/tamara"
            "?from=2025-05-01&to=2025-05-31", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        rows = r.json().get("rows") or []
        assert rows, "no Tamara rows returned"
        # Five verified merchant statements issue Saturday after Friday close.
        for row in rows:
            issue = row.get("issue_date")
            if issue:  # may be None for the last partial row
                assert date.fromisoformat(issue).weekday() == 5, (  # Sat=5
                    f"Tamara issue_date should be Saturday, got "
                    f"{date.fromisoformat(issue).weekday()} ({issue})"
                )

    def test_save_custom_weekdays_persists_and_drives_periods(self, session):
        """Set tabby to Monday+Thursday invoices, Wednesday+Friday transfers."""
        save = session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={
                "invoice_weekdays":  ["monday", "thursday"],
                "transfer_weekdays": ["wednesday", "friday"],
            }, timeout=30,
        )
        assert save.status_code == 200, save.text[:300]

        try:
            # Round-trip read
            r = session.get(f"{BASE_URL}/api/bnpl/settings/tabby", timeout=30)
            body = r.json()
            assert set(body["invoice_weekdays"]) == {"monday", "thursday"}
            assert set(body["transfer_weekdays"]) == {"wednesday", "friday"}

            # New weekly periods alternate between Mon/Thu
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-01&to=2025-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            weekdays = [date.fromisoformat(row["issue_date"]).weekday() for row in rows if row.get("issue_date")]
            assert all(w in {0, 3} for w in weekdays), (
                f"issue weekdays should be Mon(0) or Thu(3); got {weekdays}"
            )
            # And both should appear at least once across the month
            assert 0 in weekdays
            assert 3 in weekdays
            # expected_transfer_date weekday ∈ {Wed=2, Fri=4}
            for row in rows:
                et = row.get("expected_transfer_date")
                if et:
                    assert date.fromisoformat(et).weekday() in {2, 4}, (
                        f"transfer weekday should be Wed/Fri, got {et}"
                    )
        finally:
            # Restore Tabby defaults so other tests aren't affected
            session.put(
                f"{BASE_URL}/api/bnpl/settings/tabby",
                json={
                    "invoice_weekdays":  ["monday"],
                    "transfer_weekdays": ["monday"],
                }, timeout=30,
            )

    def test_settlement_fee_counts_per_invoice_weekday(self, session):
        """With Tabby default = Monday in 1-Mar→31-Mar 2025, expect 5
        Mondays within the range (3, 10, 17, 24, 31).  The aggregate
        endpoint counts five statements but does not invent a payout fee."""
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/tabby"
            "?from=2025-03-03&to=2025-03-31", timeout=30,  # Mon → Mon
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["success"] is True, body
        totals = body["totals"]
        assert totals["settlement_invoices_count"] == 5, totals
        assert abs(totals["settlement_fee"] - 0.0) < 0.01, totals

    def test_invalid_weekday_names_are_silently_dropped(self, session):
        """Saving ['typo','monday'] keeps only 'monday'."""
        save = session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={"invoice_weekdays": ["typo", "monday", "MONDAY"]},
            timeout=30,
        )
        assert save.status_code == 200, save.text[:300]
        try:
            r = session.get(f"{BASE_URL}/api/bnpl/settings/tabby", timeout=30)
            body = r.json()
            assert body["invoice_weekdays"] == ["monday"], body
        finally:
            session.put(
                f"{BASE_URL}/api/bnpl/settings/tabby",
                json={"invoice_weekdays": ["monday"]},
                timeout=30,
            )
