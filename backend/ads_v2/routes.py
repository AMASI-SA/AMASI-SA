"""Ads V2 — Phase 0 routes.

All endpoints under `/api/ads-v2/...`.

Phase 0 scope:
  • GET    /settings                          → snapshot for UI
  • POST   /settings/accounts/discover        → read V1 tokens, list accounts
  • POST   /settings/accounts                 → create/link from discovery
  • PATCH  /settings/accounts/{id}            → edit fx / bank_fee / review
  • DELETE /settings/accounts/{id}            → soft delete
  • POST   /settings/accounts/{id}/check-token → ping V1 token health (RO)
  • GET    /settings/activity                 → ads_sync_logs (paginated)

Forbidden in Phase 0 (responses return 501 if accidentally hit):
  • Any write to general_ledger
  • Any OAuth flow
  • Any modification to V1 collections
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .data_layer import discovery, settings as settings_dl

logger = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────
class AccountCreateIn(BaseModel):
    provider: str
    external_account_id: str
    display_name: Optional[str] = None
    currency_native: Optional[str] = "SAR"
    timezone: Optional[str] = "Asia/Riyadh"
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    v1_token_ref: Optional[dict] = None
    # optional initial settings
    fx_to_sar: Optional[dict] = None
    bank_fee: Optional[dict] = None
    review_settings: Optional[dict] = None


class AccountPatchIn(BaseModel):
    display_name: Optional[str] = None
    currency_native: Optional[str] = None
    timezone: Optional[str] = None
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    fx_to_sar: Optional[dict] = None
    bank_fee: Optional[dict] = None
    review_settings: Optional[dict] = None
    sync_enabled: Optional[bool] = None
    sync_status: Optional[str] = None


# ── Router factory ─────────────────────────────────────────────────────
def make_ads_v2_router(db, current_user_dep):
    router = APIRouter(prefix="/ads-v2", tags=["ads-v2"])

    async def _user(user=Depends(current_user_dep)) -> dict:
        return user

    # ── GET /settings ───────────────────────────────────────────────
    @router.get("/settings")
    async def get_settings(user: dict = Depends(_user)):
        snap = await settings_dl.get_settings_snapshot(db, user["id"])
        # Augment each account with a lightweight token health check
        for acct in snap["accounts"]:
            health = await settings_dl.check_v1_token_health(
                db, user["id"], acct)
            acct["_v1_token_health"] = health
        return {"ok": True, "data": snap}

    # ── POST /settings/accounts/discover ────────────────────────────
    @router.post("/settings/accounts/discover")
    async def discover_accounts(user: dict = Depends(_user)):
        """Read V1 tokens and list ad accounts available per provider.

        READ-ONLY: no writes to V1, no inserts to ads_accounts.
        """
        try:
            blocks = await discovery.discover_all_providers(db, user["id"])
        except Exception as exc:
            logger.exception("discover failed")
            raise HTTPException(
                status_code=500, detail=f"discover_failed: {exc}")
        # Annotate each discovered account with whether it's already
        # linked to an ads_accounts row.
        linked = {
            (d["provider"], d["external_account_id"]): d
            async for d in db.ads_accounts.find(
                {"user_id": user["id"], "soft_deleted": False},
                {"_id": 0, "id": 1, "provider": 1,
                 "external_account_id": 1, "sync_status": 1},
            )
        }
        for provider_name, block in blocks.items():
            for a in block.get("accounts", []):
                key = (a["provider"], a["external_account_id"])
                if key in linked:
                    a["_linked"] = True
                    a["_linked_account_id"] = linked[key]["id"]
                    a["_linked_status"] = linked[key]["sync_status"]
                else:
                    a["_linked"] = False
        return {"ok": True, "data": blocks}

    # ── POST /settings/accounts ─────────────────────────────────────
    @router.post("/settings/accounts")
    async def create_account(
        body: AccountCreateIn,
        user: dict = Depends(_user),
    ):
        if body.provider not in ("meta", "snapchat", "tiktok",
                                   "google_ads"):
            raise HTTPException(status_code=400,
                                 detail="invalid_provider")
        payload = body.model_dump()
        result = await settings_dl.create_or_link_account(
            db, user["id"], payload, actor_email=user.get("email"))
        return {"ok": True, "data": result}

    # ── PATCH /settings/accounts/{id} ───────────────────────────────
    @router.patch("/settings/accounts/{account_id}")
    async def patch_account(
        account_id: str,
        body: AccountPatchIn,
        user: dict = Depends(_user),
    ):
        patch = {k: v for k, v in body.model_dump().items()
                  if v is not None}
        if not patch:
            return {"ok": True, "data": {"updated": 0,
                                            "reason": "empty_patch"}}
        res = await settings_dl.update_account(
            db, user["id"], account_id, patch,
            actor_email=user.get("email"))
        return {"ok": True, "data": res}

    # ── DELETE /settings/accounts/{id} ──────────────────────────────
    @router.delete("/settings/accounts/{account_id}")
    async def delete_account(
        account_id: str, user: dict = Depends(_user),
    ):
        res = await settings_dl.soft_delete_account(
            db, user["id"], account_id, actor_email=user.get("email"))
        return {"ok": True, "data": res}

    # ── POST /settings/accounts/{id}/check-token ────────────────────
    @router.post("/settings/accounts/{account_id}/check-token")
    async def check_token(
        account_id: str, user: dict = Depends(_user),
    ):
        acct = await db.ads_accounts.find_one(
            {"user_id": user["id"], "id": account_id}, {"_id": 0})
        if not acct:
            raise HTTPException(status_code=404, detail="account_not_found")
        health = await settings_dl.check_v1_token_health(
            db, user["id"], acct)
        # Log event (read-only check, but useful for audit timeline)
        await db.ads_sync_logs.insert_one({
            "id":           uuid.uuid4().hex,
            "user_id":      user["id"],
            "account_id":   account_id,
            "event":        "token_alert" if not health["ok"] else "reconciliation_checked",
            "actor_user_id": user["id"],
            "actor_email":  user.get("email"),
            "details":      {"check": "v1_token_health", "result": health},
            "at":           settings_dl.utc_now_iso(),
        })
        return {"ok": True, "data": health}

    # ── GET /settings/activity ──────────────────────────────────────
    @router.get("/settings/activity")
    async def get_activity(
        user: dict = Depends(_user),
        limit: int = Query(50, ge=1, le=500),
        account_id: Optional[str] = None,
        event: Optional[str] = None,
    ):
        q: dict = {"user_id": user["id"]}
        if account_id:
            q["account_id"] = account_id
        if event:
            q["event"] = event
        rows: list[dict] = []
        async for ev in db.ads_sync_logs.find(q, {"_id": 0}) \
                                          .sort("at", -1).limit(limit):
            rows.append(ev)
        return {"ok": True, "data": {"events": rows, "count": len(rows)}}

    return router
