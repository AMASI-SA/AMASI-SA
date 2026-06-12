"""BNPL diagnostics — Iter-116 verification report.

Builds a forensic snapshot of what `payment_transactions`,
`payment_refunds`, and `unified_orders` actually contain for a given
merchant — so the user can confirm with their own eyes that sync
fetched real data, not just that test-connection succeeded.

Answers (in one round-trip):

  1. Last sync timestamps (Tabby + Tamara).
  2. Sync scope: actual since-date used per provider.
  3. Matching: matched_orders / created_orders / unmatched_txns.
  4. Refunds detected per provider + orders updated.
  5. Mismatch report — sample of payments without unified order link,
     and unified orders without any payment_transactions row.
  6. Comparison rows (Order / Provider / Status / Amount / Refunds /
     Match).
  7. Dedup audit: the unique key in use + estimated skipped duplicates.
  8. Behavior contract: how old orders get updated, NEVER recreated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, Request

from auth import get_current_user_from_db

from .config_store import BNPL_PROVIDERS, get_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _provider_stats(db, user_id: str, provider: str) -> Dict[str, Any]:
    """Aggregate per-provider stats from local collections."""
    ptx_filter = {"user_id": user_id, "provider": provider}
    refunds_filter = {"user_id": user_id, "provider": provider}

    transactions_count = await db.payment_transactions.count_documents(ptx_filter)
    refunds_count = await db.payment_refunds.count_documents(refunds_filter)

    # earliest / latest synced (by created_at_provider)
    earliest_doc = await db.payment_transactions.find_one(
        ptx_filter, sort=[("created_at_provider", 1)],
        projection={"_id": 0, "created_at_provider": 1, "amount": 1},
    )
    latest_doc = await db.payment_transactions.find_one(
        ptx_filter, sort=[("created_at_provider", -1)],
        projection={"_id": 0, "created_at_provider": 1, "amount": 1},
    )

    return {
        "transactions_count": transactions_count,
        "refunds_count": refunds_count,
        "earliest_payment_at": (earliest_doc or {}).get("created_at_provider") or None,
        "latest_payment_at": (latest_doc or {}).get("created_at_provider") or None,
    }


async def _matching_stats(db, user_id: str, provider: str) -> Dict[str, Any]:
    """How many txns are linked to unified_orders?  How many created new
    orders (source=provider)?  How many txns have no order at all?"""
    created_orders = await db.unified_orders.count_documents({
        "user_id": user_id, "source": provider,
    })

    matched_orders_pipeline = [
        {"$match": {"user_id": user_id, "provider": provider}},
        {"$project": {"order_reference_id": 1, "_id": 0}},
        {"$match": {"order_reference_id": {"$nin": [None, ""]}}},
        {"$lookup": {
            "from": "unified_orders",
            "let": {"ref": "$order_reference_id"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$user_id", user_id]},
                            {"$or": [
                                {"$eq": ["$order_reference_id", "$$ref"]},
                                {"$eq": ["$order_number", "$$ref"]},
                            ]},
                        ],
                    },
                }},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": "matched",
        }},
        {"$match": {"matched.0": {"$exists": True}}},
        {"$count": "n"},
    ]
    cur = db.payment_transactions.aggregate(matched_orders_pipeline)
    matched_orders = 0
    async for r in cur:
        matched_orders = r.get("n", 0)

    unmatched_txns_pipeline = [
        {"$match": {"user_id": user_id, "provider": provider}},
        {"$lookup": {
            "from": "unified_orders",
            "let": {"ref": "$order_reference_id"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$user_id", user_id]},
                            {"$or": [
                                {"$eq": ["$order_reference_id", "$$ref"]},
                                {"$eq": ["$order_number", "$$ref"]},
                            ]},
                        ],
                    },
                }},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": "matched",
        }},
        {"$match": {"matched.0": {"$exists": False}}},
        {"$count": "n"},
    ]
    cur = db.payment_transactions.aggregate(unmatched_txns_pipeline)
    unmatched_txns = 0
    async for r in cur:
        unmatched_txns = r.get("n", 0)

    return {
        "matched_orders": matched_orders,
        "created_orders": created_orders,
        "unmatched_txns": unmatched_txns,
    }


async def _refunds_stats(db, user_id: str, provider: str) -> Dict[str, Any]:
    refunds_detected = await db.payment_refunds.count_documents({
        "user_id": user_id, "provider": provider,
    })
    orders_with_refunds = await db.unified_orders.count_documents({
        "user_id": user_id,
        "refunded_amount": {"$gt": 0},
        "sources_seen": provider,
    })
    return {
        "refunds_detected": refunds_detected,
        "orders_with_refunds_from_provider": orders_with_refunds,
    }


async def _samples_unmatched(db, user_id: str, provider: str,
                             limit: int = 10) -> List[Dict[str, Any]]:
    """Sample of payment_transactions with no unified_orders link
    (provider has payment but our orders DB doesn't know about it)."""
    pipeline = [
        {"$match": {"user_id": user_id, "provider": provider}},
        {"$lookup": {
            "from": "unified_orders",
            "let": {"ref": "$order_reference_id"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$user_id", user_id]},
                            {"$or": [
                                {"$eq": ["$order_reference_id", "$$ref"]},
                                {"$eq": ["$order_number", "$$ref"]},
                            ]},
                        ],
                    },
                }},
                {"$limit": 1},
                {"$project": {"_id": 1}},
            ],
            "as": "matched",
        }},
        {"$match": {"matched.0": {"$exists": False}}},
        {"$limit": int(limit)},
        {"$project": {
            "_id": 0, "provider_id": 1, "order_reference_id": 1,
            "amount": 1, "currency": 1, "status": 1,
            "refunded_amount": 1, "created_at_provider": 1,
        }},
    ]
    rows: List[Dict[str, Any]] = []
    async for r in db.payment_transactions.aggregate(pipeline):
        rows.append(r)
    return rows


