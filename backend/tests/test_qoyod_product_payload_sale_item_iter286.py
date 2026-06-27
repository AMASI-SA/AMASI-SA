"""Iter-286 — Qoyod /products payload uses `sale_item: 1`, not `is_sold`.

User-reported production failure (2026-02-27)
─────────────────────────────────────────────
Order `268756329` reached FAILED_PRODUCT with Qoyod 422:
    {"base": ["enter at least a purchase price or a sales price to continue."]}
even though the request had `is_sold: true` + `selling_price: 5`.

Root cause
──────────
Qoyod's `/products` endpoint expects the integer-flag activation
fields `sale_item` / `purchase_item`, NOT the boolean Rails-style
`is_sold` / `is_bought`. Without `sale_item: 1` the validator
ignores `selling_price` and rejects the create.

Iter-286 fixes
──────────────
1. `_build_product_payload` now emits `sale_item: 1`, `purchase_item: 0`,
   `selling_price`. Drops `is_sold` / `is_bought` entirely.
2. New `_build_product_payload_fallback` — minimal-fields payload
   (name, sku, sale_item=1, selling_price) used by the self-healing
   422 retry path.
3. `_ensure_products` catches `QoyodAPIError` with status 422 +
   "purchase price" / "sales price" in the response excerpt, and
   retries ONCE with the fallback payload. If THAT also fails, the
   row goes to FAILED_PRODUCT as before (no infinite retry).

These tests lock in the contract.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.product_resolver import (
    _build_product_payload, _build_product_payload_fallback,
    resolve_products as _ensure_products, ProductsResolutionResult,
    ProductResolutionItem,
)


# Adapter helper: tests use positional kwarg style for resolve_products.
_DEFAULTS = {
    "default_product_category_id":   "CAT-99",
    "default_product_tax_id":        "TAX-15",
    "default_product_unit_type_id":  "UNIT-PIECE",
    "default_sales_account_id":      "ACC-SALES",
}


async def _ensure_products_compat(db, *, api_client, user_id, trace_id,
                                   items, settings):
    # Iter-287 — auto-stamp the four required ids so the preflight
    # doesn't refuse Iter-286-era tests that pre-date the fields.
    merged = {**_DEFAULTS, **(settings or {})}
    return await _ensure_products(
        db, user_id, items, merged,
        trace_id=trace_id, api_client=api_client)


# ─── Order 268756329 — first item ───────────────────────────────────
def test_order_268756329_first_item_uses_sale_item_one():
    """The user-reported failing SKU. Locks in the corrected field
    names. Without this, Qoyod returns 422 even with selling_price."""
    item = {
        "sku":        "AMS11961",
        "name":       "تغليف انيق معا الورد - أماسي",
        "quantity":   1,
        "unit_price": 5.0,
    }
    settings = {"default_product_type": "service"}
    payload = _build_product_payload(item, settings)
    body = payload["product"]
    assert body["sku"]            == "AMS11961"
    assert body["name"]           == "تغليف انيق معا الورد - أماسي"
    assert body["sale_item"]      == 1        # ← THE fix
    assert body["purchase_item"]  == 0
    assert body["selling_price"]  == 5.0
    assert body["type"]           == "service"
    assert body["is_non_stock"]   is True


def test_payload_drops_is_sold_and_is_bought_fields():
    """No legacy boolean activation flags — they trigger 422."""
    item = {"sku": "A", "name": "x", "unit_price": 10}
    body = _build_product_payload(item, {})["product"]
    assert "is_sold"   not in body
    assert "is_bought" not in body


def test_payload_coerces_string_price_to_float():
    item = {"sku": "A", "name": "x", "unit_price": "199.50"}
    body = _build_product_payload(item, {})["product"]
    assert body["selling_price"] == 199.50
    assert isinstance(body["selling_price"], float)


def test_payload_defaults_price_to_zero_when_missing():
    item = {"sku": "A", "name": "x"}   # no unit_price
    body = _build_product_payload(item, {})["product"]
    assert body["selling_price"] == 0.0
    # selling_price MUST be present whenever sale_item=1.
    assert body["sale_item"] == 1


def test_payload_uses_settings_default_product_type():
    item = {"sku": "A", "name": "x", "unit_price": 5}
    body = _build_product_payload(item, {"default_product_type": "product"})["product"]
    assert body["type"] == "product"
    # `is_non_stock` reflects the type — true only for service.
    assert body["is_non_stock"] is False


# ─── Fallback payload (Iter-286 self-healing) ───────────────────────
def test_fallback_payload_is_minimal():
    item = {"sku": "AMS11961", "name": "x", "unit_price": 5}
    body = _build_product_payload_fallback(item, {})["product"]
    # Only four fields — name, sku, sale_item, selling_price.
    assert set(body.keys()) == {"name", "sku", "sale_item", "selling_price"}
    assert body["sale_item"]     == 1
    assert body["selling_price"] == 5.0


def test_fallback_drops_type_and_is_non_stock():
    """Even with default_product_type set, the fallback omits `type` so
    Qoyod uses its tenant default. This is what cleared the 422 in
    production."""
    item = {"sku": "A", "name": "x", "unit_price": 5}
    body = _build_product_payload_fallback(
        item, {"default_product_type": "service"})["product"]
    assert "type"         not in body
    assert "is_non_stock" not in body
    assert "purchase_item" not in body


# ─── Self-healing retry — end-to-end ────────────────────────────────
@pytest.mark.asyncio
async def test_ensure_products_retries_once_on_422_purchase_price():
    """Simulate Qoyod returning a 422 with the exact "purchase price"
    error on the first POST, then 201 on the fallback retry. The
    resolver MUST surface success (created_new=True), not block."""
    from integrations.qoyod.api_client import QoyodAPIError

    # Fake API client that fails once, succeeds on the retry.
    class _StubClient:
        def __init__(self) -> None:
            self.calls = []
            self._first = True
        async def find_product_by_sku(self, sku):
            return None
        async def create_product(self, payload, *, idem):
            self.calls.append({"payload": payload, "idem": idem})
            if self._first:
                self._first = False
                raise QoyodAPIError(
                    code="qoyod_validation_error",
                    message="HTTP 422",
                    status_code=422,
                    endpoint="POST /products",
                    response_excerpt=(
                        '{"base":["enter at least a purchase '
                        'price or a sales price to continue."]}'),
                    request_body_json=payload,
                )
            return {"product": {"id": "Q-FALLBACK-OK"}}

    # Minimal fake DB.
    class _Col:
        def __init__(self): self.rows = []
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False):
            self.rows.append((q, u))
            class _R: matched_count = 0; upserted_id = "x"
            return _R()
    class _DB:
        def __init__(self):
            self.qoyod_products_mapping = _Col()
            self.qoyod_settings = _Col()

    db   = _DB()
    api  = _StubClient()
    items = [{"sku": "AMS11961", "name": "تغليف", "unit_price": 5,
               "quantity": 1, "tax_amount": 0}]
    res  = await _ensure_products_compat(
        db, api_client=api,
        user_id="main", trace_id="t1",
        items=items,
        settings={"default_product_type": "service"})

    assert isinstance(res, ProductsResolutionResult)
    assert res.success is True, f"failures: {res.items}"
    assert len(api.calls) == 2, "should attempt fallback exactly once"
    # First call uses the canonical payload (has `type` + `purchase_item`)
    first_body = api.calls[0]["payload"]["product"]
    assert first_body["sale_item"]  == 1
    assert "type" in first_body
    # Second call uses the minimal fallback (no `type`)
    second_body = api.calls[1]["payload"]["product"]
    assert second_body["sale_item"] == 1
    assert "type" not in second_body
    # Mapping was upserted with the returned Qoyod id.
    assert res.items[0].qoyod_product_id == "Q-FALLBACK-OK"


@pytest.mark.asyncio
async def test_ensure_products_does_not_retry_on_non_price_422():
    """Other 422 errors (e.g. duplicate SKU) must NOT trigger the
    fallback — only the specific "purchase price"/"sales price"
    message."""
    from integrations.qoyod.api_client import QoyodAPIError

    class _StubClient:
        def __init__(self): self.calls = []
        async def find_product_by_sku(self, sku): return None
        async def create_product(self, payload, *, idem):
            self.calls.append(payload)
            raise QoyodAPIError(
                code="qoyod_validation_error",
                message="HTTP 422",
                status_code=422,
                endpoint="POST /products",
                response_excerpt='{"sku":["has already been taken"]}',
                request_body_json=payload,
            )

    class _Col:
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False): pass
    class _DB:
        def __init__(self):
            self.qoyod_products_mapping = _Col()
            self.qoyod_settings = _Col()

    api = _StubClient()
    res = await _ensure_products_compat(
        _DB(), api_client=api, user_id="main", trace_id="t1",
        items=[{"sku": "DUP", "name": "x", "unit_price": 1, "quantity": 1}],
        settings={})
    assert res.success is False
    assert len(api.calls) == 1, "duplicate-SKU must NOT trigger fallback retry"


@pytest.mark.asyncio
async def test_ensure_products_surfaces_fallback_failure():
    """If BOTH the canonical and the fallback fail with 422, the
    resolver gives up (no infinite retry) and surfaces the second
    error with `fallback_attempted: True`."""
    from integrations.qoyod.api_client import QoyodAPIError

    class _StubClient:
        def __init__(self): self.calls = []
        async def find_product_by_sku(self, sku): return None
        async def create_product(self, payload, *, idem):
            self.calls.append(payload)
            raise QoyodAPIError(
                code="qoyod_validation_error",
                message="HTTP 422",
                status_code=422,
                endpoint="POST /products",
                response_excerpt='{"base":["enter at least a sales price"]}',
                request_body_json=payload,
            )

    class _Col:
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False): pass
    class _DB:
        def __init__(self):
            self.qoyod_products_mapping = _Col()
            self.qoyod_settings = _Col()

    api = _StubClient()
    res = await _ensure_products_compat(
        _DB(), api_client=api, user_id="main", trace_id="t1",
        items=[{"sku": "A", "name": "x", "unit_price": 1, "quantity": 1}],
        settings={})
    assert res.success is False
    assert len(api.calls) == 2, "fallback must be tried exactly once"
    err = res.items[0].error
    assert err.get("fallback_attempted") is True
