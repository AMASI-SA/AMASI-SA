"""Ads V2 — Settings data layer (Phase 0).

Provides:
  • `get_settings_snapshot(db, user_id)` — full single-payload read for
    the /ads-v2/settings page (accounts + recent activity).
  • `create_or_link_account(db, user_id, payload)` — adds a row to
    ads_accounts (from a discovery selection), records a sync_log
    `account_created` event.
  • `update_account(db, user_id, account_id, patch)` — partial update
    on ads_accounts (FX, bank_fee, review_settings, sync_enabled,
    display_name, timezone), logged.
  • `soft_delete_account(db, user_id, account_id)` — sets soft_deleted.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import (
    AdsAccount, AdsSyncLog, BankFee, FxToSar, ReviewSettings,
    V1TokenRef, utc_now_iso,
)

logger = logging.getLogger(__name__)

ALLOWED_PATCH_FIELDS = {
    "display_name", "currency_native", "timezone",
    "organization_id", "organization_name",
    "fx_to_sar", "bank_fee", "review_settings",
    "sync_enabled", "sync_status",
}


async def get_settings_snapshot(db, user_id: str) -> dict:
    """Single-payload snapshot for the settings page.

    Each account row is enriched with `_status` containing the 3-tier
    status (token / connection / sync_run) + `_status_reason` carrying
    the actual machine-readable cause from the latest activity, so the
    UI never has to show a bare "خطأ".
    """
    # Accounts (active only by default; UI can opt to show soft_deleted)
    accounts: list[dict] = []
    async for doc in db.ads_accounts.find(
        {"user_id": user_id, "soft_deleted": False}, {"_id": 0},
    ).sort("provider", 1):
        accounts.append(doc)

    # Pre-fetch last 50 events per account so we can summarize quickly
    if accounts:
        ids = [a["id"] for a in accounts]
        recent_by_acct: dict[str, list[dict]] = {a_id: [] for a_id in ids}
        async for ev in db.ads_sync_logs.find(
            {"user_id": user_id, "account_id": {"$in": ids}}, {"_id": 0},
        ).sort("at", -1).limit(500):
            lst = recent_by_acct.get(ev["account_id"])
            if lst is not None and len(lst) < 5:
                lst.append(ev)
        # Days-with-data count per account (last 30 days)
        from datetime import date, timedelta as _td
        cutoff = (date.today() - _td(days=30)).isoformat()
        for acct in accounts:
            count = await db.ads_daily.count_documents({
                "user_id": user_id, "account_id": acct["id"],
                "date": {"$gte": cutoff},
            })
            acct["_days_with_data_30d"] = count
            acct["_status"] = _compute_account_status(
                acct, recent_by_acct.get(acct["id"]) or [], count,
            )

    # Last 10 events from sync_logs (for diagnostic strip in UI)
    activity: list[dict] = []
    async for ev in db.ads_sync_logs.find(
        {"user_id": user_id}, {"_id": 0},
    ).sort("at", -1).limit(10):
        activity.append(ev)

    return {
        "accounts": accounts,
        "recent_activity": activity,
        "stats": {
            "accounts_total":  len(accounts),
            "accounts_active": sum(
                1 for a in accounts
                if a.get("sync_enabled") and a.get("sync_status") == "active"
            ),
            "accounts_discovered": sum(
                1 for a in accounts if a.get("sync_status") == "discovered"
            ),
        },
        "_meta": {
            "source_layer": "ads_v2.data_layer.settings.get_settings_snapshot",
            "ssot": "ads_accounts + ads_sync_logs + ads_daily",
            "computed_at": utc_now_iso(),
        },
    }


def _compute_account_status(
    account: dict, recent_events: list[dict], days_with_data_30d: int,
) -> dict:
    """Return a 3-tier status object + structured `reason` for the row.

    {
      token:      ok | expired | needs_relink | missing
      connection: connected | unreachable | api_error | unknown
      sync_run:   synced | awaiting_first | no_data | last_failed | disabled
      reason:     specific machine-readable code (see UI translation)
    }
    """
    # ── Token tier ───────────────────────────────────────────────
    ref = account.get("v1_token_ref") or {}
    if not ref.get("collection"):
        token_status = "missing"
    else:
        # we only have last_token_check_status on the ref + the last
        # sync_log unauthorized events — combine them.
        last_check = ref.get("last_token_check_status")
        recent_unauth = next(
            (e for e in recent_events
             if (e.get("details") or {}).get("api_status") == "token_invalid"
             or e.get("event") == "token_expired"),
            None,
        )
        if recent_unauth and recent_unauth.get("event") == "token_expired":
            token_status = "expired"
        elif recent_unauth:
            token_status = "needs_relink"
        elif last_check == "missing":
            token_status = "missing"
        elif last_check == "expired":
            token_status = "expired"
        else:
            token_status = "ok"

    # ── Connection tier ──────────────────────────────────────────
    # Look at the latest sync_run / sync_failed / reconciliation_checked
    last_call = next(
        (e for e in recent_events
         if e.get("event") in (
             "sync_run", "sync_failed", "reconciliation_checked",
         )),
        None,
    )
    if not last_call:
        connection_status = "unknown"
        connection_reason = "no_call_yet"
    else:
        api_status = (last_call.get("details") or {}).get("api_status")
        result = (last_call.get("details") or {}).get("result")
        if api_status == "ok" or result == "ok":
            connection_status = "connected"
            connection_reason = "ok"
        elif api_status == "rate_limited":
            connection_status = "api_error"
            connection_reason = "api_rate_limit"
        elif api_status == "not_found":
            connection_status = "api_error"
            connection_reason = "account_not_found"
        elif api_status == "token_invalid":
            connection_status = "api_error"
            connection_reason = "token_no_access_to_account"
        elif api_status == "http_error":
            connection_status = "api_error"
            connection_reason = "api_http_error"
        elif api_status == "exception":
            connection_status = "unreachable"
            connection_reason = "network_or_timeout"
        elif api_status == "empty":
            connection_status = "connected"
            connection_reason = "no_data_for_date"
        else:
            connection_status = "unknown"
            connection_reason = api_status or "unknown"

    # ── Sync-run tier ────────────────────────────────────────────
    if not account.get("sync_enabled"):
        sync_run_status = "disabled"
    elif account.get("sync_status") == "error" or account.get("sync_status") == "unauthorized":
        sync_run_status = "last_failed"
    elif not account.get("last_synced_date"):
        sync_run_status = "awaiting_first"
    elif days_with_data_30d == 0:
        sync_run_status = "no_data"
    else:
        sync_run_status = "synced"

    # ── Aggregate primary reason ─────────────────────────────────
    # Priority: token issues > connection errors > sync state
    if token_status in ("expired", "needs_relink", "missing"):
        primary_reason = f"token_{token_status}"
    elif connection_status in ("api_error", "unreachable"):
        primary_reason = connection_reason
    elif sync_run_status == "awaiting_first":
        primary_reason = "awaiting_first_sync"
    elif sync_run_status == "no_data":
        primary_reason = "no_data_for_account"
    elif sync_run_status == "last_failed":
        primary_reason = "last_sync_failed"
    elif sync_run_status == "disabled":
        primary_reason = "sync_disabled"
    else:
        primary_reason = "ok"

    return {
        "token":              token_status,
        "connection":         connection_status,
        "connection_reason":  connection_reason,
        "sync_run":           sync_run_status,
        "days_with_data_30d": days_with_data_30d,
        "reason":             primary_reason,
        "last_sync_finished_at": account.get("last_sync_finished_at"),
        "last_sync_error":    account.get("sync_error_message"),
    }


# ── Live diagnostics — pings the provider's API (READ-ONLY) ──────────
async def diagnose_account(
    db, user_id: str, account_id: str,
) -> dict:
    """Run a comprehensive read-only diagnostic against one account.

    Returns a dict with:
      • token_check       — V1 token doc presence
      • api_probe         — a fresh fetch_day call against yesterday
      • stats             — counts from ads_daily + ads_sync_logs
      • last_events       — last 10 events for this account
      • status            — same 3-tier object as snapshot, recomputed
    """
    from ..sync import adapters
    from datetime import date, timedelta

    account = await db.ads_accounts.find_one(
        {"user_id": user_id, "id": account_id, "soft_deleted": False},
        {"_id": 0},
    )
    if not account:
        return {"ok": False, "error": "account_not_found"}

    # ── Token check (read V1 doc only) ──
    token_check = await check_v1_token_health(db, user_id, account)

    # ── Live API probe with yesterday's date ──
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    api_probe: dict
    fetched_native: Optional[float] = None
    if token_check.get("ok"):
        ref = account.get("v1_token_ref") or {}
        v1_doc = await db[ref["collection"]].find_one(
            {"user_id": ref.get("user_id") or user_id},
            {"_id": 0, "access_token": 1},
        )
        access_token = (v1_doc or {}).get("access_token")
        fetched, status = await adapters.fetch_day(
            provider=account["provider"],
            access_token=access_token,
            external_account_id=account["external_account_id"],
            date_iso=yesterday,
            account_timezone=account.get("timezone") or "Asia/Riyadh",
        )
        api_probe = {
            "called_at":   utc_now_iso(),
            "date_tested": yesterday,
            "status":      status,
            "ok":          fetched is not None,
        }
        if fetched is not None:
            fetched_native = float(fetched.get("spend_native") or 0)
            api_probe["fetched_spend_native"] = fetched_native
            api_probe["currency_native"] = fetched.get("currency_native")
    else:
        api_probe = {
            "called_at":   utc_now_iso(),
            "date_tested": yesterday,
            "status":      {"code": "skipped_no_token"},
            "ok":          False,
        }

    # ── Stats from ads_daily ──
    cutoff_30 = (date.today() - timedelta(days=30)).isoformat()
    days_30 = await db.ads_daily.count_documents({
        "user_id": user_id, "account_id": account_id,
        "date": {"$gte": cutoff_30},
    })
    days_with_spend = await db.ads_daily.count_documents({
        "user_id": user_id, "account_id": account_id,
        "date": {"$gte": cutoff_30}, "spend_native": {"$gt": 0},
    })
    total_records = await db.ads_daily.count_documents({
        "user_id": user_id, "account_id": account_id,
    })

    # ── Last events ──
    last_events: list[dict] = []
    async for ev in db.ads_sync_logs.find(
        {"user_id": user_id, "account_id": account_id}, {"_id": 0},
    ).sort("at", -1).limit(10):
        last_events.append(ev)

    # ── Recompute 3-tier status with the live result mixed in ──
    status = _compute_account_status(
        account, last_events, days_30,
    )
    # Override connection based on the LIVE probe
    if api_probe["ok"]:
        status["connection"] = "connected"
        status["connection_reason"] = (
            "no_data_for_date" if (fetched_native or 0) == 0 else "ok"
        )
    elif api_probe["status"].get("code") == "rate_limited":
        status["connection"] = "api_error"
        status["connection_reason"] = "api_rate_limit"
    elif api_probe["status"].get("code") == "token_invalid":
        status["connection"] = "api_error"
        status["connection_reason"] = "token_no_access_to_account"
        status["token"] = "needs_relink"
    elif api_probe["status"].get("code") == "not_found":
        status["connection"] = "api_error"
        status["connection_reason"] = "account_not_found"
    elif api_probe["status"].get("code") == "exception":
        status["connection"] = "unreachable"
        status["connection_reason"] = "network_or_timeout"

    return {
        "ok":              True,
        "account_id":      account_id,
        "provider":        account["provider"],
        "external_account_id": account["external_account_id"],
        "display_name":    account.get("display_name"),
        "status":          status,
        "token_check":     token_check,
        "api_probe":       api_probe,
        "stats": {
            "days_in_last_30d":  days_30,
            "days_with_spend":   days_with_spend,
            "total_daily_rows":  total_records,
            "last_synced_date":  account.get("last_synced_date"),
            "last_sync_started_at":  account.get("last_sync_started_at"),
            "last_sync_finished_at": account.get("last_sync_finished_at"),
            "last_sync_error":   account.get("sync_error_message"),
        },
        "last_events":     last_events,
        "diagnosed_at":    utc_now_iso(),
    }


async def create_or_link_account(
    db, user_id: str, payload: dict, actor_email: Optional[str] = None,
) -> dict:
    """Insert a row in ads_accounts and audit-log it.

    Required payload keys:
      provider, external_account_id, display_name, currency_native,
      timezone, v1_token_ref (dict from discovery).

    Optional:
      organization_id, organization_name, fx_to_sar, bank_fee,
      review_settings.
    """
    provider = payload["provider"]
    external = payload["external_account_id"]

    # If a soft-deleted row exists for this (provider, external), revive
    # it instead of inserting a duplicate.
    existing = await db.ads_accounts.find_one({
        "user_id": user_id,
        "provider": provider,
        "external_account_id": external,
    })
    if existing and existing.get("soft_deleted"):
        await db.ads_accounts.update_one(
            {"id": existing["id"]},
            {"$set": {
                "soft_deleted": False,
                "sync_status": "discovered",
                "updated_at": utc_now_iso(),
            }},
        )
        await _log(db, user_id, existing["id"], "account_relinked_v1",
                    {"reason": "revived_soft_deleted"}, actor_email)
        return await db.ads_accounts.find_one(
            {"id": existing["id"]}, {"_id": 0},
        )
    if existing and not existing.get("soft_deleted"):
        return {"already_exists": True,
                "account": {k: v for k, v in existing.items() if k != "_id"}}

    # Normalize v1_token_ref so user_id always points to the V1 doc owner.
    # The UI passes the ref blindly from discovery — we fix `user_id` here
    # so the read-only lookup actually hits the correct V1 row.
    raw_ref = payload.get("v1_token_ref")
    v1_ref_obj = None
    if raw_ref:
        ref_data = {**raw_ref, "user_id": user_id, "snapshot_only": True}
        v1_ref_obj = V1TokenRef(**ref_data)

    # Build with defaults
    acct = AdsAccount(
        id=uuid.uuid4().hex,
        user_id=user_id,
        provider=provider,
        external_account_id=external,
        display_name=payload.get("display_name") or external,
        currency_native=payload.get("currency_native") or "SAR",
        timezone=payload.get("timezone") or "Asia/Riyadh",
        organization_id=payload.get("organization_id"),
        organization_name=payload.get("organization_name"),
        v1_token_ref=v1_ref_obj,
        fx_to_sar=FxToSar(**payload["fx_to_sar"])
            if payload.get("fx_to_sar") else FxToSar(
                rate=1.0 if payload.get("currency_native") == "SAR" else 3.75
            ),
        bank_fee=BankFee(**payload["bank_fee"])
            if payload.get("bank_fee") else BankFee(),
        review_settings=ReviewSettings(**payload["review_settings"])
            if payload.get("review_settings") else ReviewSettings(),
        sync_enabled=False,           # discovery defaults to disabled
        sync_status="discovered",     # phase-0 default
    )
    doc = acct.model_dump()
    await db.ads_accounts.insert_one(doc)
    await _log(db, user_id, acct.id, "account_created",
                {
                    "provider": provider,
                    "external_account_id": external,
                    "display_name": acct.display_name,
                    "currency_native": acct.currency_native,
                    "organization_name": acct.organization_name,
                }, actor_email)
    return {k: v for k, v in doc.items() if k != "_id"}


async def update_account(
    db, user_id: str, account_id: str, patch: dict,
    actor_email: Optional[str] = None,
) -> dict:
    """Apply a partial patch to an ads_accounts row."""
    clean: dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k not in ALLOWED_PATCH_FIELDS:
            continue
        if k == "fx_to_sar":
            clean[k] = FxToSar(**v).model_dump()
        elif k == "bank_fee":
            clean[k] = BankFee(**v).model_dump()
        elif k == "review_settings":
            clean[k] = ReviewSettings(**v).model_dump()
        else:
            clean[k] = v
    if not clean:
        return {"updated": 0}
    clean["updated_at"] = utc_now_iso()
    res = await db.ads_accounts.update_one(
        {"user_id": user_id, "id": account_id, "soft_deleted": False},
        {"$set": clean},
    )
    if res.matched_count == 0:
        return {"updated": 0, "error": "not_found_or_deleted"}

    # Per-field audit
    event_name = "account_modified"
    if "fx_to_sar" in clean:
        event_name = "fx_changed"
    elif "bank_fee" in clean:
        event_name = "bank_fee_changed"
    await _log(db, user_id, account_id, event_name,
                {"fields_changed": list(clean.keys())}, actor_email)

    doc = await db.ads_accounts.find_one(
        {"id": account_id}, {"_id": 0}
    )
    return {"updated": 1, "account": doc}


async def soft_delete_account(
    db, user_id: str, account_id: str,
    actor_email: Optional[str] = None,
) -> dict:
    res = await db.ads_accounts.update_one(
        {"user_id": user_id, "id": account_id, "soft_deleted": False},
        {"$set": {
            "soft_deleted": True,
            "sync_enabled": False,
            "sync_status": "paused",
            "updated_at": utc_now_iso(),
        }},
    )
    if res.matched_count == 0:
        return {"deleted": 0, "error": "not_found"}
    await _log(db, user_id, account_id, "account_disabled",
                {"reason": "soft_delete"}, actor_email)
    return {"deleted": 1}


# ── Internal ─────────────────────────────────────────────────────────
async def _log(
    db, user_id: str, account_id: Optional[str], event: str,
    details: dict, actor_email: Optional[str],
) -> None:
    entry = AdsSyncLog(
        id=uuid.uuid4().hex,
        user_id=user_id,
        account_id=account_id,
        event=event,
        actor_user_id=user_id,
        actor_email=actor_email,
        details=details,
    )
    await db.ads_sync_logs.insert_one(entry.model_dump())


# ── Token health check (read-only) ───────────────────────────────────
async def check_v1_token_health(
    db, user_id: str, account: dict,
) -> dict:
    """Read the V1 token doc to verify it still has an access_token.

    NEVER mutates V1. Returns a small status dict only.
    """
    ref = account.get("v1_token_ref") or {}
    coll = ref.get("collection")
    if not coll:
        return {"ok": False, "reason": "no_v1_ref"}
    try:
        v1_doc = await db[coll].find_one(
            {"user_id": ref.get("user_id") or user_id},
            {"_id": 0, "access_token": 1, "expires_at": 1, "updated_at": 1},
        )
    except Exception as exc:
        return {"ok": False, "reason": "v1_read_error",
                "message": str(exc)[:200]}
    if not v1_doc:
        return {"ok": False, "reason": "v1_doc_missing"}
    if not v1_doc.get("access_token"):
        return {"ok": False, "reason": "no_access_token"}
    return {
        "ok": True,
        "has_token": True,
        "v1_updated_at": v1_doc.get("updated_at"),
        "expires_at": v1_doc.get("expires_at"),
    }
