"""Iter-126 — Unified fee-rate source of truth.

Verifies that `_merchant_fee_rates` reads commission / fixed_fee / VAT
from `users.settings.payment_methods` (the Settings UI source) before
falling back to `bnpl_settings.mdr_percent`.  When a user saves rates
in either UI, BOTH locations stay in sync.
"""
from __future__ import annotations

import os
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


def _settlement_rates(session, provider="tabby"):
    r = session.get(
        f"{BASE_URL}/api/bnpl/settlements/{provider}"
        "?from=2025-05-01&to=2025-05-31", timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    return r.json().get("fee_rates", {})


def _restore_defaults(session):
    session.put(
        f"{BASE_URL}/api/bnpl/settings/tabby",
        json={
            "mdr_percent":        0.05,
            "fixed_fee_per_order": 1.0,
            "vat_on_fees_percent": 0.15,
            "settlement_fee_per_invoice": 5.0,
        },
        timeout=30,
    )


class TestUnifiedFeeSource:

    def test_response_includes_fee_source_field(self, session):
        rates = _settlement_rates(session)
        assert "fee_source" in rates, rates
        assert rates["fee_source"] in {
            "payment_methods_settings",
            "bnpl_settings_legacy",
            "code_default",
        }

    def test_saving_via_bnpl_mirrors_into_payment_methods(self, session):
        """When user saves MDR=8% via BNPL Integrations page, the same
        rate must appear in `users.settings.payment_methods` so the
        Settings page reflects it too."""
        save = session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={"mdr_percent": 0.08, "fixed_fee_per_order": 2.5}, timeout=30,
        )
        assert save.status_code == 200
        try:
            # Read user profile and check تابي in payment_methods
            r = session.get(f"{BASE_URL}/api/auth/me", timeout=30)
            pm_list = ((r.json().get("settings") or {})
                       .get("payment_methods") or [])
            tabby = next((p for p in pm_list if p.get("name") == "تابي"), None)
            # If the user has a تابي entry, it should now be 8% / 2.5
            if tabby is not None:
                assert abs(tabby.get("commission_percent", 0) - 8.0) < 0.01, tabby
                assert abs(tabby.get("fixed_fee", 0) - 2.5) < 0.01, tabby
            # Either way, the settlement engine should now reflect 8%
            rates = _settlement_rates(session)
            assert abs(rates["commission_pct"] - 8.0) < 0.01, rates
            assert abs(rates["fixed_fee_per_order"] - 2.5) < 0.01, rates
        finally:
            _restore_defaults(session)

    def test_settlement_engine_uses_unified_rates(self, session):
        """The settlement engine's commission_pct value MUST always
        equal what either payment_methods or bnpl_settings exposes —
        no hardcoded values leaking into per-merchant calculations."""
        save = session.put(
            f"{BASE_URL}/api/bnpl/settings/tabby",
            json={"mdr_percent": 0.0699}, timeout=30,
        )
        assert save.status_code == 200
        try:
            rates = _settlement_rates(session)
            assert abs(rates["commission_pct"] - 6.99) < 0.01, (
                f"engine must use saved 6.99%, got {rates['commission_pct']}"
            )
        finally:
            _restore_defaults(session)
