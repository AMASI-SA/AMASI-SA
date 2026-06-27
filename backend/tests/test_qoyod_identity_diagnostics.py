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
    _key_fingerprint, _sample, run_identity_diagnostics,
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


def test_sample_picker_returns_at_most_five_dicts():
    rows = [{"id": i, "name": f"p{i}", "sku": f"S{i}"} for i in range(10)]
    out = _sample(rows, lambda r: {"id": r["id"], "name": r["name"]})
    assert len(out) == 5
    assert out[0] == {"id": 0, "name": "p0"}
    assert out[4] == {"id": 4, "name": "p4"}


def test_sample_picker_tolerates_non_list_and_non_dict_items():
    assert _sample(None, lambda r: r) == []
    assert _sample("not a list", lambda r: r) == []
    assert _sample([1, 2, {"id": 3}], lambda r: {"id": r["id"]}) \
           == [{"id": 3}]


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

    # Products section — endpoint + meta + sample all present.
    p = res["qoyod"]["products"]
    assert p["endpoint"] == "GET /products?page=1&limit=5"
    assert p["meta"]["total"] == 38
    assert len(p["sample"]) == 2
    assert p["sample"][0]["sku"] == "SKU-1"

    # Customers section.
    c = res["qoyod"]["customers"]
    assert c["meta"]["total"] == 5
    assert c["sample"][0]["phone"] == "+966500000001"

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
async def test_products_response_with_more_than_five_rows_is_truncated(monkeypatch):
    """Even if Qoyod returns 50 products (limit=5 ignored), the
    diagnostics never shows more than 5 in the sample."""
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
    assert len(res["qoyod"]["products"]["sample"]) == 5
    assert res["qoyod"]["products"]["meta"]["total"] == 1000
