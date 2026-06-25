"""Ads V2 — Snapchat Re-link flow (safe, non-destructive).

This module lets the merchant obtain a NEW Snapchat token without
ever touching the V1 `snapchat_connections` document. The fresh token
is parked in `ads_v2_pending_tokens` until the merchant explicitly
approves it after seeing a side-by-side comparison report.

Safety invariants enforced here:
  • V1 doc (snapchat_connections) is NEVER modified by this module
    except inside `approve_pending_token()` AND only after the user
    explicitly POSTs to /approve.
  • Approval ALWAYS appends the current V1 token into a
    `legacy_versions[]` array on the V1 doc so the old token is
    preserved verbatim.
  • Discard is a soft-delete; the pending row keeps the token until
    auto-prune > 30 days.

Endpoints (all under /api/ads-v2/settings/snapchat/relink, mounted in
the parent ads_v2 router):
  POST /start                     → returns OAuth URL (reuses V1 setup)
  POST /manual                    → fallback: accepts pasted tokens
  GET  /pending                   → list active pending tokens
  POST /{id}/compare              → runs live API probes for old & new
  POST /{id}/approve              → swaps tokens (V1 backed-up first)
  POST /{id}/discard              → soft-discard the pending token
"""
from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

SNAPCHAT_AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"
SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
SNAPCHAT_SCOPE = "snapchat-marketing-api"

JWT_ALGO = "HS256"
V2_RELINK_STATE_PURPOSE = "ads_v2_snapchat_relink"

# Pending tokens older than this auto-prune cutoff are ignored.
PENDING_TTL_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


def _jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def _encode_state(user_id: str, pending_id: str) -> str:
    """Produce a JWT-signed state with a V2-specific purpose marker.
    The V1 OAuth callback recognizes this purpose and dispatches to
    handle_v2_relink_callback() — keeping V1 credentials untouched."""
    payload = {
        "user_id": user_id,
        "pending_id": pending_id,
        "purpose": V2_RELINK_STATE_PURPOSE,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGO)


def _decode_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, _jwt_secret(), algorithms=[JWT_ALGO])
        if payload.get("purpose") != V2_RELINK_STATE_PURPOSE:
            raise ValueError("wrong_purpose")
        return payload
    except Exception as e:
        logger.warning("v2 relink state decode failed: %s", e)
        raise HTTPException(status_code=400, detail="invalid_state")


# ═════════════════════════════════════════════════════════════════════
# Snapchat API probe — used by the comparison flow
# ═════════════════════════════════════════════════════════════════════
async def _probe_snapchat_token(access_token: str) -> dict:
    """Return a structured snapshot of what `access_token` can access.

    {
      valid:            bool,
      user_id:          str | None,
      display_name:     str | None,
      organizations:    [{id, name, type, my_member_id}],
      ad_accounts:      [{id, name, currency, organization_id, status}],
      can_access_self_service: bool,
      can_access_riyadh:       bool,
      error:            str | None,
      probed_at:        iso-datetime,
    }
    """
    out: dict = {
        "valid":           False,
        "user_id":         None,
        "display_name":    None,
        "organizations":   [],
        "ad_accounts":     [],
        "can_access_self_service": False,
        "can_access_riyadh":       False,
        "error":           None,
        "probed_at":       _now(),
    }
    if not access_token:
        out["error"] = "no_token"
        return out

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=25.0, headers=headers) as http:
        # 1) /me — identity check
        try:
            r = await http.get(f"{SNAPCHAT_API_BASE}/me")
            if r.status_code == 401:
                out["error"] = "unauthorized"
                return out
            if r.status_code >= 400:
                out["error"] = f"me_http_{r.status_code}"
                return out
            me = (r.json().get("me") or {})
            out["user_id"] = me.get("id")
            out["display_name"] = me.get("display_name") or me.get("email")
            out["valid"] = True
        except Exception as e:
            out["error"] = f"me_exception:{type(e).__name__}"
            return out

        # 2) /me/organizations
        try:
            r = await http.get(f"{SNAPCHAT_API_BASE}/me/organizations")
            if r.status_code == 200:
                for w in r.json().get("organizations") or []:
                    org = w.get("organization") or {}
                    out["organizations"].append({
                        "id":            org.get("id"),
                        "name":          org.get("name"),
                        "type":          org.get("type"),
                        "my_member_id":  org.get("my_member_id"),
                    })
        except Exception:
            pass

        # 3) /organizations/{id}/adaccounts for each org
        for org in out["organizations"]:
            try:
                r = await http.get(
                    f"{SNAPCHAT_API_BASE}/organizations/{org['id']}/adaccounts",
                )
                if r.status_code != 200:
                    continue
                for w in r.json().get("adaccounts") or []:
                    a = w.get("adaccount") or {}
                    out["ad_accounts"].append({
                        "id":              a.get("id"),
                        "name":            a.get("name"),
                        "currency":        a.get("currency"),
                        "status":          a.get("status"),
                        "organization_id": org["id"],
                        "organization_name": org["name"],
                    })
            except Exception:
                continue

    # 4) Heuristic detection of "Self Service" + "Riyadh"
    SS_PATTERNS = ("self service", "self-service", "selfservice")
    RY_PATTERNS = ("riyadh", "الرياض", "ar-riyadh")

    def _matches(text: Optional[str], pats: tuple) -> bool:
        if not text:
            return False
        t = text.lower()
        return any(p in t for p in pats)

    for org in out["organizations"]:
        if (_matches(org.get("name"), SS_PATTERNS)
            or _matches(org.get("type"), SS_PATTERNS)):
            out["can_access_self_service"] = True
    for a in out["ad_accounts"]:
        if (_matches(a.get("name"), SS_PATTERNS)
            or _matches(a.get("organization_name"), SS_PATTERNS)):
            out["can_access_self_service"] = True
        if _matches(a.get("name"), RY_PATTERNS):
            out["can_access_riyadh"] = True

    return out


