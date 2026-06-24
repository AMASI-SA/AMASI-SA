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
    """Single-payload snapshot for the settings page."""
    # Accounts (active only by default; UI can opt to show soft_deleted)
    accounts: list[dict] = []
    async for doc in db.ads_accounts.find(
        {"user_id": user_id, "soft_deleted": False}, {"_id": 0},
    ).sort("provider", 1):
        accounts.append(doc)

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
            "ssot": "ads_accounts + ads_sync_logs",
            "computed_at": utc_now_iso(),
        },
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
