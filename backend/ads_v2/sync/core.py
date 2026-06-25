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


# iter-260 — Map adapter error codes to the connection-state vocabulary
# (`platform_check_status`). This is the SINGLE place that translates
# API errors into connection-state values, keeping match_status free
# of API concerns.
def _map_check_status(api_code: Optional[str]) -> str:
    """Translate an adapter `status.code` into PLATFORM_CHECK_STATUSES."""
    if not api_code:
        return "api_error"
    code = str(api_code).lower()
    if code in ("token_invalid", "token_expired", "unauthorized"):
        return "token_expired"
    if code in ("rate_limited", "too_many_requests"):
        return "rate_limited"
    if code in ("http_error", "network_error", "timeout"):
        return "last_check_failed"
    return "api_error"


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
    manual_value_native: Optional[float] = None,
) -> tuple[list[str], Optional[float], Optional[float], str, dict]:
    """Returns (anomaly_flags, drift_pct_vs_previous_sync,
    drift_pct_vs_manual, initial_review_status, drift_reason).

    Drift handling rules:
      • drift_pct is ONLY meaningful when there's something to compare to.
        - No previous sync → drift_pct_vs_previous_sync = None
        - No manual value  → drift_pct_vs_manual        = None
      • The displayed drift_pct (in ads_daily) MUST be None when there is
        no comparison anchor — UI shows "—" instead of misleading "0%".
      • drift_reason is a structured object explaining the most likely
        cause for any non-zero drift the merchant sees in reports.
    """
    flags: list[str] = []
    drift_prev: Optional[float] = None
    drift_manual: Optional[float] = None

    if prev_spend_native is not None and prev_spend_native > 0:
        delta = new_spend_native - prev_spend_native
        drift_prev = round(abs(delta / prev_spend_native) * 100.0, 2)

    if manual_value_native is not None and manual_value_native > 0:
        delta_m = new_spend_native - manual_value_native
        drift_manual = round(abs(delta_m / manual_value_native) * 100.0, 2)

    warn = float(review_settings.get("drift_warning_threshold_pct", 5.0))
    block = float(review_settings.get("drift_block_threshold_pct", 15.0))

    # Manual drift takes priority (it compares against ground truth)
    primary_drift = drift_manual if drift_manual is not None else drift_prev

    if primary_drift is not None:
        if primary_drift >= block:
            flags.append("drift_above_15pct")
        elif primary_drift >= warn:
            flags.append("drift_above_5pct")
    if hours_after_close > 24 and (drift_prev or 0) >= warn:
        flags.append("late_reporting")
    if manual_value_native and (drift_manual or 0) >= warn:
        flags.append("mismatch_vs_ads_manager")

    if not has_fx and new_spend_native > 0:
        flags.append("missing_fx")

    initial = "pending"
    if "missing_fx" in flags:
        initial = "held_needs_fx"
    elif "drift_above_15pct" in flags:
        initial = "held_anomaly"
    elif "drift_above_5pct" in flags or "late_reporting" in flags or "mismatch_vs_ads_manager" in flags:
        initial = "held_drift"

    # ── Structured drift_reason ────────────────────────────────────
    drift_reason: dict = {
        "has_manual_value":   manual_value_native is not None,
        "compared_against":   (
            "ads_manager_manual" if manual_value_native is not None
            else ("previous_sync" if prev_spend_native is not None else "none")),
        "hours_after_close":  round(hours_after_close, 1),
        "likely_causes":      [],
    }
    if primary_drift is None or primary_drift == 0:
        drift_reason["likely_causes"] = []
    else:
        causes: list[str] = []
        if hours_after_close < 24:
            causes.append("sync_before_close")  # day still ongoing
        if 24 <= hours_after_close <= 72:
            causes.append("late_reporting_window")  # Meta 24-72h settling
        if "mismatch_vs_ads_manager" in flags:
            causes.append("ads_manager_value_differs")
        if "late_reporting" in flags:
            causes.append("post_close_provider_update")
        if "missing_fx" in flags:
            causes.append("missing_fx_rate")
        if not causes:
            # Drift exists but we couldn't classify → unknown
            causes.append("unclassified_drift")
        drift_reason["likely_causes"] = causes

    return flags, drift_prev, drift_manual, initial, drift_reason


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
        # iter-260 — Architectural separation: API failures update
        # CONNECTION-STATE (`platform_check_status`) only. They MUST
        # NOT mutate accounting state (`match_status`) when valid SSOT
        # data already exists in ads_daily. `sync_failed` on
        # match_status is reserved for the explicit "no data exists"
        # case kept for legacy visibility (the report layer can also
        # show it for new rows that never received a successful sync).
        existing = await db.ads_daily.find_one(
            {"user_id": user_id, "account_id": account_id, "date": date_iso},
            {"_id": 0, "spend_native": 1, "match_status": 1},
        )
        has_valid_data = bool(
            existing and (float(existing.get("spend_native") or 0) > 0)
        )
        check_status = _map_check_status(status.get("code"))
        update_set: dict = {
            "platform_check_status":   check_status,
            "platform_check_error":    (status.get("code") or "")[:200],
            "platform_last_checked_at": finished_at,
        }
        if not has_valid_data:
            # No prior data → mark accounting state as no_data (the row
            # exists but holds zero spend). Legacy `sync_failed` value
            # is still allowed by the schema for backward-compat rows.
            update_set["match_status"] = "no_data"
        if existing:
            await db.ads_daily.update_one(
                {"user_id": user_id, "account_id": account_id, "date": date_iso},
                {"$set": update_set},
            )
        await _log_sync_event(db, user_id, account_id, "sync_failed",
                                date_iso, sync_run_id,
                                {"status": status,
                                 "platform_check_status": check_status,
                                 "preserved_existing_data": has_valid_data})
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
        {"_id": 0, "spend_native": 1, "review_status": 1,
         "platform_manual_value_native": 1, "platform_manual_value_sar": 1,
         "platform_manual_entered_at": 1, "platform_manual_entered_by": 1},
    )
    prev_spend = prev.get("spend_native") if prev else None
    manual_native = prev.get("platform_manual_value_native") if prev else None
    hours_after_close = _compute_hours_after_close(
        date_iso, account.get("timezone") or "Asia/Riyadh",
    )
    flags, drift_prev, drift_manual, computed_initial_status, drift_reason = _compute_anomaly_flags(
        new_spend_native=spend_native,
        prev_spend_native=prev_spend,
        has_fx=has_fx,
        review_settings=account.get("review_settings") or {},
        hours_after_close=hours_after_close,
        manual_value_native=manual_native,
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

    # The displayed drift_pct reflects the MOST MEANINGFUL comparison:
    #   • If merchant entered an Ads Manager value → drift vs manual
    #   • Else if we have a previous sync         → drift vs previous
    #   • Else → None (UI must show "—", not "0%")
    display_drift_pct = drift_manual if drift_manual is not None else drift_prev

    # Compute match_status (initial state right after sync).
    initial_match_status = _compute_match_status(
        drift_pct=display_drift_pct,
        review_settings=account.get("review_settings") or {},
        confidence=confidence,
        hours_after_close=hours_after_close,
        sync_failed=False,
        has_data=True,
    )

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
        "drift_pct":        display_drift_pct,
        "drift_pct_vs_previous_sync": drift_prev,
        "drift_pct_vs_manual": drift_manual,
        "drift_reason":     drift_reason,
        "anomaly_flags":    flags,
        "review_status":    final_review_status,
        "match_status":     initial_match_status,
        # iter-260 — Connection-state field, always set on successful sync.
        "platform_check_status":    "ok",
        "platform_check_error":     None,
        "platform_last_checked_at": finished_at,
        "last_synced_at":   finished_at,
        "last_recomputed_at": finished_at,
        "confidence":       confidence,
        "updated_at":       finished_at,
        "raw_excerpt":      fetched.get("raw_excerpt") or {},
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
        "drift_pct": display_drift_pct,
        "drift_pct_vs_previous_sync": drift_prev,
        "drift_pct_vs_manual": drift_manual,
        "drift_reason": drift_reason,
        "manual_value_native": manual_native,
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
        "drift_pct":       display_drift_pct,
        "drift_pct_vs_previous_sync": drift_prev,
        "drift_pct_vs_manual": drift_manual,
        "drift_reason":    drift_reason,
        "anomaly_flags":   flags,
        "review_status":   final_review_status,
        "api_status":      status.get("code"),
    }


