"""Empirical audit — Plan B invoices: Salla vs Qoyod actual totals.

Purpose (2026-02, user directive):
    Answer the decisive question — does Qoyod produce a total that
    matches Salla's document-level total, or does it echo our own
    line-level `expected_qoyod_total`?

The report joins every Plan-B invoice with its originating
`integration_inbox` trace, computes:
    salla_total          — canonical_payload.total_amount from inbox
    qoyod_actual_total   — the total قيود stored on the invoice row
    diff                 = salla_total − qoyod_actual_total
    has_adjustment_line  — invoice notes/description mention the
                           "تسوية فرق التقريب مع سلة" product line
And prints them in a compact table plus three aggregate views:
    • overall  count / matches / non-matches / |diff| distribution
    • per-adjustment-flag  matches breakdown
    • the 15 most-recent invoices with full detail

STRICT read-only:
    • Only `find()` / `aggregate()` — no insert / update / delete.
    • No Qoyod HTTP calls. No Salla HTTP calls. No Make calls.
    • Local Mongo only, via `MONGO_URL` from /app/backend/.env.

Usage:
    cd /app/backend && python scripts/audit_plan_b_invoices.py
    cd /app/backend && python scripts/audit_plan_b_invoices.py --limit 100
    cd /app/backend && python scripts/audit_plan_b_invoices.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


TWO = Decimal("0.01")


def _q2(x) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, dict):
        x = x.get("amount", 0)
    try:
        return Decimal(str(x)).quantize(TWO, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _detect_adjustment_line(inv: dict) -> bool:
    """Return True if the invoice was built with our "تسوية فرق
    التقريب مع سلة" rounding product-line. We check `notes`,
    `description`, and any `lines`/`line_items` snapshot we may
    have persisted."""
    hay: list[str] = []
    for k in ("notes", "description"):
        v = inv.get(k)
        if isinstance(v, str):
            hay.append(v)
    for line_key in ("lines", "line_items", "items", "qoyod_payload"):
        v = inv.get(line_key)
        if isinstance(v, list):
            for line in v:
                if isinstance(line, dict):
                    for k in ("description", "name", "product_name"):
                        val = line.get(k)
                        if isinstance(val, str):
                            hay.append(val)
        elif isinstance(v, dict):
            # `qoyod_payload` may be a wrapper dict; look inside.
            for line in (v.get("line_items") or v.get("lines") or []):
                if isinstance(line, dict):
                    for k in ("description", "name"):
                        val = line.get(k)
                        if isinstance(val, str):
                            hay.append(val)
    blob = " | ".join(hay)
    return ("تسوية فرق التقريب" in blob) or ("rounding" in blob.lower())


async def _find_inbox_for_invoice(
    db, inv: dict,
) -> tuple[dict | None, str | None]:
    """Locate the inbox row Plan-B stamped with this invoice id.
    Return (row, salla_order_number_hint)."""
    qoyod_id = inv.get("qoyod_invoice_id")
    ref = inv.get("reference") or inv.get("salla_order_number")
    row = None
    if qoyod_id:
        row = await db.integration_inbox.find_one(
            {"manual_qoyod_invoice_id": qoyod_id},
            {"_id": 0, "trace_id": 1, "canonical_payload": 1,
             "salla_order_number": 1, "connector_key": 1,
             "received_at": 1},
        ) or await db.integration_inbox.find_one(
            {"qoyod_invoice_id": qoyod_id},
            {"_id": 0, "trace_id": 1, "canonical_payload": 1,
             "salla_order_number": 1, "connector_key": 1,
             "received_at": 1},
        )
    if row is None and ref:
        # Fall back to matching by salla_order_number — newest trace.
        pipeline = [
            {"$match": {"salla_order_number": str(ref)}},
            {"$sort":  {"received_at": -1}},
            {"$limit": 1},
        ]
        async for r in db.integration_inbox.aggregate(pipeline):
            row = r
            break
    return row, ref


async def _load_invoices(db, *, limit: int) -> list[dict]:
    """Pull the most-recent Plan-B invoices. We accept any of the
    `source` markers Plan-B has used across iterations."""
    q = {
        "$or": [
            {"source":    {"$in": ["plan_b_send", "manual_send",
                                    "qoyod_manual", "plan_b"]}},
            {"created_by": {"$regex": "plan[_ ]?b", "$options": "i"}},
            {"pipeline":   {"$in": ["plan_b_send", "manual_send"]}},
        ]
    }
    rows = await db.qoyod_invoices.find(
        q, {"_id": 0}
    ).sort([("created_at", -1)]).to_list(limit)
    # Fallback — if there is no `source`-tagged corpus (older
    # deployments), take everything sorted by newest.
    if not rows:
        rows = await db.qoyod_invoices.find(
            {}, {"_id": 0}
        ).sort([("created_at", -1)]).to_list(limit)
    return rows


async def _main(limit: int, want_json: bool) -> int:
    load_dotenv("/app/backend/.env")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    invs = await _load_invoices(db, limit=limit)
    if not invs:
        print("(no qoyod_invoices found — nothing to audit)")
        return 0

    detailed: list[dict] = []
    for inv in invs:
        row, ref = await _find_inbox_for_invoice(db, inv)
        canon = (row or {}).get("canonical_payload") or {}
        salla_total = _q2(canon.get("total_amount"))
        qoyod_total = _q2(inv.get("total"))
        diff = (salla_total - qoyod_total).quantize(TWO)
        detailed.append({
            "order_number":       str(ref or inv.get("reference")
                                       or inv.get("salla_order_number") or "—"),
            "invoice_number":     inv.get("invoice_number"),
            "qoyod_invoice_id":   inv.get("qoyod_invoice_id"),
            "trace_id":           (row or {}).get("trace_id"),
            "salla_total":        float(salla_total),
            "qoyod_actual_total": float(qoyod_total),
            "diff":               float(diff),
            "abs_diff_hallalat":  int((abs(diff) * 100).to_integral_value()),
            "has_adjustment":     _detect_adjustment_line(inv),
            "status":             inv.get("status"),
            "created_at":         (str(inv.get("created_at") or ""))[:19],
            "connector_key":      (row or {}).get("connector_key"),
        })

    # ── Aggregates ───────────────────────────────────────────────
    total     = len(detailed)
    matches   = sum(1 for r in detailed if r["diff"] == 0)
    diff_dist = Counter(r["abs_diff_hallalat"] for r in detailed)
    with_adj  = [r for r in detailed if r["has_adjustment"]]
    no_adj    = [r for r in detailed if not r["has_adjustment"]]

    def _group_stats(rows: list[dict]) -> dict:
        m = sum(1 for r in rows if r["diff"] == 0)
        return {"count": len(rows), "matches": m,
                "match_rate": (round(m / len(rows) * 100, 1)
                                if rows else None),
                "max_abs_diff_sar":  (max((abs(r["diff"]) for r in rows),
                                          default=0)),
                "avg_abs_diff_sar":  (round(sum(abs(r["diff"]) for r in rows)
                                            / len(rows), 4)
                                       if rows else None)}

    summary = {
        "total_invoices_audited": total,
        "salla_equals_qoyod":     matches,
        "salla_differs_from_qoyod": total - matches,
        "match_rate_pct":         (round(matches / total * 100, 1)
                                    if total else None),
        "|diff| histogram (hallalat, i.e. cents)": {
            f"{k} hallalat": v for k, v in
            sorted(diff_dist.items())
        },
        "with_adjustment_line":    _group_stats(with_adj),
        "without_adjustment_line": _group_stats(no_adj),
    }

    if want_json:
        print(json.dumps({"summary": summary, "rows": detailed},
                         indent=2, ensure_ascii=False, default=str))
        return 0

    # ── Pretty print (Arabic + English mixed, ASCII table) ──────
    print("═" * 96)
    print("═══ PLAN-B INVOICES · Empirical Audit (Salla vs Qoyod actual)")
    print("═" * 96)
    print(f"\nSummary:")
    for k, v in summary.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk}: {vv}")
        else:
            print(f"  {k}: {v}")

    print(f"\n── Detailed rows (most-recent {min(len(detailed), 15)}) ──")
    hdr = (f"{'order#':<12} {'salla':>10} {'qoyod':>10} {'diff':>8} "
           f"{'adj?':>5} {'inv#':<10} {'trace_id':<20} {'created_at':<19}")
    print(hdr)
    print("-" * len(hdr))
    for r in detailed[:15]:
        adj = "yes" if r["has_adjustment"] else "no"
        print(
            f"{r['order_number']:<12} "
            f"{r['salla_total']:>10.2f} "
            f"{r['qoyod_actual_total']:>10.2f} "
            f"{r['diff']:>+8.2f} "
            f"{adj:>5} "
            f"{str(r['invoice_number'] or '—')[:10]:<10} "
            f"{str(r['trace_id'] or '—')[:20]:<20} "
            f"{r['created_at']:<19}")

    # ── Decisive conclusion ─────────────────────────────────────
    print("\n── Conclusion ──")
    if total == 0:
        print("  No invoices to conclude on.")
    elif matches == total:
        print("  ✅ Qoyod ACTUAL total == Salla total for ALL rows.")
        print("     → قيود يحسب on document-level. `expected_qoyod_total`")
        print("       عندنا هو مصدر التنبؤ الخاطئ. الحل الأنظف: إزالة")
        print("       منطق منتج التسوية بالكامل (لا LRM ولا discount).")
    elif matches == 0:
        print("  ⚠️  قيود ACTUAL total يختلف عن Salla في كل صف.")
        print("     → قيود يحسب line-level (يطابق حسبتنا).")
        print("       الحل: تفعيل LRM (dead code موجود) أو")
        print("       `discount_on_document_total`.")
    else:
        print(f"  ⚠️  {matches}/{total} matched. تحقّق من `has_adjustment`:")
        print(f"     • مع adjustment line: {_group_stats(with_adj)}")
        print(f"     • بدون adjustment line: {_group_stats(no_adj)}")
        print("     → إذا الصفوف بدون adjustment مطابقة تماماً، فهذا يعني")
        print("       أن قيود يعمل document-level، ومنتج التسوية القديم")
        print("       يعمل ضدّ ذلك.")

    print("\n(read-only: no writes, no Qoyod calls, no Salla calls)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100,
                    help="Max invoices to audit (default 100).")
    ap.add_argument("--json", action="store_true",
                    help="Print full JSON instead of the table.")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.limit, args.json)))
