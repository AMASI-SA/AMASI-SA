"""Tests for the Qoyod tenant-identity diagnostics endpoint.

User concern (2026-02-26):
    QYD-GO reported "38 products in Qoyod" but the Qoyod UI for the
    same account showed none. This module probes Qoyod live and
    returns enough evidence (sample rows + endpoint + meta) for the
    operator to verify the API key Mezan is using belongs to the same
    Qoyod tenant they see in the Qoyod web UI.

What the tests guarantee:
    1. NO local-cache fallback — the diagnostics never substitute
       stale data; if Qoyod is unreachable we surface the error.
    2. Sample rows from /products and /customers are present in the
       response (id, name, sku) — the operator's smoking gun.
    3. API key is NEVER echoed; only a sha256-prefix fingerprint.
    4. `base_url`, `endpoint`, `queried_at` are always populated.
    5. Tenant hints (org name, branches) are surfaced from /branches.
    6. Graceful partial-failure: if /products fails but /customers
       works, the operator still sees customer evidence.
"""
from __future__ import annotations

import os
import hashlib
import pytest

from integrations.qoyod.identity_diagnostics import (
    _key_fingerprint, _sample, _raw_first, _raw_rows,
    _extract_name, _name_source, _is_system_product,
    run_identity_diagnostics,
)
from integrations.qoyod.api_client import QoyodAPIError


# ─── Helpers ─────────────────────────────────────────────────────────
def test_key_fingerprint_is_stable_and_never_exposes_key():
    k = "secret-qoyod-api-key-1234567890"
    fp = _key_fingerprint(k)
    expected = hashlib.sha256(k.encode("utf-8")).hexdigest()[:12]
    assert fp == expected
    # And critically, the raw key is NOT in the fingerprint.
    assert k not in fp
    assert "secret" not in fp


def test_key_fingerprint_handles_empty():
    assert _key_fingerprint("") == ""
    assert _key_fingerprint(None) == ""  # type: ignore[arg-type]


def test_sample_picker_returns_at_most_ten_dicts_by_default():
    rows = [{"id": i, "name": f"p{i}", "sku": f"S{i}"} for i in range(20)]
    out = _sample(rows, lambda r: {"id": r["id"], "name": r["name"]})
    assert len(out) == 10
    assert out[0] == {"id": 0, "name": "p0"}
    assert out[9] == {"id": 9, "name": "p9"}


def test_sample_picker_honours_explicit_limit():
    rows = [{"id": i} for i in range(20)]
    out = _sample(rows, lambda r: {"id": r["id"]}, limit=3)
    assert len(out) == 3


def test_sample_picker_tolerates_non_list_and_non_dict_items():
    assert _sample(None, lambda r: r) == []
    assert _sample("not a list", lambda r: r) == []
    assert _sample([1, 2, {"id": 3}], lambda r: {"id": r["id"]}) \
           == [{"id": 3}]


# ─── Name extraction fallback chain ─────────────────────────────────
def test_extract_name_prefers_name_field():
    assert _extract_name({"name": "Real Name", "arabic_name": "كذا"}) \
           == "Real Name"


def test_extract_name_falls_back_to_arabic_name_when_name_blank():
    assert _extract_name({"name": "", "arabic_name": "اسم عربي"}) \
           == "اسم عربي"


def test_extract_name_tries_all_locale_variants():
    for k in ("english_name", "name_ar", "name_en", "display_name", "title"):
        assert _extract_name({k: "From " + k}) == "From " + k


def test_extract_name_falls_back_to_localizations_array():
    row = {"name": None,
           "localizations": [{"locale": "ar", "name": "اسم"}]}
    assert _extract_name(row) == "اسم"


def test_extract_name_returns_none_when_truly_blank():
    assert _extract_name({"sku": "AMS11903"}) is None
    assert _extract_name({"name": "   "}) is None


def test_name_source_tells_which_field_was_used():
    assert _name_source({"name": "X"})         == "name"
    assert _name_source({"name": "", "arabic_name": "Y"}) == "arabic_name"
    assert _name_source({"localizations": [{"name": "Z"}]}) == "localizations"
    assert _name_source({"sku": "x"}) is None


