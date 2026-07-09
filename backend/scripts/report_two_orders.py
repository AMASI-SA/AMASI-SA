"""Read-only diagnostic for two orders in production.

Usage (from production shell, inside /app/backend):
    python scripts/report_two_orders.py

The script ONLY reads collections. It does not create, update, delete,
or reprocess anything. It does not hit Qoyod, Salla, or Make. No
network calls beyond the local Mongo connection.

For each order it prints:
    • qoyod_invoices row(s)          → invoice id, number, status, created_at, reference, salla_order_number
    • integration_inbox row(s)       → connector_key, source, pipeline_stage, received_at,
                                       manual_qoyod_invoice_id, qoyod_invoice_id, trace_id,
                                       stage_history summary
    • manual_send_audit / send_audit → any send attempts (actor, source_of_call, result, created_at)
    • send_locks                     → any lock still held (indicates in-flight or crashed send)
    • Cross-analysis                 → source of sending, attempt count, invoice duplicates,
                                       why the order shows as `already_sent` in the diagnostic
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


ORDERS = ["270572499", "270988155"]


def _fmt(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _dump(o, indent: int = 4) -> str:
    return json.dumps(o, ensure_ascii=False, indent=indent, default=_fmt)


async def _report(db, n: str) -> None:
    print("\n" + "═" * 72)
    print(f"═══ ORDER {n}")
    print("═" * 72)

    # ── qoyod_invoices ──────────────────────────────────────────────
    invs = await db.qoyod_invoices.find(
        {"$or": [{"salla_order_number": n}, {"reference": n}]},
        {"_id": 0}
    ).to_list(50)
    print(f"\n▸ qoyod_invoices  →  {len(invs)} row(s)")
    for inv in invs:
        print(_dump({
            "qoyod_invoice_id":   inv.get("qoyod_invoice_id"),
            "invoice_number":     inv.get("invoice_number"),
            "reference":          inv.get("reference"),
            "salla_order_number": inv.get("salla_order_number"),
            "status":             inv.get("status"),
            "total":              inv.get("total"),
            "created_at":         inv.get("created_at"),
            "created_by":         inv.get("created_by"),
            "user_email":         inv.get("user_email"),
            "notes_head":         (inv.get("notes") or "")[:200],
        }))

    # ── integration_inbox ──────────────────────────────────────────
    rows = await db.integration_inbox.find(
        {"salla_order_number": n},
        {"_id": 0}
    ).sort([("received_at", 1)]).to_list(50)
    print(f"\n▸ integration_inbox  →  {len(rows)} row(s)")
    for r in rows:
        hist = r.get("stage_history") or []
        canon = r.get("canonical_payload") or {}
        print(_dump({
            "id":                       r.get("id"),
            "trace_id":                 r.get("trace_id"),
            "connector_key":            r.get("connector_key"),
            "source":                   r.get("source"),
            "pipeline_stage":           r.get("pipeline_stage"),
            "received_at":              r.get("received_at"),
            "idempotency_key":          r.get("idempotency_key"),
            "manual_qoyod_invoice_id":  r.get("manual_qoyod_invoice_id"),
            "qoyod_invoice_id":         r.get("qoyod_invoice_id"),
            "canonical.total_amount":   canon.get("total_amount"),
            "canonical.order_status":   canon.get("order_status"),
            "user_id":                  r.get("user_id"),
            "stage_history_last":       hist[-1] if hist else None,
            "stage_history_count":      len(hist),
        }))

    # ── manual_send_audit + send_audit ─────────────────────────────
    audits: list[dict] = []
    for coll_name in ("manual_send_audit", "send_audit",
                      "qoyod_send_audit", "audit_events"):
        try:
            cursor = db[coll_name].find(
                {"$or": [{"salla_order_number": n},
                         {"order_number": n},
                         {"reference": n}]},
                {"_id": 0}
            ).sort([("created_at", 1)])
            found = await cursor.to_list(50)
            for a in found:
                a["_collection"] = coll_name
                audits.append(a)
        except Exception:
            pass
    print(f"\n▸ audit rows (all collections)  →  {len(audits)} row(s)")
    for a in audits:
        print(_dump({
            "_collection":     a.get("_collection"),
            "actor":           a.get("actor"),
            "user_email":      a.get("user_email"),
            "created_by":      a.get("created_by"),
            "source":          a.get("source"),
            "source_of_call":  a.get("source_of_call"),
            "action":          a.get("action"),
            "result":          a.get("result"),
            "outcome":         a.get("outcome"),
            "created_at":      a.get("created_at"),
            "qoyod_invoice_id": (a.get("qoyod_invoice_id")
                                 or a.get("manual_qoyod_invoice_id")),
            "trace_id":        a.get("trace_id"),
            "reason":          a.get("reason"),
        }))

    # ── send_locks (still held? indicates crashed/in-flight send) ──
    try:
        locks = await db.send_locks.find(
            {"$or": [{"order_number": n}, {"salla_order_number": n}]},
            {"_id": 0}
        ).to_list(20)
        print(f"\n▸ send_locks  →  {len(locks)} row(s)")
        for l in locks:
            print(_dump({k: l.get(k) for k in
                         ("order_number", "salla_order_number", "actor",
                          "acquired_at", "expires_at", "released_at",
                          "source_of_call")}))
    except Exception as e:
        print(f"  (send_locks read failed: {e})")

    # ── Cross-analysis ─────────────────────────────────────────────
    print(f"\n▸ Cross-analysis")
    invoice_ids = {i.get("qoyod_invoice_id") for i in invs
                   if i.get("qoyod_invoice_id")}
    inbox_invoice_ids = {r.get("manual_qoyod_invoice_id")
                         for r in rows if r.get("manual_qoyod_invoice_id")}
    inbox_invoice_ids |= {r.get("qoyod_invoice_id")
                          for r in rows if r.get("qoyod_invoice_id")}
    print(f"  invoice_ids in qoyod_invoices:  {invoice_ids or 'None'}")
    print(f"  invoice_ids stamped on inbox :  {inbox_invoice_ids or 'None'}")
    print(f"  duplicate invoices?            "
          f"{'YES' if len(invoice_ids) > 1 else 'no'}")
    print(f"  multiple send attempts?        "
          f"{'YES ('+str(len(audits))+')' if len(audits) > 1 else 'no'}")

    # Send source inference (best-effort):
    sources: list[str] = []
    for a in audits:
        s = (a.get("source_of_call") or a.get("source") or "").lower()
        if s:
            sources.append(f"{a.get('_collection')}:{s}")
    for r in rows:
        if r.get("manual_qoyod_invoice_id"):
            sources.append(f"inbox({r.get('connector_key')}):manual_stamp")
    print(f"  send source(s) inferred        : {sources or ['unknown']}")

    # already_sent explanation
    reasons: list[str] = []
    if invoice_ids:
        reasons.append(
            f"qoyod_invoices row exists with reference={n} → strict "
            f"reference match rule short-circuits Plan-B send.")
    for r in rows:
        if r.get("manual_qoyod_invoice_id") or r.get("qoyod_invoice_id"):
            reasons.append(
                f"inbox row trace_id={r.get('trace_id')} has "
                f"invoice stamp → guard treats it as already sent.")
    print(f"  reason(s) for `already_sent`   :")
    for r in reasons or ["(none — order is NOT already sent according to data)"]:
        print(f"    • {r}")


async def main():
    load_dotenv("/app/backend/.env")
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    for n in ORDERS:
        await _report(db, n)


if __name__ == "__main__":
    asyncio.run(main())
