"""Read-only dry-run of the قيود invoice payload for a single order.

Usage (from production shell):
    cd /app/backend && python scripts/dry_run_qoyod_payload.py 270457540

The script:
  • Loads the order's newest inbox trace from `integration_inbox`
    (dedup rule matches `list_pending_orders` — newest received_at).
  • Loads قيود settings from `qoyod_settings`.
  • Replicates the payload builder in `send.py` (uses the actual
    `_compute_item_line` + `_line_gross` helpers so every 2dp
    quantisation is identical to the real send path).
  • Does NOT hit قيود. Product ids are stubbed with sentinel
    values so `_compute_item_line` accepts them.
  • Does NOT hit Salla. Does NOT insert / update / delete.
  • Prints the exact `lines` list that would ship, the per-line
    breakdown (unit_price, quantity, discount, tax_percent,
    line_net_after_disc, line_tax_15pct, line_gross_after_tax,
    delta_vs_salla_line), and the three totals:
        sum(line_gross_after_tax)
        expected_qoyod_total
        salla_total_amount

Explicitly avoids the residual-distribution and adjustment-line
passes so the raw arithmetic is visible.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/backend")
from integrations.qoyod_manual.send import (  # noqa: E402
    _compute_item_line,
    _line_gross,
    _q2,
    _f,
    _TWO_PLACES,
    _to_int,
    _unwrap_id,
)

SENTINEL_PID = 9999999  # placeholder id — real send resolves via قيود


def _dump(o) -> str:
    return json.dumps(o, ensure_ascii=False, indent=2, default=str)


async def _load_canon(db, order_number: str) -> dict | None:
    """Newest trace canonical, matching what Manual-Send would read."""
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


async def _load_settings(db) -> dict:
    doc = await db.qoyod_settings.find_one({}, {"_id": 0}) or {}
    return doc


async def _main(order_number: str) -> int:
    load_dotenv("/app/backend/.env")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    row = await _load_canon(db, order_number)
    if not row:
        print(f"❌ order {order_number} not found in integration_inbox")
        return 2
    canon = row.get("canonical_payload") or {}
    settings = await _load_settings(db)

    print("═" * 72)
    print(f"═══ DRY-RUN Qoyod payload for order {order_number}")
    print("═" * 72)
    print(f"\ntrace_id       : {row.get('trace_id')}")
    print(f"connector_key  : {row.get('connector_key')}")
    print(f"source         : {row.get('source')}")
    print(f"pipeline_stage : {row.get('pipeline_stage')}")
    print(f"received_at    : {row.get('received_at')}")
    print(f"salla_order_id : {row.get('salla_order_id')}")

    salla_total = _q2(canon.get("total_amount"))
    shipping_amount = _q2(canon.get("shipping_amount"))
    cod_fee = _q2(canon.get("cod_fee_amount"))

    print(f"\n── Salla canonical (from inbox) ──")
    print(f"  total_amount      : {salla_total}")
    print(f"  subtotal          : {canon.get('subtotal')}")
    print(f"  tax_amount        : {canon.get('tax_amount')}")
    print(f"  shipping_amount   : {shipping_amount}")
    print(f"  cod_fee_amount    : {cod_fee}")
    print(f"  currency          : {canon.get('currency')}")
    print(f"  order_status      : {canon.get('order_status')}")
    print(f"  items[]           : {len(canon.get('items') or [])}")

    # Show raw items so unit_price/quantity/discount/tax_percent are visible.
    print(f"\n── Raw items (canonical) ──")
    for i, it in enumerate(canon.get("items") or [], 1):
        print(f"  [{i}] sku={it.get('sku')!r}  qty={it.get('quantity')}  "
              f"unit={it.get('unit_price')}  total={it.get('total')}  "
              f"tax={it.get('tax_amount')}  discount={it.get('discount_amount')}  "
              f"name={it.get('name')!r}")

    tax_percent = _q2(float(settings.get("qoyod_tax_percent") or 15))
    tax_factor = 1.0 + tax_percent / 100.0

    print(f"\n── Qoyod settings (relevant) ──")
    print(f"  qoyod_tax_percent            : {tax_percent}")
    print(f"  default_shipping_product_id  : "
          f"{settings.get('default_shipping_product_id')}")
    print(f"  default_cod_fee_product_id   : "
          f"{settings.get('default_cod_fee_product_id')}")
    print(f"  rounding_adjustment_product_id : "
          f"{settings.get('rounding_adjustment_product_id')}")

    # ── Rebuild per-line payloads WITHOUT residual distribution
    #    and WITHOUT the adjustment line. Stub product_ids so
    #    `_compute_item_line` accepts them.
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

    # Shipping line (only when configured + non-zero).
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
            ship_gross = _line_gross(unit_price=ship_unit, quantity=1,
                                     discount=ship_discount, tax_percent=tax_percent)
            lines.append({"product_id": ship_pid, "description": "شحن (Shipping)",
                          "quantity": 1, "unit_price": ship_unit,
                          "discount": ship_discount, "discount_type": "amount",
                          "tax_percent": tax_percent})
            expected_dec += Decimal(str(ship_gross))
            breakdown.append({"kind": "shipping",
                              "salla_declared_amount": _q2(shipping_amount),
                              "qoyod_unit_price": ship_unit,
                              "qoyod_discount": ship_discount,
                              "line_gross_after_tax": ship_gross})
        else:
            breakdown.append({"kind": "shipping",
                              "included": False,
                              "salla_declared_amount": _q2(shipping_amount),
                              "reason": "no product_id or non-positive target"})

    if cod_fee > 0:
        cod_pid = _to_int(settings.get("default_cod_fee_product_id"))
        if cod_pid is not None:
            cod_net = cod_fee / tax_factor
            cod_discount = _q2(cod_fee - cod_net)
            cod_unit = _q2(cod_fee)
            cod_gross = _line_gross(unit_price=cod_unit, quantity=1,
                                    discount=cod_discount, tax_percent=tax_percent)
            lines.append({"product_id": cod_pid,
                          "description": "رسوم الدفع عند الاستلام (COD Fee)",
                          "quantity": 1, "unit_price": cod_unit,
                          "discount": cod_discount, "discount_type": "amount",
                          "tax_percent": tax_percent})
            expected_dec += Decimal(str(cod_gross))
            breakdown.append({"kind": "cod",
                              "salla_declared_amount": _q2(cod_fee),
                              "line_gross_after_tax": cod_gross})
        else:
            breakdown.append({"kind": "cod",
                              "included": False,
                              "salla_declared_amount": _q2(cod_fee),
                              "reason": "no default_cod_fee_product_id"})

    expected_qoyod_total = float(
        expected_dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    sum_line_gross = float(
        sum(Decimal(str(r.get("line_gross_after_tax") or 0))
            for r in breakdown
            if r.get("line_gross_after_tax") is not None
        ).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))

    # ── Output ─────────────────────────────────────────────────
    print(f"\n── Line-by-line breakdown ──")
    for i, r in enumerate(breakdown, 1):
        print(f"  [{i}] " + _dump(r))

    print(f"\n── Qoyod `lines` payload (as would be sent) ──")
    print(_dump(lines))

    print(f"\n── Totals ──")
    print(f"  sum(line_gross_after_tax)      : {sum_line_gross}")
    print(f"  expected_qoyod_total           : {expected_qoyod_total}")
    print(f"  salla_total_amount             : {salla_total}")

    diff_sum_vs_salla = _q2(sum_line_gross - salla_total)
    diff_exp_vs_salla = _q2(expected_qoyod_total - salla_total)
    diff_sum_vs_exp   = _q2(sum_line_gross - expected_qoyod_total)
    print(f"\n  Δ  sum(line_gross)  − salla    : {diff_sum_vs_salla:+.2f}")
    print(f"  Δ  expected_qoyod   − salla    : {diff_exp_vs_salla:+.2f}")
    print(f"  Δ  sum(line_gross)  − expected : {diff_sum_vs_exp:+.2f}")

    # Root-cause hints (read-only reasoning).
    print(f"\n── Discrepancy explanation ──")
    hints: list[str] = []
    # 1) items rounding — each line is (unit_price − discount) × qty
    #    then × 1.15 quantised to 2dp. Multiple lines compound.
    if any(abs(r.get("delta_vs_salla_line") or 0) > 0 for r in breakdown
           if r.get("delta_vs_salla_line") is not None):
        deltas = [(r.get("sku"), r.get("delta_vs_salla_line"))
                  for r in breakdown
                  if r.get("delta_vs_salla_line") is not None
                  and abs(r.get("delta_vs_salla_line") or 0) > 0]
        hints.append(f"per-line rounding drift vs Salla (2dp × 1.15): {deltas}")
    if diff_sum_vs_exp != 0:
        hints.append(
            "sum(line_gross) ≠ expected_qoyod_total → the builder "
            "adds each `Decimal(str(gross))` (14 dp) into "
            "`expected_dec`, then quantises to 2dp ONCE. Summing "
            "already-2dp `line_gross_after_tax` values instead "
            "aggregates the truncated cents differently — this is "
            "the 0.04 SAR gap the user observed.")
    if any(r.get("kind") == "shipping" and not r.get("included")
           for r in breakdown):
        hints.append("shipping present in Salla but ignored by قيود "
                     "(no default_shipping_product_id) — real "
                     "config gap.")
    if any(r.get("kind") == "cod" and not r.get("included")
           for r in breakdown):
        hints.append("cod_fee present in Salla but ignored (no "
                     "default_cod_fee_product_id).")
    for h in hints or ["(no obvious rounding gap detected — check "
                       "raw Salla payload for hidden shipping/COD/"
                       "manual discount lines)"]:
        print(f"  • {h}")

    print(f"\n(read-only: no send, no reprocess, no writes)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("order_number",
                    help="Salla order number, e.g. 270457540")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.order_number)))