# ─── System / shadow product detection ──────────────────────────────
@pytest.mark.parametrize("sku,expected", [
    ("cod_item",        True),
    ("custom_product",  True),
    ("shipping_fee",    True),
    ("delivery_fee",    True),
    ("discount_item",   True),
    ("fees_item",       True),
    ("shipping_express",True),    # prefix match
    ("system_x",        True),    # prefix match
    ("AMS11903",        False),
    ("",                False),
    ("normal-sku",      False),
])
def test_is_system_product(sku, expected):
    assert _is_system_product({"sku": sku}) is expected


def test_is_system_product_uses_reference_when_sku_missing():
    assert _is_system_product({"reference": "cod_item"}) is True
    assert _is_system_product({"reference": "ABC123"}) is False


# ─── raw_rows helper ────────────────────────────────────────────────
def test_raw_rows_returns_all_up_to_limit():
    rows = [{"id": i, "sku": f"S{i}"} for i in range(15)]
    assert len(_raw_rows(rows)) == 10
    assert _raw_rows(rows, limit=3) == [
        {"id": 0, "sku": "S0"}, {"id": 1, "sku": "S1"}, {"id": 2, "sku": "S2"}]


def test_raw_rows_caps_each_row_at_50_keys():
    big_row = {f"k{i}": i for i in range(80)}
    out = _raw_rows([big_row])
    assert len(out) == 1
    assert len(out[0]) == 50


