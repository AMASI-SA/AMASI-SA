"""Iter-287 — Qoyod-required product fields + preflight gate.

User-reported production failure (2026-02-27)
─────────────────────────────────────────────
After Iter-286 cleared the `sale_item` 422, the SAME order
`268756329` hit a second 422 from Qoyod:

    {
      "category_id":          ["Please Select The Category"],
      "tax_id":               ["Please select taxes"],
      "product_unit_type_id": ["Please Select The Unit Type"],
      "sales_account_id":     ["Can't be blank"]
    }

Root cause
──────────
Qoyod's `/products` validator (post-`sale_item:1` activation) demands
FOUR additional tenant ids:
  • `category_id`           — Qoyod product category
  • `tax_id`                — tax record applied at sale time
  • `product_unit_type_id`  — e.g. "piece" / "service hour"
  • `sales_account_id`      — GL account credited on sale

These come from MEZAN settings, not from Salla. They MUST be filled
once by the operator before any Qoyod write.

Iter-287 fixes
──────────────
1. `_build_product_payload` stamps the four ids from settings.
2. `_build_product_payload_fallback` ALSO stamps them (Qoyod requires
   them even on the minimal retry payload).
3. NEW `validate_product_defaults(settings)` preflight returns
   `(ok, missing_keys)`. Called BEFORE any POST in `resolve_products`.
4. NEW `build_missing_product_defaults_error(missing_keys)` returns
   a structured Arabic error with code
   `missing_qoyod_product_defaults` + `failed_at_stage
   PREFLIGHT_PRODUCT_DEFAULTS`.
5. `preview_reprocess` surfaces `product_defaults_status` in
   `stages.products_preview` so the operator sees the gap up-front
   without sending anything.

Lock-in scenario: order 268756329 with three SKUs.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.product_resolver import (
    _build_product_payload, _build_product_payload_fallback,
    validate_product_defaults, build_missing_product_defaults_error,
    REQUIRED_PRODUCT_DEFAULT_KEYS,
    resolve_products, ProductsResolutionResult, ProductResolutionItem,
)


_FULL_SETTINGS = {
    "default_product_type":          "service",
    "default_product_category_id":   "CAT-99",
    "default_product_tax_id":        "TAX-15",
    "default_product_unit_type_id":  "UNIT-PIECE",
    "default_sales_account_id":      "ACC-SALES",
}


# ─── _build_product_payload stamps required ids ────────────────────
def test_full_payload_stamps_all_four_required_ids():
    item = {"sku": "AMS11961",
            "name": "تغليف انيق معا الورد - أماسي",
            "unit_price": 5.0}
    body = _build_product_payload(item, _FULL_SETTINGS)["product"]
    assert body["category_id"]          == "CAT-99"
    # Iter-290g — `tax_id` is a SCALAR (Iter-289 array shape reverted
    # — Qoyod's live validator rejects arrays).
    assert body["tax_id"]               == "TAX-15"
    assert body["product_unit_type_id"] == "UNIT-PIECE"
    assert body["sales_account_id"]     == "ACC-SALES"
    # Iter-286 contract preserved.
    assert body["sale_item"]            == 1
    assert body["selling_price"]        == 5.0


def test_fallback_payload_also_stamps_required_ids():
    """Even the minimal-fields retry payload MUST carry the four
    required ids — Qoyod rejects the create without them either way."""
    item = {"sku": "X", "name": "y", "unit_price": 10}
    body = _build_product_payload_fallback(item, _FULL_SETTINGS)["product"]
    assert body["category_id"]          == "CAT-99"
    assert body["tax_id"]               == "TAX-15"     # Iter-290g scalar
    assert body["product_unit_type_id"] == "UNIT-PIECE"
    assert body["sales_account_id"]     == "ACC-SALES"
    # Still minimal otherwise: no type, no is_non_stock, no purchase_item.
    assert "type" not in body
    assert "is_non_stock" not in body
    assert "purchase_item" not in body


def test_full_payload_drops_missing_optional_ids_silently():
    """Iter-287 preflight blocks BEFORE any POST when settings are
    missing. But the builder itself is safe — it skips empty values
    rather than emitting `category_id: ""` which would also 422."""
    item = {"sku": "X", "name": "y", "unit_price": 10}
    body = _build_product_payload(item, {})["product"]
    for k in ("category_id", "tax_id", "product_unit_type_id",
              "sales_account_id"):
        assert k not in body


# ─── validate_product_defaults ──────────────────────────────────────
def test_validate_passes_when_all_four_ids_present():
    ok, missing = validate_product_defaults(_FULL_SETTINGS)
    assert ok is True
    assert missing == []


def test_validate_returns_missing_keys_in_canonical_order():
    ok, missing = validate_product_defaults({})
    assert ok is False
    # Order MUST be deterministic so the UI lists them consistently.
    assert missing == list(REQUIRED_PRODUCT_DEFAULT_KEYS)


def test_validate_treats_empty_string_as_missing():
    settings = dict(_FULL_SETTINGS)
    settings["default_product_tax_id"] = "   "
    ok, missing = validate_product_defaults(settings)
    assert ok is False
    assert missing == ["default_product_tax_id"]


def test_validate_treats_non_string_as_missing():
    settings = dict(_FULL_SETTINGS)
    settings["default_sales_account_id"] = None
    ok, missing = validate_product_defaults(settings)
    assert ok is False
    assert missing == ["default_sales_account_id"]


# ─── build_missing_product_defaults_error ───────────────────────────
def test_error_payload_includes_arabic_message_and_canonical_code():
    err = build_missing_product_defaults_error([
        "default_product_category_id",
        "default_sales_account_id",
    ])
    assert err["code"]            == "missing_qoyod_product_defaults"
    assert err["failed_at_stage"] == "PREFLIGHT_PRODUCT_DEFAULTS"
    assert err["missing"]         == [
        "default_product_category_id",
        "default_sales_account_id",
    ]
    # Arabic labels surfaced so the operator knows what to fix.
    assert "التصنيف"        in err["message"]
    assert "حساب المبيعات"  in err["message"]


# ─── resolve_products gates before POST ─────────────────────────────
@pytest.mark.asyncio
async def test_resolve_products_blocks_before_post_when_defaults_missing():
    """Settings missing the four ids → resolver MUST refuse BEFORE
    calling api_client.create_product. The error code is
    `missing_qoyod_product_defaults` and `qoyod_request_sent=False`."""
    posted = []

    class _StubClient:
        async def find_product_by_sku(self, sku): return None
        async def create_product(self, payload, *, idem):
            posted.append(payload)
            raise AssertionError(
                "create_product MUST NOT be called when defaults are missing")

    class _Col:
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False): pass
    class _DB:
        def __init__(self):
            self.qoyod_products_mapping = _Col()
            self.qoyod_settings = _Col()

    res = await resolve_products(
        _DB(), "main",
        [{"sku": "AMS11961", "name": "تغليف", "unit_price": 5,
          "quantity": 1}],
        {},   # ← empty settings = all four ids missing
        trace_id="t1", api_client=_StubClient())
    assert res.success is False
    assert posted == [], "no /products POST should have happened"
    err = res.items[0].error
    assert err["code"]            == "missing_qoyod_product_defaults"
    assert err["failed_at_stage"] == "PREFLIGHT_PRODUCT_DEFAULTS"
    assert set(err["missing"]) >= set(REQUIRED_PRODUCT_DEFAULT_KEYS)


@pytest.mark.asyncio
async def test_resolve_products_passes_through_when_defaults_present():
    """Sanity: with the four ids configured, resolver proceeds to
    POST /products (and the payload carries the four ids)."""
    captured = []

    class _StubClient:
        async def find_product_by_sku(self, sku): return None
        async def create_product(self, payload, *, idem):
            captured.append(payload)
            return {"product": {"id": "Q-OK"}}

    class _Col:
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False): pass
    class _DB:
        def __init__(self):
            self.qoyod_products_mapping = _Col()
            self.qoyod_settings = _Col()

    res = await resolve_products(
        _DB(), "main",
        [{"sku": "AMS11961", "name": "تغليف", "unit_price": 5,
          "quantity": 1}],
        _FULL_SETTINGS,
        trace_id="t1", api_client=_StubClient())
    assert res.success is True
    assert len(captured) == 1
    body = captured[0]["product"]
    assert body["category_id"]          == "CAT-99"
    assert body["tax_id"]               == "TAX-15"     # Iter-290g scalar
    assert body["product_unit_type_id"] == "UNIT-PIECE"
    assert body["sales_account_id"]     == "ACC-SALES"
    assert body["sku"] == "AMS11961"


# ─── Preview surface ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_preview_reprocess_surfaces_missing_defaults_block():
    """The operator hits preview → sees the missing-defaults gap BEFORE
    flipping anything live."""
    from integrations.qoyod.preview_reprocess import (
        preview_reprocess_one_order,
    )
    from datetime import datetime, timezone

    class _Cursor:
        def __init__(self, rows): self._rows = list(rows)
        async def to_list(self, *, length=None):
            return list(self._rows) if length is None else self._rows[:length]
    class _Col:
        def __init__(self, rows=None): self.rows = list(rows or [])
        def find(self, q):
            def m(r):
                for k, v in q.items():
                    if k == "$or":
                        if not any(_match_one(r, o) for o in v): return False
                    else:
                        if r.get(k) != v: return False
                return True
            return _Cursor([r for r in self.rows if m(r)])
        async def find_one(self, q, projection=None): return None
    def _match_one(r, sub):
        for k, v in sub.items():
            if r.get(k) != v: return False
        return True
    class _DB:
        def __init__(self):
            self.integration_inbox = _Col([{
                "trace_id": "t1", "id": "t1", "user_id": "main",
                "salla_order_number": "268756329",
                "salla_order_id": "538555555",
                "received_at": datetime(2026, 6, 27, tzinfo=timezone.utc).isoformat(),
                "pipeline_stage": "DEAD_LETTER",
                "raw_payload": {
                    "event_type": "order_completed",
                    "order_status_slug": "completed",
                    "order_status": "تم التنفيذ",
                    "order_number": "268756329",
                    "order_id": "538555555",
                    "currency": "SAR",
                    "subtotal": 304,
                    "total_amount": 290.63,
                    "shipping_cost": 0,
                    "customer_name": "x",
                    "payment_method": "tamara_installment",
                    "items": [{
                        "sku": "AMS11961", "name": "تغليف", "quantity": 1,
                        "amounts": {
                            "price_without_tax": {"amount": 5,    "currency": "SAR"},
                            "total_discount":    {"amount": 5,    "currency": "SAR"},
                            "tax":   {"percent": "0", "amount": {"amount": 0, "currency": "SAR"}},
                            "total": {"amount": 0,    "currency": "SAR"},
                        }
                    }]
                }
            }])
            # No qoyod_settings configured → all four ids missing.
            self.qoyod_settings = _Col([{"user_id": "main"}])
            self.qoyod_invoices = _Col([])

    out = await preview_reprocess_one_order(
        _DB(), user_id="main", trace_id="t1")
    assert out["ok"] is True   # preview never raises
    pdf = out["stages"]["products_preview"]["product_defaults_status"]
    assert pdf["ok"] is False
    assert pdf["code"] == "missing_qoyod_product_defaults"
    assert "التصنيف" in pdf["message"]
    assert set(pdf["missing"]) == set(REQUIRED_PRODUCT_DEFAULT_KEYS)
