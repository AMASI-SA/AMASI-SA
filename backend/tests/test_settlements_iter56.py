"""Iter-56 — Payment Settlements unified ledger.

Covers:
- Provider auto-detection from raw payment_method strings.
- CRUD: create, list, edit, delete with owner scoping.
- 14-day window classification (Salla only).
- Adjusted_at vs order_created_at: adjustments fall in their adjusted_at period.
- Dashboard integration: settlements deduct from electronic_net / bnpl_net / bank_net.
- adjustment_amount is canonicalised server-side (original - new) regardless of client input.
"""
import os
import time
from datetime import datetime, timedelta

import requests

BACKEND_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
API = f"{BACKEND_URL}/api"

OWNER_EMAIL = "admin@hesab.app"
OWNER_PWD = "admin123"


def _login(email=OWNER_EMAIL, password=OWNER_PWD):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _cleanup_settlements(token, order_number_prefix="ITER56_"):
    r = requests.get(f"{API}/settlements?from_date=2020-01-01&to_date=2099-12-31",
                     headers=_h(token), timeout=15)
    for s in r.json():
        if s["order_number"].startswith(order_number_prefix):
            requests.delete(f"{API}/settlements/{s['id']}", headers=_h(token), timeout=15)


def test_provider_detection_via_create_route():
    token = _login()
    try:
        cases = [
            ("مدفوعات سلة", "salla"),
            ("Mada Card", "salla"),  # any electronic-ish without keyword → salla
            ("Apple Pay (سلة)", "salla"),
            ("تابي - Tabby", "tabby"),
            ("تمارا (Tamara)", "tamara"),
            ("إمكان", "emkan"),
            ("تحويل بنكي AlRajhi", "bank_transfer"),
            ("Cash on delivery", "cod"),
        ]
        for i, (raw, expected) in enumerate(cases):
            r = requests.post(f"{API}/settlements", headers=_h(token), json={
                "order_number": f"ITER56_PROV_{i}",
                "payment_method": raw,
                "original_amount": 100.0, "new_amount": 50.0,
                "adjustment_type": "partial_refund",
                "order_created_at": "2026-02-01", "adjusted_at": "2026-02-05",
            }, timeout=15)
            assert r.status_code == 200, f"{raw}: {r.text}"
            got = r.json()["provider"]
            assert got == expected, f"For '{raw}' expected {expected} got {got}"
    finally:
        _cleanup_settlements(token, "ITER56_PROV_")


def test_create_canonicalises_adjustment_amount():
    token = _login()
    try:
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_AMT_1",
            "payment_method": "مدفوعات سلة",
            "original_amount": 500.0, "new_amount": 361.0,
            "adjustment_type": "partial_refund",
            "order_created_at": "2026-02-01", "adjusted_at": "2026-02-04",
        }, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["adjustment_amount"] == 139.0

        # Client-supplied mismatched amount is rejected
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_AMT_2",
            "payment_method": "مدفوعات سلة",
            "original_amount": 500.0, "new_amount": 361.0,
            "adjustment_amount": 200.0,  # mismatch
            "adjustment_type": "partial_refund",
            "order_created_at": "2026-02-01", "adjusted_at": "2026-02-04",
        }, timeout=15)
        assert r.status_code == 400

        # new_amount >= original_amount → rejected
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_AMT_3",
            "payment_method": "مدفوعات سلة",
            "original_amount": 100.0, "new_amount": 100.0,
            "adjustment_type": "partial_refund",
            "order_created_at": "2026-02-01", "adjusted_at": "2026-02-04",
        }, timeout=15)
        assert r.status_code == 400
    finally:
        _cleanup_settlements(token, "ITER56_AMT_")


def test_14d_window_classification():
    token = _login()
    today = datetime.utcnow().date()
    inside_order_date = (today - timedelta(days=7)).isoformat()
    outside_order_date = (today - timedelta(days=30)).isoformat()
    try:
        # Inside 14 days
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_WIN_INSIDE",
            "payment_method": "مدفوعات سلة",
            "original_amount": 200.0, "new_amount": 50.0,
            "adjustment_type": "partial_refund",
            "order_created_at": inside_order_date, "adjusted_at": today.isoformat(),
        }, timeout=15)
        assert r.json()["window"] == "inside_14d", r.text

        # Outside 14 days
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_WIN_OUTSIDE",
            "payment_method": "مدفوعات سلة",
            "original_amount": 200.0, "new_amount": 50.0,
            "adjustment_type": "partial_refund",
            "order_created_at": outside_order_date, "adjusted_at": today.isoformat(),
        }, timeout=15)
        assert r.json()["window"] == "outside_14d", r.text

        # Non-Salla provider has no 14d window
        r = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_WIN_TABBY",
            "payment_method": "تابي",
            "original_amount": 200.0, "new_amount": 50.0,
            "adjustment_type": "partial_refund",
            "order_created_at": outside_order_date, "adjusted_at": today.isoformat(),
        }, timeout=15)
        assert r.json()["window"] is None, r.text
    finally:
        _cleanup_settlements(token, "ITER56_WIN_")


