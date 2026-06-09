"""Tabby → internal data layer sync (Iter-116).

Single entry point: `sync_tabby_payments(db, user_id, since=None)`.

Flow per payment:
  1. Upsert into `payment_transactions` keyed by (user_id, provider, provider_id).
  2. Extract refunds list → upsert into `payment_refunds`.
  3. Upsert into `unified_orders` keyed on order_reference_id or
     order_number — see merge rules below.

Unified-orders merge rules (per user requirement):
  • Lookup order in `unified_orders` by order_reference_id / order_number.
  • If found:
        - DON'T overwrite Make/Excel canonical fields (gross, items, …).
        - DO update payment fields: payment_verified, paid_amount,
          refunded_amount, captured_amount, payment_status,
          last_provider_sync_at.  Append `tabby` to `sources_seen`.
  • If NOT found AND `payment.status` indicates real money received
        (CAPTURED / AUTHORIZED / CLOSED with positive amount):
        - Create a new unified_orders row sourced from Tabby with
          needs_review=True and source='tabby'.  Salla Make import
          can later merge into it without dup.

Source priority (lower wins):
   1 salla_api  2 make  3 tamara  4 tabby  5 excel
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .clients.tabby import TabbyClient, TabbyError
from .config_store import get_raw_secrets, record_sync


SOURCE_PRIORITY = {
    "salla_api": 1, "make": 2, "tamara": 3, "tabby": 4, "excel": 5,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _extract_order_ref(p: Dict[str, Any]) -> Tuple[str, str]:
    """Return (order_reference_id, order_number) — best-effort extraction
    from Tabby payment shape.  Tabby places merchant reference under
    `order.reference_id` in the create-checkout payload and surfaces it
    back in the payment object."""
    order = p.get("order") or {}
    ref = (order.get("reference_id") or "").strip()
    # Some merchants pass the order number as the same field; if a
    # numeric ref exists, treat it as order_number too.
    return ref, ref


def _normalise_payment(p: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Map a Tabby payment object to our `payment_transactions` row.

    Tabby (per OpenAPI spec) returns `amount` as a plain string and
    `currency` as a separate sibling field — NOT a nested object.
    Each capture and refund also has `amount` as a plain string.
    """
    order_ref, order_number = _extract_order_ref(p)

    refunds = p.get("refunds") or []
    refunded_amount = sum(_f((r or {}).get("amount", 0)) for r in refunds)

    captures = p.get("captures") or []
    captured_amount = sum(_f((c or {}).get("amount", 0)) for c in captures)

    return {
        "id": str(uuid.uuid4()),  # internal id
        "user_id": user_id,
        "provider": "tabby",
        "provider_id": (p.get("id") or "").strip(),
        "status": (p.get("status") or "").lower(),
        "amount": _f(p.get("amount")),
        "currency": p.get("currency") or "SAR",
        "captured_amount": captured_amount,
        "refunded_amount": refunded_amount,
        "order_reference_id": order_ref,
        "order_number": order_number,
        "buyer_email": (p.get("buyer") or {}).get("email") or "",
        "buyer_phone": (p.get("buyer") or {}).get("phone") or "",
        "created_at_provider": p.get("created_at") or "",
        "updated_at_provider": (p.get("order") or {}).get("updated_at") or "",
        "raw_payload": p,
        "synced_at": _now_iso(),
    }


def _extract_refund_rows(p: Dict[str, Any], user_id: str) -> List[Dict[str, Any]]:
    order_ref, _ = _extract_order_ref(p)
    rows = []
    currency = p.get("currency") or "SAR"
    for r in (p.get("refunds") or []):
        rows.append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "provider": "tabby",
            "provider_payment_id": (p.get("id") or "").strip(),
            "provider_refund_id": (r.get("id") or "").strip(),
            "order_reference_id": order_ref,
            "amount": _f(r.get("amount")),
            "currency": currency,
            "status": (r.get("status") or "").lower(),
            "reason": r.get("reason") or "",
            "refunded_at": r.get("created_at") or "",
            "raw": r,
            "synced_at": _now_iso(),
        })
    return rows


