"""Iter-82 — Status-driven refund detection in
/api/payment-gateway-metrics + /api/reports/refunds-alert.

Validates that orders with `order_status` = "مسترجع" (or any string
matching the regex) are:
  • excluded from net (treated as full refund)
  • included in `refund_full` and `refunded_orders_count`
  • surfaced by the Refund Monitor even without a settlement file
"""
import os
import sys
import requests
import pytest

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
)
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    token = r.json()["access_token"]
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def central(auth):
    r = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _row(payload, key):
    for r in payload.get("rows", []):
        if r["key"] == key:
            return r
    return None


def test_tamara_refunds_excluded_from_net(central):
    t = _row(central, "tamara")
    assert t is not None, "Tamara row missing"
    # Refund tracking is on — status-driven refunds are detected.
    assert t["refunded_orders_count"] >= 11, t
    assert t["refund_full"] >= 2_600, t
    # Iter-83: pending orders moved out of net into pending_gross,
    # so net is now lower than the iter-82 confirmed-only baseline.
    # net still equals (confirmed-gross − fees − vat − refunds) exactly:
    computed = t["gross"] - t["fees"] - t["fees_vat"] - t["refund_full"] - t["refund_partial"]
    assert abs(computed - t["net"]) < 1.0, t


def test_tabby_refunds_excluded_from_net(central):
    t = _row(central, "tabby")
    assert t is not None, "Tabby row missing"
    assert t["refunded_orders_count"] >= 1, t
    assert t["refund_full"] >= 350, t
    computed = t["gross"] - t["fees"] - t["fees_vat"] - t["refund_full"] - t["refund_partial"]
    assert abs(computed - t["net"]) < 1.0, t


def test_no_fees_charged_on_refunded_orders(central):
    """Fees + VAT should NOT be applied to status-refunded orders."""
    t = _row(central, "tamara")
    # Excluding refunded orders: confirmed-only gross × 6.99%.
    # Confirmed = total gross (gross already excludes pending+cancelled+refund-only count).
    confirmed_billable = t["gross"] - t["refund_full"]
    expected_fees = round(confirmed_billable * 0.0699, 2)
    assert abs(t["fees"] - expected_fees) < 2.0, (t, expected_fees)


def test_reconciliation_matches_central(auth, central):
    r = auth.get(
        f"{BASE_URL}/api/reconciliation/summary"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    )
    r.raise_for_status()
    rec = r.json()
    rec_map = {p["normalized_payment_method"]: p
               for p in rec.get("platforms", [])}
    for key in ("tamara", "tabby"):
        crow = _row(central, key)
        prow = rec_map.get(key)
        assert prow is not None, f"missing rec row for {key}"
        assert prow["expected_source"] == "central"
        assert abs(prow["expected"] - crow["net"]) < 0.5, (key, prow, crow)


def test_refund_monitor_surfaces_status_refunds(auth):
    """The Refund Monitor must surface Tamara/Tabby refunds even though
    no settlement file is uploaded — they come purely from order_status."""
    r = auth.get(f"{BASE_URL}/api/reports/refunds-alert?period=this_year", timeout=15)
    r.raise_for_status()
    data = r.json()
    assert data["summary"]["refund_orders_count"] >= 12  # at least tamara 11 + tabby 1
    # by_payment_method should include both
    methods = {row["payment_method"]: row for row in data.get("by_payment_method", [])}
    assert "تمارا" in methods, methods
    assert "تابي" in methods, methods
    assert methods["تمارا"]["amount"] >= 2_600, methods["تمارا"]
    assert methods["تابي"]["amount"] >= 350, methods["تابي"]


def test_totals_remain_internally_consistent(central):
    """gross − fees − vat − refund_full − refund_partial == net per row."""
    for r in central.get("rows", []):
        computed = (r["gross"] - r["fees"] - r["fees_vat"]
                    - r["refund_full"] - r["refund_partial"])
        assert abs(computed - r["net"]) < 1.0, r


def test_totals_match_sum_of_rows(central):
    rows = [r for r in central.get("rows", []) if r["key"] != "_other"]
    s_gross = sum(r["gross"] for r in rows)
    s_net = sum(r["net"] for r in rows)
    s_refund = sum(r["refund_full"] + r["refund_partial"] for r in rows)
    t = central["totals"]
    # totals include _other if present, allow small drift
    assert abs(t["gross"] - s_gross) < 5_000, (t, s_gross)
    assert abs(t["net"] - s_net) < 5_000, (t, s_net)
    assert t["refund_total"] >= s_refund - 1, (t, s_refund)
