"""Phase-1 balances tests: shipping & COD split (approved/unapproved).

Covers:
- GET /api/settings includes default shipping_approved_statuses & cod_approved_statuses
- PUT /api/settings persists overrides
- GET /api/balances structure (shipping/cod totals & buckets)
- Status matching is case-insensitive and works against order_status + slug
- COD only counts orders with cod-like payment_method
- /api/webhook/make/{token} accepts order_status_slug
- Dashboard totals include shipping_approved/unapproved & cod_approved/unapproved
- Date filtering works
- Per-user isolation
- Mutating shipping_approved_statuses via PUT reflects immediately in GET /balances
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def _login_or_register(email: str, password: str = "test12345"):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/register", json={"name": "T", "email": email, "password": password})
    if r.status_code not in (200, 201, 400):
        pytest.fail(f"register failed: {r.status_code} {r.text}")
    if r.status_code == 400:
        # already exists -> login
        r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    token = r.json().get("access_token")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _make_token(session) -> str:
    r = session.get(f"{BASE_URL}/api/webhook/settings")
    if r.status_code != 200:
        pytest.fail(f"token: {r.status_code} {r.text}")
    return r.json()["token"]


def _post_order(token: str, order: dict):
    return requests.post(f"{BASE_URL}/api/webhook/make/{token}", json=order)


@pytest.fixture(scope="module")
def user_a():
    return _login_or_register(f"TEST_bal_a_{uuid.uuid4().hex[:8]}@hesab.app")


@pytest.fixture(scope="module")
def user_b():
    return _login_or_register(f"TEST_bal_b_{uuid.uuid4().hex[:8]}@hesab.app")


@pytest.fixture(scope="module")
def seeded_user_a(user_a):
    """Seed user_a with 4 orders: 2 COD + 2 electronic, mixed statuses."""
    tok = _make_token(user_a)
    # 1: COD + delivered (Arabic) -> both shipping & COD approved
    _post_order(tok, {"order_number": "BAL-1", "order_date": "2026-01-10",
                      "order_status": "تم التوصيل", "payment_method": "الدفع عند الاستلام",
                      "shipping_company": "سمسا", "shipping_cost": "20", "total": "300"}).raise_for_status()
    # 2: COD + قيد التنفيذ -> neither approved
    _post_order(tok, {"order_number": "BAL-2", "order_date": "2026-01-11",
                      "order_status": "قيد التنفيذ", "payment_method": "cod",
                      "shipping_company": "سمسا", "shipping_cost": "25", "total": "150"}).raise_for_status()
    # 3: Electronic (مدى) + delivered (slug) -> shipping approved, COD not counted
    _post_order(tok, {"order_number": "BAL-3", "order_date": "2026-01-12",
                      "order_status": "Delivered", "order_status_slug": "delivered",
                      "payment_method": "مدى",
                      "shipping_company": "أرامكس", "shipping_cost": "30", "total": "500"}).raise_for_status()
    # 4: Electronic (Apple Pay) + جاري التوصيل -> not approved, not COD
    _post_order(tok, {"order_number": "BAL-4", "order_date": "2026-01-13",
                      "order_status": "جاري التوصيل", "payment_method": "Apple Pay",
                      "shipping_company": "جندل", "shipping_cost": "15", "total": "200"}).raise_for_status()
    return user_a


# ── Settings ----------------------------------------------------------------
class TestSettingsApprovedStatuses:
    def test_get_settings_defaults_present(self, user_a):
        r = user_a.get(f"{BASE_URL}/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "shipping_approved_statuses" in data
        assert "cod_approved_statuses" in data
        # spec defaults
        for v in ("تم التوصيل", "delivered", "completed", "تم الاستلام"):
            assert v in data["shipping_approved_statuses"], f"missing {v}"
        for v in ("تم التوصيل", "delivered", "completed"):
            assert v in data["cod_approved_statuses"], f"missing {v}"

    def test_put_settings_persists_lists(self, user_a):
        cur = user_a.get(f"{BASE_URL}/api/settings").json()
        payload = {
            "payment_methods": cur["payment_methods"],
            "shipping_companies": cur["shipping_companies"],
            "shipping_approved_statuses": ["تم التوصيل", "delivered", "completed", "تم الاستلام"],
            "cod_approved_statuses": ["تم التوصيل", "delivered", "completed"],
        }
        r = user_a.put(f"{BASE_URL}/api/settings", json=payload)
        assert r.status_code == 200, r.text
        # GET back
        after = user_a.get(f"{BASE_URL}/api/settings").json()
        assert sorted(after["shipping_approved_statuses"]) == sorted(payload["shipping_approved_statuses"])
        assert sorted(after["cod_approved_statuses"]) == sorted(payload["cod_approved_statuses"])


# ── Webhook order_status_slug -----------------------------------------------
class TestWebhookSlug:
    def test_webhook_accepts_slug(self, user_a):
        tok = _make_token(user_a)
        r = _post_order(tok, {
            "order_number": f"SLUG-{uuid.uuid4().hex[:6]}",
            "order_date": "2026-01-15",
            "order_status": "Delivered", "order_status_slug": "delivered",
            "payment_method": "مدى", "shipping_company": "سمسا",
            "shipping_cost": "10", "total": "100",
        })
        assert r.status_code == 200, r.text


# ── Balances core -----------------------------------------------------------
class TestBalancesCore:
    def test_balances_structure(self, seeded_user_a):
        r = seeded_user_a.get(f"{BASE_URL}/api/balances",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-13"})
        assert r.status_code == 200, r.text
        data = r.json()
        for top in ("shipping", "cod"):
            assert top in data
            for k in ("total_approved", "total_unapproved", "by_company", "by_status",
                      "approved_orders", "unapproved_orders"):
                assert k in data[top], f"{top}.{k} missing"

    def test_shipping_split_amounts(self, seeded_user_a):
        r = seeded_user_a.get(f"{BASE_URL}/api/balances",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        # Default approved statuses: تم التوصيل + delivered + completed + تم الاستلام
        # BAL-1 (تم التوصيل, 20) + BAL-3 (delivered slug, 30) = 50
        # BAL-2 (قيد التنفيذ, 25) + BAL-4 (جاري التوصيل, 15) = 40
        assert r["shipping"]["total_approved"] == pytest.approx(50.0, abs=0.05)
        assert r["shipping"]["total_unapproved"] == pytest.approx(40.0, abs=0.05)
        assert r["shipping"]["approved_orders"] == 2
        assert r["shipping"]["unapproved_orders"] == 2

    def test_cod_only_for_cod_orders(self, seeded_user_a):
        r = seeded_user_a.get(f"{BASE_URL}/api/balances",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        # Only BAL-1 (300, approved) and BAL-2 (150, unapproved) are COD
        assert r["cod"]["total_approved"] == pytest.approx(300.0, abs=0.05)
        assert r["cod"]["total_unapproved"] == pytest.approx(150.0, abs=0.05)
        assert r["cod"]["approved_orders"] == 1
        assert r["cod"]["unapproved_orders"] == 1

    def test_case_insensitive_match(self, seeded_user_a):
        # BAL-3 uses order_status="Delivered" (capital D); should still match
        r = seeded_user_a.get(f"{BASE_URL}/api/balances",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        # approved_orders==2 verified above already includes BAL-3 capital D
        assert r["shipping"]["approved_orders"] >= 2

    def test_date_filter_excludes_outside(self, seeded_user_a):
        r = seeded_user_a.get(f"{BASE_URL}/api/balances",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-10"}).json()
        # Only BAL-1 in this window
        assert r["shipping"]["approved_orders"] + r["shipping"]["unapproved_orders"] == 1
        assert r["shipping"]["total_approved"] == pytest.approx(20.0, abs=0.05)


# ── Dashboard totals --------------------------------------------------------
class TestDashboardBalances:
    def test_dashboard_totals_have_balance_keys(self, seeded_user_a):
        r = seeded_user_a.get(f"{BASE_URL}/api/dashboard",
                              params={"from_date": "2026-01-10", "to_date": "2026-01-13"})
        assert r.status_code == 200, r.text
        totals = r.json().get("totals", {})
        for k in ("shipping_approved", "shipping_unapproved", "cod_approved", "cod_unapproved"):
            assert k in totals, f"totals.{k} missing"
        assert totals["shipping_approved"] == pytest.approx(50.0, abs=0.05)
        assert totals["shipping_unapproved"] == pytest.approx(40.0, abs=0.05)
        assert totals["cod_approved"] == pytest.approx(300.0, abs=0.05)
        assert totals["cod_unapproved"] == pytest.approx(150.0, abs=0.05)


# ── Isolation ---------------------------------------------------------------
class TestIsolation:
    def test_user_b_sees_zero(self, seeded_user_a, user_b):
        r = user_b.get(f"{BASE_URL}/api/balances",
                       params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        assert r["shipping"]["total_approved"] == 0
        assert r["shipping"]["total_unapproved"] == 0
        assert r["cod"]["total_approved"] == 0
        assert r["cod"]["total_unapproved"] == 0


# ── Settings mutation reflects in balances -----------------------------------
class TestSettingsMutationReflectsBalances:
    def test_adding_extra_status_moves_unapproved_to_approved(self, seeded_user_a):
        # Before: BAL-4 "جاري التوصيل" (15) is unapproved
        before = seeded_user_a.get(f"{BASE_URL}/api/balances",
                                   params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        # PUT settings: add "جاري التوصيل" to shipping_approved
        cur = seeded_user_a.get(f"{BASE_URL}/api/settings").json()
        payload = {
            "payment_methods": cur["payment_methods"],
            "shipping_companies": cur["shipping_companies"],
            "shipping_approved_statuses": cur["shipping_approved_statuses"] + ["جاري التوصيل"],
            "cod_approved_statuses": cur["cod_approved_statuses"],
        }
        r = seeded_user_a.put(f"{BASE_URL}/api/settings", json=payload)
        assert r.status_code == 200, r.text
        # After: 15 should shift from unapproved -> approved
        after = seeded_user_a.get(f"{BASE_URL}/api/balances",
                                  params={"from_date": "2026-01-10", "to_date": "2026-01-13"}).json()
        assert after["shipping"]["total_approved"] == pytest.approx(
            before["shipping"]["total_approved"] + 15.0, abs=0.05
        )
        assert after["shipping"]["total_unapproved"] == pytest.approx(
            before["shipping"]["total_unapproved"] - 15.0, abs=0.05
        )
        # Restore for other tests
        payload["shipping_approved_statuses"] = cur["shipping_approved_statuses"]
        seeded_user_a.put(f"{BASE_URL}/api/settings", json=payload)