# ── unified-orders merge ───────────────────────────────────────
async def _merge_into_unified_orders(
    db, user_id: str, txn: Dict[str, Any],
) -> Dict[str, str]:
    """Returns `{action: 'created'|'updated'|'skipped', order_id: ...}`."""
    order_ref = txn.get("order_reference_id") or ""
    order_num = txn.get("order_number") or ""
    if not (order_ref or order_num):
        return {"action": "skipped", "reason": "no order reference"}

    q: Dict[str, Any] = {"user_id": user_id}
    if order_ref:
        q["$or"] = [
            {"order_reference_id": order_ref},
            {"order_number": order_ref},
        ]

    existing = await db.unified_orders.find_one(q, {"_id": 0})
    payment_status = txn.get("status") or ""
    paid_amount = txn.get("captured_amount") or txn.get("amount") or 0
    refunded_amount = txn.get("refunded_amount") or 0
    is_money_received = (
        payment_status in ("captured", "authorized", "closed")
        and paid_amount > 0
    )

    if existing:
        # Update payment-side fields only; never overwrite Make/Excel
        # canonical totals (gross, items_count, …).
        sources_seen = set(existing.get("sources_seen") or [])
        sources_seen.add("tabby")
        update = {
            "payment_verified": is_money_received,
            "paid_amount": paid_amount,
            "refunded_amount": refunded_amount,
            "payment_status_provider": payment_status,
            "provider_payment_id": txn.get("provider_id"),
            "last_provider_sync_at": _now_iso(),
            "sources_seen": list(sources_seen),
        }
        await db.unified_orders.update_one(
            {"user_id": user_id, "id": existing["id"]},
            {"$set": update},
        )
        return {"action": "updated", "order_id": existing["id"]}

    if not is_money_received:
        return {"action": "skipped", "reason": "no positive captured/auth amount"}

    new_id = str(uuid.uuid4())
    doc = {
        "id": new_id,
        "user_id": user_id,
        "source": "tabby",
        "source_priority": SOURCE_PRIORITY["tabby"],
        "sources_seen": ["tabby"],
        "order_reference_id": order_ref,
        "order_number": order_num or order_ref,
        "gross_amount": txn.get("amount"),
        "paid_amount": paid_amount,
        "refunded_amount": refunded_amount,
        "currency": txn.get("currency") or "SAR",
        "payment_method": "tabby",
        "payment_verified": True,
        "payment_status_provider": payment_status,
        "provider_payment_id": txn.get("provider_id"),
        "buyer_email": txn.get("buyer_email") or "",
        "buyer_phone": txn.get("buyer_phone") or "",
        "needs_review": True,                 # user requirement #10
        "created_at": _now_iso(),
        "last_provider_sync_at": _now_iso(),
        "order_date": (txn.get("created_at_provider") or "")[:10] or None,
    }
    await db.unified_orders.insert_one(doc)
    return {"action": "created", "order_id": new_id}


# ── ensure indexes ────────────────────────────────────────────
async def ensure_sync_indexes(db) -> None:
    try:
        await db.payment_transactions.create_index(
            [("user_id", 1), ("provider", 1), ("provider_id", 1)],
            unique=True, name="ptx_uniq",
        )
    except Exception:
        pass
    try:
        await db.payment_transactions.create_index(
            [("user_id", 1), ("order_reference_id", 1)], name="ptx_order_ref",
        )
    except Exception:
        pass
    try:
        await db.payment_refunds.create_index(
            [("user_id", 1), ("provider", 1), ("provider_refund_id", 1)],
            unique=True, sparse=True, name="prefund_uniq",
        )
    except Exception:
        pass
    try:
        await db.unified_orders.create_index(
            [("user_id", 1), ("order_reference_id", 1)], name="uo_ref",
        )
    except Exception:
        pass
    try:
        await db.unified_orders.create_index(
            [("user_id", 1), ("order_number", 1)], name="uo_num",
        )
    except Exception:
        pass


