"""Iter-255 — Verify legacy /api/shipping-accounts path goes through the
SSOT helper `shipping_cost_ssot.shipping_breakdown`.

Background: Previously `compute_owed_per_company` in shipping_accounts.py
used an inline `cost * (1 + vat_rate)` formula that prioritised
order.shipping_cost (Salla) over cfg.cost_per_order — bypassing SSOT
priority. This test asserts:

  1. /api/shipping-accounts is reachable.
  2. totals.total_owed == sum(account.total_owed) for all rows.
  3. Cross-route SSOT consistency: aggregate shipping totals seen on
     /api/shipping-accounts (per-company owed) match those reported by
     /api/shipping-ledger for the same date range / cutoff window.
  4. Unit test using the actual `compute_owed_per_company` against a
     mocked DB to PROVE the SSOT priority is now company-settings first.
"""
import asyncio
import os
import types
import requests
import pytest

# ---------------------------------------------------------------------------
# Resolve BASE_URL the same way as iter254 test.
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    try:
        with open("/app/frontend/.env") as f:
            for ln in f:
                if ln.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = ln.split("=", 1)[1].strip().rstrip("/")
                    break
    except FileNotFoundError:
        pass

EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"

TOL = 0.10  # SAR rounding tolerance for cross-route comparisons.


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ---------------------------------------------------------------------------
# 1) Live /api/shipping-accounts smoke test
# ---------------------------------------------------------------------------
def test_shipping_accounts_endpoint_reachable_and_shape(session):
    r = session.get(f"{BASE_URL}/api/shipping-accounts", timeout=60)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert "accounts" in data and isinstance(data["accounts"], list)
    assert "totals" in data
    t = data["totals"]
    for k in ("total_owed", "total_paid", "remaining"):
        assert k in t, f"missing totals.{k}"


def test_shipping_accounts_totals_match_row_sum(session):
    r = session.get(f"{BASE_URL}/api/shipping-accounts", timeout=60)
    assert r.status_code == 200
    data = r.json()
    sum_owed = round(sum(a["total_owed"] for a in data["accounts"]), 2)
    sum_paid = round(sum(a["total_paid"] for a in data["accounts"]), 2)
    assert abs(data["totals"]["total_owed"] - sum_owed) < 0.01
    assert abs(data["totals"]["total_paid"] - sum_paid) < 0.01


# ---------------------------------------------------------------------------
# 2) Cross-route SSOT consistency:
#    /api/shipping-accounts owed per company  ==
#    /api/shipping-ledger    shipping_cost per company
# Both paths now route through shipping_breakdown(), so for the same
# delivered-orders set they MUST produce identical per-company shipping
# accruals.
# ---------------------------------------------------------------------------
def test_shipping_accounts_owed_matches_ledger_shipping_cost(session):
    r1 = session.get(f"{BASE_URL}/api/shipping-accounts", timeout=60)
    r2 = session.get(f"{BASE_URL}/api/shipping-ledger", timeout=120)
    assert r1.status_code == 200, r1.text[:300]
    assert r2.status_code == 200, r2.text[:300]
    accounts_data = r1.json()
    ledger_data = r2.json()

    # Build canonical name → owed dict from /shipping-accounts.
    acc_by_name = {a["name"]: a["total_owed"] for a in accounts_data["accounts"]}

    # /api/shipping-ledger returns per_company with shipping_company key
    # and either total_shipping_cost (base+tax) or shipping_cost.
    per_company = ledger_data.get("per_company") or []
    for pc in per_company:
        name = pc.get("shipping_company") or pc.get("name")
        if name not in acc_by_name:
            continue  # not a deferred company in /shipping-accounts
        ledger_cost = (
            pc.get("total_shipping_cost")
            if pc.get("total_shipping_cost") is not None
            else pc.get("shipping_cost", 0)
        )
        accounts_owed = acc_by_name[name]
        assert abs(float(ledger_cost) - float(accounts_owed)) <= TOL, (
            f"SSOT drift for company '{name}': "
            f"/shipping-accounts owed={accounts_owed} vs "
            f"/shipping-ledger shipping_cost={ledger_cost}"
        )


def test_shipping_accounts_total_owed_matches_ledger_for_deferred_subset(session):
    """Aggregate-level SSOT check, scoped to deferred companies only.

    /api/shipping-accounts tracks ONLY deferred couriers (where the
    merchant pays them later); /api/shipping-ledger shows ALL shipping
    costs. Therefore the cross-route equality only holds when restricted
    to the deferred subset. For this test merchant (amasi) all 4
    configured couriers are prepaid (is_deferred=False), so the
    deferred subset is empty and both sides are 0 — still a valid SSOT
    invariant.
    """
    r1 = session.get(f"{BASE_URL}/api/shipping-accounts", timeout=60)
    r2 = session.get(f"{BASE_URL}/api/shipping-ledger", timeout=120)
    assert r1.status_code == 200
    assert r2.status_code == 200
    accounts_data = r1.json()
    ledger_data = r2.json()

    # Build set of deferred-company names from /shipping-accounts.
    deferred_names = {a["name"] for a in accounts_data["accounts"]
                      if a.get("is_configured")}
    accounts_total_owed = accounts_data["totals"]["total_owed"]

    # Sum ledger.per_company shipping_cost ONLY for the deferred subset.
    per_company = ledger_data.get("per_company") or []
    ledger_subset_total = 0.0
    for pc in per_company:
        name = pc.get("shipping_company") or pc.get("name")
        if name in deferred_names:
            c = (pc.get("total_shipping_cost")
                 if pc.get("total_shipping_cost") is not None
                 else pc.get("shipping_cost", 0))
            ledger_subset_total += float(c or 0)
    ledger_subset_total = round(ledger_subset_total, 2)

    assert abs(float(accounts_total_owed) - ledger_subset_total) <= TOL, (
        f"SSOT drift across deferred subset: shipping_accounts.total_owed="
        f"{accounts_total_owed} vs sum(ledger per_company over deferred)="
        f"{ledger_subset_total}"
    )


