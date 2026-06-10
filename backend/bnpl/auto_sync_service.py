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
            # Tamara's backfill uses date_from / date_to params.
            since_day = since_iso[:10]
            today = datetime.now(timezone.utc).date().isoformat()
            res = await backfill_tamara(
                db, user_id, date_from=since_day, date_to=today,
            )
        else:
            return {"ok": False, "provider": provider, "error": f"unknown provider {provider}"}

        ok = bool(res.get("ok", True))
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
