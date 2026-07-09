"""TEMPORARY read-only diagnostic endpoint (2026-02).

Purpose
-------
Preview the exact Qoyod invoice payload that would be built for a
single Salla order — WITHOUT hitting Qoyod, WITHOUT writing to any
collection, WITHOUT changing any Plan-B state.

Contract (user directive, 2026-02):
  • Path: POST /api/admin/dry-run-qoyod-payload
  • Body: {"order_number": "270457540", "token": "<uuid>"}
  • Guard 1: `DIAGNOSTIC_TOKEN` env var MUST be set. If it isn't
    set / is empty, the route returns 404 — the endpoint effectively
    "doesn't exist" from an attacker's point of view.
  • Guard 2: caller's `token` must match `DIAGNOSTIC_TOKEN` exactly
    (constant-time compare). Any mismatch → 404 (same shape as the
    env-missing branch, so probes can't distinguish states).
  • Guard 3: only ONE hard-coded order number is accepted:
    "270457540". Any other → 404.
  • The handler NEVER calls Qoyod (no `qoyod_client`, no HTTP).
  • The handler NEVER writes to Mongo (`find` / `count_documents`
    only — no insert/update/delete).

Lifecycle (per user):
  1. Deploy with this router included.
  2. Set env var `DIAGNOSTIC_TOKEN=<uuid>` in production.
  3. Call the endpoint ONCE, capture the JSON.
  4. Remove `DIAGNOSTIC_TOKEN` from env → endpoint auto-404s.
  5. Delete this file in a follow-up commit and redeploy.
"""
from __future__ import annotations

import hmac
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from integrations.qoyod_manual.send import (
    _compute_item_line, _line_gross, _q2, _f,
    _TWO_PLACES, _to_int,
)

# The only order number the user pre-authorised (see 2026-02 directive).
ALLOWED_ORDER_NUMBERS = {"270457540"}

SENTINEL_PID = 9999999   # placeholder — real send resolves via قيود


def _diagnostic_token() -> str:
    """Fresh read each request — a stale process-local cache would
    let the endpoint keep working after the operator removes the
    env var. That would violate the "deactivate by env removal"
    lifecycle guarantee."""
    return (os.environ.get("DIAGNOSTIC_TOKEN") or "").strip()


def _not_found() -> HTTPException:
    # Uniform 404 regardless of which guard failed so a probe cannot
    # tell "env missing" from "wrong token" from "wrong order".
    return HTTPException(status_code=404, detail="Not Found")