async def _comparison_rows(db, user_id: str, limit_per_provider: int = 50,
                           ) -> List[Dict[str, Any]]:
    """Combined Order/Provider/Status/Amount/Refund/Match table — covers
    every payment_transactions row for the user (capped per provider)."""
    out: List[Dict[str, Any]] = []
    for provider in BNPL_PROVIDERS:
        async for ptx in (
            db.payment_transactions
              .find({"user_id": user_id, "provider": provider}, {"_id": 0, "raw_payload": 0})
              .sort([("created_at_provider", -1)])
              .limit(limit_per_provider)
        ):
            ref = ptx.get("order_reference_id") or ptx.get("order_number") or ""
            match_doc = None
            if ref:
                match_doc = await db.unified_orders.find_one(
                    {"user_id": user_id,
                     "$or": [
                         {"order_reference_id": ref},
                         {"order_number": ref},
                     ]},
                    {"_id": 0, "id": 1, "source": 1, "needs_review": 1,
                     "order_status": 1, "payment_status_provider": 1,
                     "sources_seen": 1},
                )
            if not ref:
                match = "no_reference"
            elif match_doc is None:
                match = "unmatched"
            elif (match_doc.get("source") or "") == provider:
                match = "created_by_provider"
            else:
                match = "matched"
            out.append({
                "order_number": ref or "—",
                "provider": provider,
                "payment_status": ptx.get("status") or "",
                "order_status": (match_doc or {}).get("order_status") or "",
                "amount": float(ptx.get("amount") or 0),
                "refund_amount": float(ptx.get("refunded_amount") or 0),
                "match_status": match,
                "provider_id": ptx.get("provider_id"),
                "created_at_provider": ptx.get("created_at_provider"),
            })
    out.sort(
        key=lambda r: r.get("created_at_provider") or "", reverse=True,
    )
    return out