def test_adjustments_use_adjusted_at_not_order_date_for_range_filter():
    """Critical business rule: a refund on a 30-day-old order should show up
    in TODAY's settlement totals, not in the order's original month."""
    token = _login()
    today = datetime.utcnow().date()
    old_order_date = (today - timedelta(days=30)).isoformat()
    try:
        requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_RANGE_1",
            "payment_method": "مدفوعات سلة",
            "original_amount": 1000.0, "new_amount": 500.0,
            "adjustment_type": "partial_refund",
            "order_created_at": old_order_date,
            "adjusted_at": today.isoformat(),
        }, timeout=15)

        # Range matching adjusted_at → should include the settlement
        r = requests.get(
            f"{API}/settlements/summary?from_date={today.isoformat()}&to_date={today.isoformat()}",
            headers=_h(token), timeout=15,
        ).json()
        assert r["by_provider"]["salla"]["total_adjustment"] >= 500.0

        # Range matching order_date (old) → should NOT include this settlement
        r = requests.get(
            f"{API}/settlements/summary?from_date={old_order_date}&to_date={old_order_date}",
            headers=_h(token), timeout=15,
        ).json()
        # Should be 0 for this specific old date (nothing was adjusted_at that day)
        assert r["by_provider"]["salla"]["total_adjustment"] == 0.0
    finally:
        _cleanup_settlements(token, "ITER56_RANGE_")


def test_dashboard_subtracts_salla_settlements_from_electronic_net():
    token = _login()
    today = datetime.utcnow().date().isoformat()
    try:
        # Get baseline electronic_net
        r0 = requests.get(f"{API}/dashboard?from_date={today}&to_date={today}",
                          headers=_h(token), timeout=15).json()
        base_net = float(r0["totals"]["electronic_net"])
        base_settles = float(r0["totals"]["settlements_by_provider"]["salla"]["total_adjustment"])

        # Add a 139 SAR adjustment
        requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_DASH_1",
            "payment_method": "مدفوعات سلة",
            "original_amount": 500.0, "new_amount": 361.0,
            "adjustment_type": "partial_refund",
            "order_created_at": today, "adjusted_at": today,
        }, timeout=15)

        r1 = requests.get(f"{API}/dashboard?from_date={today}&to_date={today}",
                          headers=_h(token), timeout=15).json()
        new_net = float(r1["totals"]["electronic_net"])
        new_settles = float(r1["totals"]["settlements_by_provider"]["salla"]["total_adjustment"])

        assert round(new_settles - base_settles, 2) == 139.0, \
            f"settlement total didn't move by 139: {base_settles} → {new_settles}"
        assert round(base_net - new_net, 2) == 139.0, \
            f"electronic_net didn't drop by 139: {base_net} → {new_net}"

        # Also: electronic_net_before_settlements should be unchanged
        before_ns = float(r1["totals"]["electronic_net_before_settlements"])
        assert round((before_ns - new_net), 2) == round(new_settles, 2), \
            "electronic_net_before_settlements - electronic_net should equal settlements"
    finally:
        _cleanup_settlements(token, "ITER56_DASH_")


def test_delete_and_update():
    token = _login()
    today = datetime.utcnow().date().isoformat()
    try:
        cr = requests.post(f"{API}/settlements", headers=_h(token), json={
            "order_number": "ITER56_CRUD_1",
            "payment_method": "تمارا",
            "original_amount": 300.0, "new_amount": 200.0,
            "adjustment_type": "partial_refund",
            "order_created_at": today, "adjusted_at": today,
            "reason": "initial",
        }, timeout=15)
        sid = cr.json()["id"]

        # Update new_amount → adjustment_amount must recompute
        up = requests.put(f"{API}/settlements/{sid}", headers=_h(token), json={
            "new_amount": 150.0,
            "reason": "updated",
        }, timeout=15).json()
        assert up["adjustment_amount"] == 150.0
        assert up["reason"] == "updated"

        # Bad update — new_amount >= original
        bad = requests.put(f"{API}/settlements/{sid}", headers=_h(token), json={
            "new_amount": 999.0,
        }, timeout=15)
        assert bad.status_code == 400

        # Delete
        d = requests.delete(f"{API}/settlements/{sid}", headers=_h(token), timeout=15)
        assert d.status_code == 200

        # 404 after delete
        d2 = requests.delete(f"{API}/settlements/{sid}", headers=_h(token), timeout=15)
        assert d2.status_code == 404
    finally:
        _cleanup_settlements(token, "ITER56_CRUD_")
