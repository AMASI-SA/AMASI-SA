"""Backend API tests for Hesab (Salla Accounting App)."""
import os
import io
import uuid
import pytest
import requests
from datetime import date

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
SAMPLE_XLSX = "/tmp/salla_test.xlsx"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@hesab.app")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(session):
    r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


@pytest.fixture(scope="session")
def test_user(session):
    """Register a fresh user per test session."""
    email = f"TEST_{uuid.uuid4().hex[:8]}@hesab.app"
    payload = {"name": "Test User", "email": email, "password": "test12345"}
    r = session.post(f"{API}/auth/register", json=payload)
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    data = r.json()
    return {"email": email, "password": "test12345", "token": data["access_token"], "id": data["id"]}


@pytest.fixture(scope="session")
def user_headers(test_user):
    return {"Authorization": f"Bearer {test_user['token']}", "Content-Type": "application/json"}


# ── Health ────────────────────────────────────────────────────────────────────
def test_health_root():
    r = requests.get(f"{API}/")
    assert r.status_code == 200
    assert "Hesab" in r.json().get("message", "")


# ── Auth ──────────────────────────────────────────────────────────────────────
class TestAuth:
    def test_admin_login(self, session):
        r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data and len(data["access_token"]) > 20
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"
        # httpOnly cookie should be set
        assert "access_token" in r.cookies

    def test_login_invalid(self, session):
        r = session.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me_with_token(self, session, admin_token):
        r = session.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_me_no_token(self, session):
        # Fresh session to avoid cookie reuse
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_register_new_user(self, session):
        email = f"TEST_{uuid.uuid4().hex[:8]}@hesab.app"
        r = session.post(f"{API}/auth/register", json={"name": "Reg", "email": email, "password": "secret123"})
        assert r.status_code == 200
        d = r.json()
        assert d["email"] == email.lower()
        assert "access_token" in d

    def test_register_duplicate(self, session, test_user):
        r = session.post(f"{API}/auth/register", json={"name": "Dup", "email": test_user["email"], "password": "pw123456"})
        assert r.status_code == 400

    def test_logout(self, session, admin_token):
        r = session.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200


# ── Settings ──────────────────────────────────────────────────────────────────
class TestSettings:
    def test_get_default_settings(self, user_headers):
        r = requests.get(f"{API}/settings", headers=user_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["payment_methods"], list) and len(data["payment_methods"]) > 0
        assert isinstance(data["shipping_companies"], list) and len(data["shipping_companies"]) > 0
        # Verify default payment method exists
        names = [pm["name"] for pm in data["payment_methods"]]
        assert "مدى" in names

    def test_update_settings(self, user_headers):
        payload = {
            "payment_methods": [
                {"name": "مدى", "commission_percent": 1.5},
                {"name": "Apple Pay", "commission_percent": 2.5},
            ],
            "shipping_companies": [{"name": "سمسا", "cost_per_order": 25.0}],
        }
        r = requests.put(f"{API}/settings", headers=user_headers, json=payload)
        assert r.status_code == 200
        # Verify persistence
        g = requests.get(f"{API}/settings", headers=user_headers)
        assert g.status_code == 200
        body = g.json()
        assert body["payment_methods"][0]["commission_percent"] == 1.5
        assert body["shipping_companies"][0]["cost_per_order"] == 25.0


