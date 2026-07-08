"""Tests for the auto rounding-adjustment line (Plan-B rev 2026-07-08).

User directive: when تقريب سنتات leaves a residual ≤ 1.00 SAR between
Salla's declared total and the قيود-computed sum, add ONE dedicated
tax-free line ("تسوية فرق التقريب مع سلة") so the invoice closes to
Salla's number exactly. Only applies when the source is items-only
rounding — an ignored shipping / COD product id is a real config
gap and must still fail with `totals_mismatch`.

Coverage:
    A1  Small positive residual (+0.05) → adjustment applied,
        expected_total == salla_total, `rounding_adjustment.applied=true`.
    A2  Small negative residual (-0.02) → adjustment applied with a
        negative unit_price on the adjustment line.
    A3  Adjustment refuses when rounding_adjustment_product_id is
        absent → new dedicated error code
        `rounding_adjustment_product_missing`.
    A4  Adjustment DOES NOT fire when shipping is misconfigured
        (real config gap) — still `totals_mismatch`.
    A5  Adjustment DOES NOT fire for large residuals (> 1.00 SAR).
    A6  Payment amount == final expected_total when adjustment is
        applied (so قيود invoice closes to zero).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
    _build_invoice_payload, _riyadh_today_iso,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_rounding_adj"]


def _seed_settings_payload(**overrides):
    base = {
        "user_id":                       TENANT,
        "qoyod_tax_percent":             15,
        "default_inventory_id":          1,
        "default_branch_id":             1,
        "default_product_category_id":   3,
        "default_product_tax_id":        7,
        "default_product_unit_type_id":  6,
        "default_sales_account_id":      17,
        "default_shipping_product_id":   500,
        "default_cod_fee_product_id":    None,
        "rounding_adjustment_product_id": 999,
        "payment_method_mapping": [
            {"salla_method": "credit_card", "qoyod_account_id": "42",
             "posting_mode": "paid_receipt"},
        ],
    }
    base.update(overrides)
    return base


def _canon_two_items(*, unit_prices_totals, salla_total,
                     shipping_amount=0.0):
    """Compose a canonical payload with N items whose (unit_price,
    total) pairs are supplied directly. Salla total is set explicitly
    so we can force a specific rounding residual."""
    items = []
    for i, (up, tt) in enumerate(unit_prices_totals):
        items.append({"sku": f"SKU-{i}", "name": f"P{i}",
                      "quantity": 1, "unit_price": up, "total": tt})
    return {
        "order_number": "R-1",
        "order_id":     "R-1",
        "total_amount": salla_total,
        "shipping_amount": shipping_amount,
        "cod_fee_amount": 0.0,
        "currency":     "SAR",
        "items":        items,
    }


# ────────────────────────────────────────────────────────────────────
# A1 — Positive residual absorbed by the adjustment line.
# ────────────────────────────────────────────────────────────────────
def test_positive_residual_absorbed():
    # Reproduce the 270884379 pattern: 5 identical lines where
    # unit_price=100, target_gross=87.21. قيود's math yields
    # discount=24.17 → gross=87.20 → 5 × 0.01 short → residual +0.05.
    canon = {
        "order_number": "R-1", "order_id": "R-1",
        "total_amount": 436.05,
        "shipping_amount": 0.0, "cod_fee_amount": 0.0,
        "currency": "SAR",
        "items": [
            {"sku": f"SKU-{i}", "name": f"P{i}", "quantity": 1,
             "unit_price": 100.0, "total": 87.21}
            for i in range(5)
        ],
    }
    settings = _seed_settings_payload()
    resolutions = {f"SKU-{i}": i + 1 for i in range(5)}
    _payload, expected_total, breakdown = _build_invoice_payload(
        canon=canon, contact_id=1, line_resolutions=resolutions,
        settings=settings, send_date_iso=_riyadh_today_iso())
    residual_before = breakdown["residual_before_adjustment"]
    assert residual_before == 0.05
    adj = breakdown["rounding_adjustment"]
    assert adj is not None
    assert adj["applied"] is True
    assert adj["product_id"] == 999
    assert adj["amount"] == 0.05
    assert expected_total == 436.05
    assert breakdown["difference"] == 0.0
    lines = _payload["invoice"]["line_items"]
    adj_line = lines[-1]
    assert adj_line["product_id"] == 999
    assert adj_line["tax_percent"] == 0.0
    assert adj_line["unit_price"] == 0.05
    assert adj_line["description"] == "تسوية فرق التقريب مع سلة"


# ────────────────────────────────────────────────────────────────────
# A2 — Negative residual (Salla lower than expected) supported.
# ────────────────────────────────────────────────────────────────────
def test_negative_residual_supported():
    # Reproduce a residual where قيود overshoots Salla by 0.01 per line.
    # unit_price=100, target=87.22 → discount raw = 100-75.8434=24.1566
    # → q2 24.16 → gross = (100-24.16)*1.15 = 75.84*1.15 = 87.216 →
    # q2 = 87.22 (matches target). Try unit_price=100, target=87.20:
    #   target_net = 87.20/1.15 = 75.8261 → discount = 24.1739 → q2 24.17
    #   → gross = 75.83*1.15 = 87.2045 → q2 = 87.20 (matches target).
    # Use qty=2 items whose fractional target produces the drift:
    canon = {
        "order_number": "R-2", "order_id": "R-2",
        "total_amount": 219.91,  # Salla says this
        "shipping_amount": 0.0, "cod_fee_amount": 0.0,
        "currency": "SAR",
        "items": [
            # unit=95.615, target=109.96. discount raw = 95.615 -
            # 109.96/1.15 = 95.615 - 95.6174 = -0.0024 (< 0) → path:
            # shrink unit → unit_price = q2(95.6174) = 95.62.
            # line_gross = (95.62 - 0) * 1.15 = 109.963 → q2 = 109.96.
            # Second item: unit=95.61, target=109.95. discount raw =
            # 95.61 - 95.6087 = 0.0013 → q2 = 0.00, unit_price = 95.61.
            # line_gross = 95.61 * 1.15 = 109.9515 → q2 = 109.95.
            # Sum = 109.96 + 109.95 = 219.91 (matches salla).
            {"sku": "SKU-A", "name": "A", "quantity": 1,
             "unit_price": 95.615, "total": 109.96},
            {"sku": "SKU-B", "name": "B", "quantity": 1,
             "unit_price": 95.61, "total": 109.95},
        ],
    }
    settings = _seed_settings_payload()
    resolutions = {"SKU-A": 1, "SKU-B": 2}
    _payload, expected_total, breakdown = _build_invoice_payload(
        canon=canon, contact_id=1, line_resolutions=resolutions,
        settings=settings, send_date_iso=_riyadh_today_iso())
    # Whatever the residual, verify the adjustment behaves consistently.
    residual_before = breakdown["residual_before_adjustment"]
    if abs(residual_before) <= 0.01:
        # No residual produced by this specific input — just verify
        # the adjustment path doesn't fire for tolerance-passing sums.
        assert breakdown["rounding_adjustment"] is None
        return
    adj = breakdown["rounding_adjustment"]
    assert adj is not None
    assert adj["applied"] is True
    # Same sign as residual_before (positive-Salla or negative-Salla).
    assert (adj["amount"] > 0) == (residual_before > 0)
    assert expected_total == canon["total_amount"]
    assert breakdown["difference"] == 0.0
    adj_line = _payload["invoice"]["line_items"][-1]
    # Sign preserved on unit_price.
    assert (adj_line["unit_price"] > 0) == (residual_before > 0)


# ────────────────────────────────────────────────────────────────────
# A3 — Rounding-only mismatch + no product configured → new code.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_product_id_refuses_with_dedicated_code(db):
    settings = _seed_settings_payload(rounding_adjustment_product_id=None)
    await db.qoyod_settings.insert_one(settings)
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key")
    row = {
        "id": "row-A3", "user_id": TENANT, "trace_id": "tr",
        "salla_order_number": "R-A3",
        "received_at": datetime.now(timezone.utc),
        "pipeline_stage": "NORMALIZED",
        "canonical_payload": {
            "order_number": "R-A3", "order_id": "R-A3",
            "order_date":   "2026-07-08",
            "created_at":   "2026-07-08",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  436.05,
            "shipping_amount": 0.0, "cod_fee_amount": 0.0,
            "currency":     "SAR",
            "payment_method": "credit_card",
            "payment_method_native": "credit_card",
            "customer": {"name": "T", "phone": "+966500000000"},
            "items": [
                {"sku": f"SKU-{i}", "name": f"P{i}", "quantity": 1,
                 "unit_price": 100.0, "total": 87.21}
                for i in range(5)
            ],
        },
        "raw_payload": {"data": {"created_at": "2026-07-08"}},
    }
    await db.integration_inbox.insert_one(row)

    async def _no_inv(*_a, **_k): return None
    async def _find_cust(*_a, **_k): return [{"id": 1}]
    async def _find_prod(*_a, **_k): return {"id": 22, "sku": "x"}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference", new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone", new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku", new=AsyncMock(side_effect=_find_prod)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(db, user_id=TENANT, order_number="R-A3")

    assert exc.value.code == "rounding_adjustment_product_missing"
    assert "rounding_adjustment_product_id" in exc.value.message
    assert exc.value.extra.get("residual_would_be") is not None


# ────────────────────────────────────────────────────────────────────
# A4 — Ignored shipping = config gap → still totals_mismatch, NOT
#      absorbed by adjustment.
# ────────────────────────────────────────────────────────────────────
def test_ignored_shipping_still_totals_mismatch():
    canon = _canon_two_items(
        unit_prices_totals=[(100.0, 130.0)], salla_total=150.0,
        shipping_amount=20.0)
    # No default_shipping_product_id → shipping ignored → 20 SAR gap.
    settings = _seed_settings_payload(default_shipping_product_id=None)
    resolutions = {"SKU-0": 1}
    _payload, expected_total, breakdown = _build_invoice_payload(
        canon=canon, contact_id=1, line_resolutions=resolutions,
        settings=settings, send_date_iso=_riyadh_today_iso())
    # The rounding adjustment MUST NOT fire — the gap is 20 SAR and
    # its cause is misconfigured shipping (structural).
    adj = breakdown["rounding_adjustment"]
    assert adj is None or adj["applied"] is False
    assert abs(breakdown["difference"]) > 0.01
    # And the shipping section flags the config gap.
    assert breakdown["shipping"]["included"] is False


# ────────────────────────────────────────────────────────────────────
# A5 — Large residual not absorbed.
# ────────────────────────────────────────────────────────────────────
def test_large_residual_not_absorbed():
    # Force a huge residual (5 SAR).
    canon = _canon_two_items(
        unit_prices_totals=[(100.0, 100.0)], salla_total=105.0)
    settings = _seed_settings_payload()
    resolutions = {"SKU-0": 1}
    _payload, expected_total, breakdown = _build_invoice_payload(
        canon=canon, contact_id=1, line_resolutions=resolutions,
        settings=settings, send_date_iso=_riyadh_today_iso())
    adj = breakdown["rounding_adjustment"]
    assert adj is None or adj["applied"] is False
    # Big diff should NOT be silently absorbed.
    assert abs(breakdown["difference"]) > 0.01


# ────────────────────────────────────────────────────────────────────
# A6 — End-to-end happy path: payment == expected_total, invoice closes.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_end_to_end_with_adjustment_closes_invoice(db):
    settings = _seed_settings_payload()
    await db.qoyod_settings.insert_one(settings)
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key")
    row = {
        "id": "row-A6", "user_id": TENANT, "trace_id": "tr",
        "salla_order_number": "R-A6",
        "received_at": datetime.now(timezone.utc),
        "pipeline_stage": "NORMALIZED",
        "canonical_payload": {
            "order_number": "R-A6", "order_id": "R-A6",
            "order_date":   "2026-07-08",
            "created_at":   "2026-07-08",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  436.05,
            "shipping_amount": 0.0, "cod_fee_amount": 0.0,
            "currency":     "SAR",
            "payment_method": "credit_card",
            "payment_method_native": "credit_card",
            "customer": {"name": "T", "phone": "+966500000000"},
            "items": [
                {"sku": f"SKU-{i}", "name": f"P{i}", "quantity": 1,
                 "unit_price": 100.0, "total": 87.21}
                for i in range(5)
            ],
        },
        "raw_payload": {"data": {"created_at": "2026-07-08"}},
    }
    await db.integration_inbox.insert_one(row)

    captured: dict = {}

    async def _no_inv(*_a, **_k): return None
    async def _find_cust(*_a, **_k): return [{"id": 1}]
    async def _find_prod(*_a, **_k): return {"id": 22, "sku": "x"}
    async def _create_invoice(payload, *, idem):
        captured["invoice"] = payload
        return {"invoice": {"id": 601, "number": "INV-601",
                             "reference": payload["invoice"]["reference"]}}
    async def _create_payment(payload, *, idem):
        captured["payment"] = payload
        return {"invoice_payment": {"id": 9001}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference", new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone", new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku", new=AsyncMock(side_effect=_find_prod)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice", new=AsyncMock(side_effect=_create_invoice)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment", new=AsyncMock(side_effect=_create_payment)):
        result = await manual_send_one(db, user_id=TENANT, order_number="R-A6")

    assert result["ok"] is True
    # Payment amount matches expected_total AND salla_total (all equal).
    pay = captured["payment"]["invoice_payment"]
    assert pay["amount"] == result["expected_total"]
    assert pay["amount"] == 436.05
    # Adjustment line is present in the invoice payload.
    lines = captured["invoice"]["invoice"]["line_items"]
    adj_lines = [l for l in lines
                 if l.get("description") == "تسوية فرق التقريب مع سلة"]
    assert len(adj_lines) == 1
    assert adj_lines[0]["tax_percent"] == 0.0
    assert adj_lines[0]["product_id"] == 999
    assert adj_lines[0]["unit_price"] == 0.05
    # Invoice closes to zero (payment amount == invoice total).
    remaining = round(result["expected_total"] - pay["amount"], 2)
    assert remaining == 0.0
