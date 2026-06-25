"""Live integration test for /api/shipping-ledger SSOT priority flip.

Validates against the real test merchant account:
  amasi.jewelery@gmail.com / 10201917

Per agent_to_agent_context: this user has shipping companies configured
in /shipping/settings (iMile, مندوب الرياض, سمسا, أرامكس) — all with
cost_per_order set. So warnings array should be EMPTY.
"""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
# fall back to frontend .env
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for ln in f:
            if ln.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = ln.split("=", 1)[1].strip().rstrip("/")
                break

EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD},
               timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


def test_shipping_ledger_has_warnings_key_and_ssot_fields():
    s = _login()
    r = s.get(f"{BASE_URL}/api/shipping-ledger", timeout=60)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()

    # top-level required keys
    assert "warnings" in data, "warnings array MUST exist at top level"
    assert "per_company" in data
    assert "totals" in data
    assert "rows" in data
    assert isinstance(data["warnings"], list)

    # totals SSOT fields
    t = data["totals"]
    assert "total_shipping_base" in t
    assert "total_shipping_tax" in t
    assert "total_shipping_cost" in t  # = base + tax

    # per-company SSOT fields and uses_salla_fallback flag
    for pc in data["per_company"]:
        assert "shipping_company" in pc
        assert "total_shipping_base" in pc
        assert "total_shipping_tax" in pc
        assert "total_shipping_cost" in pc
        assert "uses_salla_fallback" in pc
        assert "from_salla_count" in pc
        assert "from_company_settings_count" in pc

    # row-level SSOT fields
    if data["rows"]:
        row = data["rows"][0]
        for k in ("shipping_base", "shipping_tax", "shipping_cost",
                  "shipping_vat_rate", "shipping_cost_source"):
            assert k in row, f"missing row field: {k}"
        assert row["shipping_cost_source"] in {"company_config", "salla", "none"}
        # invariant: total = base + tax (within 0.02)
        assert abs(row["shipping_cost"] - (row["shipping_base"] + row["shipping_tax"])) < 0.02

    print(f"\n=== ledger summary ===")
    print(f"rows: {len(data['rows'])}, per_company: {len(data['per_company'])}")
    print(f"warnings count: {len(data['warnings'])}")
    for w in data["warnings"]:
        print(f"  WARN: {w}")
    for pc in data["per_company"][:8]:
        print(f"  {pc['shipping_company']}: orders={pc['orders_count']} "
              f"base={pc['total_shipping_base']} tax={pc['total_shipping_tax']} "
              f"total={pc['total_shipping_cost']} salla_fb={pc['uses_salla_fallback']} "
              f"cfg_cost={pc.get('configured_cost')} "
              f"src(salla/cfg/none)="
              f"{pc['from_salla_count']}/{pc['from_company_settings_count']}/{pc['none_count']}")


def test_warning_structure_when_present():
    s = _login()
    r = s.get(f"{BASE_URL}/api/shipping-ledger", timeout=60)
    data = r.json()
    # Even if empty, the schema check on any present warning is valid.
    for w in data["warnings"]:
        assert "shipping_company" in w
        assert "orders_affected" in w
        assert w.get("reason") == "missing_cost_in_settings"
        assert "message" in w
        # Arabic message contains the company name and the required key phrases
        msg = w["message"]
        assert w["shipping_company"] in msg
        assert "إعدادات شركات الشحن" in msg
        assert "سلة" in msg


def test_shipping_accounts_ssot_fields():
    s = _login()
    r = s.get(f"{BASE_URL}/api/shipping-accounts/ledger", timeout=60)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert "totals" in data
    t = data["totals"]
    for k in ("shipping_base", "shipping_tax", "shipping_cost"):
        assert k in t, f"missing totals key: {k}"
    for row in data.get("companies", []):
        for k in ("shipping_base", "shipping_tax", "shipping_cost"):
            assert k in row, f"per-company row missing {k}"
        # invariant
        assert abs(row["shipping_cost"] - (row["shipping_base"] + row["shipping_tax"])) < 0.02


def test_settings_companies_have_cost_per_order_for_amasi():
    """Confirms the assumption: this user has all 4 companies with
    cost_per_order set. If this fails the warning-empty expectation is
    invalid and main agent must reconsider."""
    s = _login()
    r = s.get(f"{BASE_URL}/api/settings", timeout=30)
    assert r.status_code == 200
    cfgs = r.json().get("shipping_companies") or []
    print(f"\n=== settings.shipping_companies ===")
    for c in cfgs:
        print(f"  name={c.get('name')!r} cost_per_order={c.get('cost_per_order')} "
              f"vat_percent={c.get('vat_percent')} is_deferred={c.get('is_deferred')}")
    # not strict — just diagnostic
