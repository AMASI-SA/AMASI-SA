"""Iter-288 — Auto-Adopt existing Qoyod products by SKU.

User decision (2026-02-27)
──────────────────────────
The operator uploads Amasi's product catalog to Qoyod trial manually.
SKU is the canonical key between Salla and Qoyod. When Mezan resolves
an order line item, it MUST:

    1. Check local mapping (db.qoyod_products_mapping).
    2. If no mapping → ASK QOYOD via SKU lookup (GET /products?q[sku_eq]).
    3. If Qoyod has 1 match → AUTO-ADOPT (write local mapping, reuse id).
       NO call to POST /products.
    4. If Qoyod has 2+ matches → BLOCK (`duplicate_qoyod_sku`).
    5. If Qoyod has 0 matches → THEN create with POST /products.

Default for trial: `settings.auto_adopt_existing_qoyod_products = True`.
For final production we can flip it off (strict Trust Gate, manual adopt).

This file locks in every branch.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.product_resolver import resolve_products
from integrations.qoyod.api_client import QoyodAPIError


_FULL_DEFAULTS = {
    "default_product_category_id":   "CAT-99",
    "default_product_tax_id":        "TAX-15",
    "default_product_unit_type_id":  "UNIT-PIECE",
    "default_sales_account_id":      "ACC-SALES",
}


class _FakeCursor:
    def __init__(self, rows): self._rows = list(rows)
    async def to_list(self, *, length=None):
        return list(self._rows) if length is None else self._rows[:length]


class _Col:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.upserts = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return r
        return None
    async def update_one(self, q, u, upsert=False):
        self.upserts.append({"filter": dict(q), "update": dict(u),
                              "upsert": upsert})
        class _R: matched_count = 0; upserted_id = "_id_x"
        return _R()


class _DB:
    def __init__(self, mapping_rows=None):
        self.qoyod_products_mapping = _Col(mapping_rows or [])
        self.qoyod_settings = _Col()


class _StubClient:
    def __init__(self, *, find_returns=None, find_raises=None):
        self.calls = []
        self._returns = find_returns or []
        self._raises = find_raises

    async def find_all_products_by_sku(self, sku, *, limit=10):
        self.calls.append(("find_all", sku))
        if self._raises:
            raise self._raises
        return list(self._returns)

    # Legacy single-row API; we no longer use it from resolver but
    # keep stubbed so any older code paths don't crash.
    async def find_product_by_sku(self, sku):
        rows = await self.find_all_products_by_sku(sku, limit=1)
        return rows[0] if rows else None

    async def create_product(self, payload, *, idem):
        self.calls.append(("create_product", payload, idem))
        return {"product": {"id": "Q-NEWLY-CREATED"}}


# ─── Path A — local mapping wins, no Qoyod call at all ──────────────
@pytest.mark.asyncio
async def test_local_mapping_short_circuits_before_any_qoyod_call():
    """If a local mapping exists, resolver MUST NOT call Qoyod for
    find OR create."""
    db = _DB(mapping_rows=[{
        "user_id": "main", "sku": "AMS11961",
        "qoyod_product_id": "Q-EXISTING-LOCAL",
        "adopted": True,
    }])
    api = _StubClient(find_returns=[])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        _FULL_DEFAULTS,
        trace_id="t1", api_client=api)
    assert res.success is True
    assert res.items[0].qoyod_product_id == "Q-EXISTING-LOCAL"
    assert res.items[0].created_new is False
    # ZERO Qoyod calls.
    assert api.calls == []


# ─── Path B — no local mapping, 1 Qoyod match → AUTO-ADOPT ──────────
@pytest.mark.asyncio
async def test_auto_adopt_single_qoyod_match_skips_post_products():
    """The headline contract: when Qoyod has exactly one product with
    the matching SKU, the resolver adopts it WITHOUT calling
    POST /products."""
    qoyod_row = {"id": 12345, "sku": "AMS11961",
                 "name": "تغليف انيق معا الورد - أماسي",
                 "selling_price": 5.0, "type": "service"}
    db  = _DB()
    api = _StubClient(find_returns=[qoyod_row])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        {**_FULL_DEFAULTS, "auto_adopt_existing_qoyod_products": True},
        trace_id="t1", api_client=api)
    assert res.success is True
    assert res.items[0].qoyod_product_id == "12345"
    assert res.items[0].created_new is False
    assert res.items[0].trust_source == "auto_adopted"
    # Exactly ONE call — to find_all. NO create_product.
    assert len(api.calls) == 1
    assert api.calls[0][0] == "find_all"
    # Local mapping was upserted with the adoption metadata.
    upsert = db.qoyod_products_mapping.upserts[-1]
    set_clause = upsert["update"]["$set"]
    assert set_clause["qoyod_product_id"] == "12345"
    assert set_clause["adopted"] is True
    assert set_clause["adopted_by"] == "system"
    assert set_clause["source"] == "auto_adopted_from_qoyod"
    assert set_clause["resolved_via"] == "auto_adopt_sku_match"


# ─── Path C — no local mapping, 2+ Qoyod matches → BLOCK ────────────
@pytest.mark.asyncio
async def test_duplicate_qoyod_sku_blocks_and_surfaces_matches():
    db  = _DB()
    api = _StubClient(find_returns=[
        {"id": 1, "sku": "AMS11961", "name": "نسخة قديمة", "selling_price": 4},
        {"id": 2, "sku": "AMS11961", "name": "نسخة جديدة", "selling_price": 5},
    ])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        _FULL_DEFAULTS,
        trace_id="t1", api_client=api)
    assert res.success is False
    err = res.items[0].error
    assert err["code"]            == "duplicate_qoyod_sku"
    assert err["failed_at_stage"] == "PRODUCT_MATCH"
    assert "AMS11961" in err["message"]
    # All matches surfaced so the operator sees what to clean up.
    assert len(err["matches"]) == 2
    assert {m["qoyod_product_id"] for m in err["matches"]} == {"1", "2"}
    # No create attempt.
    create_calls = [c for c in api.calls if c[0] == "create_product"]
    assert create_calls == []


# ─── Path D — no local mapping, 0 Qoyod matches → CREATE ────────────
@pytest.mark.asyncio
async def test_sku_not_found_in_qoyod_proceeds_to_create():
    """If Qoyod has no matching SKU, the resolver falls through to
    the create path — same flow as before Iter-288."""
    db  = _DB()
    api = _StubClient(find_returns=[])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        _FULL_DEFAULTS,
        trace_id="t1", api_client=api)
    assert res.success is True
    assert res.items[0].qoyod_product_id == "Q-NEWLY-CREATED"
    assert res.items[0].created_new is True
    # Two calls: find_all then create_product.
    kinds = [c[0] for c in api.calls]
    assert "find_all" in kinds
    assert "create_product" in kinds


# ─── Path E — auto_adopt disabled → strict Trust Gate ──────────────
@pytest.mark.asyncio
async def test_strict_mode_refuses_with_untrusted_when_match_exists():
    """auto_adopt=false reverts to the legacy strict Trust Gate
    (`untrusted_qoyod_product_match`). Operator must adopt manually."""
    db  = _DB()
    api = _StubClient(find_returns=[
        {"id": 12345, "sku": "AMS11961", "name": "x", "selling_price": 5},
    ])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        {**_FULL_DEFAULTS, "auto_adopt_existing_qoyod_products": False},
        trace_id="t1", api_client=api)
    assert res.success is False
    err = res.items[0].error
    assert err.get("code", "").startswith("qoyod_existing_untrusted")
    # No create.
    assert not any(c[0] == "create_product" for c in api.calls)
    # No mapping written.
    assert db.qoyod_products_mapping.upserts == []


@pytest.mark.asyncio
async def test_default_is_auto_adopt_true():
    """Setting omitted → auto_adopt=true (Iter-288 trial default)."""
    db  = _DB()
    api = _StubClient(find_returns=[
        {"id": 12345, "sku": "AMS11961", "name": "x", "selling_price": 5},
    ])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        _FULL_DEFAULTS,    # ← does NOT set auto_adopt_existing_qoyod_products
        trace_id="t1", api_client=api)
    assert res.success is True
    assert res.items[0].trust_source == "auto_adopted"


# ─── Multi-item order (Order 268756329 — 3 SKUs) ────────────────────
@pytest.mark.asyncio
async def test_order_268756329_three_skus_mixed_resolution_paths():
    """Realistic: one SKU is already locally mapped, one is found in
    Qoyod (auto-adopt), one is new (create)."""
    db = _DB(mapping_rows=[
        {"user_id": "main", "sku": "A",
         "qoyod_product_id": "Q-LOCAL-A", "adopted": True},
    ])
    class _MixedClient(_StubClient):
        async def find_all_products_by_sku(self, sku, *, limit=10):
            self.calls.append(("find_all", sku))
            if sku == "B":
                return [{"id": "Q-EXISTING-B", "sku": "B",
                         "name": "موجود مسبقاً", "selling_price": 199}]
            return []
    api = _MixedClient()
    items = [
        {"sku": "A", "name": "محلي",  "unit_price": 5,   "quantity": 1},
        {"sku": "B", "name": "ملاقى", "unit_price": 199, "quantity": 1},
        {"sku": "C", "name": "جديد",  "unit_price": 100, "quantity": 1},
    ]
    res = await resolve_products(
        db, "main", items, _FULL_DEFAULTS,
        trace_id="t1", api_client=api)
    assert res.success is True
    by_sku = {r.sku: r for r in res.items}
    assert by_sku["A"].qoyod_product_id == "Q-LOCAL-A"
    assert by_sku["A"].created_new is False
    assert by_sku["A"].trust_source != "auto_adopted"  # was already local
    assert by_sku["B"].qoyod_product_id == "Q-EXISTING-B"
    assert by_sku["B"].created_new is False
    assert by_sku["B"].trust_source == "auto_adopted"
    assert by_sku["C"].qoyod_product_id == "Q-NEWLY-CREATED"
    assert by_sku["C"].created_new is True
    # Only ONE POST /products (for SKU C only).
    create_calls = [c for c in api.calls if c[0] == "create_product"]
    assert len(create_calls) == 1
    assert create_calls[0][1]["product"]["sku"] == "C"


# ─── Edge: Qoyod returns match without an id ────────────────────────
@pytest.mark.asyncio
async def test_qoyod_match_without_id_blocks_safely():
    db  = _DB()
    api = _StubClient(find_returns=[
        {"sku": "AMS11961", "name": "بلا id"},   # malformed Qoyod row
    ])
    res = await resolve_products(
        db, "main",
        [{"sku": "AMS11961", "name": "x", "unit_price": 5, "quantity": 1}],
        _FULL_DEFAULTS,
        trace_id="t1", api_client=api)
    assert res.success is False
    assert res.items[0].error["code"] == "qoyod_match_missing_id"
