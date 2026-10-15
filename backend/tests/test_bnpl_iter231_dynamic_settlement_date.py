"""Iter-231 — Dynamic BNPL settlement_date from transfer_weekdays config.

Asserts the `/api/bnpl/settlements/import-preview/{provider}` endpoint
picks the prefilled `settlement_date` based on the merchant's
`transfer_weekdays` saved at /integrations/bnpl, NOT a hardcoded
provider rule.

Cases:
  1. Tabby with transfer_weekdays=["tuesday","wednesday"] and date_to=
     2025-09-01 (Monday) → settlement_date == 2025-09-02 (Tuesday).
  2. Tamara with transfer_weekdays=["tuesday"] and date_to=
     2025-09-05 (Friday) → settlement_date == 2025-09-09 (Tuesday).
  3. After overriding Tamara transfer_weekdays to ["thursday"] the same
     period_end Fri 2025-09-05 yields settlement_date 2025-09-11 (Thu).
"""
from __future__ import annotations

import os
from datetime import date

import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
# Frontend .env carries REACT_APP_BACKEND_URL (the externally-reachable URL).
load_dotenv(os.path.join(os.path.dirname(_BACKEND_DIR), "frontend", ".env"))

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
    assert r.status_code == 200, r.text[:300]
    token = r.json().get("access_token")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


class TestDynamicSettlementDate:

    def test_tabby_uses_configured_transfer_weekday(self, session):
        # Explicit custom schedule: invoice=Mon, transfer=Tue/Wed.
        session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={
                "invoice_weekdays":  ["monday"],
                "transfer_weekdays": ["tuesday", "wednesday"],
            }, timeout=30,
        )
        # Use explicit period to avoid relying on "this_week" math.
        # 2025-09-01 was a Monday → settlement_date should be Tue 2025-09-02.
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/import-preview/tabby"
            "?date_from=2025-08-25&date_to=2025-09-01", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("success") is True, body
        sd = (body.get("prefill") or {}).get("settlement_date")
        assert sd == "2025-09-02", (
            f"expected Tabby settlement_date=2025-09-02, got {sd}"
        )
        # And that 2025-09-02 IS a Tuesday (weekday=1).
        assert date.fromisoformat(sd).weekday() == 1

    def test_tamara_default_picks_tuesday_after_friday(self, session):
        # Defaults: Tamara transfer = Tuesday.
        session.put(
            f"{BASE_URL}/api/bnpl/settings/tamara",
            json={
                "invoice_weekdays":  ["sunday"],
                "transfer_weekdays": ["tuesday"],
            }, timeout=30,
        )
        # 2025-09-05 is Friday → next Tuesday = 2025-09-09.
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/import-preview/tamara"
            "?date_from=2025-08-30&date_to=2025-09-05", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        sd = (r.json().get("prefill") or {}).get("settlement_date")
        assert sd == "2025-09-09", (
            f"expected Tamara settlement_date=2025-09-09, got {sd}"
        )
        assert date.fromisoformat(sd).weekday() == 1  # Tuesday

    def test_tamara_override_thursday_changes_settlement_date(self, session):
        try:
            session.put(
                f"{BASE_URL}/api/bnpl/settings/tamara",
                json={"transfer_weekdays": ["thursday"]}, timeout=30,
            )
            # 2025-09-05 = Friday → next Thursday = 2025-09-11.
            r = session.get(
                f"{BASE_URL}/api/bnpl/settlements/import-preview/tamara"
                "?date_from=2025-08-30&date_to=2025-09-05", timeout=30,
            )
            assert r.status_code == 200, r.text[:300]
            sd = (r.json().get("prefill") or {}).get("settlement_date")
            assert sd == "2025-09-11", (
                f"expected Tamara settlement_date=2025-09-11 (Thu), got {sd}"
            )
            assert date.fromisoformat(sd).weekday() == 3  # Thursday
        finally:
            # Restore default so other tests aren't affected.
            session.put(
                f"{BASE_URL}/api/bnpl/settings/tamara",
                json={"transfer_weekdays": ["tuesday"]}, timeout=30,
            )

    def test_period_end_on_transfer_day_skips_to_next_week(self, session):
        """If date_to is already a transfer day, picker must skip to NEXT
        week (not return date_to itself). With custom transfer=Tue/Wed, set
        date_to=2025-09-02 (Tuesday) → settlement = 2025-09-03 (Wed)."""
        session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={
                "invoice_weekdays":  ["monday"],
                "transfer_weekdays": ["tuesday", "wednesday"],
            }, timeout=30,
        )
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/import-preview/tabby"
            "?date_from=2025-08-26&date_to=2025-09-02", timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        sd = (r.json().get("prefill") or {}).get("settlement_date")
        # Day strictly AFTER 2025-09-02 matching Tue/Wed = Wed 2025-09-03.
        assert sd == "2025-09-03", (
            f"expected 2025-09-03 (Wed, next after Tue), got {sd}"
        )
