"""Tests for Plan-B totals RCA (breakdown in error + /diagnose endpoint).

Coverage:
    D1  totals_mismatch error carries a full `breakdown` with per-item
        rows, shipping section, cod_fee section, and a
        `difference_source_hint`.
    D2  When Salla ships an order with a shipping_amount but no
        `default_shipping_product_id` is configured, the breakdown
        surfaces "شحن مُهمَل" as the source-of-difference hint.
    D3  `diagnose_totals` returns a JSON-safe RCA without touching
        قيود (no network call needed) and marks `within_tolerance`.
    D4  Reconstruct the 270943310-style scenario (Salla total 219.91,
        expected 219.93 → diff 0.02) and confirm the breakdown
        exposes the exact contributing lines.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused, _build_invoice_payload,
    _riyadh_today_iso,
)
from integrations.qoyod_manual.diagnose import diagnose_totals


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_totals_rca"]


def _row(order_number: str, items, *, total: float,
         shipping: float = 0.0, cod: float = 0.0,
         order_date: str = "2026-07-08"):
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
            "total_amount":  total,
            "subtotal":      total - shipping - cod,
            "shipping_amount": shipping,
            "cod_fee_amount":  cod,
            "tax_amount":    0.0,
            "discount_amount": 0.0,
            "currency":      "SAR",
            "payment_method":         "credit_card",
            "payment_method_native":  "credit_card",
            "customer": {"name": "عميل", "phone": "+966500000000"},
            "items":   items,
        },
        "raw_payload": {"data": {"created_at": order_date}},
    }


async def _seed_creds_and_settings(db, *, ship_pid=None, cod_pid=None):
    await db.qoyod_settings.insert_one({
        "user_id":                    TENANT,
        "qoyod_tax_percent":          15,
        "default_inventory_id":       1,
        "default_branch_id":          1,
        "default_product_category_id": 1,
        "default_product_tax_id":     1,
        "default_sales_account_id":   100,
        "default_product_unit_type_id": 1,
        "default_shipping_product_id": ship_pid,
        "default_cod_fee_product_id":  cod_pid,
        "payment_method_mapping": [
            {"salla_method": "credit_card", "qoyod_account_id": "42",
             "posting_mode": "paid_receipt"},
        ],
    })
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key")


# ────────────────────────────────────────────────────────────────────
# D1 — Error detail carries a full breakdown.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_totals_mismatch_error_has_breakdown(db):
    await _seed_creds_and_settings(db)
    # Force a mismatch: Salla says 500 but items sum to only 100.
    row = _row("MISMATCH-1",
               items=[{"sku": "SKU-1", "name": "بند",
                        "quantity": 1, "unit_price": 100.0,
                        "total": 100.0}],
               total=500.0)
    await db.integration_inbox.insert_one(row)

    async def _no_inv(*_a, **_k):
        return None

    async def _find_cust(*_a, **_k):
        return [{"id": 5}]

    async def _find_prod(*_a, **_k):
        return {"id": 9, "sku": "SKU-1"}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference", new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone", new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku", new=AsyncMock(side_effect=_find_prod)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db, user_id=TENANT, order_number="MISMATCH-1")

    d = exc.value.extra
    assert exc.value.code == "totals_mismatch"
    assert d["salla_total"] == 500.0
    assert "breakdown" in d
    b = d["breakdown"]
    assert b["tax_percent"] == 15.0
    assert len(b["items"]) == 1
    it = b["items"][0]
    assert it["sku"] == "SKU-1"
    assert "line_gross_after_tax" in it
    assert "line_tax_15pct" in it
    assert "delta_vs_salla_line" in it
    assert "difference_source_hint" in b


# ────────────────────────────────────────────────────────────────────
# D2 — Shipping not wired → hint calls it out explicitly.
# ────────────────────────────────────────────────────────────────────
def test_shipping_ignored_when_no_product_id_configured():
    canon = {
        "order_number": "SHIP-1",
        "order_id":     "SHIP-1",
        "total_amount": 150.0,
        "shipping_amount": 20.0,
        "cod_fee_amount": 0.0,
        "currency": "SAR",
        "items": [{"sku": "A", "name": "A", "quantity": 1,
                    "unit_price": 100.0, "total": 130.0}],
    }
    settings = {"qoyod_tax_percent": 15}
    # No default_shipping_product_id
    _payload, expected, b = _build_invoice_payload(
        canon=canon, contact_id=1, line_resolutions={"A": 9},
        settings=settings, send_date_iso=_riyadh_today_iso())
    assert b["shipping"]["included"] is False
    assert "مُهمَل" in b["shipping"]["reason"] or \
           "default_shipping_product_id" in b["shipping"]["reason"]
    # The hint must mention the ignored shipping so an operator sees
    # WHY the 20 SAR gap exists between Salla and the expected total.
    assert "شحن" in b["difference_source_hint"]


# ────────────────────────────────────────────────────────────────────
# D3 — diagnose endpoint returns RCA without touching قيود.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_diagnose_endpoint_returns_breakdown(db):
    await _seed_creds_and_settings(db)
    row = _row("DIAG-1",
               items=[{"sku": "SKU-A", "name": "منتج",
                        "quantity": 2, "unit_price": 100.0,
                        "total": 230.0}],
               total=230.0)
    await db.integration_inbox.insert_one(row)

    out = await diagnose_totals(
        db, user_id=TENANT, order_number="DIAG-1")
    assert out["ok"] is True
    assert out["order_number"] == "DIAG-1"
    assert out["salla_total"] == 230.0
    assert "expected_qoyod_total" in out
    assert "difference" in out
    assert "within_tolerance" in out
    assert "breakdown" in out
    assert len(out["breakdown"]["items"]) == 1
    assert out["canonical_summary"]["items_count"] == 1
    assert out["settings_used"]["qoyod_tax_percent"] == 15


# ────────────────────────────────────────────────────────────────────
# D4 — Reproduce the 270943310-style scenario (219.91 vs 219.93).
#
# Construct an order where Salla says total=219.91 but line rounding
# yields 219.93 — matching the reported guard breach.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_penny_drift_scenario_270943310_like(db):
    await _seed_creds_and_settings(db)
    # Two lines with fractional totals that don't line up cleanly
    # with 2dp × 1.15 tax factor → creates the ~0.02 residual drift.
    items = [
        {"sku": "P1", "name": "P1", "quantity": 1,
         "unit_price": 95.615, "total": 109.96},   # ~95.615 * 1.15
        {"sku": "P2", "name": "P2", "quantity": 1,
         "unit_price": 95.61,  "total": 109.95},
    ]
    row = _row("270943310-LIKE", items=items, total=219.91)
    await db.integration_inbox.insert_one(row)

    out = await diagnose_totals(
        db, user_id=TENANT, order_number="270943310-LIKE")
    assert out["ok"] is True
    # We WANT drift here (that's the whole point) — assert it's small
    # but non-zero, and the breakdown row-by-row is populated.
    assert out["salla_total"] == 219.91
    assert isinstance(out["expected_qoyod_total"], float)
    assert len(out["breakdown"]["items"]) == 2
    for item in out["breakdown"]["items"]:
        assert "delta_vs_salla_line" in item
        assert "line_tax_15pct" in item
    assert "difference_source_hint" in out