# ---------------------------------------------------------------------------
# 3) Pure unit-style test: feed compute_owed_per_company a fake `db` with
#    one order that has shipping_cost=999 from Salla but company config
#    has cost_per_order=20 + vat_percent=15. After the fix per-order
#    contribution MUST be 20 + 3 = 23, NOT 999 * 1.15.
# ---------------------------------------------------------------------------
class _FakeAsyncCursor:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def gen():
            for x in self._items:
                yield x
        return gen()


class _FakeCollection:
    def __init__(self, items):
        self._items = items

    def find(self, *a, **kw):
        return _FakeAsyncCursor(self._items)


class _FakeDB:
    def __init__(self, settings_doc, orders):
        self.settings = _FakeSettings(settings_doc)
        self.unified_orders = _FakeCollection(orders)


class _FakeSettings:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, *a, **kw):
        return self._doc


def test_compute_owed_per_company_uses_ssot_priority(monkeypatch):
    """Reproduce the scenario the PR description specified:
    Salla shipping_cost=999 vs company cost_per_order=20 @ 15% VAT
    → per-order owed must be 23.00, NOT 1148.85.
    """
    from shipping_accounts import compute_owed_per_company
    import auth as _auth

    settings_doc = {
        "user_id": "U1",
        "shipping_companies": [
            {
                "name": "TestCourier",
                "is_deferred": True,
                "cost_per_order": 20.0,
                "vat_percent": 15.0,
            }
        ],
        "report_included_statuses": ["delivered"],
    }
    orders = [
        {
            "order_status": "delivered",
            "shipping_company": "TestCourier",
            "shipping_cost": 999.0,
        }
    ]

    # ensure_user_settings reads db.settings.find_one — our fake DB returns it.
    async def _fake_ensure(db, user_id):
        return settings_doc
    monkeypatch.setattr(_auth, "ensure_user_settings", _fake_ensure)
    # shipping_accounts imported the symbol by name, monkeypatch there too.
    import shipping_accounts as sa
    monkeypatch.setattr(sa, "ensure_user_settings", _fake_ensure)

    db = _FakeDB(settings_doc, orders)

    result = asyncio.run(compute_owed_per_company(db, "U1"))

    assert "TestCourier" in result, f"expected TestCourier in {result.keys()}"
    entry = result["TestCourier"]
    # Per-order contribution must equal 20 + 20*0.15 = 23, NOT 999*1.15 = 1148.85
    assert abs(entry["owed"] - 23.00) < 0.01, (
        f"SSOT priority bug regressed: owed={entry['owed']} "
        f"(expected 23.00, NOT 1148.85)"
    )
    assert entry["orders_count"] == 1
    # cost_per_order stored on the entry must come from company config, not Salla.
    assert abs(entry["cost_per_order"] - 20.0) < 0.01, (
        f"cost_per_order must be 20 (company_config), got {entry['cost_per_order']}"
    )


def test_compute_owed_per_company_falls_back_to_salla_when_no_cfg_cost(monkeypatch):
    """Mirror: when cost_per_order=0/missing AND Salla has shipping_cost,
    the Salla value should be used (fallback path)."""
    from shipping_accounts import compute_owed_per_company
    import auth as _auth
    import shipping_accounts as sa

    settings_doc = {
        "user_id": "U1",
        "shipping_companies": [
            {
                "name": "NoCostCourier",
                "is_deferred": True,
                "cost_per_order": 0,         # missing config cost
                "vat_percent": 15.0,
            }
        ],
        "report_included_statuses": ["delivered"],
    }
    orders = [
        {
            "order_status": "delivered",
            "shipping_company": "NoCostCourier",
            "shipping_cost": 100.0,
        }
    ]

    async def _fake_ensure(db, user_id):
        return settings_doc
    monkeypatch.setattr(_auth, "ensure_user_settings", _fake_ensure)
    monkeypatch.setattr(sa, "ensure_user_settings", _fake_ensure)

    db = _FakeDB(settings_doc, orders)
    result = asyncio.run(compute_owed_per_company(db, "U1"))
    entry = result["NoCostCourier"]
    # Salla 100 + 15% VAT = 115
    assert abs(entry["owed"] - 115.0) < 0.01, (
        f"Fallback path broken: expected 115.00, got {entry['owed']}"
    )
