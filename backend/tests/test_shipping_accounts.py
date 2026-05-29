"""Backend tests for the deferred shipping accounts feature."""
import os
import uuid
import urllib.parse
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
SAMPLE_XLSX = "/tmp/salla_test.xlsx"


# ── Helpers ────────────────────────────────────────────────────────────────
def _register():
    email = f"TEST_ship_{uuid.uuid4().hex[:8]}@hesab.app"
    r = requests.post(f"{API}/auth/register", json={"name": "ShipUser", "email": email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], email


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def _set_deferred(token, company_name="سمسا"):
    """Mark a shipping company as deferred via PUT /api/settings."""
    s = requests.get(f"{API}/settings", headers=_hdr(token)).json()
    for sc in s["shipping_companies"]:
        sc["is_deferred"] = (sc["name"] == company_name)
    r = requests.put(f"{API}/settings", headers=_hdr(token),
                     json={"payment_methods": s["payment_methods"], "shipping_companies": s["shipping_companies"]})
    assert r.status_code == 200, r.text


def _upload_xlsx(token, name="TEST_ship_analysis"):
    files = {"file": ("salla_test.xlsx", open(SAMPLE_XLSX, "rb"),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = requests.post(f"{API}/analyses", headers={"Authorization": f"Bearer {token}"},
                      files=files, params={"name": name, "date": "2026-01-09"})
    assert r.status_code == 200, r.text
    return r.json()


# ── Module-scoped: user with deferred 'سمسا' + one uploaded analysis ───────
@pytest.fixture(scope="module")
def deferred_user():
    token, email = _register()
    _set_deferred(token, "سمسا")
    analysis = _upload_xlsx(token)
    return {"token": token, "email": email, "analysis": analysis}


# ── 1. Settings: is_deferred round-trip ────────────────────────────────────
class TestSettingsIsDeferred:
    def test_default_is_deferred_false(self):
        tok, _ = _register()
        r = requests.get(f"{API}/settings", headers=_hdr(tok))
        assert r.status_code == 200
        for sc in r.json()["shipping_companies"]:
            assert sc.get("is_deferred") is False

    def test_put_persists_is_deferred(self):
        tok, _ = _register()
        _set_deferred(tok, "سمسا")
        r = requests.get(f"{API}/settings", headers=_hdr(tok))
        assert r.status_code == 200
        names = {sc["name"]: sc.get("is_deferred") for sc in r.json()["shipping_companies"]}
        assert names["سمسا"] is True
        assert names["جندل"] is False


# ── 2. /shipping-accounts list + totals ────────────────────────────────────
class TestShippingAccountsList:
    def test_empty_user_returns_only_configured(self):
        tok, _ = _register()
        _set_deferred(tok, "أرامكس")
        r = requests.get(f"{API}/shipping-accounts", headers=_hdr(tok))
        assert r.status_code == 200
        body = r.json()
        assert "accounts" in body and "totals" in body
        names = [a["name"] for a in body["accounts"]]
        assert "أرامكس" in names
        aramex = next(a for a in body["accounts"] if a["name"] == "أرامكس")
        assert aramex["total_owed"] == 0.0 and aramex["total_paid"] == 0.0 and aramex["remaining"] == 0.0
        assert body["totals"] == {"total_owed": 0.0, "total_paid": 0.0, "remaining": 0.0}

    def test_after_upload_owed_is_positive(self, deferred_user):
        r = requests.get(f"{API}/shipping-accounts", headers=_hdr(deferred_user["token"]))
        assert r.status_code == 200
        body = r.json()
        semsa = next((a for a in body["accounts"] if a["name"] == "سمسا"), None)
        assert semsa is not None, f"سمسا missing from accounts: {body}"
        assert semsa["total_owed"] > 0, f"expected total_owed>0 for سمسا, got {semsa}"
        assert body["totals"]["total_owed"] >= semsa["total_owed"]
        assert body["totals"]["remaining"] == round(body["totals"]["total_owed"] - body["totals"]["total_paid"], 2)


# ── 3. POST /shipping-accounts/{company}/payments ──────────────────────────
class TestAddPayment:
    def test_add_payment_success_and_persist(self, deferred_user):
        tok = deferred_user["token"]
        name = "سمسا"
        url = f"{API}/shipping-accounts/{urllib.parse.quote(name, safe='')}/payments"
        payload = {"amount": 50.0, "payment_date": "2026-01-09", "invoice_number": "INV-1", "note": "first"}
        r = requests.post(url, headers=_hdr(tok), json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["amount"] == 50.0 and body["invoice_number"] == "INV-1"
        assert "id" in body and "_id" not in body

        # Verify totals updated
        s = requests.get(f"{API}/shipping-accounts", headers=_hdr(tok)).json()
        semsa = next(a for a in s["accounts"] if a["name"] == "سمسا")
        assert semsa["total_paid"] >= 50.0

    def test_reject_negative_amount(self, deferred_user):
        url = f"{API}/shipping-accounts/{urllib.parse.quote('سمسا', safe='')}/payments"
        r = requests.post(url, headers=_hdr(deferred_user["token"]),
                          json={"amount": -5, "payment_date": "2026-01-09"})
        assert r.status_code == 422

    def test_reject_zero_amount(self, deferred_user):
        url = f"{API}/shipping-accounts/{urllib.parse.quote('سمسا', safe='')}/payments"
        r = requests.post(url, headers=_hdr(deferred_user["token"]),
                          json={"amount": 0, "payment_date": "2026-01-09"})
        assert r.status_code == 422

    def test_reject_bad_date(self, deferred_user):
        url = f"{API}/shipping-accounts/{urllib.parse.quote('سمسا', safe='')}/payments"
        r = requests.post(url, headers=_hdr(deferred_user["token"]),
                          json={"amount": 10, "payment_date": "09-01-2026"})
        assert r.status_code == 400


# ── 4. GET payments + URL-encoded Arabic + DESC order ──────────────────────
class TestListAndDeletePayments:
    def test_list_payments_desc(self, deferred_user):
        tok = deferred_user["token"]
        encoded = urllib.parse.quote("سمسا", safe="")
        # Add two payments with different dates
        requests.post(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok),
                      json={"amount": 11, "payment_date": "2026-01-02"})
        requests.post(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok),
                      json={"amount": 22, "payment_date": "2026-01-08"})
        r = requests.get(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok))
        assert r.status_code == 200
        items = r.json()["payments"]
        assert len(items) >= 2
        dates = [p["payment_date"] for p in items]
        assert dates == sorted(dates, reverse=True), f"payments not DESC: {dates}"

    def test_delete_payment_and_404(self, deferred_user):
        tok = deferred_user["token"]
        encoded = urllib.parse.quote("سمسا", safe="")
        added = requests.post(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok),
                              json={"amount": 9, "payment_date": "2026-01-10"}).json()
        pid = added["id"]
        d = requests.delete(f"{API}/shipping-accounts/payments/{pid}", headers=_hdr(tok))
        assert d.status_code == 200
        # Second delete -> 404
        d2 = requests.delete(f"{API}/shipping-accounts/payments/{pid}", headers=_hdr(tok))
        assert d2.status_code == 404


