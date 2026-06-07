"""Iter-83 — Order Status Policy + Pending bucket end-to-end.

Validates:
  • GET  /api/order-status-policy returns rows + 4 categories
  • PUT  /api/order-status-policy persists overrides
  • POST /api/order-status-policy/reset removes overrides
  • compute_metrics honours overrides — pending status moves from net
    into pending_gross
  • Cross-page: pending_gross also exposed in totals
"""
import os
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
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    yield s
    # cleanup: reset policy
    s.post(f"{BASE_URL}/api/order-status-policy/reset", timeout=10)


def test_get_policy_returns_categories_and_rows(auth):
    r = auth.get(f"{BASE_URL}/api/order-status-policy", timeout=10)
    r.raise_for_status()
    d = r.json()
    keys = [c["key"] for c in d["categories"]]
    assert keys == ["confirmed", "pending", "refunded", "cancelled"]
    # observed at least the well-known statuses
    statuses = {row["status"] for row in d["rows"]}
    assert "تم التوصيل" in statuses
    assert "ملغي" in statuses
    assert "مسترجع" in statuses


def test_defaults_correct(auth):
    r = auth.get(f"{BASE_URL}/api/order-status-policy", timeout=10).json()
    by_status = {row["status"]: row for row in r["rows"]}
    assert by_status["تم التوصيل"]["category"] == "confirmed"
    assert by_status["تم التنفيذ"]["category"] == "confirmed"
    assert by_status["تم الشحن"]["category"] == "pending"
    assert by_status["جاري التوصيل"]["category"] == "pending"
    assert by_status["تم المراجعة"]["category"] == "pending"
    assert by_status["قيد التنفيذ"]["category"] == "pending"
    assert by_status["بإنتظار المراجعة"]["category"] == "pending"
    assert by_status["مسترجع"]["category"] == "refunded"
    assert by_status["ملغي"]["category"] == "cancelled"


def test_pending_bucket_exposed_in_metrics(auth):
    auth.post(f"{BASE_URL}/api/order-status-policy/reset", timeout=10)
    r = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    ).json()
    t = r["totals"]
    # pending_gross / pending_orders_count must be present
    assert "pending_gross" in t
    assert "pending_orders_count" in t
    assert t["pending_gross"] > 0, t
    assert t["pending_orders_count"] >= 700, t
    # net must not include pending
    # confirmed sales gross + refunded gross + pending gross + cancelled
    # ≈ all orders total. net should be much less than all-gross.
    assert t["net"] < (t["gross"] + t["pending_gross"]), t


def test_override_moves_status_from_confirmed_to_pending(auth):
    # First, base state
    base = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    ).json()
    base_net = base["totals"]["net"]
    base_pending = base["totals"]["pending_gross"]

    # Move "تم التوصيل" from confirmed → pending
    r = auth.put(f"{BASE_URL}/api/order-status-policy", json={
        "items": [{"status": "تم التوصيل", "category": "pending"}]
    }, timeout=10)
    r.raise_for_status()

    after = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    ).json()
    assert after["totals"]["net"] < base_net, (after["totals"], base_net)
    assert after["totals"]["pending_gross"] > base_pending, (
        after["totals"]["pending_gross"], base_pending,
    )

    # Reset
    auth.post(f"{BASE_URL}/api/order-status-policy/reset", timeout=10)
    after_reset = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    ).json()
    assert abs(after_reset["totals"]["net"] - base_net) < 1.0
    assert abs(after_reset["totals"]["pending_gross"] - base_pending) < 1.0


def test_reset_returns_count(auth):
    # seed
    auth.put(f"{BASE_URL}/api/order-status-policy", json={
        "items": [{"status": "تم التوصيل", "category": "pending"}]
    }, timeout=10)
    r = auth.post(f"{BASE_URL}/api/order-status-policy/reset", timeout=10)
    r.raise_for_status()
    assert r.json()["deleted"] >= 1


def test_invalid_category_rejected(auth):
    r = auth.put(f"{BASE_URL}/api/order-status-policy", json={
        "items": [{"status": "ملغي", "category": "junk"}]
    }, timeout=10)
    assert r.status_code in (400, 422), r.status_code


def test_per_gateway_pending_present(auth):
    r = auth.get(
        f"{BASE_URL}/api/payment-gateway-metrics"
        f"?from_date=2026-01-01&to_date=2026-12-31",
        timeout=15,
    ).json()
    # at least one row exposes pending fields
    rows = r["rows"]
    assert any("pending_gross" in row and "pending_orders_count" in row for row in rows)
    # tamara / tabby should have pending counts
    by_key = {row["key"]: row for row in rows}
    assert by_key.get("tamara", {}).get("pending_orders_count", 0) > 0
    assert by_key.get("tabby", {}).get("pending_orders_count", 0) > 0