# ─────────────────────────────────────────────────────────────────────
# Recompute drift when the merchant enters an Ads Manager value
# ─────────────────────────────────────────────────────────────────────
async def recompute_drift_for_day(
    db, user_id: str, account_id: str, date_iso: str,
    manual_value_native: float, actor_email: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Store the merchant-entered Ads Manager value and recompute drift,
    anomaly flags, and review status for that (account, date). Does NOT
    re-fetch from the provider — uses the existing spend_native.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    daily = await db.ads_daily.find_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {"_id": 0},
    )
    if not daily:
        return {"ok": False, "error": "no_ads_daily_row_for_date",
                "hint": "Run a sync for this date first."}

    account = await db.ads_accounts.find_one(
        {"user_id": user_id, "id": account_id, "soft_deleted": False},
        {"_id": 0, "review_settings": 1, "timezone": 1,
         "fx_to_sar": 1, "currency_native": 1, "bank_fee": 1},
    )
    if not account:
        return {"ok": False, "error": "account_not_found"}

    spend_native = float(daily.get("spend_native") or 0)
    has_fx = float(daily.get("fx_rate") or 0) > 0
    hours_after_close = _compute_hours_after_close(
        date_iso, account.get("timezone") or "Asia/Riyadh",
    )
    # prev_spend_native: keep the same drift_pct_vs_previous_sync
    flags, drift_prev, drift_manual, init_status, drift_reason = _compute_anomaly_flags(
        new_spend_native=spend_native,
        prev_spend_native=daily.get("spend_native"),
        has_fx=has_fx,
        review_settings=account.get("review_settings") or {},
        hours_after_close=hours_after_close,
        manual_value_native=manual_value_native,
    )
    # Keep old drift_prev (not relevant here — comparing to itself yields 0)
    drift_prev = daily.get("drift_pct_vs_previous_sync")

    manual_sar = round(manual_value_native * float(daily.get("fx_rate") or 1), 2)
    display = drift_manual if drift_manual is not None else drift_prev

    # Status: only update if previously open (pending / held_*)
    prev_status = daily.get("review_status") or "pending"
    if prev_status in ("approved", "rejected", "reopened"):
        final_status = prev_status
    else:
        final_status = init_status

    await db.ads_daily.update_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {"$set": {
            "platform_manual_value_native": manual_value_native,
            "platform_manual_value_sar":    manual_sar,
            "platform_manual_entered_at":   now_iso,
            "platform_manual_entered_by":   actor_email or "user",
            "platform_manual_note":         note or "",
            "drift_pct":                    display,
            "drift_pct_vs_manual":          drift_manual,
            "drift_reason":                 drift_reason,
            "anomaly_flags":                flags,
            "review_status":                final_status,
            "last_recomputed_at":           now_iso,
            "updated_at":                   now_iso,
        }},
    )

    await db.ads_sync_logs.insert_one({
        "id":            uuid.uuid4().hex,
        "user_id":       user_id,
        "account_id":    account_id,
        "date":          date_iso,
        "event":         "reconciliation_checked",
        "actor_email":   actor_email,
        "details":       {
            "manual_value_native":   manual_value_native,
            "manual_value_sar":      manual_sar,
            "drift_pct_vs_manual":   drift_manual,
            "anomaly_flags":         flags,
            "drift_reason":          drift_reason,
            "review_status_before":  prev_status,
            "review_status_after":   final_status,
            "note":                  note,
        },
        "at":            now_iso,
    })

    return {
        "ok":                    True,
        "account_id":            account_id,
        "date":                  date_iso,
        "spend_native":          spend_native,
        "manual_value_native":   manual_value_native,
        "manual_value_sar":      manual_sar,
        "drift_pct_vs_manual":   drift_manual,
        "drift_pct":             display,
        "drift_reason":          drift_reason,
        "anomaly_flags":         flags,
        "review_status":         final_status,
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



# ═══════════════════════════════════════════════════════════════════════
# Auto-Reconciliation — fetch current provider value WITHOUT touching the
# SSOT (`spend_native`). Stores the freshly-fetched figure in the shadow
# fields `platform_authoritative_*` so the merchant can SEE platform drift
# in real time, while the audit-trail `ads_daily` row remains stable until
# Phase 2 review/posting.
# ═══════════════════════════════════════════════════════════════════════
def _compute_match_status(
    drift_pct: Optional[float],
    review_settings: dict,
    confidence: str,
    hours_after_close: float,
    sync_failed: bool,
    has_data: bool,
) -> str:
    """One of: matched | pending_platform | drift_review | sync_failed | no_data.

    Priority order:
      1. sync_failed   → 🔴
      2. no_data       → ⚪ (never synced)
      3. drift > warn  → 🟠
      4. provisional + hours_after_close < 24 → 🟡
      5. otherwise     → 🟢
    """
    if sync_failed:
        return "sync_failed"
    if not has_data:
        return "no_data"
    warn = float(review_settings.get("drift_warning_threshold_pct", 5.0))
    if drift_pct is not None and drift_pct >= warn:
        return "drift_review"
    if confidence == "provisional" and hours_after_close < 24:
        return "pending_platform"
    return "matched"


async def auto_reconcile_for_day(
    db, user_id: str, account_id: str, date_iso: str,
    actor_email: Optional[str] = None,
) -> dict:
    """Re-query the provider API and store the result in shadow fields
    `platform_authoritative_*` on the existing ads_daily row. Does NOT
    modify `spend_native` (the SSOT). Recomputes drift + match_status.

    Returns: {ok, drift_pct_vs_platform, match_status, ...}
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    daily = await db.ads_daily.find_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {"_id": 0},
    )
    if not daily:
        return {"ok": False, "error": "no_ads_daily_row_for_date",
                "hint": "Run a sync for this date first."}

    account = await db.ads_accounts.find_one(
        {"user_id": user_id, "id": account_id, "soft_deleted": False},
    )
    if not account:
        return {"ok": False, "error": "account_not_found"}

    # ── Resolve token & call provider (READ-ONLY) ──
    token, token_status = await _resolve_access_token(db, account)
    if not token:
        # iter-260 — Token failure is CONNECTION state, not data state.
        # When the row already holds valid SSOT spend, the user's
        # accounting numbers are still trustworthy; only the latest
        # platform check failed.
        ssot_has_data = float(daily.get("spend_native") or 0) > 0
        check_status = _map_check_status(token_status.get("code"))
        update_set: dict = {
            "platform_check_status":   check_status,
            "platform_check_error":    token_status.get("code"),
            "platform_last_checked_at": now_iso,
            "updated_at": now_iso,
        }
        if not ssot_has_data:
            update_set["match_status"] = "no_data"
        await db.ads_daily.update_one(
            {"user_id": user_id, "account_id": account_id, "date": date_iso},
            {"$set": update_set},
        )
        return {"ok": False, "error": "no_token",
                "token_status": token_status,
                "platform_check_status": check_status,
                "preserved_existing_data": ssot_has_data,
                "match_status": (
                    daily.get("match_status") if ssot_has_data
                    else "no_data"
                )}

    fetched, status = await adapters.fetch_day(
        provider=account["provider"],
        access_token=token,
        external_account_id=account["external_account_id"],
        date_iso=date_iso,
        account_timezone=account.get("timezone") or "Asia/Riyadh",
    )
    if fetched is None:
        # iter-260 — Same architectural separation. Update connection
        # state only; do not touch accounting `match_status` when
        # valid SSOT data exists.
        ssot_has_data = float(daily.get("spend_native") or 0) > 0
        check_status = _map_check_status(status.get("code"))
        update_set = {
            "platform_check_status":   check_status,
            "platform_check_error":    (status.get("code") or "")[:200],
            "platform_last_checked_at": now_iso,
            "updated_at":              now_iso,
        }
        if not ssot_has_data:
            update_set["match_status"] = "no_data"
        await db.ads_daily.update_one(
            {"user_id": user_id, "account_id": account_id, "date": date_iso},
            {"$set": update_set},
        )
        await _log_sync_event(
            db, user_id, account_id, "reconciliation_checked", date_iso,
            uuid.uuid4().hex,
            {"result": "failed", "api_status": status.get("code"),
             "platform_check_status": check_status,
             "actor": actor_email,
             "preserved_existing_data": ssot_has_data},
        )
        return {"ok": False, "error": status,
                "platform_check_status": check_status,
                "preserved_existing_data": ssot_has_data,
                "match_status": (
                    daily.get("match_status") if ssot_has_data
                    else "no_data"
                )}

    # ── Compute platform-authoritative figures (shadow only) ──
    plat_native = float(fetched.get("spend_native") or 0)
    fx_rate = float(daily.get("fx_rate") or 1)
    plat_sar = round(plat_native * fx_rate, 2)
    ssot_native = float(daily.get("spend_native") or 0)
    diff_native = round(plat_native - ssot_native, 2)
    diff_sar = round(plat_sar - float(daily.get("spend_sar") or 0), 2)
    if ssot_native > 0:
        drift_platform = round(
            abs(diff_native) / ssot_native * 100.0, 2,
        )
    elif plat_native > 0:
        drift_platform = 100.0
    else:
        drift_platform = 0.0

    # Rebuild flags + reason (manual value still takes precedence if set)
    has_fx = fx_rate > 0
    hours_after_close = _compute_hours_after_close(
        date_iso, account.get("timezone") or "Asia/Riyadh",
    )
    manual_native = daily.get("platform_manual_value_native")
    # Use platform_authoritative as new comparison anchor if no manual entered
    flags, drift_prev, drift_manual, init_status, drift_reason = _compute_anomaly_flags(
        new_spend_native=ssot_native,
        prev_spend_native=plat_native,        # platform is the "ground truth"
        has_fx=has_fx,
        review_settings=account.get("review_settings") or {},
        hours_after_close=hours_after_close,
        manual_value_native=manual_native,
    )
    # The drift the user cares about is "stored vs current platform"
    display_drift = drift_manual if drift_manual is not None else drift_platform
    if display_drift == 0 and plat_native == 0 and ssot_native == 0:
        display_drift = None

    confidence = daily.get("confidence") or "provisional"
    match_status = _compute_match_status(
        drift_pct=display_drift,
        review_settings=account.get("review_settings") or {},
        confidence=confidence,
        hours_after_close=hours_after_close,
        sync_failed=False,
        has_data=True,
    )

    # Preserve approved/rejected decisions
    prev_status = daily.get("review_status") or "pending"
    final_review_status = (
        prev_status if prev_status in ("approved", "rejected", "reopened")
        else init_status
    )

    await db.ads_daily.update_one(
        {"user_id": user_id, "account_id": account_id, "date": date_iso},
        {"$set": {
            "platform_authoritative_native":   plat_native,
            "platform_authoritative_sar":      plat_sar,
            "platform_authoritative_currency": fetched.get("currency_native"),
            "platform_last_checked_at":        now_iso,
            "platform_check_error":            None,
            # iter-260 — Connection state explicitly OK on success.
            "platform_check_status":           "ok",
            "diff_native":                     diff_native,
            "diff_sar":                        diff_sar,
            "drift_pct_vs_platform":           drift_platform,
            "drift_pct":                       display_drift,
            "drift_reason":                    drift_reason,
            "anomaly_flags":                   flags,
            "review_status":                   final_review_status,
            "match_status":                    match_status,
            "last_recomputed_at":              now_iso,
            "updated_at":                      now_iso,
        }},
    )

    await _log_sync_event(
        db, user_id, account_id, "reconciliation_checked", date_iso,
        uuid.uuid4().hex,
        {
            "result":                   "ok",
            "actor":                    actor_email,
            "platform_native":          plat_native,
            "platform_sar":             plat_sar,
            "ssot_native":              ssot_native,
            "diff_native":              diff_native,
            "diff_sar":                 diff_sar,
            "drift_pct_vs_platform":    drift_platform,
            "match_status":             match_status,
        },
    )

    return {
        "ok":                       True,
        "account_id":               account_id,
        "date":                     date_iso,
        "ssot_spend_native":        ssot_native,
        "platform_authoritative_native": plat_native,
        "platform_authoritative_sar":    plat_sar,
        "diff_native":              diff_native,
        "diff_sar":                 diff_sar,
        "drift_pct_vs_platform":    drift_platform,
        "drift_pct":                display_drift,
        "drift_reason":             drift_reason,
        "anomaly_flags":            flags,
        "match_status":             match_status,
        "review_status":            final_review_status,
    }


async def auto_reconcile_user(
    db, user_id: str, dates: list[str],
    account_ids: Optional[list[str]] = None,
    actor_email: Optional[str] = None,
) -> dict:
    """Run auto-reconciliation for every sync-enabled account × date."""
    q: dict = {"user_id": user_id, "soft_deleted": False,
                "sync_enabled": True}
    if account_ids:
        q["id"] = {"$in": account_ids}
    accounts = [a async for a in db.ads_accounts.find(q, {"id": 1, "_id": 0})]
    results: list[dict] = []
    for a in accounts:
        for d in dates:
            r = await auto_reconcile_for_day(
                db, user_id, a["id"], d, actor_email=actor_email,
            )
            results.append(r)
    matched = sum(1 for r in results if r.get("match_status") == "matched")
    drift = sum(1 for r in results if r.get("match_status") == "drift_review")
    pending = sum(1 for r in results if r.get("match_status") == "pending_platform")
    failed = sum(1 for r in results if r.get("match_status") == "sync_failed")
    return {
        "accounts_processed": len(accounts),
        "dates":              dates,
        "checked_count":      len(results),
        "matched_count":      matched,
        "drift_count":        drift,
        "pending_count":      pending,
        "failed_count":       failed,
        "results":            results,
    }