async def _load_canon(db, order_number: str) -> Optional[dict]:
    """Newest inbox trace for the order — matches list_pending_orders
    dedup rule (newest received_at wins)."""
    pipeline = [
        {"$match": {"salla_order_number": order_number}},
        {"$sort":  {"received_at": -1}},
        {"$group": {"_id": "$salla_order_number",
                    "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    async for row in db.integration_inbox.aggregate(pipeline):
        return row
    return None


def _serialize(row: dict) -> dict:
    """Drop Mongo `_id` (BSON ObjectId) from a returned document."""
    row = {k: v for k, v in row.items() if k != "_id"}
    return row


async def _build_dry_run_report(db, order_number: str) -> dict:
    row = await _load_canon(db, order_number)
    if not row:
        return {"ok": False,
                "reason": "order_not_found_in_integration_inbox",
                "order_number": order_number}
    canon = row.get("canonical_payload") or {}
    settings = await db.qoyod_settings.find_one({}, {"_id": 0}) or {}

    salla_total     = _q2(canon.get("total_amount"))
    shipping_amount = _q2(canon.get("shipping_amount"))
    cod_fee         = _q2(canon.get("cod_fee_amount"))
    tax_percent     = _q2(float(settings.get("qoyod_tax_percent") or 15))
    tax_factor      = 1.0 + tax_percent / 100.0

    # Product ids are STUBBED — the real send path resolves them via
    # قيود's SKU-lookup. This endpoint MUST NOT hit قيود.
    line_resolutions = {
        str((it.get("sku") or "").strip()): SENTINEL_PID
        for it in (canon.get("items") or [])
    }

    lines: list[dict] = []
    breakdown: list[dict] = []
    expected_dec = Decimal("0")

    for it in canon.get("items") or []:
        payload, row_b, gross = _compute_item_line(
            it, line_resolutions, tax_factor, tax_percent)
        lines.append(payload)
        breakdown.append(row_b)
        expected_dec += Decimal(str(gross))

    # Shipping — matches _build_invoice_payload rules.
    if shipping_amount > 0:
        ship_pid = _to_int(settings.get("default_shipping_product_id"))
        items_gross_sum = sum(_f(it.get("total"))
                              for it in canon.get("items") or [])
        ship_target_gross = _f(canon.get("total_amount")) - items_gross_sum
        if ship_pid is not None and ship_target_gross > 0:
            ship_target_net = ship_target_gross / tax_factor
            ship_unit_raw = shipping_amount
            ship_discount_raw = ship_unit_raw - ship_target_net
            if ship_discount_raw < 0:
                ship_unit = _q2(ship_target_net); ship_discount = 0.0
            else:
                ship_unit = _q2(ship_unit_raw); ship_discount = _q2(ship_discount_raw)
            ship_gross = _line_gross(
                unit_price=ship_unit, quantity=1,
                discount=ship_discount, tax_percent=tax_percent)
            lines.append({
                "product_id": ship_pid, "description": "شحن (Shipping)",
                "quantity": 1, "unit_price": ship_unit,
                "discount": ship_discount, "discount_type": "amount",
                "tax_percent": tax_percent,
            })
            expected_dec += Decimal(str(ship_gross))
            breakdown.append({
                "kind": "shipping",
                "salla_declared_amount": _q2(shipping_amount),
                "qoyod_unit_price":      ship_unit,
                "qoyod_discount":        ship_discount,
                "line_gross_after_tax":  ship_gross,
            })
        else:
            breakdown.append({
                "kind": "shipping", "included": False,
                "salla_declared_amount": _q2(shipping_amount),
                "reason": ("no default_shipping_product_id in settings"
                           if ship_pid is None
                           else "shipping target gross ≤ 0"),
            })

    # COD — matches _build_invoice_payload rules.
    if cod_fee > 0:
        cod_pid = _to_int(settings.get("default_cod_fee_product_id"))
        if cod_pid is not None:
            cod_net = cod_fee / tax_factor
            cod_discount = _q2(cod_fee - cod_net)
            cod_unit = _q2(cod_fee)
            cod_gross = _line_gross(
                unit_price=cod_unit, quantity=1,
                discount=cod_discount, tax_percent=tax_percent)
            lines.append({
                "product_id": cod_pid,
                "description": "رسوم الدفع عند الاستلام (COD Fee)",
                "quantity": 1, "unit_price": cod_unit,
                "discount": cod_discount, "discount_type": "amount",
                "tax_percent": tax_percent,
            })
            expected_dec += Decimal(str(cod_gross))
            breakdown.append({
                "kind": "cod",
                "salla_declared_amount": _q2(cod_fee),
                "line_gross_after_tax": cod_gross,
            })
        else:
            breakdown.append({
                "kind": "cod", "included": False,
                "salla_declared_amount": _q2(cod_fee),
                "reason": "no default_cod_fee_product_id in settings",
            })

    expected_qoyod_total = float(
        expected_dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    sum_line_gross = float(
        sum(Decimal(str(r.get("line_gross_after_tax") or 0))
            for r in breakdown
            if r.get("line_gross_after_tax") is not None
        ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))

    return {
        "ok": True,
        "order_number": order_number,
        "trace": {
            "trace_id":       row.get("trace_id"),
            "connector_key":  row.get("connector_key"),
            "source":         row.get("source"),
            "pipeline_stage": row.get("pipeline_stage"),
            "received_at":    row.get("received_at"),
            "salla_order_id": row.get("salla_order_id"),
            "user_id":        row.get("user_id"),
        },
        "canonical_summary": {
            "total_amount":    salla_total,
            "subtotal":        canon.get("subtotal"),
            "tax_amount":      canon.get("tax_amount"),
            "shipping_amount": shipping_amount,
            "cod_fee_amount":  cod_fee,
            "currency":        canon.get("currency"),
            "order_status":    canon.get("order_status"),
            "item_count":      len(canon.get("items") or []),
        },
        "raw_items": [
            {"sku": it.get("sku"), "name": it.get("name"),
             "quantity": it.get("quantity"),
             "unit_price": it.get("unit_price"),
             "total": it.get("total"),
             "tax_amount": it.get("tax_amount"),
             "discount_amount": it.get("discount_amount")}
            for it in canon.get("items") or []
        ],
        "settings_relevant": {
            "qoyod_tax_percent":              tax_percent,
            "default_shipping_product_id":    settings.get("default_shipping_product_id"),
            "default_cod_fee_product_id":     settings.get("default_cod_fee_product_id"),
            "rounding_adjustment_product_id": settings.get("rounding_adjustment_product_id"),
        },
        "breakdown_per_line": breakdown,
        "qoyod_lines_payload": lines,
        "totals": {
            "sum_line_gross_after_tax": sum_line_gross,
            "expected_qoyod_total":     expected_qoyod_total,
            "salla_total_amount":       salla_total,
            "delta_sum_vs_salla":       _q2(sum_line_gross - salla_total),
            "delta_expected_vs_salla":  _q2(expected_qoyod_total - salla_total),
            "delta_sum_vs_expected":    _q2(sum_line_gross - expected_qoyod_total),
        },
        "notes": [
            "Product ids in `qoyod_lines_payload` are STUB (9999999). "
            "The real send path resolves them via قيود's SKU lookup.",
            "This report is read-only. No Qoyod HTTP was invoked. "
            "No Mongo write happened.",
            "The residual-distribution pass and the "
            "`rounding_adjustment_product_id` line are DISABLED here "
            "so the raw arithmetic is visible.",
        ],
    }


def make_admin_diagnostics_router(db) -> APIRouter:
    """Wire the temporary diagnostic route. Kept in a factory so the
    tests can pass an in-memory Mongo without importing the global
    FastAPI app."""
    router = APIRouter(prefix="/admin", tags=["admin-diagnostics-TEMP"])

    @router.post("/dry-run-qoyod-payload")
    async def _dry_run(req: Request) -> dict:
        # Guard 1 — env token must be configured.
        expected = _diagnostic_token()
        if not expected:
            raise _not_found()

        try:
            body = await req.json()
        except Exception:
            raise _not_found()

        supplied = str((body or {}).get("token") or "")
        order   = str((body or {}).get("order_number") or "").strip()

        # Guard 2 — token match (constant-time).
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise _not_found()

        # Guard 3 — only the pre-authorised order number.
        if order not in ALLOWED_ORDER_NUMBERS:
            raise _not_found()

        return await _build_dry_run_report(db, order)

    return router