# ── Daily Costs ───────────────────────────────────────────────────────────────
class TestDailyCosts:
    @pytest.fixture(scope="class")
    def today(self):
        return date.today().isoformat()

    def test_upsert_daily_cost(self, user_headers, today):
        r = requests.post(
            f"{API}/daily-costs",
            headers=user_headers,
            json={"date": today, "snapchat_ads": 100, "tiktok_ads": 50, "instagram_ads": 25, "product_costs": 200},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["snapchat_ads"] == 100
        assert "id" in d

    def test_list_daily_costs(self, user_headers, today):
        r = requests.get(f"{API}/daily-costs", headers=user_headers)
        assert r.status_code == 200
        items = r.json()
        assert any(it["date"] == today for it in items)

    def test_upsert_overwrites(self, user_headers, today):
        r = requests.post(
            f"{API}/daily-costs",
            headers=user_headers,
            json={"date": today, "snapchat_ads": 999, "tiktok_ads": 0, "instagram_ads": 0, "product_costs": 0},
        )
        assert r.status_code == 200
        items = requests.get(f"{API}/daily-costs", headers=user_headers).json()
        match = [i for i in items if i["date"] == today]
        assert len(match) == 1 and match[0]["snapchat_ads"] == 999

    def test_delete_daily_cost(self, user_headers, today):
        r = requests.delete(f"{API}/daily-costs/{today}", headers=user_headers)
        assert r.status_code == 200
        items = requests.get(f"{API}/daily-costs", headers=user_headers).json()
        assert not any(i["date"] == today for i in items)


# ── Analyses ──────────────────────────────────────────────────────────────────
class TestAnalyses:
    @pytest.fixture(scope="class")
    def analysis_id(self, user_headers):
        """Create one analysis to use across tests."""
        with open(SAMPLE_XLSX, "rb") as f:
            files = {"file": ("salla_test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            params = {
                "name": "TEST_analysis",
                "date": "2026-01-10",
                "snapchat_ads": 50,
                "tiktok_ads": 30,
                "instagram_ads": 20,
                "product_costs": 100,
            }
            # Strip Content-Type to let requests set multipart boundary
            headers = {k: v for k, v in user_headers.items() if k.lower() != "content-type"}
            r = requests.post(f"{API}/analyses", headers=headers, files=files, params=params)
        assert r.status_code == 200, f"create_analysis failed: {r.status_code} {r.text}"
        d = r.json()
        assert d["report"]["summary"]["total_orders"] > 0
        assert d["report"]["summary"]["total_sales"] > 0
        return d["id"]

    def test_create_analysis_with_excel(self, analysis_id):
        assert analysis_id  # success in fixture

    def test_list_analyses(self, user_headers, analysis_id):
        r = requests.get(f"{API}/analyses", headers=user_headers)
        assert r.status_code == 200
        ids = [a["id"] for a in r.json()]
        assert analysis_id in ids

    def test_get_analysis_detail(self, user_headers, analysis_id):
        r = requests.get(f"{API}/analyses/{analysis_id}", headers=user_headers)
        assert r.status_code == 200
        d = r.json()
        summary = d["report"]["summary"]
        for k in ("total_sales", "total_orders", "total_payment_fees", "total_shipping_cost", "net_profit"):
            assert k in summary

    def test_export_excel(self, user_headers, analysis_id):
        r = requests.get(f"{API}/analyses/{analysis_id}/export/excel", headers=user_headers)
        assert r.status_code == 200
        assert r.content[:2] == b"PK"  # xlsx signature
        assert "spreadsheet" in r.headers.get("content-type", "").lower()

    def test_export_pdf(self, user_headers, analysis_id):
        r = requests.get(f"{API}/analyses/{analysis_id}/export/pdf", headers=user_headers)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"

    def test_auth_isolation(self, session, analysis_id):
        """Different user can't read another user's analysis."""
        email = f"TEST_iso_{uuid.uuid4().hex[:6]}@hesab.app"
        reg = session.post(f"{API}/auth/register", json={"name": "Iso", "email": email, "password": "pw123456"})
        assert reg.status_code == 200
        token = reg.json()["access_token"]
        r = requests.get(f"{API}/analyses/{analysis_id}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_delete_analysis(self, user_headers, analysis_id):
        r = requests.delete(f"{API}/analyses/{analysis_id}", headers=user_headers)
        assert r.status_code == 200
        # Verify deletion
        g = requests.get(f"{API}/analyses/{analysis_id}", headers=user_headers)
        assert g.status_code == 404


# ── Dashboard ─────────────────────────────────────────────────────────────────
class TestDashboard:
    def test_dashboard_aggregate(self, user_headers):
        # Create a quick analysis first
        with open(SAMPLE_XLSX, "rb") as f:
            files = {"file": ("salla_test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            headers = {k: v for k, v in user_headers.items() if k.lower() != "content-type"}
            requests.post(f"{API}/analyses", headers=headers, files=files,
                          params={"name": "TEST_dash", "date": "2026-01-05"})
        r = requests.get(f"{API}/dashboard", headers=user_headers)
        assert r.status_code == 200
        d = r.json()
        assert "totals" in d and "monthly" in d and "recent_analyses" in d
        assert d["totals"]["analyses_count"] >= 1


# ── Invalid file handling ─────────────────────────────────────────────────────
def test_create_analysis_invalid_file(user_headers):
    files = {"file": ("bad.txt", io.BytesIO(b"not an excel"), "text/plain")}
    headers = {k: v for k, v in user_headers.items() if k.lower() != "content-type"}
    r = requests.post(f"{API}/analyses", headers=headers, files=files)
    assert r.status_code == 400
