"""Backend tests for Phase 2.2 — Reconciliation Screen.

Endpoints under test:
- GET /api/reconciliation/summary
- GET /api/reconciliation/platform/{account_id}
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"
SALLA_ACCOUNT_ID = "94342064-ecfe-4419-8890-fa99f6cdc0be"


# ---------- shared session ----------
@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token")
    assert token, "No access_token returned"
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


# ---------- Auth gating ----------
class TestReconciliationAuth:
    def test_summary_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/reconciliation/summary", timeout=20)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"

    def test_platform_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/reconciliation/platform/{SALLA_ACCOUNT_ID}", timeout=20)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ---------- Summary endpoint ----------
class TestReconciliationSummary:
    def test_summary_shape(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "totals" in data and "platforms" in data
        t = data["totals"]
        for k in ("expected", "transferred", "pending", "collection_rate"):
            assert k in t, f"missing totals.{k}"

        # Each platform row contains required fields
        required = {
            "account_id", "name", "normalized_payment_method", "orders_count",
            "expected", "transferred", "pending", "current_balance",
            "collection_rate", "transfers_count", "last_transfer_at",
            "last_transfer_to_bank", "currency",
        }
        for p in data["platforms"]:
            missing = required - set(p.keys())
            assert not missing, f"platform {p.get('name')} missing fields: {missing}"

    def test_totals_match_accounts_expected(self, auth_session):
        # total_expected should equal sum of expected_orders_balance for non-hidden payment_platform accounts
        recon = auth_session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30).json()
        accs = auth_session.get(f"{BASE_URL}/api/accounts", timeout=30).json()
        # accounts API may return list or {accounts:[...]}
        accs_list = accs if isinstance(accs, list) else accs.get("accounts", [])
        pp = [a for a in accs_list
              if a.get("account_type") == "payment_platform"
              and a.get("status") != "hidden"]
        expected_sum = round(sum(float(a.get("expected_orders_balance") or 0) for a in pp), 2)
        assert abs(recon["totals"]["expected"] - expected_sum) < 0.05, \
            f"summary.totals.expected={recon['totals']['expected']} vs accounts sum={expected_sum}"
        # platforms count should equal pp count
        assert len(recon["platforms"]) == len(pp), \
            f"platforms count {len(recon['platforms'])} != non-hidden payment_platform accounts {len(pp)}"

    def test_pending_and_rate_math(self, auth_session):
        recon = auth_session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30).json()
        for p in recon["platforms"]:
            # pending = expected - transferred (rounded to 2 decimals)
            assert abs(p["pending"] - round(p["expected"] - p["transferred"], 2)) < 0.02, \
                f"pending mismatch on {p['name']}"
            if p["expected"] > 0:
                expected_rate = round(p["transferred"] / p["expected"] * 100, 2)
                assert abs(p["collection_rate"] - expected_rate) < 0.02, \
                    f"collection_rate mismatch on {p['name']}"
            else:
                assert p["collection_rate"] == 0.0

    def test_total_transferred_matches_outgoing_to_bank(self, auth_session):
        recon = auth_session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30).json()
        # Approach: sum platforms.transferred and ensure equals totals.transferred
        rows_sum = round(sum(p["transferred"] for p in recon["platforms"]), 2)
        assert abs(recon["totals"]["transferred"] - rows_sum) < 0.02, \
            f"totals.transferred={recon['totals']['transferred']} vs row sum={rows_sum}"

        # Cross-check with /api/transfers if available
        tr = auth_session.get(f"{BASE_URL}/api/transfers", timeout=30)
        if tr.status_code == 200:
            transfers = tr.json() if isinstance(tr.json(), list) else tr.json().get("transfers", [])
            # Get account map to identify bank destinations
            accs = auth_session.get(f"{BASE_URL}/api/accounts", timeout=30).json()
            accs_list = accs if isinstance(accs, list) else accs.get("accounts", [])
            bank_ids = {a["id"] for a in accs_list if a.get("account_type") == "bank"}
            pp_ids = {a["id"] for a in accs_list
                      if a.get("account_type") == "payment_platform" and a.get("status") != "hidden"}
            outgoing_to_bank = sum(
                float(t.get("amount") or 0) for t in transfers
                if t.get("from_account_id") in pp_ids and t.get("to_account_id") in bank_ids
            )
            assert abs(recon["totals"]["transferred"] - round(outgoing_to_bank, 2)) < 0.05, \
                f"recon transferred {recon['totals']['transferred']} vs transfers sum {outgoing_to_bank}"


# ---------- Platform detail endpoint ----------
class TestReconciliationPlatformDetail:
    def test_salla_detail_has_inma_transfer(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reconciliation/platform/{SALLA_ACCOUNT_ID}", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "summary" in data and "transfers" in data
        assert data["summary"]["account_id"] == SALLA_ACCOUNT_ID
        # At least one transfer
        assert len(data["transfers"]) >= 1, "expected at least 1 transfer (Salla→Inma 40,000)"
        # All transfers must be to bank accounts
        for t in data["transfers"]:
            assert t.get("to_account_type") == "bank", \
                f"non-bank destination in transfer {t.get('id')}: {t.get('to_account_type')}"
        # Inma Bank 40,000 transfer present
        inma = [t for t in data["transfers"]
                if (t.get("to_account_name") or "").find("الإنماء") >= 0
                or (t.get("to_account_name") or "").lower().find("inma") >= 0]
        assert inma, f"No Inma transfer found; names: {[t.get('to_account_name') for t in data['transfers']]}"
        amounts = [float(t.get("amount") or 0) for t in inma]
        assert any(abs(a - 40000.0) < 0.5 for a in amounts), \
            f"40,000 SAR transfer to Inma not found; amounts: {amounts}"

    def test_invalid_id_returns_404(self, auth_session):
        r = auth_session.get(f"{BASE_URL}/api/reconciliation/platform/this-does-not-exist", timeout=20)
        assert r.status_code == 404
        # Detail should be Arabic
        detail = r.json().get("detail", "")
        assert any("\u0600" <= ch <= "\u06FF" for ch in detail), f"Arabic detail expected, got: {detail!r}"