# ── 5. User isolation ─────────────────────────────────────────────────────
class TestUserIsolation:
    def test_user_b_cannot_see_user_a_accounts_or_payments(self, deferred_user):
        tok_a = deferred_user["token"]
        encoded = urllib.parse.quote("سمسا", safe="")
        # user A creates a payment
        added = requests.post(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok_a),
                              json={"amount": 77, "payment_date": "2026-01-11"}).json()
        pid_a = added["id"]

        tok_b, _ = _register()
        # B's accounts should NOT include A's owed
        rb = requests.get(f"{API}/shipping-accounts", headers=_hdr(tok_b)).json()
        assert rb["totals"]["total_owed"] == 0.0
        assert rb["totals"]["total_paid"] == 0.0

        # B's list of payments for سمسا should be empty
        lp = requests.get(f"{API}/shipping-accounts/{encoded}/payments", headers=_hdr(tok_b)).json()
        assert lp["payments"] == []

        # B trying to delete A's payment -> 404
        d = requests.delete(f"{API}/shipping-accounts/payments/{pid_a}", headers=_hdr(tok_b))
        assert d.status_code == 404


# ── 6. Excel upload with is_deferred=true integration ─────────────────────
class TestExcelIntegration:
    def test_upload_after_deferred_flag(self, deferred_user):
        report = deferred_user["analysis"]["report"]
        assert report["summary"]["deferred_shipping_cost"] > 0
        semsa_row = next(s for s in report["shipping_breakdown"] if s["name"] == "سمسا")
        assert semsa_row["is_deferred"] is True
        # total_shipping_cost INCLUDES deferred
        assert report["summary"]["total_shipping_cost"] >= report["summary"]["deferred_shipping_cost"]

        accts = requests.get(f"{API}/shipping-accounts", headers=_hdr(deferred_user["token"])).json()
        semsa = next(a for a in accts["accounts"] if a["name"] == "سمسا")
        # accounts.total_owed should equal sum of shipping_breakdown.total_cost for deferred=true
        # Since we have one analysis, should equal semsa_row total_cost
        assert abs(semsa["total_owed"] - semsa_row["total_cost"]) < 0.5


# ── 7. /api/dashboard new fields ──────────────────────────────────────────
class TestDashboardNewFields:
    def test_dashboard_has_new_shipping_fields(self, deferred_user):
        r = requests.get(f"{API}/dashboard", headers=_hdr(deferred_user["token"]))
        assert r.status_code == 200
        t = r.json()["totals"]
        for key in ("deferred_shipping_cost", "regular_shipping_cost", "expected_salla_transfer"):
            assert key in t, f"missing key {key} in dashboard totals"
        assert t["deferred_shipping_cost"] > 0
        # expected_salla_transfer == total_sales - total_payment_fees - regular_shipping_cost
        expected = round(t["total_sales"] - t["total_payment_fees"] - t["regular_shipping_cost"], 2)
        assert abs(t["expected_salla_transfer"] - expected) < 0.01
        # regular == total - deferred
        assert abs(t["regular_shipping_cost"] - (t["total_shipping_cost"] - t["deferred_shipping_cost"])) < 0.01
