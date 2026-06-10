"""Iter-123 — Period convention: invoice_weekday is the period START.

The user's expectation:
  invoice_weekdays = [Monday]
  filter from Apr 27 → May 31 2026
  expected:
    Row 1: from=Mon Apr 27, to=Sun May 3   (full Mon→Sun cycle)
    Row 2: from=Mon May 4,  to=Sun May 10
    Row 3: from=Mon May 11, to=Sun May 17
    Row 4: from=Mon May 18, to=Sun May 24
    Row 5: from=Mon May 25, to=Sun May 31  (or partial if ceil ends earlier)

Each row also exposes:
  • issue_date            = the NEXT invoice weekday after period_end
                            (i.e. the day Tabby/Tamara generates the file)
  • expected_transfer_date = first transfer_weekday on/after issue_date
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


def _save_tabby(session, inv, trf):
    r = session.put(
        f"{BASE_URL}/api/bnpl/settings/tabby",
        json={"invoice_weekdays": inv, "transfer_weekdays": trf}, timeout=30,
    )
    assert r.status_code == 200, r.text[:300]


def _restore(session):
    _save_tabby(session, ["monday"], ["tuesday", "wednesday"])


class TestPeriodStartConvention:
    """Iter-123 — period STARTS on invoice_weekday, ENDS day before next."""

    def test_apr27_to_may3_is_a_single_full_week(self, session):
        """Real user scenario: invoice=[Mon], floor=Apr 27 2026 (Mon).
        Expect first row Apr 27 → May 3 (Sun), NOT Apr 27 → Apr 27."""
        _save_tabby(session, ["monday"], ["tuesday", "wednesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2026-04-27&to=2026-05-31", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert rows, "no rows returned"
            first = rows[0]
            assert first["from"] == "2026-04-27", (
                f"first row 'from' should be Mon Apr 27, got {first['from']}"
            )
            assert first["to"] == "2026-05-03", (
                f"first row 'to' should be Sun May 3 (day before next Mon), "
                f"got {first['to']}"
            )
            # issue_date = Mon May 4 (next Mon after period_end)
            assert first["issue_date"] == "2026-05-04", first
            # expected_transfer = May 5 (Tue, first transfer day ≥ May 4)
            assert first["expected_transfer_date"] == "2026-05-05", first
            # And the SECOND row should be Mon May 4 → Sun May 10
            second = rows[1]
            assert second["from"] == "2026-05-04"
            assert second["to"]   == "2026-05-10"
            assert second["issue_date"] == "2026-05-11"
        finally:
            _restore(session)

    def test_period_starts_on_invoice_weekday(self, session):
        """All FULL-cycle rows (non-partial) start on Monday and end on
        Sunday for invoice=[Monday]."""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-05&to=2025-05-25", timeout=30,
            )
            rows = r.json().get("rows") or []
            assert len(rows) == 3, f"expected 3 full cycles, got {len(rows)}: {rows}"
            for row in rows:
                assert date.fromisoformat(row["from"]).weekday() == 0, (
                    f"period should start on Monday, got {row['from']}"
                )
                assert date.fromisoformat(row["to"]).weekday() == 6, (
                    f"period should end on Sunday, got {row['to']}"
                )
                # 7-day span
                d_from = date.fromisoformat(row["from"])
                d_to = date.fromisoformat(row["to"])
                assert (d_to - d_from).days == 6, row
        finally:
            _restore(session)

    def test_mid_week_start_creates_partial_first_row(self, session):
        """floor=Wed → partial first row covers Wed→Sun, then full
        weeks Mon→Sun."""
        _save_tabby(session, ["monday"], ["tuesday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2025-05-07&to=2025-05-19", timeout=30,  # Wed → Mon
            )
            rows = r.json().get("rows") or []
            assert len(rows) >= 2, rows
            # First row: Wed May 7 → Sun May 11 (5 days, partial)
            assert rows[0]["from"] == "2025-05-07"
            assert rows[0]["to"]   == "2025-05-11"
            # Issue date = Mon May 12
            assert rows[0]["issue_date"] == "2025-05-12"
            # Second row: full week Mon May 12 → Sun May 18
            assert rows[1]["from"] == "2025-05-12"
            assert rows[1]["to"]   == "2025-05-18"
        finally:
            _restore(session)

    def test_two_invoice_days_split_week(self, session):
        """invoice=[Mon, Thu] → Mon-Wed cycle + Thu-Sun cycle."""
        _save_tabby(session, ["monday", "thursday"], ["wednesday", "friday"])
        try:
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/weekly/tabby"
                "?from=2026-04-27&to=2026-05-10", timeout=30,
            )
            rows = r.json().get("rows") or []
            # Apr 27 Mon → Apr 29 Wed
            # Apr 30 Thu → May 3 Sun
            # May 4 Mon → May 6 Wed
            # May 7 Thu → May 10 Sun
            assert len(rows) == 4, (
                f"expected 4 rows for 2-week Mon/Thu cycle, got {len(rows)}: {rows}"
            )
            assert rows[0]["from"] == "2026-04-27" and rows[0]["to"] == "2026-04-29"
            assert rows[1]["from"] == "2026-04-30" and rows[1]["to"] == "2026-05-03"
            assert rows[2]["from"] == "2026-05-04" and rows[2]["to"] == "2026-05-06"
            assert rows[3]["from"] == "2026-05-07" and rows[3]["to"] == "2026-05-10"
        finally:
            _restore(session)
