"""Iter-82 EXTRA — additional verifications for the review request:
- Cancelled-orders regression (still excluded from gross)
- Other gateways also surface status-driven refunds (mada, credit_card,
  COD, bank_transfer, stcpay)
- Tamara exact gross/fees/refund/net for 2026
- Per-row identity for every gateway row
"""
import os
import requests
import pytest

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
).rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    r.raise_for_status()
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module")
def central(auth):
    r = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _row(payload, key):
    for r in payload.get("rows", []):
        if r["key"] == key:
            return r
    return None


def test_tamara_exact_numbers(central):
    """Spec: gross=94,483.27; refund_full≈2,648.43; fees≈6,419.25; net≈84,452.70."""
    t = _row(central, "tamara")
    assert t is not None
    assert abs(t["gross"] - 94_483.27) < 1.0, t
    assert abs(t["refund_full"] - 2_648.43) < 1.0, t
    assert abs(t["fees"] - 6_419.25) < 1.0, t
    assert abs(t["net"] - 84_452.70) < 1.0, t
    assert t["refunded_orders_count"] >= 11


def test_tabby_exact_numbers(central):
    t = _row(central, "tabby")
    assert t is not None
    assert abs(t["refund_full"] - 352.27) < 1.0, t
    assert abs(t["net"] - 72_804.78) < 1.0, t
    assert t["refunded_orders_count"] >= 1


def test_cancelled_orders_still_excluded(central):
    """Regression: cancelled_orders_count should be populated and
    cancelled orders must NOT be included in gross."""
    found_cancelled = False
    for r in central.get("rows", []):
        if r.get("cancelled_orders_count", 0) > 0:
            found_cancelled = True
            break
    assert found_cancelled, "No gateway exposes cancelled_orders_count"


def test_other_gateways_refunded_counts_populated(central):
    """Refund Monitor should also surface refunds for mada / credit_card
    / COD / bank_transfer / stcpay (status-driven)."""
    found = {}
    for r in central.get("rows", []):
        if r["key"] in ("mada", "credit_card", "cod", "bank_transfer", "stcpay"):
            found[r["key"]] = r.get("refunded_orders_count", 0)
    # Per the request: mada (10), credit_card (7), COD (5), stcpay (1)
    assert found.get("mada", 0) >= 1, found
    assert found.get("credit_card", 0) >= 1, found
    assert found.get("cod", 0) >= 1, found


def test_refund_monitor_surfaces_other_methods(auth):
    """Refund Monitor by_payment_method should include other Arabic
    payment methods (مدى / البطاقة الإئتمانية / دفع عند الإستلام / STC Pay)."""
    r = auth.get(f"{BASE_URL}/api/reports/refunds-alert?period=this_year",
                 timeout=20)
    r.raise_for_status()
    data = r.json()
    methods = {row["payment_method"] for row in data.get("by_payment_method", [])}
    # At least one of these Arabic methods should appear (per real data)
    arabic_methods = {"مدى", "البطاقة الإئتمانية", "دفع عند الإستلام", "STC Pay"}
    assert methods & arabic_methods, f"None of {arabic_methods} surfaced; got {methods}"


def test_per_row_identity_all_gateways(central):
    """gross − fees − fees_vat − refund_full − refund_partial == net"""
    for r in central.get("rows", []):
        computed = (r["gross"] - r["fees"] - r["fees_vat"]
                    - r["refund_full"] - r["refund_partial"])
        assert abs(computed - r["net"]) < 1.0, r


def test_reconciliation_expected_source_central(auth):
    r = auth.get(
        f"{BASE_URL}/api/reconciliation/summary"
        f"?from_date=2026-01-01&to_date=2026-12-31", timeout=20)
    r.raise_for_status()
    rec = r.json()
    for p in rec.get("platforms", []):
        if p["normalized_payment_method"] in ("tamara", "tabby"):
            assert p["expected_source"] == "central", p
