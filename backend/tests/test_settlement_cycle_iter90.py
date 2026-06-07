"""Iter-90 — Settlement cycle settings + health state machine."""
import os
import requests
import pytest

BASE = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
)


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login",
               json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
               timeout=10)
    r.raise_for_status()
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    yield s
    s.post(f"{BASE}/api/settlement-cycle/reset", timeout=10)


def test_settings_lists_four_gateways(auth):
    d = auth.get(f"{BASE}/api/settlement-cycle/settings", timeout=10).json()
    assert {"salla", "tamara", "tabby", "emkan"} == {g["key"] for g in d["gateways"]}
    for g in d["gateways"]:
        assert g["issuance_days"] == 8
        assert g["transfer_days"] == 2
        assert g["is_default"] is True


def test_settings_update_and_reset(auth):
    payload = {"items": [{
        "gateway": "tamara", "issuance_days": 10, "transfer_days": 3,
        "transfer_weekdays": [0, 3], "alerts_enabled": False,
    }]}
    r = auth.put(f"{BASE}/api/settlement-cycle/settings", json=payload, timeout=10)
    r.raise_for_status()
    d = auth.get(f"{BASE}/api/settlement-cycle/settings", timeout=10).json()
    tam = next(g for g in d["gateways"] if g["key"] == "tamara")
    assert tam["issuance_days"] == 10
    assert tam["transfer_days"] == 3
    assert tam["transfer_weekdays"] == [0, 3]
    assert tam["alerts_enabled"] is False
    assert tam["is_default"] is False
    # reset
    r = auth.post(f"{BASE}/api/settlement-cycle/reset", timeout=10)
    assert r.json()["deleted"] >= 1


def test_health_returns_real_data_buckets(auth):
    d = auth.get(f"{BASE}/api/settlement-cycle/health", timeout=20).json()
    assert "today" in d
    assert {"expected", "transferred", "pending", "overdue", "overdue_count"} == set(d["totals"].keys())
    assert d["totals"]["expected"] > 0   # real merchant data
    # 4 gateways
    assert {r["gateway"] for r in d["rows"]} == {"salla", "tamara", "tabby", "emkan"}
    for r in d["rows"]:
        b = r["buckets"]
        for k in ("in_cycle", "awaiting", "due_today", "overdue"):
            assert k in b
            assert b[k]["amount"] >= 0
            assert b[k]["count"] >= 0
        # consistency: sum of buckets == pending total
        bsum = sum(b[k]["amount"] for k in ("in_cycle", "awaiting", "due_today", "overdue"))
        assert abs(bsum - r["totals"]["pending"]) < 1.0


def test_health_overdue_provides_oldest(auth):
    d = auth.get(f"{BASE}/api/settlement-cycle/health", timeout=20).json()
    # at least one gateway has overdue with real data
    overdue_rows = [r for r in d["rows"] if r["buckets"]["overdue"]["count"] > 0]
    assert len(overdue_rows) >= 1
    for r in overdue_rows:
        ov = r["buckets"]["overdue"]
        assert ov["oldest_due_date"] is not None
        assert ov["max_days_late"] >= 0


def test_health_totals_consistency(auth):
    d = auth.get(f"{BASE}/api/settlement-cycle/health", timeout=20).json()
    # Sum of per-row totals = grand totals
    for k in ("expected", "transferred", "pending", "overdue"):
        s = round(sum(r["totals"][k] for r in d["rows"]), 2)
        assert abs(s - d["totals"][k]) < 1.0, (k, s, d["totals"][k])
