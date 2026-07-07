"""Tests for Plan-B product payload shape (Iter-286/287-derived).

The user reported orders 270930851 / 270674297 / 270619255 failing on
POST /products with:
    422 — "enter at least a purchase price or a sales price to continue"
while Plan-B was sending `buying_price` / `selling_price`. The fix
adopts the SAME proven shape used by the legacy product_resolver:
`sale_item: 1` + `selling_price` + 4 required scalar ids (category,
tax, unit_type, sales_account) + `type` (not `product_type`).

Coverage:
    P1  Primary payload carries `sale_item: 1`, `selling_price`,
        `type`, and no legacy `buying_price` / `product_type` fields.
    P2  Required tenant ids are stamped as SCALARS (int when possible).
    P3  Zero-price items get bumped to selling_price=1.0 in the
        catalog payload (invoice line still uses the real price).
    P4  On a 422 from primary, the fallback payload is tried ONCE
        with the minimal fields; both payloads are recorded in the
        `product_create_failed` error detail if the fallback also
        fails.
    P5  Fallback payload contains ONLY the fields Qoyod's validator
        absolutely needs (no type/is_non_stock/purchase_item).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
    _build_product_payload, _build_product_payload_fallback,
    _unwrap_id,
)
from integrations.qoyod_manual.client import ManualQoyodError


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_product_payload"]


def _settings_dict(**overrides):
    base = {
        "user_id":                       TENANT,
        "qoyod_tax_percent":             15,
        "default_inventory_id":          1,
        "default_branch_id":             1,
        "default_product_category_id":   3,
        "default_product_tax_id":        7,
        "default_product_unit_type_id":  6,
        "default_sales_account_id":      17,
        "default_product_type":          "service",
        "payment_method_mapping": [
            {"salla_method": "credit_card", "qoyod_account_id": "42",
             "posting_mode": "paid_receipt"},
        ],
    }
    base.update(overrides)
    return base


def _row(order_number: str, sku: str, name: str, *,
         unit_price: float, order_date: str = "2026-07-08"):
    return {
        "id":                 f"row-{order_number}",
        "user_id":            TENANT,
        "trace_id":           f"tr-{order_number}",
        "salla_order_number": order_number,
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":  order_number,
            "order_id":      order_number,
            "order_date":    order_date,
            "created_at":    order_date,
            "order_status":  "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  unit_price,
            "subtotal":      unit_price,
            "shipping_amount": 0,
            "cod_fee_amount":  0,
            "currency":      "SAR",
            "payment_method": "credit_card",
            "payment_method_native": "credit_card",
            "customer": {"name": "عميل", "phone": "+966500000000"},
            "items": [{"sku": sku, "name": name,
                        "quantity": 1, "unit_price": unit_price,
                        "total": unit_price}],
        },
        "raw_payload": {"data": {"created_at": order_date}},
    }


async def _seed(db, settings=None):
    await db.qoyod_settings.insert_one(settings or _settings_dict())
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key")


# ────────────────────────────────────────────────────────────────────
# P1 — Primary payload has the shape Qoyod accepts.
# ────────────────────────────────────────────────────────────────────
def test_primary_product_payload_shape():
    item = {"sku": "AMS10939",
            "name": "هودي ليحتضنك دائمًا - ألوان متعددة",
            "quantity": 1, "unit_price": 129.0, "total": 129.0}
    payload = _build_product_payload(item, _settings_dict())
    p = payload["product"]
    # MUST be present (this is what fixes the 422 the user hit):
    assert p["sale_item"] == 1
    assert p["selling_price"] == 129.0
    # MUST NOT contain the fields Qoyod rejected:
    assert "buying_price" not in p
    assert "product_type" not in p
    assert "name_ar" not in p
    assert "name_en" not in p
    # The correct field is `type`, not `product_type`.
    assert p["type"] == "service"
    assert p["name"] == item["name"]
    assert p["sku"] == "AMS10939"
    # is_non_stock true when type=service.
    assert p["is_non_stock"] is True
    assert p["purchase_item"] == 0


# ────────────────────────────────────────────────────────────────────
# P2 — Required tenant ids stamped as SCALARS.
# ────────────────────────────────────────────────────────────────────
def test_required_ids_stamped_as_scalars():
    settings = _settings_dict(
        default_product_category_id=["3"],   # array shape → collapse
        default_product_tax_id="7",           # numeric string → int
        default_product_unit_type_id=6,       # int as-is
        default_sales_account_id="17",
    )
    p = _build_product_payload(
        {"sku": "X", "name": "X", "quantity": 1,
         "unit_price": 10.0, "total": 10.0},
        settings)["product"]
    assert p["category_id"] == 3
    assert p["tax_id"] == 7
    assert p["product_unit_type_id"] == 6
    assert p["sales_account_id"] == 17
    # Extra defence — never emit arrays.
    for k in ("category_id", "tax_id", "product_unit_type_id",
              "sales_account_id"):
        assert not isinstance(p[k], (list, tuple))


def test_unwrap_id_handles_all_shapes():
    assert _unwrap_id(None) is None
    assert _unwrap_id("") is None
    assert _unwrap_id([]) is None
    assert _unwrap_id([""]) is None
    assert _unwrap_id(3) == 3
    assert _unwrap_id("3") == 3
    assert _unwrap_id("3.0") == 3
    assert _unwrap_id(["7"]) == 7
    assert _unwrap_id(["a"]) == "a"
    assert _unwrap_id([None, "", "5"]) == 5


# ────────────────────────────────────────────────────────────────────
# P3 — Zero-price bumped to 1.0 in catalog only.
# ────────────────────────────────────────────────────────────────────
def test_zero_price_bumped_in_catalog():
    p = _build_product_payload(
        {"sku": "FREE", "name": "هدية", "quantity": 1,
         "unit_price": 0, "total": 0},
        _settings_dict())["product"]
    assert p["selling_price"] == 1.0


# ────────────────────────────────────────────────────────────────────
# P4 — 422 self-heal: fallback tried ONCE; both payloads surfaced on
#      final failure.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_422_triggers_fallback_and_surfaces_both_payloads(db):
    await _seed(db)
    row = _row("270619255", "AMS11220",
                "محفظة للجوال تصميم حسب الطلب", unit_price=88.0)
    await db.integration_inbox.insert_one(row)

    call_log: list[dict] = []

    async def _no_inv(*_a, **_k): return None

    async def _find_cust(*_a, **_k): return [{"id": 100}]

    async def _no_product(*_a, **_k): return None

    async def _create_product_always_422(payload, *, idem):
        call_log.append({"idem": idem, "payload": payload})
        raise ManualQoyodError(
            status_code=422,
            endpoint="POST /products",
            response_excerpt=(
                "{'errors': {'base': ['enter at least a purchase price "
                "or a sales price to continue.']}}"),
            request_body=payload,
        )

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(side_effect=_no_product)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_product",
               new=AsyncMock(side_effect=_create_product_always_422)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db, user_id=TENANT, order_number="270619255")

    assert exc.value.code == "product_create_failed"
    # Fallback WAS attempted (exactly one retry) → 2 calls total.
    assert len(call_log) == 2
    # Primary carries the full shape.
    assert call_log[0]["payload"]["product"]["sale_item"] == 1
    assert "type" in call_log[0]["payload"]["product"]
    # Fallback strips the extras.
    fb = call_log[1]["payload"]["product"]
    assert fb["sale_item"] == 1
    assert fb["selling_price"] == 88.0
    assert "type" not in fb
    assert "is_non_stock" not in fb
    assert "purchase_item" not in fb
    # Error detail exposes BOTH attempts for the operator.
    d = exc.value.extra
    assert "primary_attempt" in d
    assert "fallback_attempt" in d
    assert d["primary_attempt"]["payload"]["product"]["sale_item"] == 1
    assert d["fallback_attempt"]["payload"]["product"]["sale_item"] == 1
    # Both قيود responses are captured with the offending message.
    assert d["primary_attempt"]["response"]["status_code"] == 422
    assert d["fallback_attempt"]["response"]["status_code"] == 422


# ────────────────────────────────────────────────────────────────────
# P5 — Primary succeeds → no fallback call, trace records used_stage.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_primary_success_records_used_stage(db):
    await _seed(db)
    row = _row("P-OK", "SKU-OK", "منتج ناجح", unit_price=50.0)
    await db.integration_inbox.insert_one(row)

    async def _no_inv(*_a, **_k): return None

    async def _find_cust(*_a, **_k): return [{"id": 1}]

    async def _no_product(*_a, **_k): return None

    calls = []

    async def _create_product_ok(payload, *, idem):
        calls.append(payload)
        return {"product": {"id": 555}}

    async def _create_invoice(payload, *, idem):
        return {"invoice": {"id": 999, "number": "INV-999",
                             "reference": payload["invoice"]["reference"]}}

    async def _create_payment(payload, *, idem):
        return {"invoice_payment": {"id": 3000}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(side_effect=_no_product)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_product",
               new=AsyncMock(side_effect=_create_product_ok)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice",
               new=AsyncMock(side_effect=_create_invoice)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_create_payment)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P-OK")

    assert result["ok"] is True
    # Only primary attempt.
    assert len(calls) == 1
    # Trace records the request_body for auditing.
    products_step = next(s for s in result["steps"]
                          if s["step"] == "products")
    entry = products_step["resolutions"][0]
    assert entry["used_stage"] == "primary"
    assert entry["attempts"] == 1
    assert "request_body" in entry
    assert entry["request_body"]["product"]["sku"] == "SKU-OK"
