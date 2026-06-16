"""Iter-236 — Ads currency & bank-commission settings end-to-end."""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
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
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


class TestAdsCurrencySettings:

    def test_pure_math_matches_merchant_example(self):
        """Merchant's stated example: SAR 5000 @ 2.30% → 5,115."""
        from ads_currency_routes import compute_ads_amounts
        # SAR — no exchange.
        r = compute_ads_amounts(original_amount=5000, currency="SAR",
                                usd_to_sar_rate=3.7544,
                                bank_commission_pct=2.30,
                                apply_bank_commission=True)
        assert r["sar_amount"] == 5000.0
        assert r["exchange_rate_used"] == 0.0
        assert r["bank_commission_amount"] == 115.0
        assert r["total_due_sar"] == 5115.0
        # USD — exchange + commission.
        r = compute_ads_amounts(original_amount=1000, currency="USD",
                                usd_to_sar_rate=3.7544,
                                bank_commission_pct=2.30,
                                apply_bank_commission=True)
        assert r["sar_amount"] == 3754.4
        assert r["exchange_rate_used"] == 3.7544
        assert r["bank_commission_amount"] == 86.35
        assert r["total_due_sar"] == 3840.75
        # apply_bank_commission=False → no fee.
        r = compute_ads_amounts(original_amount=1000, currency="SAR",
                                usd_to_sar_rate=3.7544,
                                bank_commission_pct=2.30,
                                apply_bank_commission=False)
        assert r["bank_commission_amount"] == 0.0
        assert r["total_due_sar"] == 1000.0

    def test_put_get_settings(self, session):
        r = session.put(f"{BASE_URL}/api/ads-currency-settings",
                        json={"usd_to_sar_rate": 3.7544,
                              "bank_commission_pct": 2.30}, timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert body["usd_to_sar_rate"] == 3.7544
        assert body["bank_commission_pct"] == 2.30
        # Bank-fees expense account auto-created.
        assert body.get("bank_fees_expense_account_id"), body

    def test_preview_endpoint_matches_pure_math(self, session):
        r = session.get(f"{BASE_URL}/api/ads-currency-settings/preview",
                        params={"original_amount": 5000,
                                "currency": "SAR",
                                "apply_bank_commission": True},
                        timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert body["sar_amount"] == 5000.0
        assert body["bank_commission_amount"] == 115.0
        assert body["total_due_sar"] == 5115.0

    def test_create_liability_with_usd_snapshot(self, session):
        # 1. Find/create an ad_account counterparty.
        r = session.get(f"{BASE_URL}/api/counterparties?kind=ad_account",
                        timeout=30)
        items = r.json().get("items") or []
        if not items:
            r = session.post(f"{BASE_URL}/api/counterparties",
                             json={"kind": "ad_account",
                                   "name": f"iter236-{uuid.uuid4().hex[:6]}",
                                   "ad_provider": "snapchat"}, timeout=30)
            assert r.status_code == 200, r.text[:300]
            cp_id = r.json()["id"]
        else:
            cp_id = items[0]["id"]
        # 2. Configure currency=USD on it.
        r = session.put(
            f"{BASE_URL}/api/ads-currency-settings/account/{cp_id}",
            json={"currency": "USD", "apply_bank_commission": True},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        # 3. Create a liability with original_amount=100 USD.
        r = session.post(f"{BASE_URL}/api/liabilities",
                         json={"kind": "ad_account",
                               "ad_provider": "snapchat",
                               "counterparty_id": cp_id,
                               "original_amount": 100,
                               "original_currency": "USD",
                               "expected_amount": 1,    # ignored when
                                                         # original given
                               "due_date": "2026-06-30",
                               "description": "iter236 test"},
                         timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        # 4. Verify snapshot persisted + computed correctly.
        assert d["original_amount"] == 100.0
        assert d["original_currency"] == "USD"
        assert d["sar_amount"] == 375.44
        assert d["exchange_rate_used"] == 3.7544
        assert d["bank_commission_pct_used"] == 2.30
        assert d["bank_commission_amount"] == 8.64
        # expected_amount becomes total_due_sar.
        assert d["expected_amount"] == 384.08
        # 5. Cleanup — delete the test liability.
        session.delete(f"{BASE_URL}/api/liabilities/{d['id']}", timeout=30)
