"""Iter-251 v12 — Ad-Spend Scheduler Diagnostics (Read-Only).

Pure observability for the iter-215 AM/PM window posting loop.
Answers the merchant's 3 forensic questions:

  1. Is the `_ad_spend_window_post_loop` actually running in Production?
     → Read `cron_runs` filtered by type ∈ {ad_spend_window_post,
       ad_spend_window_catchup} for the last N hours.

  2. WHY are all 486 counterparties being skipped every window?
     → Each heartbeat row carries `skipped_reasons` aggregated by
       reason (zero_amount, idempotent_duplicate, missing_ext_id,
       exception, …) so we can pinpoint the failure class.

  3. For THIS merchant specifically, what would the next AM/PM
     posting attempt actually do?
     → A dry-run preview that iterates the merchant's ad-account
       counterparties, computes `_cumulative_spend` and the would-be
       `amount`, and reports per-account which reason would block
       (or pass) the posting. NO `general_ledger` writes.

The endpoint additionally surfaces:
  • Selected snapchat accounts (`snapchat_ad_accounts.enabled=True`)
  • All ad_account counterparties with currency / bank_fee fields
  • All ads_currency_settings rows
  • Last 30 raw source rows per provider (meta_ads_daily,
    snapchat_account_daily) for the requested date.

Triggered via:
    GET /api/ad-spend-rca/scheduler-diagnostics?date=YYYY-MM-DD&hours_back=72
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, date as _date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query


def make_ad_spend_scheduler_diagnostics_router(db, current_user):
    router = APIRouter(prefix="/ad-spend-rca", tags=["ad-spend-rca"])

    @router.get("/scheduler-diagnostics")
    async def scheduler_diagnostics(
        date: str = Query(
            ..., description="Target date YYYY-MM-DD for the dry-run "
                              "preview (typically the date with missing "
                              "GL entries)"),
        hours_back: int = Query(
            72, ge=1, le=720,
            description="How many hours of heartbeat history to load"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        cutoff_iso = cutoff.isoformat()

        # ── 1. Heartbeat history from cron_runs ──────────────────────
        heartbeats: list[dict] = []
        async for r in db.cron_runs.find(
            {"type": {"$in": [
                "ad_spend_window_post",
                "ad_spend_window_catchup",
                "ad_spend_window_post_loop_start",
            ]},
             "ran_at": {"$gte": cutoff_iso}},
            {"_id": 0},
        ).sort("ran_at", -1).limit(300):
            heartbeats.append(r)

        # Roll up summary across heartbeats
        loop_start_seen = any(
            h.get("type") == "ad_spend_window_post_loop_start"
            for h in heartbeats)
        last_window_post = next(
            (h for h in heartbeats
             if h.get("type") == "ad_spend_window_post"),
            None)
        last_catchup = next(
            (h for h in heartbeats
             if h.get("type") == "ad_spend_window_catchup"),
            None)
        # Aggregate skip-reason histogram across all heartbeats
        skip_reasons: dict[str, int] = {}
        for h in heartbeats:
            for reason, count in (h.get("skipped_reasons") or {}).items():
                skip_reasons[reason] = skip_reasons.get(reason, 0) + count
        posted_totals = sum(int(h.get("posted_count") or 0)
                             for h in heartbeats
                             if h.get("type") == "ad_spend_window_post")
        skipped_totals = sum(int(h.get("skipped_count") or 0)
                              for h in heartbeats
                              if h.get("type") == "ad_spend_window_post")

        # ── 2. Counterparties (ad_account) for THIS user ─────────────
        cps: list[dict] = []
        async for cp in db.counterparties.find(
            {"user_id": uid, "kind": "ad_account"},
            {"_id": 0},
        ):
            cps.append({
                "id":                 cp.get("id"),
                "name":                cp.get("name"),
                "ad_provider":         cp.get("ad_provider"),
                "external_account_id": cp.get("external_account_id"),
                "currency":            cp.get("currency"),
                "ad_account_currency": cp.get("ad_account_currency"),
                "bank_fee_enabled":    cp.get("bank_fee_enabled"),
                "bank_fee_rate":       cp.get("bank_fee_rate"),
                "sync_via":            cp.get("sync_via"),
                "debt_mode":           cp.get("debt_mode"),
                "is_active":           cp.get("is_active"),
                "last_auto_sync_date": cp.get("last_auto_sync_date"),
                "last_auto_sync_at":   cp.get("last_auto_sync_at"),
                "balance":             cp.get("balance"),
            })

        # ── 3. Selected Snapchat ad accounts ─────────────────────────
        selected_snap: list[dict] = []
        async for r in db.snapchat_ad_accounts.find(
            {"user_id": uid}, {"_id": 0, "user_id": 0},
        ).sort("name", 1):
            selected_snap.append(r)

        # ── 4. ads_currency_settings ─────────────────────────────────
        currency_settings: list[dict] = []
        async for r in db.ads_currency_settings.find(
            {"user_id": uid}, {"_id": 0},
        ):
            currency_settings.append(r)

        # ── 5. Dry-run preview for the requested date ────────────────
        #     Simulates _process_account_for_window for AM+PM windows
        #     WITHOUT calling post_txn_group.
        from ad_account_routes import (
            HALFHOUR_SYNC_PROVIDERS, _fetch_daily_spend,
        )
        from ledger_core import compute_balance

        dry_run: list[dict] = []
        for cp in cps:
            ad_provider = cp.get("ad_provider") or ""
            sync_via = cp.get("sync_via") or ""
            ext_id = (cp.get("external_account_id") or "").strip() or None
            blockers: list[str] = []

            if ad_provider not in HALFHOUR_SYNC_PROVIDERS:
                blockers.append(
                    f"ad_provider='{ad_provider}' not in "
                    f"HALFHOUR_SYNC_PROVIDERS")
            if sync_via == "make_com":
                blockers.append("sync_via='make_com' opts out")
            if ad_provider in ("snapchat", "meta") and not ext_id:
                blockers.append("missing external_account_id")

            if blockers:
                dry_run.append({
                    "cp_id":       cp["id"],
                    "name":         cp["name"],
                    "ad_provider":  ad_provider,
                    "blockers":     blockers,
                    "cumulative_spend":        None,
                    "would_post_AM_amount":    None,
                    "would_post_PM_amount":    None,
                    "raw_source_rows":         0,
                })
                continue

            rows, source_collection = await _fetch_daily_spend(
                db, uid, ad_provider, ext_id, date, date,
            )
            total = round(sum(float(r.get("spend") or 0)
                                for r in rows), 2)

            # Compute what's already posted for AM/PM on this date
            # (read directly from general_ledger).
            am_posted = await _sum_posted_amount(
                db, uid, cp["id"], date, "AM_00_12")
            pm_posted = await _sum_posted_amount(
                db, uid, cp["id"], date, "PM_12_24")

            am_amount = total
            pm_amount = max(0.0, round(total - am_posted, 2))

            am_skip_reason = None
            pm_skip_reason = None
            if total <= 0:
                am_skip_reason = "zero_amount (no source rows for date)"
                pm_skip_reason = "zero_amount (no source rows for date)"
            else:
                if am_posted >= total and am_posted > 0:
                    am_skip_reason = (
                        "would-be amount = 0 because AM already "
                        "posted full_total")
                if am_amount > 0 and am_posted > 0:
                    am_skip_reason = (
                        "idempotent_duplicate (AM already posted "
                        f"{am_posted})")
                if pm_amount <= 0:
                    pm_skip_reason = (
                        "zero_amount (full_total - am_posted = 0)")

            dry_run.append({
                "cp_id":                   cp["id"],
                "name":                     cp["name"],
                "ad_provider":              ad_provider,
                "external_account_id":      ext_id,
                "blockers":                 [],
                "source_collection":        source_collection,
                "raw_source_rows":          len(rows),
                "cumulative_spend":         total,
                "am_already_posted_sar":    am_posted,
                "pm_already_posted_sar":    pm_posted,
                "would_post_AM_amount":     am_amount,
                "would_post_PM_amount":     pm_amount,
                "am_skip_reason_if_any":    am_skip_reason,
                "pm_skip_reason_if_any":    pm_skip_reason,
            })

        # ── 6. Raw source row sample for the requested date ──────────
        raw_samples: dict[str, list] = {}
        for col in ("meta_ads_daily", "snapchat_account_daily",
                     "snapchat_ads_daily"):
            sample = []
            async for r in db[col].find(
                {"user_id": uid, "date": date}, {"_id": 0},
            ).limit(20):
                sample.append(r)
            raw_samples[col] = sample

        return {
            "date":              date,
            "hours_back":        hours_back,
            "now_utc":           datetime.now(timezone.utc).isoformat(),
            "scheduler_status": {
                "loop_start_event_seen_in_window": loop_start_seen,
                "heartbeats_in_window":            len(heartbeats),
                "last_window_post":                last_window_post,
                "last_catchup":                    last_catchup,
                "aggregate_posted_count":          posted_totals,
                "aggregate_skipped_count":         skipped_totals,
                "aggregate_skip_reasons":          skip_reasons,
                "recent_heartbeats":               heartbeats[:25],
            },
            "counterparties":    cps,
            "selected_snapchat_accounts": {
                "rows":              selected_snap,
                "count":             len(selected_snap),
                "enabled_count":     sum(
                    1 for r in selected_snap if r.get("enabled")),
            },
            "ads_currency_settings": currency_settings,
            "dry_run_preview":   dry_run,
            "raw_source_samples": raw_samples,
            "note": (
                "Read-only diagnostics. dry_run_preview simulates "
                "what `run_window_post` would do today WITHOUT "
                "writing to general_ledger. Use this to identify "
                "exactly why the 486 skips are happening."
            ),
        }

    async def _sum_posted_amount(
        db, user_id: str, cp_id: str, date_iso: str,
        period: str,
    ) -> float:
        pipeline = [
            {"$match": {
                "user_id": user_id, "status": "posted",
                "side": "debit",
                "entity_type": "expense",
                "entity_id": "advertising",
                "metadata.ad_account_id": cp_id,
                "metadata.spend_date": date_iso,
                "metadata.window_period": period,
            }},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]
        docs = await db.general_ledger.aggregate(pipeline).to_list(1)
        return float(docs[0]["total"]) if docs else 0.0

    return router