# ═════════════════════════════════════════════════════════════════════
# V1 OAuth callback dispatch — called from snapchat_routes when state
# carries the V2 purpose marker. Returns the redirect target URL.
# ═════════════════════════════════════════════════════════════════════
async def handle_v2_relink_callback(db, code: str, state: str) -> str:
    """Exchange `code` for tokens using V1's client_id/secret, then
    store them in `ads_v2_pending_tokens`. Returns the URL to redirect
    the user's browser to.
    """
    try:
        payload = _decode_state(state)
    except HTTPException:
        return f"{_frontend_url()}/ads-v2/settings?relink_error=invalid_state"

    user_id = payload["user_id"]
    pending_id = payload["pending_id"]

    v1 = await db.snapchat_connections.find_one(
        {"user_id": user_id},
        {"_id": 0, "client_id": 1, "client_secret": 1, "redirect_uri": 1},
    )
    if not v1:
        return f"{_frontend_url()}/ads-v2/settings?relink_error=v1_missing"

    form_data = {
        "code":          code,
        "client_id":     v1["client_id"],
        "client_secret": v1["client_secret"],
        "grant_type":    "authorization_code",
        "redirect_uri":  v1["redirect_uri"],
    }
    basic_data = {
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  v1["redirect_uri"],
    }

    async with httpx.AsyncClient(timeout=25.0) as http:
        try:
            r = await http.post(SNAPCHAT_TOKEN_URL, data=form_data)
            if r.status_code == 400 and "invalid_client" in (r.text or "").lower():
                r = await http.post(
                    SNAPCHAT_TOKEN_URL, data=basic_data,
                    auth=(v1["client_id"], v1["client_secret"]),
                )
            if r.status_code >= 400:
                msg = (r.text or "exchange_failed")[:200]
                return f"{_frontend_url()}/ads-v2/settings?relink_error={msg}"
        except httpx.HTTPError:
            return f"{_frontend_url()}/ads-v2/settings?relink_error=network_error"

    tok = r.json()
    access_token = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    expires_in = int(tok.get("expires_in", 3600))
    if not access_token or not refresh_token:
        return f"{_frontend_url()}/ads-v2/settings?relink_error=missing_tokens"

    # Probe immediately so the comparison view loads fast
    snapshot = await _probe_snapchat_token(access_token)

    await db.ads_v2_pending_tokens.insert_one({
        "id":                  pending_id,
        "user_id":             user_id,
        "provider":            "snapchat",
        "status":              "pending",        # pending|approved|discarded
        "access_token":        access_token,
        "refresh_token":       refresh_token,
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat(),
        "source":              "oauth",
        "comparison_snapshot": {"new": snapshot},
        "created_at":          _now(),
        "updated_at":          _now(),
    })
    return (
        f"{_frontend_url()}/ads-v2/settings?"
        f"relink_pending_id={pending_id}&relink=success"
    )


