"""Iter-89 — Cost-per-order tile on platform cards (Snap/Meta/TikTok)."""
import os
import requests
import pytest

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
)


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
               timeout=10)
    r.raise_for_status()
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.mark.parametrize("endpoint", [
    "/api/dashboard/snapchat-summary",
    "/api/dashboard/meta-summary",
    "/api/dashboard/tiktok-summary",
])
def test_cpo_field_present_in_all_periods(auth, endpoint):
    d = auth.get(f"{BASE_URL}{endpoint}", timeout=15).json()
    for period in ("today", "month", "last_30d"):
        assert period in d, (endpoint, list(d.keys()))
        p = d[period]
        assert "cost_per_order" in p, (endpoint, period, list(p.keys()))


@pytest.mark.parametrize("endpoint", [
    "/api/dashboard/snapchat-summary",
    "/api/dashboard/meta-summary",
    "/api/dashboard/tiktok-summary",
])
def test_cpo_math_correct(auth, endpoint):
    """CPO = spend / orders when both > 0, otherwise None."""
    d = auth.get(f"{BASE_URL}{endpoint}", timeout=15).json()
    for period in ("today", "month", "last_30d"):
        p = d[period]
        spend = p.get("spend", 0)
        orders = p.get("orders", 0)
        cpo = p.get("cost_per_order")
        if spend > 0 and orders > 0:
            expected = round(spend / orders, 2)
            assert abs(cpo - expected) < 0.01, (endpoint, period, spend, orders, cpo, expected)
        else:
            assert cpo is None, (endpoint, period, spend, orders, cpo)


def test_snap_official_card_exposes_cpo(auth):
    d = auth.get(f"{BASE_URL}/api/snapchat/reference-stats", timeout=20).json()
    if "yesterday" in d and d["yesterday"].get("purchases") is not None:
        assert "cost_per_order" in d["yesterday"]
    if "month" in d and d["month"].get("purchases") is not None:
        assert "cost_per_order" in d["month"]
