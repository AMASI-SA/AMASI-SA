"""Iter-215 — Twice-daily ad-spend posting (AM/PM windows).

Replaces the per-cron-delta posting introduced in Iter-205 with a
batched, deterministic scheme aligned with Meta's known ~40-minute
reporting lag.

Windows (Riyadh time)
=====================
AM_00_12             — spend that happened today 00:00 → 12:00.
                       Posted between 12:30-13:30 Riyadh, target_date
                       = today.
PM_12_24             — spend that happened yesterday 12:00 → 23:59.
                       Posted between 00:30-01:30 Riyadh, target_date
                       = yesterday.
PM_12_24_CORRECTION  — if yesterday's full-day total grew AFTER PM was
                       posted (late Meta conversions), the delta is
                       booked in the next AM cycle, target_date =
                       yesterday, period = "PM_12_24_CORRECTION:{seq}".

Idempotency key
===============
``ad_spend:{provider}:{ad_account_id}:{spend_date}:{period_key}``
where period_key ∈ {AM_00_12, PM_12_24, PM_12_24_CORRECTION:N}.

Stored in `metadata.idempotency_key` on every leg of the txn group.

Scope
=====
Snapchat + Meta only (HALFHOUR_SYNC_PROVIDERS in ad_account_routes).
TikTok / Make.com retain Iter-205 behaviour because their delivery
cadence is irregular.

The half-hour cron in ad_account_routes._run_sync_for_all keeps
updating the upstream *_account_daily tables; for HALFHOUR_SYNC_
PROVIDERS it no longer creates `general_ledger` entries. This module
is the sole authority on `general_ledger` postings for AM/PM-eligible
accounts.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date as _date, timedelta
from typing import Optional

from ad_account_routes import (
    HALFHOUR_SYNC_PROVIDERS,
    _fetch_daily_spend,
    _now,
)
from ledger_core import compute_balance, post_txn_group
from tz_utils import riyadh_now_aware

logger = logging.getLogger(__name__)


# Windows are deliberately wider than the user-spec (40-minute target)
# so a single missed cron tick doesn't drop a posting. Catch-up scan
# guarantees recovery beyond these bounds.
AM_WINDOW_HOUR_START = 12   # 12:30 onwards
AM_WINDOW_MINUTE_START = 30
AM_WINDOW_HOUR_END = 13     # up to 13:30
AM_WINDOW_MINUTE_END = 30

PM_WINDOW_HOUR_START = 0    # 00:30 onwards
PM_WINDOW_MINUTE_START = 30
PM_WINDOW_HOUR_END = 1      # up to 01:30
PM_WINDOW_MINUTE_END = 30

# Iter-215b — historical backfill is forbidden by merchant directive.
# The catch-up helper only fills CURRENT-DAY windows past their cutoff.
# The constant below is kept as documentation only; the value is no
# longer consulted.
CATCHUP_DAYS_BACK = 0

# Public period codes
PERIOD_AM = "AM_00_12"
PERIOD_PM = "PM_12_24"
PERIOD_PM_CORRECTION_PREFIX = "PM_12_24_CORRECTION"


def current_window() -> Optional[tuple[str, str]]:
    """Return ``(period, target_date_iso)`` if the current Riyadh
    wall-clock falls within one of the configured posting windows,
    else ``None``.

    Window resolution:
        12:30 ≤ now < 13:30 Riyadh → (PERIOD_AM, today)
        00:30 ≤ now < 01:30 Riyadh → (PERIOD_PM, yesterday)
        else                       → None
    """
    now = riyadh_now_aware()
    hh, mm = now.hour, now.minute
    minutes_now = hh * 60 + mm
    am_start = AM_WINDOW_HOUR_START * 60 + AM_WINDOW_MINUTE_START
    am_end = AM_WINDOW_HOUR_END * 60 + AM_WINDOW_MINUTE_END
    pm_start = PM_WINDOW_HOUR_START * 60 + PM_WINDOW_MINUTE_START
    pm_end = PM_WINDOW_HOUR_END * 60 + PM_WINDOW_MINUTE_END
    if am_start <= minutes_now < am_end:
        return (PERIOD_AM, now.date().isoformat())
    if pm_start <= minutes_now < pm_end:
        return (PERIOD_PM, (now.date() - timedelta(days=1)).isoformat())
    return None


async def _cumulative_spend(
    db, user_id: str, cp: dict, date_iso: str,
) -> tuple[float, Optional[str]]:
    """Return ``(cumulative_spend_for_date, source_collection)``.

    Uses the same `_fetch_daily_spend` helper as the cron so the
    safety guards (cross-account isolation via `external_account_id`)
    are honoured.
    """
    ext_id = (cp.get("external_account_id") or "").strip() or None
    rows, source = await _fetch_daily_spend(
        db, user_id, cp.get("ad_provider") or "",
        ext_id, date_iso, date_iso,
    )
    total = round(sum(float(r.get("spend") or 0) for r in rows), 2)
    return total, source


async def _already_posted(
    db, user_id: str, cp_id: str, date_iso: str,
    period_or_prefix: str, *, prefix: bool = False,
) -> float:
    """Sum the debit amounts already posted to `general_ledger`
    for this account/date/period (or all periods matching prefix).

    Used to compute deltas (PM = full_day − AM; correction = full_day
    − AM − PM − previous_corrections).
    """
    q: dict = {
        "user_id": user_id,
        "status": "posted",
        "side": "debit",
        "entity_type": "expense",
        "entity_id": "advertising",
        "metadata.ad_account_id": cp_id,
        "metadata.spend_date": date_iso,
        "metadata.iter": "iter215",
    }
    if prefix:
        # Match e.g. "PM_12_24_CORRECTION:1", "PM_12_24_CORRECTION:2"…
        q["metadata.window_period"] = {
            "$regex": f"^{period_or_prefix}",
        }
    else:
        q["metadata.window_period"] = period_or_prefix
    pipeline = [
        {"$match": q},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    docs = await db.general_ledger.aggregate(pipeline).to_list(1)
    return float(docs[0]["total"]) if docs else 0.0


async def _next_correction_seq(
    db, user_id: str, cp_id: str, date_iso: str,
) -> int:
    """Return the next correction sequence number for the given
    account/date. Starts at 1 and counts up monotonically so each
    correction has a unique idempotency key."""
    q = {
        "user_id": user_id,
        "metadata.ad_account_id": cp_id,
        "metadata.spend_date": date_iso,
        "metadata.window_period": {
            "$regex": f"^{PERIOD_PM_CORRECTION_PREFIX}:",
        },
    }
    n = await db.general_ledger.count_documents(q)
    # Each correction has multiple legs (one txn_group per correction).
    # The count is over legs, but every leg of one group shares the
    # same window_period. Easiest: count DISTINCT window_period values.
    distinct = await db.general_ledger.distinct(
        "metadata.window_period", q,
    )
    return len(distinct) + 1


async def _post_one_window(
    db, *, user_id: str, actor_name: str, cp: dict,
    target_date: str, period: str, amount: float,
    full_day_total: float, source_collection: Optional[str],
) -> dict:
    """Atomically post ONE window's spend as a balanced txn group.

    Pre-validates idempotency via the dedicated key. Returns
    ``{"ok": True, "txn_group_id": ..., "amount": amount}`` on
    success, ``{"ok": True, "skipped": True, "reason": ...}`` on
    no-op.
    """
    ad_provider = (cp.get("ad_provider") or "").strip() or "unknown"
    period_key = period  # may already include ":N" for corrections
    idem_key = (
        f"ad_spend:{ad_provider}:{cp['id']}:{target_date}:{period_key}"
    )
    existing = await db.general_ledger.find_one(
        {"user_id": user_id,
         "metadata.idempotency_key": idem_key,
         "status": "posted"},
        {"_id": 0, "txn_group_id": 1},
    )
    if existing:
        return {"ok": True, "skipped": True,
                "reason": "idempotent_duplicate",
                "txn_group_id": existing.get("txn_group_id")}

    amount = round(float(amount or 0), 2)
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount"}

    bal = await compute_balance(
        db, user_id=user_id, entity_type="ad_account",
        entity_id=cp["id"], sub_account="balance",
    )
    prepaid_live = max(0.0, round(float(bal.get("net_balance") or 0), 2))
    covered = round(min(amount, prepaid_live), 2)
    uncovered = round(amount - covered, 2)

    entries = [{
        "entity_type": "expense", "entity_id": "advertising",
        "side": "debit", "amount": amount,
        "entry_type": "expense_record",
        "notes": (
            f"مصروف إعلانات — {cp.get('name')} — نافذة {period_key} "
            f"بتاريخ {target_date}"
        ),
        "metadata": {"category": "advertising",
                     "ad_account_id": cp["id"],
                     "ad_account_name": cp.get("name")},
    }]
    if covered > 0:
        entries.append({
            "entity_type": "ad_account", "entity_id": cp["id"],
            "sub_account": "balance", "side": "credit",
            "amount": covered, "entry_type": "spend",
            "notes": (
                f"استهلاك رصيد مدفوع مسبقاً — {cp.get('name')} — "
                f"نافذة {period_key}"
            ),
        })
    if uncovered > 0:
        entries.append({
            "entity_type": "ad_account", "entity_id": cp["id"],
            "sub_account": "debt", "side": "credit",
            "amount": uncovered, "entry_type": "spend",
            "notes": (
                f"مديونية إعلانية جديدة — {cp.get('name')} — "
                f"نافذة {period_key}"
            ),
        })

    description = (
        f"صرف نافذة {period_key} — {cp.get('name')} — {target_date}"
    )
    group = await post_txn_group(
        db, user_id=user_id, actor_id=user_id, actor_name=actor_name,
        txn_type="ad_account_spend",
        notes=description,
        metadata={
            "ad_account_id": cp["id"],
            "ad_account_name": cp.get("name"),
            "ad_provider": ad_provider,
            "spend_date": target_date,
            "source": "ad_spend_window",
            "amount": amount,
            "covered": covered,
            "uncovered": uncovered,
            "idempotency_key": idem_key,
            "window_period": period_key,
            "posted_for_window": {
                "period": period_key,
                "target_date": target_date,
                "full_day_total_at_posting": full_day_total,
                "source_collection": source_collection,
            },
            "iter": "iter215",
        },
        entries=entries,
    )
    return {
        "ok": True, "skipped": False,
        "txn_group_id": group["txn_group_id"],
        "amount": amount, "covered": covered, "uncovered": uncovered,
        "idempotency_key": idem_key,
    }


async def _process_account_for_window(
    db, *, user_id: str, actor_name: str, cp: dict,
    target_date: str, period: str,
) -> dict:
    """Compute the amount this account owes to `period`/`target_date`
    and post it. Handles AM, PM, and PM_CORRECTION.
    """
    full_total, source = await _cumulative_spend(
        db, user_id, cp, target_date,
    )
    if period == PERIOD_AM:
        amount = full_total
        return await _post_one_window(
            db, user_id=user_id, actor_name=actor_name, cp=cp,
            target_date=target_date, period=PERIOD_AM,
            amount=amount, full_day_total=full_total,
            source_collection=source,
        )
    if period == PERIOD_PM:
        am_posted = await _already_posted(
            db, user_id, cp["id"], target_date, PERIOD_AM,
        )
        # PM amount = full day minus what AM already booked.
        # Negative values shouldn't happen; if the platform lowered
        # the total after AM was booked we clamp to 0 and rely on the
        # correction path on a future day.
        amount = max(0.0, round(full_total - am_posted, 2))
        return await _post_one_window(
            db, user_id=user_id, actor_name=actor_name, cp=cp,
            target_date=target_date, period=PERIOD_PM,
            amount=amount, full_day_total=full_total,
            source_collection=source,
        )
    if period == "AM_FOLLOWING_CORRECTION":
        # Called by the AM scheduler for YESTERDAY only. We check if
        # yesterday's full-day total has grown since PM was posted; if
        # so we book the delta as a correction.
        am_posted = await _already_posted(
            db, user_id, cp["id"], target_date, PERIOD_AM,
        )
        pm_posted = await _already_posted(
            db, user_id, cp["id"], target_date, PERIOD_PM,
        )
        prior_corr = await _already_posted(
            db, user_id, cp["id"], target_date,
            PERIOD_PM_CORRECTION_PREFIX, prefix=True,
        )
        delta = round(
            full_total - am_posted - pm_posted - prior_corr, 2,
        )
        if delta <= 0:
            return {"ok": True, "skipped": True,
                    "reason": "no_correction_needed"}
        seq = await _next_correction_seq(
            db, user_id, cp["id"], target_date,
        )
        return await _post_one_window(
            db, user_id=user_id, actor_name=actor_name, cp=cp,
            target_date=target_date,
            period=f"{PERIOD_PM_CORRECTION_PREFIX}:{seq}",
            amount=delta, full_day_total=full_total,
            source_collection=source,
        )
    raise ValueError(f"unknown period: {period}")


async def run_window_post(
    db, period: str, target_date: str,
    *, user_id: Optional[str] = None,
) -> dict:
    """Loop every Snap/Meta ad-account owned by `user_id` (or every
    user if None) and post `target_date`'s `period` amount.

    Returns ``{"posted": [...], "skipped": [...]}``. Safe to invoke
    repeatedly within a window — idempotency keys guarantee no
    double-posting.
    """
    q: dict = {
        "kind": "ad_account",
        "ad_provider": {"$in": list(HALFHOUR_SYNC_PROVIDERS)},
        "sync_via": {"$ne": "make_com"},
    }
    if user_id:
        q["user_id"] = user_id
    posted, skipped = [], []
    async for cp in db.counterparties.find(q, {"_id": 0}):
        try:
            res = await _process_account_for_window(
                db, user_id=cp["user_id"],
                actor_name="ad_spend_window",
                cp=cp, target_date=target_date, period=period,
            )
            if res.get("skipped"):
                skipped.append({"id": cp["id"], "name": cp.get("name"),
                                 "reason": res.get("reason")})
            else:
                posted.append({"id": cp["id"], "name": cp.get("name"),
                                "amount": res.get("amount"),
                                "txn_group_id": res.get("txn_group_id")})
        except Exception as e:
            logger.exception(
                "iter-215: window post failed for %s/%s: %s",
                cp.get("user_id"), cp.get("id"), e,
            )
            skipped.append({"id": cp["id"], "name": cp.get("name"),
                            "reason": "exception", "error": str(e)})
    return {
        "ran_at": _now(),
        "period": period,
        "target_date": target_date,
        "posted": posted,
        "skipped": skipped,
        "summary": {
            "total": len(posted) + len(skipped),
            "posted": len(posted),
            "skipped": len(skipped),
        },
    }


async def catch_up_window_posts(
    db, *, user_id: Optional[str] = None,
) -> dict:
    """Iter-215b — Fill ONLY the windows whose posting time has
    passed within the CURRENT Riyadh day, never further back.

    Per merchant directive (Feb 15 2026): historical backfill is
    forbidden. Iter-215 must produce entries strictly going forward
    from the deploy moment. This helper therefore handles, at most:

      • TODAY's AM_00_12 — if Riyadh time is past 12:30 and no row
        exists yet.
      • YESTERDAY's PM_12_24 — if Riyadh time is past 00:30 and no
        row exists yet (covers the case where the server was down
        during the PM window itself; PM is for yesterday's data so
        it's allowed to lag a few hours into today).
      • YESTERDAY's PM_12_24_CORRECTION — if late Meta data raised
        the full-day total after PM was booked.

    Anything earlier than the AM cutoff for today / PM cutoff for
    yesterday is *intentionally* skipped — the merchant would rather
    miss a posting than have the system invent historical entries.

    ``user_id`` (optional) scopes the scan to a single merchant —
    used by tests; the scheduler invokes the global form.
    """
    now = riyadh_now_aware()
    today = now.date()
    yest = today - timedelta(days=1)
    am_cutoff_minutes = AM_WINDOW_HOUR_START * 60 + AM_WINDOW_MINUTE_START
    pm_cutoff_minutes = PM_WINDOW_HOUR_START * 60 + PM_WINDOW_MINUTE_START
    current_minutes = now.hour * 60 + now.minute

    all_posted: list = []
    all_skipped: list = []

    # Today's AM — only if we're past the AM cutoff.
    if current_minutes >= am_cutoff_minutes:
        res = await run_window_post(
            db, PERIOD_AM, today.isoformat(), user_id=user_id,
        )
        all_posted += res["posted"]
        all_skipped += res["skipped"]

    # Yesterday's PM — only if we're past the PM cutoff (which is
    # always true any time we're past 00:30 of today). The window
    # idempotency key prevents double-posting if PM already ran.
    if current_minutes >= pm_cutoff_minutes:
        res_pm = await run_window_post(
            db, PERIOD_PM, yest.isoformat(), user_id=user_id,
        )
        all_posted += res_pm["posted"]
        all_skipped += res_pm["skipped"]

    # Yesterday's correction — only after the next AM cutoff (so we
    # don't write CORRECTION before yesterday's PM is fully closed).
    if current_minutes >= am_cutoff_minutes:
        res_corr = await run_window_post(
            db, "AM_FOLLOWING_CORRECTION", yest.isoformat(),
            user_id=user_id,
        )
        all_posted += res_corr["posted"]
        all_skipped += res_corr["skipped"]

    return {
        "ran_at": _now(),
        "scope": "current_day_only",
        "posted": all_posted,
        "skipped": all_skipped,
        "summary": {
            "posted": len(all_posted),
            "skipped": len(all_skipped),
        },
    }


__all__ = [
    "PERIOD_AM",
    "PERIOD_PM",
    "PERIOD_PM_CORRECTION_PREFIX",
    "current_window",
    "run_window_post",
    "catch_up_window_posts",
]
