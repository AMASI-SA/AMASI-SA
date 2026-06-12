"""Hourly incremental auto-sync for BNPL providers (Tamara + Tabby).

Replaces the manual "Sync Now" / "Backfill" / "Forensic" buttons by
running an asyncio background task that every hour:

  1. Iterates over every `bnpl_settings` document that has
     `enabled=True` AND has decryptable credentials.
  2. For each (user, provider) pair calls the existing sync entry
     points incrementally — only fetching payments that were
     updated since the last successful auto-sync.
  3. Persists a run record in `bnpl_auto_sync_runs` so the UI can
     show "Last sync 17 minutes ago".

Design choices:
  • Idempotent — uses the same upsert helpers as the manual sync.
  • Per-user error isolation — one merchant's bad credentials won't
    block another's auto-sync.
  • Single source of truth — all merges land in `unified_orders`,
    so Dashboard / Reports / Profits / Assets / Settlements share
    the same data without bespoke aggregation.
  • Manual trigger preserved: `POST /api/bnpl/auto-sync/run-now`
    runs the same routine but for the current user only.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .sync_service import sync_tabby_payments
from .tamara_backfill import backfill_tamara

logger = logging.getLogger(__name__)

# ── Tuning constants (overridable via env so QA can dial down) ───
SYNC_INTERVAL_SECONDS = int(os.environ.get("BNPL_AUTO_SYNC_INTERVAL_SECONDS", "3600"))   # 1h
SYNC_LOOKBACK_DAYS = int(os.environ.get("BNPL_AUTO_SYNC_LOOKBACK_DAYS", "3"))            # safety overlap
MAX_USERS_PER_RUN = int(os.environ.get("BNPL_AUTO_SYNC_MAX_USERS", "200"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since_iso_for_user(setting: Dict[str, Any]) -> str:
    """Compute the `since` timestamp for an incremental sync.

    Strategy: take the MAX of:
      • last_auto_sync_at - lookback overlap (catch status updates)
      • activation_date  (never go before merchant onboarded)
      • now() - 30 days  (safety cap for first run with no markers)
    """
    last_auto = setting.get("last_auto_sync_at")
    activation = setting.get("activation_date")
    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=30)

    candidates: List[datetime] = [floor]
    if last_auto:
        try:
            ts = datetime.fromisoformat(last_auto.replace("Z", "+00:00"))
            candidates.append(ts - timedelta(days=SYNC_LOOKBACK_DAYS))
        except Exception:  # noqa: BLE001
            pass
    if activation:
        try:
            ts = datetime.fromisoformat(str(activation) + "T00:00:00+00:00")
            if ts > floor:
                candidates.append(ts)
        except Exception:  # noqa: BLE001
            pass

    chosen = max(candidates)
    return chosen.isoformat()


async def _propagate_refunds_to_unified(
    db, user_id: str, provider: str,
) -> int:
    """Copy `refunded_amount` from payment_transactions onto the
    matching unified_orders rows so Reports / Profits / Settlements
    reflect the refunds.  This closes the `dashboard_drift` gap that
    happens when refunds arrive via webhook/sync AFTER the order row
    was already created from Salla/Make/Excel.

    Match strategy:
        unified_orders.order_reference_id == ptx.order_reference_id
            OR unified_orders.order_number == ptx.order_reference_id

    Idempotent — only writes when the new amount is GREATER than the
    one already stored (so we never accidentally zero a real refund).
    Returns the number of unified_orders rows updated.
    """
    updates = 0
    cursor = db.payment_transactions.find(
        {"user_id": user_id, "provider": provider,
         "refunded_amount": {"$gt": 0}},
        {"_id": 0, "order_reference_id": 1, "refunded_amount": 1,
         "provider_id": 1},
    )
    async for ptx in cursor:
        ref = (ptx.get("order_reference_id") or "").strip()
        amt = float(ptx.get("refunded_amount") or 0)
        if not ref or amt <= 0:
            continue
        res = await db.unified_orders.update_one(
            {
                "user_id": user_id,
                "$or": [
                    {"order_reference_id": ref},
                    {"order_number": ref},
                ],
                "$expr": {"$lt": [
                    {"$ifNull": ["$refunded_amount", 0]}, amt,
                ]},
            },
            {"$set": {
                "refunded_amount": round(amt, 2),
                "last_refund_propagated_at": _now_iso(),
            },
             "$addToSet": {"sources_seen": provider}},
        )
        if res.modified_count > 0:
            updates += 1
    return updates


async def _sync_one_user_provider(
    db, user_id: str, provider: str, setting: Dict[str, Any],
) -> Dict[str, Any]:
    """Run ONE incremental sync for (user, provider). Caller-isolated
    error handling — never raises."""
    started = _now_iso()
    since_iso = _since_iso_for_user(setting)

    try:
        if provider == "tabby":
            res = await sync_tabby_payments(db, user_id, since_iso=since_iso)
        elif provider == "tamara":
            # Tamara's backfill scans unified_orders rows since `since`
            # (YYYY-MM-DD) and re-validates them against Tamara.
            since_day = since_iso[:10]
            res = await backfill_tamara(db, user_id, since=since_day)
        else:
            return {"ok": False, "provider": provider, "error": f"unknown provider {provider}"}

        ok = bool(res.get("ok", True))

        # ── Propagate refunded_amount to unified_orders ────────────
        # Ensures Reports / Profits / Settlements always reflect the
        # refunds we just fetched from the provider, regardless of
        # which sync path created the unified_orders row.
        if ok:
            try:
                await _propagate_refunds_to_unified(
                    db, user_id, provider,
                )
            except Exception as prop_exc:  # noqa: BLE001
                logger.warning(
                    "iter-117: refund propagation failed for %s/%s: %s",
                    user_id, provider, prop_exc,
                )
        if ok:
            # Persist last_auto_sync_at marker.
            await db.bnpl_settings.update_one(
                {"user_id": user_id, "provider": provider},
                {"$set": {
                    "last_auto_sync_at": _now_iso(),
                    "last_auto_sync_since": since_iso,
                    "last_auto_sync_status": "ok",
                    "last_auto_sync_error": "",
                }},
            )
        else:
            await db.bnpl_settings.update_one(
                {"user_id": user_id, "provider": provider},
                {"$set": {
                    "last_auto_sync_at": _now_iso(),
                    "last_auto_sync_since": since_iso,
                    "last_auto_sync_status": "error",
                    "last_auto_sync_error": str(res.get("error") or "unknown")[:300],
                }},
            )

        return {
            "ok": ok,
            "provider": provider,
            "user_id": user_id,
            "since": since_iso,
            "started_at": started,
            "finished_at": _now_iso(),
            "fetched": res.get("fetched", 0),
            "transactions_upserted": res.get("transactions_upserted", 0),
            "refunds_upserted": res.get("refunds_upserted", 0),
            "orders_created": res.get("orders_created", 0),
            "orders_updated": res.get("orders_updated", 0),
            "error": res.get("error") if not ok else None,
        }
    except Exception as exc:  # noqa: BLE001
        # Persist failure but never crash the cron loop.
        try:
            await db.bnpl_settings.update_one(
                {"user_id": user_id, "provider": provider},
                {"$set": {
                    "last_auto_sync_at": _now_iso(),
                    "last_auto_sync_status": "error",
                    "last_auto_sync_error": f"{type(exc).__name__}: {exc}"[:300],
                }},
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False, "provider": provider, "user_id": user_id,
            "since": since_iso, "started_at": started,
            "finished_at": _now_iso(),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def run_auto_sync_for_user(db, user_id: str) -> Dict[str, Any]:
    """Run BNPL auto-sync for ONE user (both providers). Used by the
    `POST /api/bnpl/auto-sync/run-now` manual trigger."""
    started = _now_iso()
    results: List[Dict[str, Any]] = []
    cursor = db.bnpl_settings.find({
        "user_id": user_id,
        "provider": {"$in": ["tabby", "tamara"]},
        "enabled": True,
    })
    async for setting in cursor:
        provider = setting["provider"]
        results.append(await _sync_one_user_provider(db, user_id, provider, setting))

    run_doc = {
        "id": str(uuid.uuid4()),
        "kind": "manual",
        "user_id": user_id,
        "started_at": started,
        "finished_at": _now_iso(),
        "providers": [r["provider"] for r in results],
        "results": results,
        "any_failures": any(not r.get("ok") for r in results),
    }
    try:
        await db.bnpl_auto_sync_runs.insert_one(run_doc)
    except Exception as e:  # noqa: BLE001
        logger.warning("bnpl auto-sync: run-log insert failed: %s", e)
    run_doc.pop("_id", None)
    return run_doc


async def run_auto_sync_for_all_users(db) -> Dict[str, Any]:
    """Iterate over every user with at least one enabled BNPL provider
    and run an incremental sync. Per-user errors are logged but never
    abort the loop."""
    started = _now_iso()
    started_dt = datetime.now(timezone.utc)
    # Distinct (user_id, provider) pairs with enabled credentials.
    pairs: List[Dict[str, Any]] = await db.bnpl_settings.find(
        {"provider": {"$in": ["tabby", "tamara"]}, "enabled": True},
        {"_id": 0, "user_id": 1, "provider": 1, "activation_date": 1,
         "last_auto_sync_at": 1},
    ).to_list(length=MAX_USERS_PER_RUN * 2)

    users_processed = set()
    results: List[Dict[str, Any]] = []
    for setting in pairs:
        uid = setting.get("user_id")
        provider = setting.get("provider")
        if not uid or not provider:
            continue
        if len(users_processed) >= MAX_USERS_PER_RUN and uid not in users_processed:
            continue
        users_processed.add(uid)
        res = await _sync_one_user_provider(db, uid, provider, setting)
        results.append(res)

    finished = _now_iso()
    duration = (datetime.now(timezone.utc) - started_dt).total_seconds()
    summary = {
        "id": str(uuid.uuid4()),
        "kind": "cron",
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": round(duration, 1),
        "pairs_processed": len(results),
        "users_processed": len(users_processed),
        "any_failures": any(not r.get("ok") for r in results),
        "by_provider": {
            "tabby": sum(1 for r in results if r["provider"] == "tabby" and r.get("ok")),
            "tamara": sum(1 for r in results if r["provider"] == "tamara" and r.get("ok")),
        },
        "results": results[:50],          # cap response/log size
    }
    try:
        await db.bnpl_auto_sync_runs.insert_one(summary)
        summary.pop("_id", None)
    except Exception as e:  # noqa: BLE001
        logger.warning("bnpl auto-sync: run-log insert failed: %s", e)
    return summary


async def get_auto_sync_status(db, user_id: str) -> Dict[str, Any]:
    """Build the dashboard status payload for the manual-trigger UI."""
    providers: List[Dict[str, Any]] = []
    cursor = db.bnpl_settings.find({
        "user_id": user_id,
        "provider": {"$in": ["tabby", "tamara"]},
    })
    async for st in cursor:
        providers.append({
            "provider": st.get("provider"),
            "enabled": bool(st.get("enabled")),
            "last_auto_sync_at": st.get("last_auto_sync_at"),
            "last_auto_sync_since": st.get("last_auto_sync_since"),
            "last_auto_sync_status": st.get("last_auto_sync_status") or "never",
            "last_auto_sync_error": st.get("last_auto_sync_error") or "",
            "activation_date": st.get("activation_date"),
        })

    last_run = await db.bnpl_auto_sync_runs.find_one(
        {"$or": [{"user_id": user_id}, {"kind": "cron"}]},
        sort=[("started_at", -1)],
        projection={"_id": 0},
    )
    return {
        "providers": providers,
        "last_run": last_run,
        "interval_seconds": SYNC_INTERVAL_SECONDS,
        "next_run_eta_seconds": None,  # filled by /status route from scheduler state
    }


# ── Iter-147 — Daily Tamara attribution sweep ────────────────────
async def run_tamara_attribution_sweep(db) -> Dict[str, Any]:
    """Walk every user with Tamara enabled and recompute settlement
    attribution on every `payment_transactions` row.

    This is a safety-net background job: most attribution updates flow
    through the inline hooks (status webhook, billing-eligible stamp,
    settlement-file import).  This sweep catches edge cases — Salla
    direct status updates that didn't pass through `orders_db.upsert_order`,
    or rows that were inserted before Iter-147 shipped.

    Idempotent: rows whose source hasn't changed are left untouched
    and no audit-log entry is written.
    """
    from .settlement_attribution import recompute_attribution_for_doc

    started = _now_iso()
    started_dt = datetime.now(timezone.utc)

    # Pick up only users with Tamara enabled.
    user_ids: set[str] = set()
    async for s in db.bnpl_settings.find(
        {"provider": "tamara", "enabled": True},
        {"_id": 0, "user_id": 1},
    ):
        uid = s.get("user_id")
        if uid:
            user_ids.add(uid)

    total_scanned = 0
    total_updated = 0
    per_user: Dict[str, Dict[str, int]] = {}
    for uid in user_ids:
        u_scanned = 0
        u_updated = 0
        try:
            async for d in db.payment_transactions.find(
                {"user_id": uid, "provider": "tamara"},
                {"_id": 0, "id": 1},
            ):
                u_scanned += 1
                r = await recompute_attribution_for_doc(
                    db, user_id=uid, txn_id=d.get("id"),
                )
                if r.get("updated"):
                    u_updated += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "iter-147 attribution sweep: user=%s failed: %s", uid, e,
            )
        per_user[uid] = {"scanned": u_scanned, "updated": u_updated}
        total_scanned += u_scanned
        total_updated += u_updated

    summary = {
        "id": str(uuid.uuid4()),
        "kind": "tamara_attribution_sweep",
        "started_at": started,
        "finished_at": _now_iso(),
        "duration_seconds": round(
            (datetime.now(timezone.utc) - started_dt).total_seconds(), 1,
        ),
        "users_processed": len(user_ids),
        "rows_scanned":    total_scanned,
        "rows_updated":    total_updated,
        # cap to keep the log doc small
        "per_user_sample": dict(list(per_user.items())[:25]),
    }
    try:
        await db.bnpl_auto_sync_runs.insert_one(summary)
        summary.pop("_id", None)
    except Exception as e:  # noqa: BLE001
        logger.warning("iter-147 attribution sweep: run-log insert failed: %s", e)
    return summary
