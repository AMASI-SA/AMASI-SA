"""Ads V2 — Phase 1 sync core (idempotent, no GL writes).

Public entry points:
  • run_sync_for_account(db, user_id, account_id, date_iso) → one day
  • run_sync_user(db, user_id, dates: list[str]) → all enabled accounts
  • run_sync_global(db, dates: list[str]) → every user with v2 enabled

Idempotency:
  • A unique idempotency_key on `ads_daily`:
      f"ads_v2:{user_id}:{account_id}:{date_iso}"
  • Re-syncing the same (account, date) UPDATES the row, never duplicates.
  • Sync runs are logged in `ads_sync_logs` as one row per sync_run.

Reconciliation (Phase 1 — embedded, no separate collection):
  • Each sync recomputes `drift_pct` vs the previous spend_daily value
    (post-close drift detection).
  • Anomaly flags are stored on the same `ads_daily` row.
  • Initial review_status is computed from the flags but NEVER posts to
    GL — that is Phase 2's responsibility.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import adapters

logger = logging.getLogger("ads_v2.sync.core")


# ─────────────────────────────────────────────────────────────────────
# Token resolver — strictly read-only on V1 collections
# ─────────────────────────────────────────────────────────────────────
async def _resolve_access_token(db, account: dict) -> tuple[Optional[str], dict]:
    """Read the V1 token doc to get the latest access_token.

    Never mutates V1. Returns (token, status_dict).
    """
    ref = account.get("v1_token_ref") or {}
    coll = ref.get("collection")
    if not coll:
        return None, {"code": "no_v1_ref"}
    try:
        v1_doc = await db[coll].find_one(
            {"user_id": ref.get("user_id") or account["user_id"]},
            {"_id": 0, "access_token": 1, "expires_at": 1, "updated_at": 1},
        )
    except Exception as exc:
        return None, {"code": "v1_read_error", "message": str(exc)[:200]}
    if not v1_doc:
        return None, {"code": "v1_doc_missing"}
    token = v1_doc.get("access_token")
    if not token:
        return None, {"code": "no_access_token"}
    return token, {"code": "ok",
                    "v1_updated_at": v1_doc.get("updated_at"),
                    "expires_at": v1_doc.get("expires_at")}


# ─────────────────────────────────────────────────────────────────────
# FX & bank fee computation
# ─────────────────────────────────────────────────────────────────────
def _compute_fx(account: dict, currency_native: str) -> tuple[float, str]:
    """Resolve fx_rate from account settings.

    No 3.75 fallback hardcoded in business logic. If the account has no
    fx_to_sar.rate, returns (0.0, 'missing') which downstream marks as
    `held_needs_fx`.
    """
    if currency_native == "SAR":
        return 1.0, "implicit_sar"
    fx = account.get("fx_to_sar") or {}
    rate = float(fx.get("rate") or 0)
    if rate <= 0:
        return 0.0, "missing"
    return rate, fx.get("mode") or "manual"


def _compute_bank_fee(account: dict, spend_sar: float) -> tuple[float, dict]:
    bf = account.get("bank_fee") or {}
    if not bf.get("enabled"):
        return 0.0, {"method": "none"}
    method = bf.get("method") or "none"
    pct = float(bf.get("rate_pct") or 0)
    flat = float(bf.get("flat_amount_sar") or 0)
    pct_amount = round(spend_sar * pct, 4)
    if method == "pct":
        return round(pct_amount, 2), {
            "method": "pct", "rate_pct": pct,
            "rate_pct_amount": pct_amount, "flat_amount": 0.0,
            "total": round(pct_amount, 2),
        }
    if method == "flat":
        return round(flat, 2), {
            "method": "flat", "rate_pct": 0.0,
            "rate_pct_amount": 0.0, "flat_amount": flat,
            "total": round(flat, 2),
        }
    if method == "pct_plus_flat":
        total = round(pct_amount + flat, 2)
        return total, {
            "method": "pct_plus_flat", "rate_pct": pct,
            "rate_pct_amount": pct_amount, "flat_amount": flat,
            "total": total,
        }
    return 0.0, {"method": "none"}


# ─────────────────────────────────────────────────────────────────────
# Reconciliation — embedded in sync (Phase 1)
# ─────────────────────────────────────────────────────────────────────
def _compute_anomaly_flags(
    new_spend_native: float,
    prev_spend_native: Optional[float],
    has_fx: bool,
    review_settings: dict,
    hours_after_close: float,
) -> tuple[list[str], float, str]:
    """Returns (anomaly_flags, drift_pct, initial_review_status)."""
    flags: list[str] = []
    drift_pct = 0.0

    if prev_spend_native is not None and prev_spend_native > 0:
        delta = new_spend_native - prev_spend_native
        drift_pct = abs(delta / prev_spend_native) * 100.0
        warn = float(review_settings.get(
            "drift_warning_threshold_pct", 5.0))
        block = float(review_settings.get(
            "drift_block_threshold_pct", 15.0))
        if drift_pct >= block:
            flags.append("drift_above_15pct")
        elif drift_pct >= warn:
            flags.append("drift_above_5pct")
        if hours_after_close > 24 and drift_pct >= warn:
            flags.append("late_reporting")

    if not has_fx and new_spend_native > 0:
        flags.append("missing_fx")

    # Initial review status
    initial = "pending"
    if "missing_fx" in flags:
        initial = "held_needs_fx"
    elif "drift_above_15pct" in flags:
        initial = "held_anomaly"
    elif "drift_above_5pct" in flags or "late_reporting" in flags:
        initial = "held_drift"

    return flags, round(drift_pct, 2), initial


# ─────────────────────────────────────────────────────────────────────
# Single (account, date) sync
# ─────────────────────────────────────────────────────────────────────
async def run_sync_for_account(
    db, user_id: str, account_id: str, date_iso: str,
    actor: str = "system",
) -> dict:
    """Sync a single (account, date) and upsert into ads_daily.

    READ-ONLY GUARANTEES:
      • V1 collections untouched (only read once for access_token).
      • general_ledger untouched (no inserts of any kind).
    """
    started_at = datetime.now(timezone.utc).isoformat()
    sync_run_id = uuid.uuid4().hex

    # ── Load account ──
    account = await db.ads_accounts.find_one({
        "user_id": user_id, "id": account_id, "soft_deleted": False,
    })
    if not account:
        return {"ok": False, "error": "account_not_found",
                "sync_run_id": sync_run_id}

    if not account.get("sync_enabled"):
        return {"ok": False, "error": "sync_disabled",
                "sync_run_id": sync_run_id}

    # ── Get access token from V1 (READ-ONLY) ──
    token, token_status = await _resolve_access_token(db, account)
    if not token:
        await _record_token_issue(db, user_id, account_id,
                                    token_status.get("code") or "unknown",
                                    sync_run_id)
        await db.ads_accounts.update_one(
            {"id": account_id},
            {"$set": {"sync_status": "token_expired",
                      "sync_error_message": token_status.get("code"),
                      "last_sync_started_at": started_at,
                      "last_sync_finished_at": datetime.now(
                          timezone.utc).isoformat()}},
        )
        return {"ok": False, "error": "no_token",
                "token_status": token_status,
                "sync_run_id": sync_run_id}

    # ── Fetch from provider ──
    fetched, status = await adapters.fetch_day(
        provider=account["provider"],
        access_token=token,
        external_account_id=account["external_account_id"],
        date_iso=date_iso,
        account_timezone=account.get("timezone") or "Asia/Riyadh",
    )
    finished_at = datetime.now(timezone.utc).isoformat()

    if fetched is None:
        await _log_sync_event(db, user_id, account_id, "sync_failed",
                                date_iso, sync_run_id,
                                {"status": status})
        # If token_invalid bubble it up to account state
        if status.get("code") == "token_invalid":
            await db.ads_accounts.update_one(
                {"id": account_id},
                {"$set": {"sync_status": "unauthorized",
                          "sync_error_message": "token_invalid",
                          "last_sync_started_at": started_at,
                          "last_sync_finished_at": finished_at}},
            )
        else:
            await db.ads_accounts.update_one(
                {"id": account_id},
                {"$set": {"sync_status": "error",
                          "sync_error_message":
                              (status.get("code") or "")[:200],
                          "last_sync_started_at": started_at,
                          "last_sync_finished_at": finished_at}},
            )
        return {"ok": False, "error": status, "sync_run_id": sync_run_id}

    # ── Compute SAR + bank fee ──
    spend_native = float(fetched.get("spend_native") or 0)
    currency_native = (
        fetched.get("currency_native") or account.get("currency_native") or "SAR"
    )
    fx_rate, fx_source = _compute_fx(account, currency_native)
    has_fx = fx_rate > 0
    spend_sar = round(spend_native * fx_rate, 2) if has_fx else 0.0
    bank_fee_sar, bank_fee_breakdown = _compute_bank_fee(account, spend_sar)
    gross_sar = round(spend_sar + bank_fee_sar, 2)

    # ── Reconciliation: drift vs previous ads_daily row ──
    prev = await db.ads_daily.find_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {"_id": 0, "spend_native": 1, "review_status": 1},
    )
    prev_spend = prev.get("spend_native") if prev else None
    hours_after_close = _compute_hours_after_close(
        date_iso, account.get("timezone") or "Asia/Riyadh",
    )
    flags, drift_pct, computed_initial_status = _compute_anomaly_flags(
        new_spend_native=spend_native,
        prev_spend_native=prev_spend,
        has_fx=has_fx,
        review_settings=account.get("review_settings") or {},
        hours_after_close=hours_after_close,
    )

    # ── Decide final review_status ──
    # Once a row is approved/rejected/reopened the status sticks — only
    # `pending`/`held_*` rows get re-evaluated on each sync.
    final_review_status = computed_initial_status
    if prev and prev.get("review_status") in (
        "approved", "rejected", "reopened",
    ):
        final_review_status = prev["review_status"]

    confidence = "final" if _days_old(date_iso) >= 3 else "provisional"

    idempotency_key = f"ads_v2:{user_id}:{account_id}:{date_iso}"

    set_doc = {
        "user_id":          user_id,
        "account_id":       account_id,
        "provider":         account["provider"],
        "date":             date_iso,
        "spend_native":     spend_native,
        "currency_native":  currency_native,
        "impressions":      int(fetched.get("impressions") or 0),
        "clicks":           int(fetched.get("clicks") or 0),
        "purchases":        int(fetched.get("purchases") or 0),
        "fx_rate":          fx_rate,
        "fx_source":        fx_source,
        "spend_sar":        spend_sar,
        "bank_fee_sar":     bank_fee_sar,
        "bank_fee_breakdown": bank_fee_breakdown,
        "gross_sar":        gross_sar,
        "platform_reported_native": spend_native,
        "platform_reported_sar":    spend_sar,
        "platform_checked_at":      finished_at,
        "drift_pct":        drift_pct,
        "anomaly_flags":    flags,
        "review_status":    final_review_status,
        "last_synced_at":   finished_at,
        "last_recomputed_at": finished_at,
        "confidence":       confidence,
        "updated_at":       finished_at,
    }
    set_on_insert = {
        "id":                uuid.uuid4().hex,
        "idempotency_key":   idempotency_key,
        "ledger_txn_group_id": None,
        "ledger_posted_at":  None,
        "ledger_reversed":   False,
        "ledger_reversal_txn_group_id": None,
        "review_decided_at": None,
        "review_decided_by": None,
        "review_decision_note": None,
        "review_reopen_count": 0,
        "created_at":        finished_at,
    }

    await db.ads_daily.update_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {
            "$set": set_doc,
            "$setOnInsert": set_on_insert,
            "$inc": {"sources_count": 1},
        },
        upsert=True,
    )

    # ── Update account sync state ──
    await db.ads_accounts.update_one(
        {"id": account_id},
        {"$set": {
            "sync_status":            "active",
            "sync_error_message":     None,
            "last_sync_started_at":   started_at,
            "last_sync_finished_at":  finished_at,
            "last_synced_date":       date_iso,
        }},
    )

    # ── Log success ──
    await _log_sync_event(db, user_id, account_id, "sync_run", date_iso,
                            sync_run_id, {
        "spend_native": spend_native,
        "currency_native": currency_native,
        "spend_sar": spend_sar,
        "bank_fee_sar": bank_fee_sar,
        "gross_sar": gross_sar,
        "fx_rate": fx_rate,
        "fx_source": fx_source,
        "drift_pct": drift_pct,
        "anomaly_flags": flags,
        "review_status": final_review_status,
        "api_status": status.get("code"),
    })

    return {
        "ok":              True,
        "sync_run_id":     sync_run_id,
        "account_id":      account_id,
        "date":            date_iso,
        "spend_native":    spend_native,
        "spend_sar":       spend_sar,
        "gross_sar":       gross_sar,
        "drift_pct":       drift_pct,
        "anomaly_flags":   flags,
        "review_status":   final_review_status,
        "api_status":      status.get("code"),
    }


# ─────────────────────────────────────────────────────────────────────
# Multi-account / multi-date
# ─────────────────────────────────────────────────────────────────────
async def run_sync_user(
    db, user_id: str, dates: list[str],
    account_ids: Optional[list[str]] = None,
    actor: str = "manual",
) -> dict:
    q: dict = {"user_id": user_id, "soft_deleted": False,
                "sync_enabled": True}
    if account_ids:
        q["id"] = {"$in": account_ids}
    accounts = [a async for a in db.ads_accounts.find(q, {"id": 1, "_id": 0})]
    results: list[dict] = []
    for a in accounts:
        for d in dates:
            r = await run_sync_for_account(
                db, user_id, a["id"], d, actor=actor,
            )
            results.append(r)
    return {
        "accounts_processed": len(accounts),
        "dates":              dates,
        "ok_count":           sum(1 for r in results if r.get("ok")),
        "fail_count":         sum(1 for r in results if not r.get("ok")),
        "results":            results,
    }


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _days_old(date_iso: str) -> int:
    from datetime import datetime, date
    try:
        d = datetime.fromisoformat(date_iso).date()
    except Exception:
        return 0
    return (date.today() - d).days


def _compute_hours_after_close(date_iso: str, tz_name: str) -> float:
    from zoneinfo import ZoneInfo
    from datetime import datetime, timedelta as _td
    try:
        d = datetime.fromisoformat(date_iso).replace(tzinfo=ZoneInfo(tz_name))
        close = d + _td(days=1)
        now = datetime.now(ZoneInfo(tz_name))
        return max(0.0, (now - close).total_seconds() / 3600.0)
    except Exception:
        return 0.0


async def _log_sync_event(
    db, user_id: str, account_id: str, event: str,
    date_iso: str, sync_run_id: str, details: dict,
) -> None:
    await db.ads_sync_logs.insert_one({
        "id":             uuid.uuid4().hex,
        "user_id":        user_id,
        "account_id":     account_id,
        "date":           date_iso,
        "event":          event,
        "actor_user_id":  None,
        "actor_email":    None,
        "details":        {**details, "sync_run_id": sync_run_id},
        "at":             datetime.now(timezone.utc).isoformat(),
    })


async def _record_token_issue(
    db, user_id: str, account_id: str, reason: str, sync_run_id: str,
) -> None:
    await db.ads_sync_logs.insert_one({
        "id":           uuid.uuid4().hex,
        "user_id":      user_id,
        "account_id":   account_id,
        "event":        "token_expired" if "expired" in reason else "token_alert",
        "details":      {"reason": reason, "sync_run_id": sync_run_id},
        "at":           datetime.now(timezone.utc).isoformat(),
    })
