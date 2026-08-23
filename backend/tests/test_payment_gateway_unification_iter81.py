"""Iter-81 — Payment-Gateway Metrics Unification.

Validates the single source of truth:
- GET /api/payment-gateway-metrics returns canonical rows + totals
- Arabic letter folding (hamza variants) resolve to canonical keys
- /api/reconciliation/summary derives expected from central (expected_source=central)
- Per-account expected matches the central row sum for that account
"""
import os
import pytest
import requests

def _read_base_url():
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        try:
            for line in open("/app/frontend/.env"):
                if line.startswith("REACT_APP_BACKEND_URL"):
                    url = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL not configured")
    return url.rstrip("/")


BASE_URL = _read_base_url()
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


# ── Central metrics endpoint ─────────────────────────────────────────
class TestCentralMetrics:
    def test_endpoint_returns_rows_and_totals(self, session):
        r = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "rows" in data and "totals" in data and "registry" in data
        assert isinstance(data["rows"], list)
        # Should have data for amasi merchant
        assert len(data["rows"]) > 0, "expected at least one gateway row"

    def test_canonical_keys_subset(self, session):
        r = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30)
        rows = r.json()["rows"]
        keys = {row["key"] for row in rows}
        canonical = {"salla", "mada", "applepay", "googlepay", "stcpay",
                     "visa", "mastercard", "credit_card", "debit_card",
                     "salla_wallet", "tamara", "tabby", "emkan",
                     "bank_transfer", "cod", "_other"}
        assert keys.issubset(canonical), f"unexpected keys: {keys - canonical}"

    def test_row_field_shape(self, session):
        r = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30)
        rows = r.json()["rows"]
        required = {"key", "name_ar", "gross", "fees", "fees_vat",
                    "refund_full", "refund_partial", "refund_total",
                    "net", "expected_in_assets", "orders_count",
                    "actual_orders_count", "refunded_orders_count",
                    "cancelled_orders_count", "coverage_pct"}
        for row in rows:
            missing = required - set(row.keys())
            assert not missing, f"row {row.get('key')} missing {missing}"

    def test_totals_match_sum_of_rows(self, session):
        d = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30).json()
        rows, totals = d["rows"], d["totals"]
        for k in ("gross", "fees", "net", "orders_count"):
            s = sum(r[k] for r in rows)
            assert abs(s - totals[k]) < 0.05, f"totals.{k} mismatch: rows={s} totals={totals[k]}"


# ── Arabic letter folding ─────────────────────────────────────────────
class TestArabicFolding:
    """Run resolve_canonical() directly via in-process import."""
    def test_hamza_variants(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from payment_gateway_metrics import resolve_canonical
        assert resolve_canonical("البطاقة الإئتمانية") == "credit_card"
        assert resolve_canonical("البطاقة الائتمانية") == "credit_card"
        assert resolve_canonical("دفع عند الإستلام") == "cod"
        assert resolve_canonical("دفع عند الاستلام") == "cod"
        assert resolve_canonical("حوالة بنكية - الراجحي") == "bank_transfer"
        assert resolve_canonical("تمارا") == "tamara"
        assert resolve_canonical("Apple Pay") == "applepay"
        assert resolve_canonical("Google Pay") == "googlepay"
        assert resolve_canonical("Visa") == "visa"
        assert resolve_canonical("MasterCard") == "mastercard"


# ── Reconciliation uses central as expected source ───────────────────
class TestReconciliationCentral:
    def test_summary_platforms_use_central(self, session):
        r = session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        platforms = data.get("platforms", [])
        assert platforms, "no platforms returned"
        # At least one platform should have expected_source='central'
        sources = {p.get("expected_source") for p in platforms}
        assert "central" in sources, f"none of the platforms use central; sources={sources}"

    def test_salla_account_equals_central_sum(self, session):
        central = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30).json()
        recon = session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30).json()

        salla_keys = {"salla", "mada", "applepay", "googlepay", "stcpay",
                      "visa", "mastercard", "credit_card", "debit_card",
                      "salla_wallet"}
        expected_salla = round(sum(
            r["net"] for r in central["rows"] if r["key"] in salla_keys
        ), 2)

        salla_platform = next(
            (p for p in recon["platforms"]
             if p.get("normalized_payment_method") == "salla"), None
        )
        if salla_platform is None:
            pytest.skip("No salla platform account for this user")
        if salla_platform.get("expected_source") != "central":
            pytest.skip(f"Salla expected_source={salla_platform.get('expected_source')} (no central data)")
        assert abs(salla_platform["expected"] - expected_salla) < 0.05, (
            f"salla expected {salla_platform['expected']} != central sum {expected_salla}"
        )

    @pytest.mark.parametrize("account_key,central_key", [
        ("tamara", "tamara"),
        ("tabby", "tabby"),
        ("emkan", "emkan"),
        ("bank_transfer", "bank_transfer"),
    ])
    def test_single_mapped_accounts(self, session, account_key, central_key):
        central = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30).json()
        recon = session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30).json()

        row = next((r for r in central["rows"] if r["key"] == central_key), None)
        if row is None:
            pytest.skip(f"No central data for {central_key}")
        platform = next(
            (p for p in recon["platforms"]
             if p.get("normalized_payment_method") == account_key), None
        )
        if platform is None:
            pytest.skip(f"No platform account for {account_key}")
        if platform.get("expected_source") != "central":
            pytest.skip(f"{account_key} expected_source={platform.get('expected_source')}")
        assert abs(platform["expected"] - row["net"]) < 0.05, (
            f"{account_key} expected {platform['expected']} != central net {row['net']}"
        )


# ── sync-payment-methods writes central expected to accounts ─────────
class TestSyncPaymentMethods:
    def test_sync_updates_expected_orders_balance(self, session):
        r = session.post(f"{BASE_URL}/api/accounts/sync-payment-methods", timeout=60)
        assert r.status_code in (200, 201), r.text

        central = session.get(f"{BASE_URL}/api/payment-gateway-metrics", timeout=30).json()
        accounts = session.get(f"{BASE_URL}/api/accounts", timeout=30).json()
        if isinstance(accounts, dict):
            accounts = accounts.get("accounts") or accounts.get("data") or []

        rows_by_key = {r["key"]: r for r in central["rows"]}

        # Validate single-key accounts (tamara/tabby/emkan/bank_transfer)
        for acc in accounts:
            if acc.get("account_type") != "payment_platform":
                continue
            key = acc.get("normalized_payment_method")
            if key in ("tamara", "tabby", "emkan", "bank_transfer"):
                central_key = key
                cr = rows_by_key.get(central_key)
                if not cr:
                    continue
                expected = round(float(acc.get("expected_orders_balance") or 0), 2)
                assert abs(expected - cr["net"]) < 0.05, (
                    f"account {acc.get('name')} expected_orders_balance={expected} "
                    f"!= central net {cr['net']}"
                )
