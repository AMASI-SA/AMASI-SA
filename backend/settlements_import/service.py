"""Settlements importer service — applies parsed entries to unified_orders.

Hard constraints (per merchant brief — Phase 80):
    • NEVER auto-create payment_adjustments rows.
    • NEVER auto-create bank transfers.
    • NEVER move money from expected → current_balance.
    • Only writes:
        - unified_orders.actual_* fields
        - settlement_files (audit row, sha256-deduped)
        - settlement_entries (per-row trace for the analytics screen)
    • Estimated rates remain the source of truth for orders NOT matched.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import openpyxl

from .registry import detect_provider, parse


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_order_number(value: Any) -> str:
    """unified_orders.order_number is stored as str. Salla / Tamara /
    Tabby all emit numeric strings — but sometimes openpyxl reads them
    as ints/floats. Always coerce to str + strip trailing `.0`."""
    if value is None:
        return ""
    s = str(value).strip()
    s = re.sub(r"\.0+$", "", s)
    return s


# ── 1. Parse + save audit row ─────────────────────────────────────────
async def import_file(
    db,
    user_id: str,
    *,
    filename: str,
    content: bytes,
    provider_hint: str | None = None,
) -> dict:
    """Top-level entrypoint.

    Workflow:
        1. Compute sha256 → check dedup (per user). If file already
           imported, short-circuit with cached summary.
        2. Open workbook, detect provider (or use hint), run parser.
        3. Persist `settlement_files` audit row + per-row entries.
        4. Apply actual_* updates to unified_orders for matched rows.
        5. Return summary (matched, unmatched, totals).
    """
    file_hash = hashlib.sha256(content).hexdigest()

    existing = await db.settlement_files.find_one({"user_id": user_id, "file_hash": file_hash})
    if existing:
        return {
            "status": "duplicate",
            "message": "هذا الملف تم رفعه مسبقاً — تم تخطّيه.",
            "file_id": existing.get("id"),
            "provider": existing.get("provider"),
            "matched": existing.get("matched", 0),
            "unmatched": existing.get("unmatched", 0),
            "totals": existing.get("totals", {}),
            "uploaded_at": (
                existing["uploaded_at"].isoformat()
                if hasattr(existing.get("uploaded_at"), "isoformat") else None
            ),
        }

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(content),
            data_only=True,
            read_only=True,
            keep_links=False,
        )
    except Exception as e:
        raise ValueError(f"تعذّر فتح الملف كـ Excel: {e}") from e

    try:
        provider = provider_hint or detect_provider(wb)
        parsed = parse(provider, wb)
    finally:
        wb.close()

    file_id = str(uuid.uuid4())
    entries = parsed["entries"]
    totals = parsed["totals"]
    header = parsed["header"]

    # 2. Apply to unified_orders (one DB roundtrip per unique order_number)
    match_result = await _apply_entries(db, user_id, provider, entries, file_id=file_id)

    # 3. Audit row
    audit_doc = {
        "id": file_id,
        "user_id": user_id,
        "provider": provider,
        "filename": filename,
        "file_hash": file_hash,
        "file_size": len(content),
        "uploaded_at": _now(),
        "header": header,
        "totals": totals,
        "rows": len(entries),
        "matched": match_result["matched"],
        "unmatched": match_result["unmatched"],
        "unmatched_orders": match_result["unmatched_orders"][:200],  # cap for storage
        # Iter-147 v2 — surface the count of payment_transactions that
        # got the official Tamara attribution (independent of unified_orders).
        "attribution_applied": match_result.get("attribution_applied", 0),
    }
    await db.settlement_files.insert_one(audit_doc)

    # 4. Per-entry trace (for analytics + drill-down)
    entry_docs = []
    for e in entries:
        entry_docs.append({
            "id": str(uuid.uuid4()),
            "file_id": file_id,
            "user_id": user_id,
            "provider": provider,
            **e,
            "matched": e["order_number"] in match_result["matched_set"],
            "created_at": _now(),
        })
    if entry_docs:
        await db.settlement_entries.insert_many(entry_docs)

    return {
        "status": "imported",
        "file_id": file_id,
        "provider": provider,
        "filename": filename,
        "rows": len(entries),
        "matched": match_result["matched"],
        "unmatched": match_result["unmatched"],
        "unmatched_orders": match_result["unmatched_orders"][:50],
        "totals": totals,
        "header": header,
        # Iter-147 v2.
        "attribution_applied": match_result.get("attribution_applied", 0),
    }


# ── 2. Apply parsed entries to unified_orders ─────────────────────────
async def _apply_entries(
    db,
    user_id: str,
    provider: str,
    entries: list[dict],
    *,
    file_id: str,
) -> dict:
    """Group entries by order_number, aggregate per order (sale + refunds),
    then update unified_orders with the consolidated `actual_*` fields."""
    by_order: dict[str, list[dict]] = {}
    salla_purchase_orders: list[str] = []
    for e in entries:
        # Wallet-recharge / Salla purchase rows are aggregated at the
        # file level only — never written to unified_orders.actual_*
        # because they're internal Salla balance deductions and don't
        # represent the real net of the order they reference.
        if e.get("event_type") == "salla_purchase":
            salla_purchase_orders.append(_normalize_order_number(e.get("order_number")))
            continue
        # Provider-level payout fees have no order to update. They remain in
        # settlement_entries / settlement_files for official reconciliation.
        if e.get("event_type") == "settlement_fee":
            continue
        key = _normalize_order_number(e.get("order_number"))
        if not key:
            continue
        by_order.setdefault(key, []).append(e)

    matched = 0
    unmatched: list[str] = []
    matched_set: set[str] = set()
    # Iter-147 v2 — counts orders whose payment_transactions got the
    # official Tamara attribution applied (may exceed unified_orders
    # matches when the merchant has Tamara orders missing from
    # unified_orders).  Boxed in a list so the inner scope can mutate it.
    attribution_count = [0]

    # Fetch all existing orders that match in one shot for efficiency
    existing_cursor = db.unified_orders.find(
        {"user_id": user_id, "order_number": {"$in": list(by_order.keys())}},
        {"order_number": 1, "_id": 0},
    )
    existing_orders = {doc["order_number"] async for doc in existing_cursor}

    for order_no, rows in by_order.items():
        consolidated = _consolidate_rows(rows)
        is_in_unified = order_no in existing_orders

        if is_in_unified:
            matched += 1
            matched_set.add(order_no)
            await db.unified_orders.update_one(
                {"user_id": user_id, "order_number": order_no},
                {
                    "$set": {
                        **{f"actual_{k}" if not k.startswith("actual_") else k: v
                           for k, v in consolidated["actual_fields"].items()},
                        "settlement_source": provider,
                        "settlement_date": consolidated["settlement_date"],
                        "settlement_reference": consolidated["settlement_reference"],
                        "payment_fee_status": "actual",
                        "last_settlement_file_id": file_id,
                        "last_settlement_applied_at": _now(),
                    },
                },
            )
        else:
            unmatched.append(order_no)

        # Iter-147 v2 — Tamara only: propagate the OFFICIAL attribution
        # to every Tamara `payment_transactions` row that matches this
        # order, INDEPENDENT of whether `unified_orders` has it.  The
        # settlement file is the source of truth for which weekly
        # invoice this order belongs to — even orders missing from
        # unified_orders can still have their settlement attribution
        # corrected on the payment side.
        if provider == "tamara":
            try:
                from bnpl.settlement_attribution import (
                    set_provider_official_attribution,
                )
                ref = ""
                latest_date: str | None = None
                for r in rows:
                    if r.get("settlement_reference") and not ref:
                        ref = str(r["settlement_reference"])
                    d = r.get("settlement_date")
                    if d and (latest_date is None or str(d) > str(latest_date)):
                        latest_date = str(d)
                if ref or latest_date:
                    tamara_order_id = next(
                        (r.get("tamara_order_id") for r in rows
                         if r.get("tamara_order_id")), None,
                    )
                    res_attr = await set_provider_official_attribution(
                        db, user_id,
                        order_number=order_no,
                        provider_id=tamara_order_id,
                        provider_settlement_id=ref or None,
                        provider_invoice_id=ref or None,
                        provider_settlement_date=latest_date,
                    )
                    # Count attribution successes separately so the UI
                    # can show "X orders attributed to provider_official".
                    if res_attr.get("matched", 0) > 0:
                        attribution_count[0] += 1
            except Exception:
                # Never let attribution bookkeeping break file import.
                pass

    return {
        "matched": matched,
        "unmatched": len(unmatched),
        "unmatched_orders": unmatched,
        "matched_set": matched_set,
        # Iter-147 v2 — Tamara settlement-file imports also report the
        # number of orders whose payment_transactions got the official
        # attribution (independent of unified_orders matching).
        "attribution_applied": attribution_count[0] if provider == "tamara" else 0,
    }


def _consolidate_rows(rows: list[dict]) -> dict:
    """When the same order_number has multiple settlement rows (e.g.,
    Tamara: capture + later refund), collapse into one set of actual_*
    fields representing the merchant's net position on this order.

    Convention:
        actual_gross_amount = max(positive gross seen)  ← original order
        actual_payment_fee  = sum(sale fees + signed refund rebates)
        actual_payment_vat  = sum(sale VAT + signed refund VAT rebates)
        actual_net_amount   = sum(all net amounts)      ← captured - refund
        actual_refund_amount        = sum(refund_full)
        actual_partial_refund_amount= sum(refund_partial)
        actual_canceled_amount      = sum(canceled events)
        actual_fee_rate     = weighted avg (against gross)
        settlement_date     = latest event date
    """
    gross = 0.0
    fees = 0.0
    vat = 0.0
    net = 0.0
    refund_full = 0.0
    refund_partial = 0.0
    canceled_amount = 0.0
    fee_rate_acc = 0.0
    fee_rate_weight = 0.0
    payment_method = ""
    settlement_date = None
    settlement_reference = ""

    for r in rows:
        gross = max(gross, float(r.get("actual_gross_amount") or 0))
        fees += float(r.get("actual_payment_fee") or 0)
        vat += float(r.get("actual_payment_vat") or 0)
        net += float(r.get("actual_net_amount") or 0)
        refund_full += float(r.get("actual_refund_amount") or 0)
        refund_partial += float(r.get("actual_partial_refund_amount") or 0)
        canceled_amount += float(r.get("actual_canceled_amount") or 0)
        rate = float(r.get("actual_fee_rate") or 0)
        if rate > 0:
            w = float(r.get("actual_gross_amount") or 0) or 1.0
            fee_rate_acc += rate * w
            fee_rate_weight += w
        payment_method = payment_method or r.get("actual_payment_method") or ""
        ev_date = r.get("settlement_date")
        if ev_date and (settlement_date is None or str(ev_date) > str(settlement_date)):
            settlement_date = ev_date
        settlement_reference = settlement_reference or r.get("settlement_reference") or ""

    fee_rate = round(fee_rate_acc / fee_rate_weight, 4) if fee_rate_weight > 0 else 0.0

    return {
        "actual_fields": {
            "actual_payment_method": payment_method,
            "actual_gross_amount": round(gross, 2),
            "actual_payment_fee": round(fees, 2),
            "actual_payment_vat": round(vat, 2),
            "actual_net_amount": round(net, 2),
            "actual_refund_amount": round(refund_full, 2),
            "actual_partial_refund_amount": round(refund_partial, 2),
            "actual_canceled_amount": round(canceled_amount, 2),
            "actual_fee_rate": fee_rate,
        },
        "settlement_date": settlement_date,
        "settlement_reference": settlement_reference,
    }


# ── 3. List uploaded files ────────────────────────────────────────────
async def list_files(db, user_id: str, *, limit: int = 50) -> list[dict]:
    cursor = db.settlement_files.find(
        {"user_id": user_id},
        {"_id": 0, "unmatched_orders": 0},
    ).sort("uploaded_at", -1).limit(limit)
    out = []
    async for doc in cursor:
        if hasattr(doc.get("uploaded_at"), "isoformat"):
            doc["uploaded_at"] = doc["uploaded_at"].isoformat()
        out.append(doc)
    return out


async def get_file_detail(db, user_id: str, file_id: str) -> dict | None:
    doc = await db.settlement_files.find_one(
        {"user_id": user_id, "id": file_id}, {"_id": 0}
    )
    if not doc:
        return None
    if hasattr(doc.get("uploaded_at"), "isoformat"):
        doc["uploaded_at"] = doc["uploaded_at"].isoformat()
    return doc


async def delete_file(db, user_id: str, file_id: str) -> dict:
    """Remove the audit row + entries AND roll back actual_* fields on
    any order that used this file as its last settlement source. Other
    settlement files that touched the same order are NOT re-applied
    automatically — caller can re-upload."""
    doc = await db.settlement_files.find_one({"user_id": user_id, "id": file_id})
    if not doc:
        return {"removed": 0}

    # Roll back orders
    rollback = await db.unified_orders.update_many(
        {"user_id": user_id, "last_settlement_file_id": file_id},
        {"$set": {
            "payment_fee_status": "estimated",
        }, "$unset": {
            "actual_payment_method": "",
            "actual_gross_amount": "",
            "actual_payment_fee": "",
            "actual_payment_vat": "",
            "actual_net_amount": "",
            "actual_refund_amount": "",
            "actual_partial_refund_amount": "",
            "actual_canceled_amount": "",
            "actual_fee_rate": "",
            "settlement_source": "",
            "settlement_date": "",
            "settlement_reference": "",
            "last_settlement_file_id": "",
            "last_settlement_applied_at": "",
        }},
    )

    await db.settlement_entries.delete_many({"user_id": user_id, "file_id": file_id})
    await db.settlement_files.delete_one({"user_id": user_id, "id": file_id})

    return {"removed": 1, "orders_rolled_back": rollback.modified_count}


# ── 4. Analytics — coverage of estimated vs actual ────────────────────
async def coverage_analytics(db, user_id: str) -> dict:
    """Aggregate over unified_orders: how many orders have actual_* data,
    and the gap between estimated and actual fees/net for matched
    orders. Used by the analysis screen."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$facet": {
            "by_status": [
                {"$group": {
                    "_id": {"$ifNull": ["$payment_fee_status", "estimated"]},
                    "count": {"$sum": 1},
                    "gross_actual": {"$sum": {"$ifNull": ["$actual_gross_amount", 0]}},
                    "fees_actual": {"$sum": {"$ifNull": ["$actual_payment_fee", 0]}},
                    "vat_actual": {"$sum": {"$ifNull": ["$actual_payment_vat", 0]}},
                    "net_actual": {"$sum": {"$ifNull": ["$actual_net_amount", 0]}},
                    "refund_full": {"$sum": {"$ifNull": ["$actual_refund_amount", 0]}},
                    "refund_partial": {"$sum": {"$ifNull": ["$actual_partial_refund_amount", 0]}},
                }},
            ],
            "by_provider": [
                {"$match": {"settlement_source": {"$exists": True, "$ne": ""}}},
                {"$group": {
                    "_id": "$settlement_source",
                    "count": {"$sum": 1},
                    "fees_actual": {"$sum": {"$ifNull": ["$actual_payment_fee", 0]}},
                    "net_actual": {"$sum": {"$ifNull": ["$actual_net_amount", 0]}},
                }},
            ],
        }},
    ]
    res = [r async for r in db.unified_orders.aggregate(pipeline)]
    facets = res[0] if res else {"by_status": [], "by_provider": []}

    by_status_map: dict = {}
    for row in facets.get("by_status", []):
        by_status_map[row["_id"] or "estimated"] = row

    actual = by_status_map.get("actual", {"count": 0})
    estimated = by_status_map.get("estimated", {"count": 0})

    return {
        "totals": {
            "orders_total": (actual.get("count", 0)) + (estimated.get("count", 0)),
            "orders_actual": actual.get("count", 0),
            "orders_estimated": estimated.get("count", 0),
            "coverage_pct": round(
                (actual.get("count", 0) /
                 max(1, (actual.get("count", 0) + estimated.get("count", 0))))
                * 100, 2,
            ),
        },
        "actual_aggregates": {
            "gross": round(actual.get("gross_actual", 0) or 0, 2),
            "fees": round(actual.get("fees_actual", 0) or 0, 2),
            "vat": round(actual.get("vat_actual", 0) or 0, 2),
            "net": round(actual.get("net_actual", 0) or 0, 2),
            "refund_full": round(actual.get("refund_full", 0) or 0, 2),
            "refund_partial": round(actual.get("refund_partial", 0) or 0, 2),
        },
        "by_provider": [
            {
                "provider": row["_id"],
                "orders": row["count"],
                "fees": round(row["fees_actual"] or 0, 2),
                "net": round(row["net_actual"] or 0, 2),
            }
            for row in facets.get("by_provider", [])
        ],
    }


async def ensure_settlements_indexes(db) -> None:
    await db.settlement_files.create_index(
        [("user_id", 1), ("file_hash", 1)], unique=True,
    )
    await db.settlement_files.create_index([("user_id", 1), ("uploaded_at", -1)])
    await db.settlement_entries.create_index([("user_id", 1), ("file_id", 1)])
    await db.settlement_entries.create_index([("user_id", 1), ("order_number", 1)])
    await db.unified_orders.create_index([("user_id", 1), ("payment_fee_status", 1)])