# ─── End-to-end with patched API client ─────────────────────────────
class _FakeClient:
    """Stub for QoyodAPIClient. Returns canned responses keyed by method."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    async def list_branches(self):
        self.calls.append("branches")
        return self._return("branches")
    async def list_products(self, *, page=1, limit=50):
        self.calls.append(f"products[p={page},l={limit}]")
        return self._return("products")
    async def list_contacts(self, *, page=1, limit=50):
        self.calls.append(f"contacts[p={page},l={limit}]")
        return self._return("contacts")
    def _return(self, key):
        v = self.responses.get(key)
        if isinstance(v, Exception):
            raise v
        return v


class _Coll:
    def __init__(self, rows=None): self.rows = rows or []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return r
        return None


class _DB:
    def __init__(self):
        # `get_api_key` reads `qoyod_credentials` and decrypts. We bypass
        # the decryption by monkeypatching `get_api_key` directly in
        # the diagnostics module — see fixtures below.
        self.qoyod_credentials = _Coll([])


@pytest.fixture(autouse=True)
def _set_base_url_and_key(monkeypatch):
    monkeypatch.setenv("QOYOD_API_BASE", "https://legacy.qoyod.com/api/2.0")
    # Bypass encryption: stub get_api_key to return a deterministic
    # plaintext value (the diagnostics module imports it by name).
    async def _stub_get_api_key(db, user_id):
        # Empty tenants get None to exercise the no-api-key path.
        return "live-key-xyz" if user_id != "empty-tenant" else None
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.get_api_key",
        _stub_get_api_key)


@pytest.mark.asyncio
async def test_returns_no_api_key_summary_when_missing():
    """Pre-config state: user hasn't saved a key yet → diagnostics
    returns a clear actionable message, NOT a crash."""
    res = await run_identity_diagnostics(_DB(), "empty-tenant")
    assert res["ok"] is False
    assert res["summary"] == "no_api_key"
    assert res["mezan"]["api_key_present"] is False
    assert res["mezan"]["api_key_fingerprint"] is None
    assert res["qoyod"] is None
    assert "احفظ" in res["next_step"]


@pytest.mark.asyncio
async def test_returns_full_diagnostics_when_all_endpoints_succeed(monkeypatch):
    fake = _FakeClient({
        "branches": {
            "organisation": "Tariq Trading Co.",
            "branches": [
                {"id": 1, "name": "Main", "code": "MAIN",
                 "organisation": "Tariq Trading Co."},
            ],
        },
        "products": {
            "meta": {"total": 38, "page": 1, "total_pages": 8},
            "products": [
                {"id": "P1", "name": "Product 1", "sku": "SKU-1",
                 "price": 100},
                {"id": "P2", "name": "Product 2", "sku": "SKU-2",
                 "price": 200},
            ],
        },
        "contacts": {
            "meta": {"total": 5},
            "contacts": [
                {"id": "C1", "name": "Cust 1",
                 "phone_number": "+966500000001"},
            ],
        },
    })
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)

    res = await run_identity_diagnostics(_DB(), "u1")

    # Mezan-side fingerprint exposes no key.
    assert res["mezan"]["api_key_present"] is True
    assert res["mezan"]["api_key_fingerprint"]
    assert "live-key-xyz" not in str(res)
    assert res["mezan"]["base_url"] == "https://legacy.qoyod.com/api/2.0"

    # Tenant hint extracted from /branches.
    assert res["qoyod"]["tenant_hints"]["organisation"] == "Tariq Trading Co."
    assert len(res["qoyod"]["tenant_hints"]["branches"]) == 1

    # Products section — endpoint + meta + sample all present + raw_first_row.
    p = res["qoyod"]["products"]
    assert p["endpoint"] == "GET /products?page=1&limit=10"
    assert p["meta"]["total"] == 38
    assert len(p["sample"]) == 2
    assert p["sample"][0]["sku"] == "SKU-1"
    # raw_first_row exposes ALL fields from the first product —
    # critical for spotting hidden flags like `archived_at`, `type`.
    assert p["raw_first_row"] is not None
    assert p["raw_first_row"]["sku"] == "SKU-1"

    # Customers section.
    c = res["qoyod"]["customers"]
    assert c["meta"]["total"] == 5
    assert c["sample"][0]["phone"] == "+966500000001"
    assert c["raw_first_row"]["name"] == "Cust 1"

    # Summary line for the UI.
    assert "المنتجات: 38" in res["summary"]
    assert "العملاء: 5" in res["summary"]


@pytest.mark.asyncio
async def test_graceful_partial_failure_products_403_customers_ok(monkeypatch):
    """If Qoyod blocks /products but /customers succeeds, the
    operator must still see customer evidence — never a hard crash."""
    forbidden = QoyodAPIError(
        status_code=403, code="qoyod_forbidden",
        message="forbidden", endpoint="GET /products?page=1&limit=5")
    fake = _FakeClient({
        "branches": {"branches": []},
        "products": forbidden,
        "contacts": {
            "meta": {"total": 2},
            "contacts": [
                {"id": "C1", "name": "Cust 1"},
                {"id": "C2", "name": "Cust 2"},
            ],
        },
    })
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)
    res = await run_identity_diagnostics(_DB(), "u1")
    assert res["ok"] is True   # at least one endpoint worked
    assert res["qoyod"]["products"]["ok"] is False
    assert res["qoyod"]["products"]["error"]["code"] == "qoyod_forbidden"
    assert res["qoyod"]["products"]["sample"] == []
    # Customers still surfaced.
    assert len(res["qoyod"]["customers"]["sample"]) == 2
    assert "العملاء: 2" in res["summary"]


@pytest.mark.asyncio
async def test_all_endpoints_unauthorized_still_returns_diagnostic(monkeypatch):
    """The exact state Mezan is in right now: API key works for
    test-connection but /products + /customers return 401. The
    diagnostics must still tell the operator WHY without crashing."""
    err = QoyodAPIError(
        status_code=401, code="qoyod_unauthorized",
        message="bad key", endpoint="x")
    fake = _FakeClient({"branches": err, "products": err, "contacts": err})
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)
    res = await run_identity_diagnostics(_DB(), "u1")
    assert res["ok"] is False
    assert res["mezan"]["api_key_fingerprint"]
    for section in ("branches", "products", "customers"):
        assert res["qoyod"][section]["ok"] is False
        assert res["qoyod"][section]["error"]["code"] == "qoyod_unauthorized"
    assert res["summary"] == "تعذّر الاستعلام"


# ─── Sample size limit ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_products_response_with_more_than_ten_rows_is_truncated(monkeypatch):
    """Even if Qoyod returns 50 products, the diagnostics sample
    never shows more than 10."""
    fake = _FakeClient({
        "branches": {"branches": []},
        "products": {
            "meta": {"total": 1000},
            "products": [{"id": f"P{i}", "name": f"p{i}", "sku": f"S{i}"}
                         for i in range(50)],
        },
        "contacts": {"meta": {"total": 0}, "contacts": []},
    })
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)
    res = await run_identity_diagnostics(_DB(), "u1")
    assert len(res["qoyod"]["products"]["sample"]) == 10
    assert res["qoyod"]["products"]["meta"]["total"] == 1000


@pytest.mark.asyncio
async def test_user_scenario_empty_name_and_system_products(monkeypatch):
    """Mirrors the EXACT user observation from production:
    - 38 products, but most have empty `name` field.
    - Some SKUs are system shadows (cod_item, custom_product).
    - Some SKUs look like Salla product IDs (AMS11903, 581412596928).

    The diagnostics must:
      • Extract names from fallback fields (arabic_name) when name=empty.
      • Flag cod_item + custom_product + shipping_* as is_system=true.
      • Return raw_rows with the FULL JSON of all 10 products so the
        operator can inspect every field Qoyod sent.
    """
    fake = _FakeClient({
        "branches": {"branches": []},
        "products": {
            "meta": {"total": 38},
            "products": [
                {"id": 1, "name": "", "arabic_name": "منتج فعلي",
                 "sku": "AMS11903", "type": "inventory",
                 "active": True, "archived_at": None,
                 "selling_price": 100},
                {"id": 2, "name": None, "sku": "cod_item",
                 "type": "service", "active": True},
                {"id": 3, "name": "", "sku": "custom_product",
                 "type": "service"},
                {"id": 4, "name": "", "sku": "shipping_express"},
                {"id": 5, "name": "", "english_name": "Real EN Name",
                 "sku": "AMS11577"},
                {"id": 6, "name": "", "sku": "581412596928"},
            ],
        },
        "contacts": {"meta": {"total": 0}, "contacts": []},
    })
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)

    res = await run_identity_diagnostics(_DB(), "u1")
    sample = res["qoyod"]["products"]["sample"]
    assert len(sample) == 6
    assert sample[0]["name"]        == "منتج فعلي"
    assert sample[0]["name_source"] == "arabic_name"
    assert sample[0]["is_system"]   is False
    assert sample[1]["name"]        is None
    assert sample[1]["is_system"]   is True
    assert sample[2]["is_system"]   is True   # custom_product
    assert sample[3]["is_system"]   is True   # shipping_express
    assert sample[4]["name"]        == "Real EN Name"
    assert sample[4]["name_source"] == "english_name"
    assert sample[5]["name"]        is None
    assert sample[5]["is_system"]   is False  # numeric SKU not system

    raw_rows = res["qoyod"]["products"]["raw_rows"]
    assert len(raw_rows) == 6
    assert raw_rows[0]["arabic_name"] == "منتج فعلي"
    assert raw_rows[1]["sku"]         == "cod_item"


@pytest.mark.asyncio
async def test_raw_first_row_exposes_hidden_archived_field(monkeypatch):
    """The exact user case: Fresh Start was run but Qoyod still returns
    products that have `archived_at` set. The standard sample picker
    extracts archived/archived_at, AND `raw_first_row` exposes every
    field so the operator can spot anything we didn't pick out."""
    fake = _FakeClient({
        "branches": {"branches": []},
        "products": {
            "meta": {"total": 38},
            "products": [
                {"id": 1, "name": None, "sku": "AMS11903",
                 "type": "service", "archived_at": "2026-06-25T10:00:00Z",
                 "category": {"name": "Hidden Cat"},
                 "custom_field_x": "anything"},
            ],
        },
        "contacts": {"meta": {"total": 0}, "contacts": []},
    })
    monkeypatch.setattr(
        "integrations.qoyod.identity_diagnostics.QoyodAPIClient",
        lambda key: fake)
    res = await run_identity_diagnostics(_DB(), "u1")
    sample = res["qoyod"]["products"]["sample"]
    assert sample[0]["sku"]         == "AMS11903"
    assert sample[0]["type"]        == "service"
    assert sample[0]["archived"]    is True
    assert sample[0]["archived_at"] == "2026-06-25T10:00:00Z"
    assert sample[0]["category"]    == "Hidden Cat"
    # raw_first_row keeps EVERYTHING, including custom fields.
    raw = res["qoyod"]["products"]["raw_first_row"]
    assert raw["custom_field_x"] == "anything"
    assert raw["archived_at"]    == "2026-06-25T10:00:00Z"