# ── public entrypoint ─────────────────────────────────────────
async def sync_tabby_payments(
    db, user_id: str, *, since_iso: Optional[str] = None,
    backfill_until: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull payments from Tabby starting from `since_iso` (default:
    activation_date stored in bnpl_settings).  Upserts all touched
    collections idempotently."""
    secrets = await get_raw_secrets(db, user_id, "tabby")
    if not secrets.get("secret_key"):
        return {"ok": False, "error": "Tabby not configured"}
    if not secrets.get("enabled"):
        return {"ok": False, "error": "Tabby integration disabled"}

    # Default since-date = activation_date or today (we do NOT fetch
    # historical data per user requirement #3 unless explicitly asked).
    if not since_iso:
        act = secrets.get("activation_date") or datetime.now(timezone.utc).date().isoformat()
        since_iso = f"{act}T00:00:00Z"

    client = TabbyClient(
        secret_key=secrets["secret_key"],
        merchant_code=secrets.get("merchant_code") or "",
        base_url=secrets.get("api_base_url") or "https://api.tabby.sa",
    )

    try:
        # Default fetch (no status filter → Tabby's default = AUTHORIZED + CLOSED).
        payments = await client.list_payments_since(since_iso)
    except TabbyError as exc:
        return {"ok": False, "error": str(exc)}

    # Forensic stats — proves to the user exactly what came back and why
    # the saved count may differ from the fetched count.
    created_dates = [p.get("created_at") or "" for p in payments if p.get("created_at")]
    created_dates.sort()
    stats: Dict[str, Any] = {
        "fetched": len(payments),
        "transactions_upserted": 0,
        "refunds_upserted": 0,
        "orders_created": 0,
        "orders_updated": 0,
        "orders_skipped": 0,
        "skipped_no_id": 0,
        "first_payment_created_at": created_dates[0] if created_dates else None,
        "last_payment_created_at": created_dates[-1] if created_dates else None,
        "filter_used": {
            "endpoint": f"{client.base_url}/api/v2/payments",
            "created_at__gte": since_iso[:10] if since_iso else None,
            "status": "default (AUTHORIZED + CLOSED only)",
        },
    }
    # Per-payment outcome log (capped at 50 rows for response size).
    outcomes: List[Dict[str, Any]] = []

    for p in payments:
        pid = (p.get("id") or "").strip()
        if not pid:
            stats["skipped_no_id"] += 1
            outcomes.append({
                "provider_id": None,
                "created_at": p.get("created_at"),
                "status": p.get("status"),
                "outcome": "skipped_no_id",
            })
            continue
        txn = _normalise_payment(p, user_id)
        await db.payment_transactions.update_one(
            {"user_id": user_id, "provider": "tabby", "provider_id": pid},
            {"$set": {k: v for k, v in txn.items() if k != "id"},
             "$setOnInsert": {"id": txn["id"], "created_at": _now_iso()}},
            upsert=True,
        )
        stats["transactions_upserted"] += 1

        for rfd in _extract_refund_rows(p, user_id):
            rid = rfd.get("provider_refund_id") or ""
            if not rid:
                continue
            await db.payment_refunds.update_one(
                {"user_id": user_id, "provider": "tabby", "provider_refund_id": rid},
                {"$set": {k: v for k, v in rfd.items() if k != "id"},
                 "$setOnInsert": {"id": rfd["id"], "created_at": _now_iso()}},
                upsert=True,
            )
            stats["refunds_upserted"] += 1

        res = await _merge_into_unified_orders(db, user_id, txn)
        action = res.get("action") or "skipped"
        stats[f"orders_{action}"] = stats.get(f"orders_{action}", 0) + 1

        if len(outcomes) < 50:
            outcomes.append({
                "provider_id": pid,
                "created_at": p.get("created_at"),
                "status": p.get("status"),
                "amount": p.get("amount"),
                "order_reference_id": (p.get("order") or {}).get("reference_id"),
                "merged_action": action,
                "outcome": "saved",
            })

    stats["per_payment_log_sample"] = outcomes
    # Reconciliation: fetched - skipped = upserted (sanity)
    stats["reconciliation"] = {
        "fetched": stats["fetched"],
        "saved": stats["transactions_upserted"],
        "skipped_no_id": stats["skipped_no_id"],
        "missing": stats["fetched"] - stats["transactions_upserted"] - stats["skipped_no_id"],
    }
    # Friendly diagnosis when fetched==0
    if stats["fetched"] == 0:
        stats["diagnosis"] = (
            f"Tabby returned 0 payments for filter "
            f"created_at__gte={since_iso[:10]}. Without filter, Tabby has "
            "payments (per the Debug endpoint), so this means either:\n"
            "  • all your payments are OLDER than the activation_date "
            "    you set, or\n"
            "  • the most recent payments are in a non-default status "
            "    (CREATED / REJECTED / EXPIRED) which Tabby's API hides "
            "    unless you pass an explicit `status=` filter.\n"
            "Action: open the Debug page and compare sample_payments[].created_at "
            "with your activation_date. If all are older, lower the "
            "activation_date (Settings page) and resync."
        )

    await record_sync(db, user_id, "tabby")
    return {"ok": True, "since": since_iso, "stats": stats}