def attach_bnpl_diagnostics_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/bnpl", tags=["bnpl-diagnostics"])

    @router.get("/diagnostics")
    async def diagnostics(
        comparison_limit: int = Query(50, ge=1, le=500),
        sample_limit: int = Query(10, ge=1, le=100),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        report: Dict[str, Any] = {
            "generated_at": _now_iso(),
            "user_id": uid,
            "providers": {},
        }

        for provider in BNPL_PROVIDERS:
            settings_doc = await get_settings(db, uid, provider)
            stats = await _provider_stats(db, uid, provider)
            matching = await _matching_stats(db, uid, provider)
            refunds = await _refunds_stats(db, uid, provider)
            unmatched_samples = await _samples_unmatched(
                db, uid, provider, limit=sample_limit,
            )

            report["providers"][provider] = {
                "settings": {
                    "enabled": settings_doc.get("enabled"),
                    "environment": settings_doc.get("environment"),
                    "activation_date": settings_doc.get("activation_date"),
                    "last_sync_at": settings_doc.get("last_sync_at"),
                    "last_webhook_at": settings_doc.get("last_webhook_at"),
                    "last_test_ok": settings_doc.get("last_test_ok"),
                    "last_test_error": settings_doc.get("last_test_error"),
                    "api_base_url": settings_doc.get("api_base_url"),
                },
                "stats": stats,
                "matching": matching,
                "refunds": refunds,
                "samples_unmatched_payments": unmatched_samples,
                "sync_scope": {
                    "since_used": (
                        f"{settings_doc.get('activation_date')}T00:00:00Z"
                        if settings_doc.get("activation_date") else None
                    ),
                    "rule": "From `activation_date` 00:00 UTC. No historical "
                            "backfill unless `?since=YYYY-MM-DD` passed to "
                            "the sync endpoint explicitly.",
                },
            }

        report["comparison_rows"] = await _comparison_rows(
            db, uid, limit_per_provider=comparison_limit,
        )

        # Dedup behaviour contract — what guarantees we don't double-write
        report["deduplication"] = {
            "transactions_unique_key": "(user_id, provider, provider_id)",
            "transactions_index": "ptx_uniq (unique)",
            "refunds_unique_key": "(user_id, provider, provider_refund_id)",
            "refunds_index": "prefund_uniq (unique, sparse)",
            "guarantee": (
                "Mongo enforces uniqueness — a second sync of the same "
                "Tabby payment_id or Tamara order_id can NEVER create a "
                "duplicate row; the upsert simply re-writes the same row."
            ),
        }

        # Old-order behaviour contract
        report["old_orders_behavior"] = {
            "mode": "update-only",
            "explanation": (
                "When a new refund/status arrives for an order that "
                "already exists in `unified_orders`, the merge layer "
                "ONLY updates payment fields "
                "(payment_verified / paid_amount / refunded_amount / "
                "payment_status_provider / last_provider_sync_at) and "
                "appends the provider to `sources_seen`. It NEVER "
                "overwrites Make/Excel canonical fields (gross_amount, "
                "items, etc.) and NEVER recreates the order."
            ),
            "match_keys_priority": [
                "order_reference_id",
                "order_number",
            ],
        }

        # Headline summary
        total_txn = sum(p["stats"]["transactions_count"]
                        for p in report["providers"].values())
        total_refunds = sum(p["stats"]["refunds_count"]
                            for p in report["providers"].values())
        total_created = sum(p["matching"]["created_orders"]
                            for p in report["providers"].values())
        total_matched = sum(p["matching"]["matched_orders"]
                            for p in report["providers"].values())
        report["headline"] = {
            "total_transactions_synced": total_txn,
            "total_refunds_synced": total_refunds,
            "total_orders_created_by_bnpl": total_created,
            "total_orders_matched": total_matched,
        }
        return report

    # ── Iter-146 — Tamara billing-eligible backfill & status ──────
    @router.get("/tamara/billing-eligible/status")
    async def billing_eligible_status(user: dict = Depends(current_user)):
        """Return how many Tamara payment_transactions already have a
        billing_eligible_at stamp and how many remain.  Used by the UI to
        decide whether to surface the backfill CTA."""
        uid = user["id"]
        total = await db.payment_transactions.count_documents(
            {"user_id": uid, "provider": "tamara"},
        )
        stamped = await db.payment_transactions.count_documents({
            "user_id": uid, "provider": "tamara",
            "billing_eligible_at": {"$nin": [None, ""]},
        })
        return {
            "total":     total,
            "stamped":   stamped,
            "unstamped": total - stamped,
            "coverage_pct": (
                round(stamped / total * 100, 2) if total > 0 else 0.0
            ),
        }

    @router.post("/tamara/billing-eligible/backfill")
    async def billing_eligible_backfill(
        dry_run: bool = Query(False),
        user: dict = Depends(current_user),
    ):
        """Backfill `billing_eligible_at` for existing Tamara payment_
        transactions by inspecting the linked `unified_orders.order_status`.

        Rules (Iter-146):
          • If the unified order's status is currently billable, stamp
            the txn's billing_eligible_at with the unified order's
            `updated_at` (best-known transition time) — else the txn's
            `updated_at_provider` → `created_at_provider`.
          • If the unified order's status is NOT billable, leave
            billing_eligible_at unset → the txn is excluded from the
            Tamara settlement until a future status update flips it.
          • Idempotent: never overwrites an already-stamped row.

        Iter-147 — also recomputes `effective_settlement_date` +
        `settlement_source` on every Tamara row so estimated/billing/
        official attribution is correct after the run.

        Set ?dry_run=true for a count-only preview.
        """
        from .billing_eligible import is_billable_status
        from .settlement_attribution import recompute_attribution_for_doc

        uid = user["id"]
        scanned = 0
        eligible = 0
        stamped = 0
        skipped_no_order = 0
        skipped_not_billable = 0
        already_stamped = 0
        attribution_recomputed = 0

        # Walk every Tamara txn that lacks billing_eligible_at.
        cur = db.payment_transactions.find(
            {
                "user_id": uid,
                "provider": "tamara",
                "$or": [
                    {"billing_eligible_at": {"$exists": False}},
                    {"billing_eligible_at": None},
                    {"billing_eligible_at": ""},
                ],
            },
            {"_id": 0, "id": 1, "order_reference_id": 1, "order_number": 1,
             "created_at_provider": 1, "updated_at_provider": 1,
             "status": 1},
        )
        async for t in cur:
            scanned += 1
            # Try Tamara's own status first.
            tamara_status = (t.get("status") or "").lower()
            if tamara_status in (
                "fully_captured", "partially_captured",
                "fully_shipped", "shipped",
                "partially_refunded", "fully_refunded",
            ):
                stamp = (
                    t.get("updated_at_provider")
                    or t.get("created_at_provider")
                    or _now_iso()
                )
                eligible += 1
                if not dry_run:
                    r = await db.payment_transactions.update_one(
                        {"user_id": uid, "id": t["id"],
                         "$or": [
                            {"billing_eligible_at": {"$exists": False}},
                            {"billing_eligible_at": None},
                            {"billing_eligible_at": ""},
                         ]},
                        {"$set": {"billing_eligible_at": stamp}},
                    )
                    stamped += int(getattr(r, "modified_count", 0) or 0)
                continue

            # Fall back to unified_orders.order_status lookup.
            ref = (t.get("order_reference_id") or "").strip()
            num = (t.get("order_number") or "").strip()
            if not (ref or num):
                skipped_no_order += 1
                continue
            keys = [k for k in (ref, num) if k]
            uo = await db.unified_orders.find_one(
                {"user_id": uid, "$or": [
                    {"order_reference_id": {"$in": keys}},
                    {"order_number":       {"$in": keys}},
                ]},
                {"_id": 0, "order_status": 1, "updated_at": 1},
            )
            if not uo:
                skipped_no_order += 1
                continue
            if not is_billable_status(uo.get("order_status")):
                skipped_not_billable += 1
                continue
            eligible += 1
            stamp = (
                uo.get("updated_at")
                or t.get("updated_at_provider")
                or t.get("created_at_provider")
                or _now_iso()
            )
            if not dry_run:
                r = await db.payment_transactions.update_one(
                    {"user_id": uid, "id": t["id"],
                     "$or": [
                        {"billing_eligible_at": {"$exists": False}},
                        {"billing_eligible_at": None},
                        {"billing_eligible_at": ""},
                     ]},
                    {"$set": {"billing_eligible_at": stamp}},
                )
                if int(getattr(r, "modified_count", 0) or 0) > 0:
                    stamped += 1
                else:
                    already_stamped += 1

        # Iter-147 — Recompute attribution on EVERY Tamara row (not
        # just newly-stamped ones) so estimated rows get an
        # effective_settlement_date too.
        if not dry_run:
            async for d in db.payment_transactions.find(
                {"user_id": uid, "provider": "tamara"},
                {"_id": 0, "id": 1},
            ):
                rr = await recompute_attribution_for_doc(
                    db, user_id=uid, txn_id=d.get("id"),
                )
                if rr.get("updated"):
                    attribution_recomputed += 1

        return {
            "ok": True,
            "dry_run": dry_run,
            "scanned": scanned,
            "eligible_found": eligible,
            "stamped": stamped,
            "already_stamped": already_stamped,
            "skipped_no_order": skipped_no_order,
            "skipped_not_billable": skipped_not_billable,
            "attribution_recomputed": attribution_recomputed,
        }

    # Iter-147 — Settlement-attribution status & forced recompute.
    @router.get("/tamara/attribution/status")
    async def attribution_status(user: dict = Depends(current_user)):
        uid = user["id"]
        total = await db.payment_transactions.count_documents(
            {"user_id": uid, "provider": "tamara"},
        )
        official = await db.payment_transactions.count_documents({
            "user_id": uid, "provider": "tamara",
            "settlement_source": "provider_official",
        })
        billing = await db.payment_transactions.count_documents({
            "user_id": uid, "provider": "tamara",
            "settlement_source": "billing_eligible",
        })
        estimated = await db.payment_transactions.count_documents({
            "user_id": uid, "provider": "tamara",
            "settlement_source": "estimated",
        })
        unattributed = total - official - billing - estimated
        return {
            "total":             total,
            "provider_official": official,
            "billing_eligible":  billing,
            "estimated":         estimated,
            "unattributed":      max(0, unattributed),
        }

    @router.post("/tamara/attribution/recompute")
    async def attribution_recompute(user: dict = Depends(current_user)):
        """Walk EVERY Tamara payment_transactions row and refresh
        `effective_settlement_date` + `settlement_source` based on the
        current state of (provider_settlement_date, captured_at_provider,
        billing_eligible_at, created_at_provider).  Idempotent — safe
        to run repeatedly.

        Iter-147 v2 — also opportunistically extracts
        `captured_at_provider` from existing `raw_payload.captures[]`
        for rows that pre-date the field being persisted natively.
        """
        from .settlement_attribution import recompute_attribution_for_doc
        uid = user["id"]
        scanned = 0
        updated = 0
        captured_extracted = 0
        async for d in db.payment_transactions.find(
            {"user_id": uid, "provider": "tamara"},
            {"_id": 0, "id": 1, "captured_at_provider": 1, "raw_payload": 1},
        ):
            scanned += 1
            # Extract captured_at_provider from raw_payload.captures[] if missing.
            if not d.get("captured_at_provider"):
                raw = d.get("raw_payload") or {}
                captures = raw.get("captures") or []
                earliest: str | None = None
                for cap in captures:
                    if not isinstance(cap, dict):
                        continue
                    ts = (cap.get("created_at") or cap.get("captured_at")
                          or cap.get("date"))
                    if ts and (earliest is None or str(ts) < str(earliest)):
                        earliest = str(ts)
                if earliest:
                    await db.payment_transactions.update_one(
                        {"user_id": uid, "id": d["id"]},
                        {"$set": {"captured_at_provider": earliest}},
                    )
                    captured_extracted += 1
            r = await recompute_attribution_for_doc(
                db, user_id=uid, txn_id=d.get("id"),
            )
            if r.get("updated"):
                updated += 1
        return {
            "ok": True,
            "scanned": scanned,
            "updated": updated,
            "captured_at_provider_extracted": captured_extracted,
        }

    @router.get("/tamara/attribution/log")
    async def attribution_log(
        limit: int = Query(50, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        """Return the recent attribution transitions (estimated →
        billing → provider_official) so the merchant can audit how
        settlement attribution evolved over time."""
        uid = user["id"]
        cur = db.tamara_attribution_log.find(
            {"user_id": uid}, {"_id": 0},
        ).sort([("at", -1)]).limit(limit)
        rows = [d async for d in cur]
        return {"rows": rows, "count": len(rows)}

    @router.post("/tamara/attribution/reapply-from-files")
    async def attribution_reapply_from_files(
        user: dict = Depends(current_user),
    ):
        """Re-process every previously-imported Tamara settlement file
        and propagate `provider_settlement_id` / `provider_settlement_date`
        to `payment_transactions`.

        Use case: a Tamara settlement file was uploaded BEFORE
        Iter-147 v2 (when attribution only flowed through unified_orders
        matches).  After upgrading, run this to retroactively apply the
        official attribution to all `payment_transactions` from those
        files — no re-upload needed.
        """
        from .settlement_attribution import (
            set_provider_official_attribution,
        )
        uid = user["id"]

        scanned_files = 0
        scanned_entries = 0
        attributed = 0
        skipped_no_ref = 0

        # Walk every Tamara settlement-file the merchant has ever uploaded.
        async for f in db.settlement_files.find(
            {"user_id": uid, "provider": "tamara"},
            {"_id": 0, "id": 1},
        ):
            scanned_files += 1
            # Walk per-entry trace rows of this file.
            entries_by_order: Dict[str, list] = {}
            async for e in db.settlement_entries.find(
                {"file_id": f["id"], "user_id": uid, "provider": "tamara"},
                {"_id": 0, "order_number": 1, "tamara_order_id": 1,
                 "settlement_reference": 1, "settlement_date": 1},
            ):
                scanned_entries += 1
                on = (e.get("order_number") or "").strip()
                if not on:
                    continue
                entries_by_order.setdefault(on, []).append(e)

            for order_no, rows in entries_by_order.items():
                ref = ""
                latest_date: str | None = None
                tamara_order_id = None
                for r in rows:
                    if r.get("settlement_reference") and not ref:
                        ref = str(r["settlement_reference"])
                    d = r.get("settlement_date")
                    if d and (latest_date is None or str(d) > str(latest_date)):
                        latest_date = str(d)
                    if r.get("tamara_order_id") and not tamara_order_id:
                        tamara_order_id = r["tamara_order_id"]
                if not (ref or latest_date):
                    skipped_no_ref += 1
                    continue
                res = await set_provider_official_attribution(
                    db, uid,
                    order_number=order_no,
                    provider_id=tamara_order_id,
                    provider_settlement_id=ref or None,
                    provider_invoice_id=ref or None,
                    provider_settlement_date=latest_date,
                )
                if res.get("matched", 0) > 0:
                    attributed += 1

        return {
            "ok": True,
            "scanned_files": scanned_files,
            "scanned_entries": scanned_entries,
            "orders_attributed": attributed,
            "skipped_no_ref": skipped_no_ref,
        }

    parent_router.include_router(router)
