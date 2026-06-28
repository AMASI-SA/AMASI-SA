"""Iter-290g — Qoyod `/products` validator wants SCALAR ids (not arrays).

Production failure reference
────────────────────────────
Order 268784455, SKU AMS11542 (كرت اهداء حسب الطلب) — 2026-02-28.
POST /products returned 422:

    {"tax_id": ["Please select taxes"]}

…while we were sending `tax_id: ["1"]` (the Iter-289 array workaround).
The live API rejects the array shape and demands a scalar (preferably
integer).

What Iter-290g locks in
───────────────────────
1. `_stamp_required_ids` ships ALL four ids (category_id, tax_id,
   product_unit_type_id, sales_account_id) as SCALARS — int when the
   value parses cleanly, else the stripped string.

2. `_unwrap_id_for_payload` collapses multi-element multiselect inputs
   to their first usable element. Empty / None / `[]` → drops the key.

3. New preflight `validate_product_id_shapes` returns a structured
   `product_payload_invalid_id_shape` error when the operator
   configured a multi-element array. Surfaces BEFORE any POST.

4. Self-healing 422 retry now also triggers when Qoyod's error message
   mentions `please select taxes` or `tax_id` (last-resort defense
   for tenants that may flip their validator shape in the future).

5. Diagnostic error payloads carry `sku` and `attempted_selling_price`
   so the operator sees exactly which line item failed — fixes the
   confusing case where item #2's failure was logged against item #1's
   SKU (preview reprocess only captured the first item).

6. Fallback payload bumps `selling_price` from 0 → 1.0 for the catalog
   row only (per user directive). The Salla-side invoice line still
   uses the real price (0 / discounted) — accounting unaffected.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.product_resolver import (
    _build_product_payload, _build_product_payload_fallback,
    _coerce_id_to_int, _unwrap_id_for_payload, _is_array_shape,
    validate_product_id_shapes, build_invalid_id_shape_error,
    validate_product_defaults,
    resolve_products,
)
from integrations.qoyod.api_client import QoyodAPIError


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────
_PROD_SETTINGS_NUMERIC = {
    "default_product_type":          "service",
    "default_product_category_id":   "1",
    "default_product_tax_id":        "1",
    "default_product_unit_type_id":  "1",
    "default_sales_account_id":      "17",
}

_AMS11542 = {
    "sku": "AMS11542",
    "name": "كرت اهداء حسب الطلب",
    "unit_price": 0,
    "quantity": 1,
}


# ─────────────────────────────────────────────────────────────────────
# 1. _coerce_id_to_int / _unwrap_id_for_payload
# ─────────────────────────────────────────────────────────────────────
def test_coerce_handles_all_realistic_shapes():
    assert _coerce_id_to_int("1") == 1
    assert _coerce_id_to_int(" 1 ") == 1
    assert _coerce_id_to_int(1) == 1
    assert _coerce_id_to_int(1.0) == 1
    assert _coerce_id_to_int("1.0") == 1
    assert _coerce_id_to_int(["1"]) == 1
    assert _coerce_id_to_int(["", "2"]) == 2
    assert _coerce_id_to_int([]) is None
    assert _coerce_id_to_int("") is None
    assert _coerce_id_to_int(None) is None
    assert _coerce_id_to_int("abc") is None     # non-numeric → None


def test_unwrap_returns_int_for_numeric_string_else_scalar_string():
    assert _unwrap_id_for_payload("17") == 17
    assert _unwrap_id_for_payload(["17"]) == 17
    # Non-numeric → passes through as the stripped string.
    assert _unwrap_id_for_payload("CAT-99") == "CAT-99"
    assert _unwrap_id_for_payload(["CAT-99"]) == "CAT-99"
    # Whitespace / None / empty → None (caller drops the key).
    assert _unwrap_id_for_payload("") is None
    assert _unwrap_id_for_payload("   ") is None
    assert _unwrap_id_for_payload(None) is None
    assert _unwrap_id_for_payload([]) is None


def test_is_array_shape_detects_multi_element_array_only():
    assert _is_array_shape(["1", "2"]) is True
    assert _is_array_shape(["1"]) is False
    assert _is_array_shape("1") is False
    assert _is_array_shape([]) is False
    assert _is_array_shape(["1", "1"]) is False    # duplicates collapse
    assert _is_array_shape([None, "", "1"]) is False    # empties drop


# ─────────────────────────────────────────────────────────────────────
# 2. Payload builder produces SCALAR tax_id (regression for Iter-289)
# ─────────────────────────────────────────────────────────────────────
def test_amg11542_payload_carries_scalar_int_tax_id():
    """Live production AMS11542 case — tax_id must be int 1 (NOT [1])."""
    body = _build_product_payload(_AMS11542, _PROD_SETTINGS_NUMERIC)["product"]
    assert body["tax_id"]              == 1
    assert body["category_id"]         == 1
    assert body["product_unit_type_id"] == 1
    assert body["sales_account_id"]    == 17
    assert not isinstance(body["tax_id"], list)
    # Selling price stays 0 — invoice math handles the discount.
    assert body["selling_price"]       == 0.0
    # Activation flags untouched (Iter-286 contract).
    assert body["sale_item"]           == 1
    assert body["purchase_item"]       == 0


def test_multiselect_array_input_unwraps_to_first_element():
    """Iter-290g — defends against UI multiselect shipping `["1"]`."""
    settings = {**_PROD_SETTINGS_NUMERIC,
                "default_product_tax_id": ["1"]}
    body = _build_product_payload(_AMS11542, settings)["product"]
    assert body["tax_id"] == 1
    assert not isinstance(body["tax_id"], list)


def test_multielement_array_takes_first_in_builder_but_preflight_blocks():
    """Belt-and-suspenders: even if the operator slips `["1","2"]` past
    the Settings UI, the builder still produces a scalar — and the
    preflight refuses the row with `product_payload_invalid_id_shape`."""
    settings = {**_PROD_SETTINGS_NUMERIC,
                "default_product_tax_id": ["1", "2"]}
    body = _build_product_payload(_AMS11542, settings)["product"]
    assert body["tax_id"] == 1   # graceful unwrap
    ok, offenders = validate_product_id_shapes(settings)
    assert ok is False
    assert any(o["field"] == "default_product_tax_id" for o in offenders)


# ─────────────────────────────────────────────────────────────────────
# 3. Preflight: invalid id shape error
# ─────────────────────────────────────────────────────────────────────
def test_invalid_id_shape_error_is_actionable():
    err = build_invalid_id_shape_error([
        {"field": "default_product_tax_id",
         "value": ["1", "2"], "issue": "multi_element_array"},
    ])
    assert err["code"] == "product_payload_invalid_id_shape"
    assert err["failed_at_stage"] == "PREFLIGHT_PRODUCT_DEFAULTS"
    assert "الضريبة" in err["message"]      # Arabic label
    assert "مصفوفات" in err["message"]      # Arabic "arrays"


# ─────────────────────────────────────────────────────────────────────
# 4. validate_product_defaults accepts numeric int / multiselect / str
# ─────────────────────────────────────────────────────────────────────
def test_validate_product_defaults_accepts_realistic_shapes():
    ok, missing = validate_product_defaults({
        "default_product_category_id": 1,                # int scalar
        "default_product_tax_id":      "1",              # numeric str
        "default_product_unit_type_id": ["1"],           # multiselect
        "default_sales_account_id":    "ACC-LEGACY",     # legacy str id
    })
    assert ok is True
    assert missing == []


def test_validate_product_defaults_refuses_empty_array():
    ok, missing = validate_product_defaults({
        "default_product_category_id": [],   # empty array → missing
        "default_product_tax_id":      "1",
        "default_product_unit_type_id": "1",
        "default_sales_account_id":    "17",
    })
    assert ok is False
    assert missing == ["default_product_category_id"]


# ─────────────────────────────────────────────────────────────────────
# 5. resolve_products refuses BEFORE POST when shape is invalid
# ─────────────────────────────────────────────────────────────────────
class _Col:
    async def find_one(self, q, projection=None): return None
    async def update_one(self, q, u, upsert=False): pass


class _DB:
    def __init__(self):
        self.qoyod_products_mapping = _Col()
        self.qoyod_settings = _Col()


class _RecordingClient:
    def __init__(self):
        self.create_calls = []

    async def find_all_products_by_sku(self, sku, *, limit=10):
        return []

    async def find_product_by_sku(self, sku):
        return None

    async def create_product(self, payload, *, idem):
        self.create_calls.append(payload)
        return {"product": {"id": "Q-NEW-1"}}


@pytest.mark.asyncio
async def test_resolve_products_blocks_before_post_on_invalid_id_shape():
    settings = {
        **_PROD_SETTINGS_NUMERIC,
        "default_product_tax_id": ["1", "2"],   # ← invalid: multi-array
    }
    api = _RecordingClient()
    res = await resolve_products(
        _DB(), "main", [_AMS11542], settings,
        trace_id="t1", api_client=api,
    )
    assert res.success is False
    err = res.items[0].error
    assert err["code"] == "product_payload_invalid_id_shape"
    assert api.create_calls == [], "no /products POST should have happened"


@pytest.mark.asyncio
async def test_resolve_products_sends_scalar_int_tax_id_to_qoyod():
    """The live POST body — proves the Iter-290g fix end-to-end."""
    api = _RecordingClient()
    res = await resolve_products(
        _DB(), "main", [_AMS11542], _PROD_SETTINGS_NUMERIC,
        trace_id="t1", api_client=api,
    )
    assert res.success is True
    assert len(api.create_calls) == 1
    sent_body = api.create_calls[0]["product"]
    assert sent_body["tax_id"]              == 1
    assert not isinstance(sent_body["tax_id"], list)
    assert sent_body["category_id"]         == 1
    assert sent_body["product_unit_type_id"] == 1
    assert sent_body["sales_account_id"]    == 17
    assert sent_body["sku"]                 == "AMS11542"


# ─────────────────────────────────────────────────────────────────────
# 6. Self-heal 422 retry triggers on "please select taxes"
# ─────────────────────────────────────────────────────────────────────
class _422TaxOnceClient(_RecordingClient):
    """First call returns 422 with the tax_id error, second call succeeds.
    Mirrors a tenant whose validator rejected the canonical payload and
    accepted the minimal fallback (defensive coverage)."""
    def __init__(self):
        super().__init__()
        self._first = True

    async def create_product(self, payload, *, idem):
        self.create_calls.append((idem, payload))
        if self._first:
            self._first = False
            raise QoyodAPIError(
                status_code=422,
                code="qoyod_validation_error",
                message="please select taxes",
                response_excerpt="{'tax_id': ['Please select taxes']}",
                endpoint="/products",
            )
        return {"product": {"id": "Q-FB"}}


@pytest.mark.asyncio
async def test_self_heal_retries_on_tax_id_422():
    api = _422TaxOnceClient()
    res = await resolve_products(
        _DB(), "main", [_AMS11542], _PROD_SETTINGS_NUMERIC,
        trace_id="t1", api_client=api,
    )
    assert res.success is True
    # Two calls: canonical + fallback.
    assert len(api.create_calls) == 2
    idem_canonical, body_canonical = api.create_calls[0]
    idem_fallback, body_fallback = api.create_calls[1]
    assert idem_fallback.endswith("-fb")
    # Both bodies carry scalar tax_id (Iter-290g shape contract).
    assert body_canonical["product"]["tax_id"] == 1
    assert body_fallback["product"]["tax_id"]  == 1
    # Fallback bumps zero-price to 1.0 for catalog row.
    assert body_fallback["product"]["selling_price"] == 1.0


# ─────────────────────────────────────────────────────────────────────
# 7. Diagnostic error SKU attribution (fixes confusing multi-item logs)
# ─────────────────────────────────────────────────────────────────────
class _422AlwaysClient(_RecordingClient):
    """Every call raises 422 — exercises the non-retryable failure path
    so we can inspect the diagnostic payload."""
    async def create_product(self, payload, *, idem):
        self.create_calls.append((idem, payload))
        raise QoyodAPIError(
            status_code=422,
            code="qoyod_validation_error",
            message="has already been taken",
            response_excerpt="{'name': ['has already been taken']}",
            endpoint="/products",
        )


@pytest.mark.asyncio
async def test_failure_diagnostic_carries_correct_sku_and_price():
    """Order with two items: AMS10002 + AMS11542. The failing diagnostic
    must reference EACH item's own SKU+price, not the first item's."""
    api = _422AlwaysClient()
    items = [
        {"sku": "AMS10002", "name": "ساعة",   "unit_price": 100, "quantity": 1},
        {"sku": "AMS11542", "name": "كرت اهداء", "unit_price": 0, "quantity": 1},
    ]
    res = await resolve_products(
        _DB(), "main", items, _PROD_SETTINGS_NUMERIC,
        trace_id="t1", api_client=api,
    )
    assert res.success is False
    # The resolver stops at the FIRST failure (sequential per the
    # current contract) — so the diagnostic must be tied to AMS10002,
    # NOT the second item.
    err = res.items[0].error
    assert err["sku"] == "AMS10002"
    assert err["attempted_selling_price"] == 100.0
