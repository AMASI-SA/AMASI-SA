"""Iter-121 — Weekday-based settlement cycle.

Asserts:
  • Tabby invoices end on Monday by default; expected transfer = Tuesday.
  • Tamara invoices end on Sunday by default; expected transfer = Tuesday.
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
        # Every invoice ends on Monday (weekday() == 0)
        for row in rows:
            inv = date.fromisoformat(row["to"])
            assert inv.weekday() == 0, (
                f"invoice #{row['invoice_no']} ends on weekday "
                f"{inv.weekday()} ({row['to']}) — expected Monday (0)"
            )
            # expected_transfer_date is Tuesday or Wednesday
            assert row.get("expected_transfer_date"), row
            et = date.fromisoformat(row["expected_transfer_date"])
            assert et.weekday() in {1, 2}, (
                f"expected transfer should be Tue/Wed, got "
                f"{et.weekday()} ({row['expected_transfer_date']})"
            )

    def test_tamara_default_invoice_day_is_sunday(self, session):
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/weekly/tamara"
            "?from=2025-05-01&to=2025-05-31", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        rows = r.json().get("rows") or []
        assert rows, "no Tamara rows returned"
        for row in rows[1:]:  # skip first partial period
            inv = date.fromisoformat(row["to"])
            assert inv.weekday() == 6, (  # Sunday = 6
                f"Tamara invoice should end on Sunday, got "
                f"{inv.weekday()} ({row['to']})"
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
            weekdays = [date.fromisoformat(row["to"]).weekday() for row in rows]
            assert all(w in {0, 3} for w in weekdays), (
                f"invoice weekdays should be Mon(0) or Thu(3); got {weekdays}"
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
                    "transfer_weekdays": ["tuesday", "wednesday"],
                }, timeout=30,
            )

    def test_settlement_fee_counts_per_invoice_weekday(self, session):
        """With Tabby default = Monday and a 1-Mar→31-Mar window in 2025
        there are exactly 5 Mondays (3, 10, 17, 24, 31), so
        settlement_fee = 5 × settlement_fee_per_invoice (default 5 SAR)
        = 25 SAR — regardless of total days in the month."""
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/tabby"
            "?from=2025-03-01&to=2025-03-31", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["success"] is True, body
        totals = body["totals"]
        assert totals["settlement_invoices_count"] == 5, totals
        # 5 SAR per invoice × 5 invoices = 25
        assert abs(totals["settlement_fee"] - 25.0) < 0.01, totals

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
