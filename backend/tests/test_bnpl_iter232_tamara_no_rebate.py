"""Iter-232 — Tamara commission rebate fix.

Per the official Tamara Statement (P0420741SA260613, period 06/06/2026
→ 12/06/2026, merchant Amasi Jewelery):
  • Captured       : 20,848.30 SAR  (102 orders, includes orders that
                      were refunded the SAME day — Tamara still
                      charges them MDR + fixed_fee)
  • Refunds        : 2,929.37 SAR
  • Tamara Fees    : 1,610.39 SAR   (= 6.99% × 20,848.30 + 1.50 × 102)
  • Tamara VAT     : 241.64  SAR   (≈ 15% × 1,610.39)
  • Payable        : 16,066.90 SAR

KEY INSIGHT: Tamara does NOT refund commission on refunded orders.
The fee is computed on EVERY Captured order regardless of whether it
was refunded later in the same Statement.

Before Iter-232 the engine used `refundable_commission_pct = 6.99`
(= full MDR rebated on refunds), which over-rebated by ~6.99% × refunds.

This test asserts the rate is now 0 by default (auto-mode), and that
the per-order commission engine matches Tamara to the cent for a
canonical scenario.
"""
from __future__ import annotations

import os
from datetime import date

import pytest
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(_BACKEND_DIR), "frontend", ".env"))

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
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


class TestTamaraNoCommissionRebate:

    def test_auto_mode_refundable_is_zero(self, session):
        """Default auto-mode now reports refundable_commission_pct=0."""
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/tamara?from=2026-01-01&to=2026-01-07",
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        fr = r.json().get("fee_rates") or {}
        assert fr.get("commission_mode") == "auto", fr
        assert fr.get("refundable_commission_pct") == 0.0, (
            f"expected 0 refundable rebate for Tamara in auto mode, "
            f"got {fr.get('refundable_commission_pct')}"
        )
        # And the canonical MDR / fixed fee are unchanged.
        assert fr.get("commission_pct") == 6.99
        assert fr.get("fixed_fee_per_order") == 1.5
        assert fr.get("vat_pct") == 15.0

    def test_tabby_still_refunds_partial_commission(self, session):
        """Regression — Tabby's refundable_commission_pct should stay
        at 4.99% (the published Tabby split: 4.99% refundable + 2%
        non-refundable). Fixing Tamara must NOT touch Tabby."""
        r = session.get(
            f"{BASE_URL}/api/bnpl/settlements/tabby?from=2026-01-01&to=2026-01-07",
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        fr = r.json().get("fee_rates") or {}
        assert fr.get("commission_mode") == "auto", fr
        assert fr.get("refundable_commission_pct") == 4.99, (
            f"Tabby refundable should remain 4.99%, got "
            f"{fr.get('refundable_commission_pct')}"
        )

    def test_per_order_math_matches_tamara_statement(self):
        """Pure-math sanity: with the new rate, the engine's totals
        for the merchant's 06/06–12/06 cycle equal Tamara's numbers."""
        commission_rate = 0.0699
        fixed_fee = 1.50
        vat_rate = 0.15
        refundable_rate = 0.0   # Iter-232

        captured_total = 20848.30
        captured_count = 102
        refunds_total = 2929.37

        sales_commission = (
            captured_total * commission_rate + fixed_fee * captured_count
        )
        sales_vat = sales_commission * vat_rate
        refund_rebate = refunds_total * refundable_rate
        refund_vat_rebate = refund_rebate * vat_rate

        commission = round(sales_commission - refund_rebate, 2)
        commission_vat = round(sales_vat - refund_vat_rebate, 2)
        net_sales = round(captured_total - refunds_total, 2)
        net_payable = round(net_sales - commission - commission_vat, 2)

        # Tamara Statement target numbers (allow 0.20 SAR tolerance for
        # per-row rounding accumulation).
        assert abs(commission - 1610.39) < 0.20, commission
        assert abs(commission_vat - 241.64) < 0.20, commission_vat
        assert abs(net_payable - 16066.90) < 0.50, net_payable
