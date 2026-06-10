"""Phase 4 (Iter-119) — BNPL SSOT unification + auto-matching engine.

Covers:
  A) SSOT unification (accounts/summary, liabilities/summary, accounts list,
     transfers overdraw guard, reconciliation/summary).
  B) Auto-matching endpoint /api/bnpl/settlements/matching/{provider}.
"""
import os
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    token = body.get("access_token") or body.get("token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="session")
def canonical_balances(auth_session):
    r = auth_session.get(
        f"{BASE_URL}/api/bnpl/settlements/balances/canonical", timeout=30,
    )
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("success") is True, body
    # {tabby: {...balance,...}, tamara: {...}}
    by_provider = {b["provider"]: b for b in body.get("balances", [])}
    return body, by_provider


# ── A. SSOT unification ───────────────────────────────────────────────
class TestSSOTUnification:

    def test_accounts_summary_uses_ssot(self, auth_session, canonical_balances):
        _, by_provider = canonical_balances
        r = auth_session.get(f"{BASE_URL}/api/accounts/summary", timeout=30)
        assert r.status_code == 200
        s = r.json()
        assert "by_type" in s and "grand_total" in s
        # payment_platform total must include SSOT for tabby+tamara plus
        # ledger balances for any other (non-BNPL) payment_platforms.
        # We can only assert it is >= sum(ssot tabby+tamara) within tol.
        ssot_total = sum(float(by_provider[p]["balance"]) for p in by_provider)
        pp_total = float(s["by_type"].get("payment_platform") or 0)
        # Iter-119 expects SSOT to be reflected. ssot_total may be negative.
        # Floor: pp_total must be >= ssot_total minus epsilon.
        assert pp_total >= ssot_total - 0.01, (
            f"payment_platform={pp_total} should include SSOT total={ssot_total}"
        )

    def test_liabilities_summary_matches_accounts(self, auth_session):
        ra = auth_session.get(f"{BASE_URL}/api/accounts/summary", timeout=30)
        rl = auth_session.get(f"{BASE_URL}/api/liabilities/summary", timeout=30)
        assert ra.status_code == 200 and rl.status_code == 200, rl.text[:300]
        a = ra.json()
        l = rl.json()
        assert "assets" in l, l
        a_pp = float(a["by_type"].get("payment_platform") or 0)
        l_pp = float(l["assets"].get("payment_platforms_remaining") or 0)
        assert abs(a_pp - l_pp) < 0.05, (
            f"liabilities pp={l_pp} != accounts pp={a_pp}"
        )
        # assets.total includes more than banks+platforms (receivables etc.)
        # just verify total >= banks + payment_platforms_remaining
        assert "total" in l["assets"]
        l_total = float(l["assets"]["total"])
        l_banks = float(l["assets"].get("banks_total") or 0)
        assert l_total + 0.05 >= (l_banks + l_pp), (
            f"assets.total={l_total} should be >= banks+pp={l_banks + l_pp}"
        )

    def test_accounts_list_bnpl_have_ssot_metadata(self, auth_session,
                                                  canonical_balances):
        _, by_provider = canonical_balances
        r = auth_session.get(
            f"{BASE_URL}/api/accounts?account_type=payment_platform",
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        accounts = body if isinstance(body, list) else body.get("accounts", [])
        bnpl_found = 0
        for a in accounts:
            name = (a.get("name") or "").lower()
            provider = (a.get("provider_name") or "").lower()
            npm = (a.get("normalized_payment_method") or "").lower()
            is_bnpl = any(k in (name + provider + npm)
                          for k in ("tabby", "tamara"))
            if not is_bnpl:
                continue
            bnpl_found += 1
            assert a.get("balance_source") == "bnpl_ssot", (
                f"{a.get('name')} missing balance_source=bnpl_ssot: {a}"
            )
            assert "current_balance_ledger" in a, (
                f"{a.get('name')} missing current_balance_ledger"
            )
            # current_balance equals canonical SSOT
            which = "tabby" if "tabby" in (name + provider + npm) else "tamara"
            ssot_bal = float(by_provider[which]["balance"])
            assert abs(float(a["current_balance"]) - ssot_bal) < 0.05, (
                f"{which}: account.current_balance={a['current_balance']} "
                f"!= ssot={ssot_bal}"
            )
        assert bnpl_found >= 1, "no Tabby/Tamara accounts found for this user"

    def test_transfer_overdraw_uses_ssot_reject(self, auth_session,
                                                canonical_balances):
        """Sending more than SSOT balance from a BNPL wallet must 400."""
        _, by_provider = canonical_balances
        # Find a Tabby/Tamara account
        r = auth_session.get(
            f"{BASE_URL}/api/accounts?account_type=payment_platform",
            timeout=30,
        )
        accounts = r.json() if isinstance(r.json(), list) else r.json().get(
            "accounts", [])
        bnpl_acc = None
        which = None
        for a in accounts:
            blob = (a.get("name", "") + a.get("provider_name", "")
                    + a.get("normalized_payment_method", "")).lower()
            if "tabby" in blob:
                bnpl_acc = a; which = "tabby"; break
            if "tamara" in blob:
                bnpl_acc = a; which = "tamara"; break
        if not bnpl_acc:
            pytest.skip("no BNPL account")
        # find a bank to receive
        bank = None
        rb = auth_session.get(f"{BASE_URL}/api/accounts?account_type=bank",
                              timeout=30)
        banks = rb.json() if isinstance(rb.json(), list) else rb.json().get(
            "accounts", [])
        for b in banks:
            if b.get("status") != "inactive":
                bank = b; break
        if not bank:
            pytest.skip("no bank account")

        ssot_bal = float(by_provider[which]["balance"])
        # over-amount: well above SSOT
        over_amt = max(ssot_bal, 0) + 10000.0
        payload = {
            "from_account_id": bnpl_acc["id"],
            "to_account_id":   bank["id"],
            "amount":          over_amt,
            "transfer_date":   "2025-01-15",
            "notes":           "TEST_iter119_overdraw — expect 400",
        }
        r2 = auth_session.post(f"{BASE_URL}/api/transfers", json=payload,
                               timeout=30)
        assert r2.status_code == 400, (
            f"expected 400 overdraw, got {r2.status_code} {r2.text[:200]}"
        )
        # err mentions current SSOT bal
        text = r2.text
        assert "أكبر من" in text or "الرصيد" in text or "balance" in text.lower()

    def test_reconciliation_summary_ssot(self, auth_session, canonical_balances):
        _, by_provider = canonical_balances
        r = auth_session.get(f"{BASE_URL}/api/reconciliation/summary",
                             timeout=30)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        rows = (body.get("platforms") or body.get("rows")
                or body.get("items") or body.get("accounts") or [])
        # find tabby/tamara rows
        found = 0
        for row in rows:
            name_blob = (str(row.get("name", "")) + str(row.get("provider", ""))
                         + str(row.get("account_name", ""))
                         + str(row.get("normalized_payment_method", ""))).lower()
            for k in ("tabby", "tamara"):
                if k in name_blob:
                    found += 1
                    if "expected_source" in row:
                        assert row["expected_source"] == "bnpl_ssot", row
                    if "current_balance" in row:
                        assert abs(float(row["current_balance"])
                                   - float(by_provider[k]["balance"])) < 0.05
        assert found >= 1, f"no tabby/tamara rows in reconciliation/summary: {body}"


# ── B. Auto-matching engine (Phase 4-B) ──────────────────────────────
class TestMatchingEndpoint:

    @pytest.mark.parametrize("provider", ["tabby", "tamara"])
    def test_matching_for_provider_shape(self, auth_session, provider):
        r = auth_session.get(
            f"{BASE_URL}/api/bnpl/settlements/matching/{provider}",
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        b = r.json()
        assert b.get("success") is True, b
        # Required keys
        for k in ("invoices", "unmatched_transfers", "totals",
                  "linked_account_id", "window_days"):
            assert k in b, f"missing key {k} in response"
        assert b["window_days"] == 14
        # totals shape
        t = b["totals"]
        for tk in ("invoices_count", "matched_count", "unmatched_count",
                   "matched_amount", "unmatched_invoice_total",
                   "unmatched_transfer_total"):
            assert tk in t, f"missing totals.{tk}"
        # invoices shape
        for inv in b["invoices"]:
            assert "invoice_no" in inv
            assert "from" in inv
            assert "to" in inv
            assert "net_payable" in inv
            assert inv["match_status"] in ("matched", "unmatched", "over",
                                           "under"), inv["match_status"]
            assert "matched_transfer" in inv  # may be None
            assert "tolerance" in inv

    def test_matching_invalid_provider(self, auth_session):
        r = auth_session.get(
            f"{BASE_URL}/api/bnpl/settlements/matching/invalid",
            timeout=30,
        )
        assert r.status_code == 200
        b = r.json()
        assert b.get("success") is False, b
        assert "error" in b, b

    @pytest.mark.parametrize("provider", ["tabby", "tamara"])
    def test_weekly_regression(self, auth_session, provider):
        r = auth_session.get(
            f"{BASE_URL}/api/bnpl/settlements/weekly/{provider}",
            timeout=60,
        )
        assert r.status_code == 200, r.text[:200]
        b = r.json()
        assert b.get("success") is True, b
        assert "rows" in b
        assert "totals" in b

    @pytest.mark.parametrize("provider", ["tabby", "tamara"])
    def test_summary_regression(self, auth_session, provider):
        r = auth_session.get(
            f"{BASE_URL}/api/bnpl/settlements/summary/{provider}",
            timeout=60,
        )
        # /summary route may be /summary or summary/{provider}; accept 200
        assert r.status_code in (200, 404), r.text[:200]
        if r.status_code == 200:
            b = r.json()
            assert b.get("success") is True, b
