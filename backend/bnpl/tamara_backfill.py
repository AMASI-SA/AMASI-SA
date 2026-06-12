"""Tamara backfill (Iter-116) — fetch payment data for existing orders.

Tamara doesn't expose a list-orders endpoint, so when a merchant has
historical orders in `unified_orders` (imported via Make/Excel) and
wants to know which of them were paid via Tamara, we walk through each
order and call `GET /merchants/orders/reference-id/{ref}`.

Behaviour (matches user spec):
  • Reads order_number/order_reference_id from `unified_orders`.
  • Calls Tamara per reference.  404 → not in Tamara → just skip.
  • 200 → normalise into payment_transactions, extract refunds,
    update the same unified_order row (NEVER creates a new order —
    we only update existing ones during backfill).
  • Updates: payment_status_provider, paid_amount, refunded_amount,
    provider_payment_id, last_provider_sync_at, sources_seen += tamara.
  • Idempotent: mongo unique index on
    (user_id, provider, provider_refund_id) and
    (user_id, provider, provider_id) prevents any duplicates.
  • Throttled: 200 ms gap between Tamara calls + max 2 concurrent.

Returns:
  {
    scanned:    int,   # how many orders were inspected
    found:      int,   # how many had a Tamara order behind them
    not_found:  int,   # 404 from Tamara (not paid via Tamara)
    errors:     int,   # transient failures (network, 5xx)
    refunds_found: int,
    orders_updated: int,
  }
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .clients.tamara import TamaraClient, TamaraError
from .config_store import DEFAULTS, get_raw_secrets, record_sync
from .webhook_routes import (
    _extract_tamara_refunds,
    _normalise_tamara_order,
)


THROTTLE_SECONDS = 0.2     # gap between Tamara calls (rate-limit guard)
MAX_CONCURRENCY = 2        # don't hammer Tamara
DEFAULT_LIMIT = 500        # safety cap per run
BATCH_SIZE = 100           # full-backfill batch size — bounded memory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _candidate_query(user_id: str, since: Optional[str]) -> Dict[str, Any]:
    """Mongo filter for orders worth backfilling against Tamara."""
    q: Dict[str, Any] = {"user_id": user_id}
    if since:
        q["order_date"] = {"$gte": since}
    q["$or"] = [
        {"sources_seen": {"$ne": "tamara"}},
        {"sources_seen": {"$exists": False}},
    ]
    return q


async def _persist_tamara_order(
    db, user_id: str, order: Dict[str, Any],
) -> Dict[str, Any]:
    """Upsert payment_transactions + payment_refunds + UPDATE existing
    unified_orders row (matched by order_reference_id).  Returns delta
    counts for the run stats."""
    txn = _normalise_tamara_order(order, user_id)
    refunds = _extract_tamara_refunds(order, user_id)

    res = {"refunds_found": 0, "updated_existing_order": False,
           "stored_transaction": False}

    if not txn.get("provider_id"):
        return res

    await db.payment_transactions.update_one(
        {"user_id": user_id, "provider": "tamara",
         "provider_id": txn["provider_id"]},
        {"$set": {k: v for k, v in txn.items() if k != "id"},
         "$setOnInsert": {"id": txn["id"], "created_at": _now_iso()}},
        upsert=True,
    )
    res["stored_transaction"] = True

    # Iter-146 — stamp billing_eligible_at idempotently when Tamara's
    # own status indicates the order has entered the settlement cycle.
    from .webhook_routes import _tamara_billing_eligible_event
    be_at = _tamara_billing_eligible_event(order)
    if be_at:
        from .billing_eligible import mark_billing_eligible_for_order
        await mark_billing_eligible_for_order(
            db, user_id,
            order_reference_id=txn.get("order_reference_id"),
            order_number=txn.get("order_number"),
            event_at=be_at,
        )

    for rfd in refunds:
        rid = rfd.get("provider_refund_id") or ""
        if not rid:
            continue
        await db.payment_refunds.update_one(
            {"user_id": user_id, "provider": "tamara",
             "provider_refund_id": rid},
            {"$set": {k: v for k, v in rfd.items() if k != "id"},
             "$setOnInsert": {"id": rfd["id"], "created_at": _now_iso()}},
            upsert=True,
        )
        res["refunds_found"] += 1

    # Update unified_orders ONLY for existing rows — backfill never
    # creates new orders (user requirement).
    ref = (txn.get("order_reference_id") or "").strip()
    num = (txn.get("order_number") or "").strip()
    if not (ref or num):
        return res

    existing = await db.unified_orders.find_one(
        {"user_id": user_id, "$or": [
            {"order_reference_id": ref or "__none__"},
            {"order_number": ref or "__none__"},
            {"order_reference_id": num or "__none__"},
            {"order_number": num or "__none__"},
        ]},
        {"_id": 0, "id": 1, "sources_seen": 1},
    )
    if not existing:
        return res

    sources_seen = set(existing.get("sources_seen") or [])
    sources_seen.add("tamara")
    payment_status = txn.get("status") or ""
    paid_amount = txn.get("captured_amount") or txn.get("amount") or 0
    refunded_amount = txn.get("refunded_amount") or 0
    is_money_received = payment_status in (
        "authorised", "captured", "fully_captured", "partially_captured",
    ) and paid_amount > 0

    await db.unified_orders.update_one(
        {"user_id": user_id, "id": existing["id"]},
        {"$set": {
            "payment_verified": is_money_received,
            "paid_amount": paid_amount,
            "refunded_amount": refunded_amount,
            "payment_status_provider": payment_status,
            "provider_payment_id": txn.get("provider_id"),
            "last_provider_sync_at": _now_iso(),
            "sources_seen": list(sources_seen),
        }},
    )
    res["updated_existing_order"] = True
    return res


async def _lookup_one(
    db, user_id: str, client: TamaraClient,
    semaphore: asyncio.Semaphore, ref: str,
) -> Dict[str, Any]:
    """Resolve one order through Tamara; returns a small status dict."""
    async with semaphore:
        try:
            order = await client.get_order_by_reference(ref)
        except TamaraError as exc:
            if exc.status == 404:
                return {"outcome": "not_found", "ref": ref}
            return {"outcome": "error", "ref": ref, "detail": str(exc)}
        # tiny throttle to be friendly to Tamara
        await asyncio.sleep(THROTTLE_SECONDS)

    if not isinstance(order, dict) or not order.get("order_id"):
        return {"outcome": "not_found", "ref": ref}

    delta = await _persist_tamara_order(db, user_id, order)
    return {"outcome": "found", "ref": ref, **delta}


async def backfill_tamara(
    db, user_id: str, *,
    since: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    """Scan up to `limit` unified_orders rows for the user (newest
    first), optionally filtered to `order_date >= since`, look each up
    against Tamara, and update local data accordingly.
    """
    secrets = await get_raw_secrets(db, user_id, "tamara")
    if not secrets.get("api_token"):
        return {"ok": False, "error": "Tamara api_token not set"}

    client = TamaraClient(
        api_token=secrets["api_token"],
        base_url=(secrets.get("api_base_url")
                  or DEFAULTS["tamara"]["api_base_url"]),
    )

    query = await _candidate_query(user_id, since)

    # Total candidates BEFORE this run (so the UI sees what's still left)
    total_candidates_before = await db.unified_orders.count_documents(query)

    # Also count already-verified rows in the same since-window for clarity.
    skipped_query: Dict[str, Any] = {"user_id": user_id,
                                     "sources_seen": "tamara"}
    if since:
        skipped_query["order_date"] = {"$gte": since}
    skipped_already_verified = await db.unified_orders.count_documents(skipped_query)

    refs: List[str] = []
    async for r in (
        db.unified_orders.find(
            query,
            {"_id": 0, "order_reference_id": 1, "order_number": 1},
        ).sort([("order_date", -1)]).limit(int(limit))
    ):
        ref = (r.get("order_reference_id") or r.get("order_number") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [_lookup_one(db, user_id, client, sem, r) for r in refs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    stats = {
        "total_orders_candidates": total_candidates_before,
        "skipped_already_verified": skipped_already_verified,
        "scanned_count": len(refs),
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "refunds_found": 0,
        "orders_updated": 0,
        "transactions_stored": 0,
        "since": since,
        "limit": int(limit),
    }
    errors_sample: List[str] = []
    for r in results:
        if isinstance(r, Exception):
            stats["errors"] += 1
            errors_sample.append(str(r))
            continue
        outcome = r.get("outcome")
        if outcome == "found":
            stats["found"] += 1
            stats["refunds_found"] += r.get("refunds_found", 0)
            if r.get("updated_existing_order"):
                stats["orders_updated"] += 1
            if r.get("stored_transaction"):
                stats["transactions_stored"] += 1
        elif outcome == "not_found":
            stats["not_found"] += 1
        else:
            stats["errors"] += 1
            if r.get("detail"):
                errors_sample.append(r["detail"])

    if errors_sample:
        stats["errors_sample"] = errors_sample[:5]

    # How many candidates remain for a follow-up batch?
    stats["remaining_after_run"] = await db.unified_orders.count_documents(query)

    await record_sync(db, user_id, "tamara")
    return {"ok": True, "stats": stats}


async def backfill_tamara_full(
    db, user_id: str, *,
    since: Optional[str] = None,
    hard_cap: int = 10000,
) -> Dict[str, Any]:
    """Process ALL pending Tamara candidates in BATCH_SIZE chunks until
    the candidate set is exhausted (or `hard_cap` is reached for
    safety).  Aggregates the per-batch stats and reports per-batch
    progress so the merchant can prove every order got checked.
    """
    secrets = await get_raw_secrets(db, user_id, "tamara")
    if not secrets.get("api_token"):
        return {"ok": False, "error": "Tamara api_token not set"}

    client = TamaraClient(
        api_token=secrets["api_token"],
        base_url=(secrets.get("api_base_url")
                  or DEFAULTS["tamara"]["api_base_url"]),
    )

    query = await _candidate_query(user_id, since)
    initial_candidates = await db.unified_orders.count_documents(query)

    skipped_query: Dict[str, Any] = {"user_id": user_id,
                                     "sources_seen": "tamara"}
    if since:
        skipped_query["order_date"] = {"$gte": since}
    skipped_already_verified = await db.unified_orders.count_documents(skipped_query)

    aggregate = {
        "total_orders_candidates": initial_candidates,
        "skipped_already_verified": skipped_already_verified,
        "scanned_count": 0,
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "refunds_found": 0,
        "orders_updated": 0,
        "transactions_stored": 0,
        "batches_completed": 0,
        "since": since,
        "hard_cap": int(hard_cap),
    }
    batches: List[Dict[str, Any]] = []
    errors_sample: List[str] = []

    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    consumed_refs: set = set()  # dedupe across batches

    while aggregate["scanned_count"] < hard_cap:
        # Re-fetch a fresh batch (skip rows we already touched this run).
        live_query = await _candidate_query(user_id, since)
        if consumed_refs:
            live_query["$and"] = [
                {"order_reference_id": {"$nin": list(consumed_refs)}},
                {"order_number": {"$nin": list(consumed_refs)}},
            ]

        refs: List[str] = []
        async for r in (
            db.unified_orders.find(
                live_query,
                {"_id": 0, "order_reference_id": 1, "order_number": 1},
            ).sort([("order_date", -1)]).limit(BATCH_SIZE)
        ):
            ref = (r.get("order_reference_id") or r.get("order_number") or "").strip()
            if ref and ref not in consumed_refs:
                refs.append(ref)
                consumed_refs.add(ref)

        if not refs:
            break

        tasks = [_lookup_one(db, user_id, client, sem, r) for r in refs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        batch_stat = {
            "batch_number": aggregate["batches_completed"] + 1,
            "size": len(refs),
            "found": 0, "not_found": 0, "errors": 0,
        }
        for r in results:
            if isinstance(r, Exception):
                batch_stat["errors"] += 1
                aggregate["errors"] += 1
                errors_sample.append(str(r))
                continue
            outcome = r.get("outcome")
            if outcome == "found":
                batch_stat["found"] += 1
                aggregate["found"] += 1
                aggregate["refunds_found"] += r.get("refunds_found", 0)
                if r.get("updated_existing_order"):
                    aggregate["orders_updated"] += 1
                if r.get("stored_transaction"):
                    aggregate["transactions_stored"] += 1
            elif outcome == "not_found":
                batch_stat["not_found"] += 1
                aggregate["not_found"] += 1
            else:
                batch_stat["errors"] += 1
                aggregate["errors"] += 1
                if r.get("detail"):
                    errors_sample.append(r["detail"])

        aggregate["scanned_count"] += len(refs)
        aggregate["batches_completed"] += 1
        batches.append(batch_stat)

        # If this batch had every request fail with auth errors, abort
        # early rather than burning through the entire candidate set.
        if batch_stat["errors"] == len(refs) and len(refs) > 0:
            aggregate["aborted"] = (
                "All requests in last batch failed. Aborting full "
                "backfill to avoid wasted calls. Check the errors_sample."
            )
            break

    if errors_sample:
        aggregate["errors_sample"] = errors_sample[:10]

    aggregate["remaining_after_run"] = await db.unified_orders.count_documents(
        await _candidate_query(user_id, since),
    )
    aggregate["per_batch"] = batches

    await record_sync(db, user_id, "tamara")
    return {"ok": True, "stats": aggregate}