# ═════════════════════════════════════════════════════════════════════
# Router
# ═════════════════════════════════════════════════════════════════════
def build_relink_router(db, current_user_dep) -> APIRouter:
    """Mount under /api/ads-v2/settings/snapchat/relink."""
    router = APIRouter(
        prefix="/settings/snapchat/relink",
        tags=["ads-v2-snapchat-relink"],
    )

    async def _user(user=Depends(current_user_dep)) -> dict:
        return user

    # ── POST /start — generate OAuth URL ─────────────────────────────
    @router.post("/start")
    async def start_relink(user: dict = Depends(_user)):
        v1 = await db.snapchat_connections.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "client_id": 1, "redirect_uri": 1},
        )
        if not v1 or not v1.get("client_id") or not v1.get("redirect_uri"):
            raise HTTPException(
                status_code=400,
                detail="V1 Snapchat config missing — يجب ضبط Client ID + Redirect URI في V1 أولاً.",
            )
        pending_id = uuid.uuid4().hex
        state = _encode_state(user["id"], pending_id)
        params = {
            "response_type": "code",
            "client_id":     v1["client_id"],
            "redirect_uri":  v1["redirect_uri"],
            "scope":         SNAPCHAT_SCOPE,
            "state":         state,
        }
        oauth_url = f"{SNAPCHAT_AUTH_URL}?{urlencode(params)}"

        # Pre-register the pending row in "awaiting_callback" state so
        # the UI can poll for completion.
        await db.ads_v2_pending_tokens.insert_one({
            "id":          pending_id,
            "user_id":     user["id"],
            "provider":    "snapchat",
            "status":      "awaiting_callback",
            "source":      "oauth",
            "created_at":  _now(),
            "updated_at":  _now(),
        })
        return {
            "ok":           True,
            "pending_id":   pending_id,
            "oauth_url":    oauth_url,
        }

    # ── POST /manual — fallback: paste tokens directly ──────────────
    @router.post("/manual")
    async def manual_paste(
        body: dict = Body(...), user: dict = Depends(_user),
    ):
        access_token = (body.get("access_token") or "").strip()
        refresh_token = (body.get("refresh_token") or "").strip()
        if not access_token:
            raise HTTPException(400, "access_token مطلوب")
        pending_id = uuid.uuid4().hex
        snapshot = await _probe_snapchat_token(access_token)
        await db.ads_v2_pending_tokens.insert_one({
            "id":                  pending_id,
            "user_id":             user["id"],
            "provider":            "snapchat",
            "status":              "pending",
            "access_token":        access_token,
            "refresh_token":       refresh_token or None,
            "source":              "manual_paste",
            "comparison_snapshot": {"new": snapshot},
            "created_at":          _now(),
            "updated_at":          _now(),
        })
        return {"ok": True, "pending_id": pending_id, "probe": snapshot}

    # ── GET /pending — list pending tokens ──────────────────────────
    @router.get("/pending")
    async def list_pending(user: dict = Depends(_user)):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=PENDING_TTL_DAYS)).isoformat()
        items = []
        async for p in db.ads_v2_pending_tokens.find(
            {"user_id": user["id"], "provider": "snapchat",
             "status": {"$in": ["pending", "awaiting_callback"]},
             "created_at": {"$gte": cutoff}},
            {"_id": 0, "access_token": 0, "refresh_token": 0},
        ).sort("created_at", -1).limit(10):
            items.append(p)
        return {"ok": True, "data": items}

    # ── POST /{id}/compare — probe both tokens side-by-side ─────────
    @router.post("/{pending_id}/compare")
    async def compare(pending_id: str, user: dict = Depends(_user)):
        p = await db.ads_v2_pending_tokens.find_one(
            {"id": pending_id, "user_id": user["id"]},
        )
        if not p:
            raise HTTPException(404, "pending_token_not_found")
        if p.get("status") not in ("pending", "approved", "discarded"):
            raise HTTPException(400, "token_not_ready")

        # Old token (from V1)
        v1 = await db.snapchat_connections.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "access_token": 1},
        )
        old_snap = await _probe_snapchat_token((v1 or {}).get("access_token") or "")
        new_snap = await _probe_snapchat_token(p.get("access_token") or "")

        # Diff
        old_org_ids = {o["id"] for o in old_snap["organizations"] if o.get("id")}
        new_org_ids = {o["id"] for o in new_snap["organizations"] if o.get("id")}
        old_acct_ids = {a["id"] for a in old_snap["ad_accounts"] if a.get("id")}
        new_acct_ids = {a["id"] for a in new_snap["ad_accounts"] if a.get("id")}
        diff = {
            "orgs_added":      sorted(new_org_ids - old_org_ids),
            "orgs_removed":    sorted(old_org_ids - new_org_ids),
            "accounts_added":  sorted(new_acct_ids - old_acct_ids),
            "accounts_removed":sorted(old_acct_ids - new_acct_ids),
        }
        # Cache the comparison
        await db.ads_v2_pending_tokens.update_one(
            {"id": pending_id, "user_id": user["id"]},
            {"$set": {
                "comparison_snapshot": {"old": old_snap, "new": new_snap, "diff": diff},
                "updated_at": _now(),
            }},
        )
        return {
            "ok":       True,
            "pending":  {
                "id": pending_id, "status": p.get("status"),
                "created_at": p.get("created_at"),
                "source": p.get("source"),
            },
            "old":      old_snap,
            "new":      new_snap,
            "diff":     diff,
        }

    # ── POST /{id}/approve — back up V1 then swap ───────────────────
    @router.post("/{pending_id}/approve")
    async def approve(pending_id: str, user: dict = Depends(_user)):
        p = await db.ads_v2_pending_tokens.find_one(
            {"id": pending_id, "user_id": user["id"],
             "status": "pending"},
        )
        if not p:
            raise HTTPException(404, "pending_token_not_found_or_consumed")
        if not p.get("access_token"):
            raise HTTPException(400, "pending_has_no_access_token")

        v1 = await db.snapchat_connections.find_one({"user_id": user["id"]})
        if not v1:
            raise HTTPException(400, "v1_connection_missing")

        now = _now()
        # 1) APPEND old V1 token into legacy_versions (immutable history)
        legacy_entry = {
            "archived_at":          now,
            "archived_by_relink_id": pending_id,
            "access_token":         v1.get("access_token"),
            "refresh_token":        v1.get("refresh_token"),
            "access_token_expires_at": v1.get("access_token_expires_at"),
            "v1_updated_at":        v1.get("updated_at"),
        }
        # 2) Apply new token + push old into history (single atomic update)
        await db.snapchat_connections.update_one(
            {"user_id": user["id"]},
            {
                "$set": {
                    "access_token":  p["access_token"],
                    "refresh_token": p.get("refresh_token") or v1.get("refresh_token"),
                    "access_token_expires_at": p.get("access_token_expires_at"),
                    "updated_at":    now,
                    "last_relinked_at": now,
                    "last_relinked_by_pending_id": pending_id,
                },
                "$push": {"legacy_versions": legacy_entry},
            },
        )
        # 3) Mark pending as approved (don't delete — audit trail)
        await db.ads_v2_pending_tokens.update_one(
            {"id": pending_id, "user_id": user["id"]},
            {"$set": {
                "status":      "approved",
                "approved_at": now,
                "updated_at":  now,
            }},
        )
        # 4) Audit log
        await db.ads_sync_logs.insert_one({
            "id":         uuid.uuid4().hex,
            "user_id":    user["id"],
            "account_id": None,
            "provider":   "snapchat",
            "event":      "account_relinked_v1",
            "actor_user_id": user["id"],
            "actor_email": user.get("email"),
            "details":    {
                "pending_id":      pending_id,
                "legacy_archived": True,
                "source":          p.get("source"),
            },
            "at":         now,
        })
        return {
            "ok": True,
            "pending_id": pending_id,
            "legacy_versions_count_after": len((v1.get("legacy_versions") or [])) + 1,
            "message": "تم اعتماد التوكن الجديد. القديم محفوظ كـ legacy.",
        }

    # ── POST /{id}/discard — soft-discard ───────────────────────────
    @router.post("/{pending_id}/discard")
    async def discard(pending_id: str, user: dict = Depends(_user)):
        res = await db.ads_v2_pending_tokens.update_one(
            {"id": pending_id, "user_id": user["id"],
             "status": {"$in": ["pending", "awaiting_callback"]}},
            {"$set": {"status": "discarded", "discarded_at": _now(),
                       "updated_at": _now()}},
        )
        if res.matched_count == 0:
            raise HTTPException(404, "pending_token_not_found")
        return {"ok": True, "pending_id": pending_id}

    # ── GET /{id} — read one pending row (incl. cached comparison) ──
    @router.get("/{pending_id}")
    async def get_pending(pending_id: str, user: dict = Depends(_user)):
        p = await db.ads_v2_pending_tokens.find_one(
            {"id": pending_id, "user_id": user["id"]},
            {"_id": 0, "access_token": 0, "refresh_token": 0},
        )
        if not p:
            raise HTTPException(404, "pending_token_not_found")
        return {"ok": True, "data": p}

    return router
