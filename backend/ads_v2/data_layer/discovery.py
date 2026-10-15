"""Ads V2 — Discovery service (Phase 0).

Reads V1 OAuth/connection collections strictly read-only and lists
the advertising accounts available under each provider.

V1 collections referenced:
  • snapchat_connections   (per-user OAuth + access_token)
  • meta_connections       (per-user OAuth + access_token)
  • tiktok_connections     (per-user OAuth + access_token) — read attempt;
                            collection may not exist yet, that is OK.

Phase 0 contract:
  • NEVER calls .update_*/insert_*/delete_*/replace_* on V1 collections.
  • NEVER triggers an OAuth flow.
  • Returns whatever it can read; missing/expired tokens are reported as
    `connection_status` flags (no automatic refresh attempted here).

External provider APIs are called only to LIST ad accounts under the
existing access_token. If the API rejects the token, we surface that as
`connection_status='token_invalid'` and continue.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# V1 endpoints (no oauth secrets touched; access_token use only)
META_GRAPH_BASE = "https://graph.facebook.com/v23.0"
SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# V1 connection readers (READ-ONLY)
# ─────────────────────────────────────────────────────────────────────
async def read_v1_meta_connection(db, user_id: str) -> Optional[dict]:
    """Read meta_connections doc (read-only). Returns None if missing."""
    return await db.meta_connections.find_one(
        {"user_id": user_id}, {"_id": 0}
    )


async def read_v1_snapchat_connection(db, user_id: str) -> Optional[dict]:
    """Read snapchat_connections doc (read-only)."""
    return await db.snapchat_connections.find_one(
        {"user_id": user_id}, {"_id": 0}
    )


async def read_v1_tiktok_connection(db, user_id: str) -> Optional[dict]:
    """Read tiktok_connections doc (read-only). Returns None if collection
    or doc is missing — this is normal in environments without TikTok yet.
    """
    try:
        return await db.tiktok_connections.find_one(
            {"user_id": user_id}, {"_id": 0}
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Provider account listers (use existing V1 access_token, read-only)
# ─────────────────────────────────────────────────────────────────────
async def list_meta_ad_accounts(access_token: str) -> tuple[list[dict], dict]:
    """List Meta ad accounts owned by the user behind access_token.

    Returns: (accounts, status). `status['ok']` is True on success.
    """
    if not access_token:
        return [], {"ok": False, "reason": "missing_token"}
    url = f"{META_GRAPH_BASE}/me/adaccounts"
    params = {
        "fields": "account_id,name,currency,timezone_name,business",
        "access_token": access_token,
        "limit": 100,
    }
    accounts: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            next_url = url
            next_params = params
            while next_url:
                resp = await http.get(next_url, params=next_params)
                if resp.status_code == 401:
                    return [], {"ok": False, "reason": "token_invalid",
                                 "http_status": 401,
                                 "body": resp.text[:300]}
                resp.raise_for_status()
                data = resp.json()
                for row in data.get("data", []) or []:
                    biz = row.get("business") or {}
                    accounts.append({
                        "provider": "meta",
                        "external_account_id": row.get("account_id")
                            if str(row.get("account_id", "")).startswith("act_")
                            else f"act_{row.get('account_id')}",
                        "display_name": row.get("name") or "",
                        "currency_native": row.get("currency") or "SAR",
                        "timezone": row.get("timezone_name") or "Asia/Riyadh",
                        "organization_id": biz.get("id"),
                        "organization_name": biz.get("name"),
                    })
                paging = data.get("paging") or {}
                next_link = paging.get("next")
                if next_link:
                    next_url = next_link
                    next_params = None  # full URL contains query
                else:
                    break
        return accounts, {"ok": True}
    except httpx.HTTPStatusError as exc:
        return [], {"ok": False, "reason": "http_error",
                     "http_status": exc.response.status_code,
                     "body": (exc.response.text or "")[:300]}
    except Exception as exc:
        return [], {"ok": False, "reason": "exception",
                     "message": str(exc)[:300]}


async def list_snapchat_ad_accounts(
    access_token: str,
) -> tuple[list[dict], dict]:
    """List Snapchat ad accounts across organizations granted to the token."""
    if not access_token:
        return [], {"ok": False, "reason": "missing_token"}
    headers = {"Authorization": f"Bearer {access_token}"}
    accounts: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            # Step 1: fetch organizations
            orgs_resp = await http.get(
                f"{SNAPCHAT_API_BASE}/me/organizations", headers=headers)
            if orgs_resp.status_code == 401:
                return [], {"ok": False, "reason": "token_invalid"}
            orgs_resp.raise_for_status()
            orgs = (orgs_resp.json() or {}).get("organizations", []) or []
            for org_wrapper in orgs:
                org = (org_wrapper or {}).get("organization") or {}
                org_id = org.get("id")
                org_name = org.get("name")
                if not org_id:
                    continue
                # Step 2: per-org adaccounts
                aa_resp = await http.get(
                    f"{SNAPCHAT_API_BASE}/organizations/{org_id}/adaccounts",
                    headers=headers,
                )
                if aa_resp.status_code != 200:
                    # Token may not have access to this org — keep iterating
                    continue
                for wrap in (aa_resp.json() or {}).get("adaccounts", []) or []:
                    aa = (wrap or {}).get("adaccount") or {}
                    if not aa.get("id"):
                        continue
                    accounts.append({
                        "provider": "snapchat",
                        "external_account_id": aa["id"],
                        "display_name": aa.get("name") or "",
                        "currency_native": aa.get("currency") or "USD",
                        "timezone": aa.get("timezone") or "America/Los_Angeles",
                        "organization_id": org_id,
                        "organization_name": org_name,
                    })
        return accounts, {"ok": True}
    except httpx.HTTPStatusError as exc:
        return [], {"ok": False, "reason": "http_error",
                     "http_status": exc.response.status_code,
                     "body": (exc.response.text or "")[:300]}
    except Exception as exc:
        return [], {"ok": False, "reason": "exception",
                     "message": str(exc)[:300]}


async def list_tiktok_ad_accounts(
    access_token: str, advertiser_ids: Optional[list[str]] = None,
) -> tuple[list[dict], dict]:
    """List TikTok advertiser accounts under the access_token."""
    if not access_token:
        return [], {"ok": False, "reason": "missing_token"}
    headers = {"Access-Token": access_token}
    accounts: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            # TikTok requires explicit advertiser_ids OR a user/info call.
            # First, try /oauth2/advertiser/get to enumerate.
            resp = await http.get(
                f"{TIKTOK_API_BASE}/oauth2/advertiser/get",
                headers=headers,
                params={"app_id": "0", "secret": "0"},
            )
            if resp.status_code == 401:
                return [], {"ok": False, "reason": "token_invalid"}
            # If we get here without auth, response may have list:
            data = resp.json() if resp.status_code == 200 else {}
            for row in (data.get("data") or {}).get("list", []) or []:
                accounts.append({
                    "provider": "tiktok",
                    "external_account_id": row.get("advertiser_id"),
                    "display_name": row.get("advertiser_name") or "",
                    "currency_native": "USD",
                    "timezone": "Asia/Riyadh",
                    "organization_id": None,
                    "organization_name": None,
                })
        return accounts, {"ok": True}
    except Exception as exc:
        # TikTok integration is optional in Phase 0
        return [], {"ok": False, "reason": "exception",
                     "message": str(exc)[:300]}


# ─────────────────────────────────────────────────────────────────────
# Top-level discover()
# ─────────────────────────────────────────────────────────────────────
async def discover_all_providers(db, user_id: str) -> dict:
    """Discover available ad accounts across V1 tokens, READ-ONLY.

    Returns a dict shaped like:
      {
        "meta":     {"connection_status": "active|missing|token_invalid",
                     "v1_token_ref": {...},
                     "accounts": [...],
                     "error": "..." }
        "snapchat": {...},
        "tiktok":   {...},
      }
    """
    out: dict = {}

    # ── Meta ──
    meta_conn = await read_v1_meta_connection(db, user_id)
    meta_block: dict = {
        "connection_status": "missing",
        "v1_token_ref": None,
        "accounts": [],
        "error": None,
    }
    if meta_conn:
        meta_block["v1_token_ref"] = {
            "provider": "meta",
            "collection": "meta_connections",
            "user_id": user_id,
            "linked_at": _now_iso(),
            "snapshot_only": True,
        }
        # Cached fallback: rows from meta_ads_daily so the merchant can
        # see something even if the token list call fails.
        cached = await _cached_accounts_for_meta(db, user_id)
        access_token = meta_conn.get("access_token") or ""
        accounts, status = await list_meta_ad_accounts(access_token)
        if status.get("ok"):
            meta_block["connection_status"] = "active"
            meta_block["accounts"] = accounts
        else:
            meta_block["connection_status"] = status.get("reason") or "error"
            meta_block["accounts"] = cached
            meta_block["error"] = status
    out["meta"] = meta_block

    # ── Snapchat ──
    # V1 discovery is frozen. Do not read the plaintext legacy token, cached
    # V1 accounts, or call Snapchat from this old control plane.
    snap_block: dict = {
        "connection_status": "legacy_frozen",
        "v1_token_ref": None,
        "accounts": [],
        "error": {
            "code": "snapchat_legacy_frozen",
            "message": "Use Snapchat in Mezan 2",
            "redirect_to": "/integrations-v2?provider=snapchat_ads",
        },
    }
    out["snapchat"] = snap_block

    # ── TikTok ──
    tt_conn = await read_v1_tiktok_connection(db, user_id)
    tt_block: dict = {
        "connection_status": "missing",
        "v1_token_ref": None,
        "accounts": [],
        "error": None,
    }
    if tt_conn:
        tt_block["v1_token_ref"] = {
            "provider": "tiktok",
            "collection": "tiktok_connections",
            "user_id": user_id,
            "linked_at": _now_iso(),
            "snapshot_only": True,
        }
        cached = await _cached_accounts_for_tiktok(db, user_id)
        access_token = tt_conn.get("access_token") or ""
        accounts, status = await list_tiktok_ad_accounts(access_token)
        if status.get("ok"):
            tt_block["connection_status"] = "active"
            tt_block["accounts"] = accounts or cached
        else:
            tt_block["connection_status"] = status.get("reason") or "error"
            tt_block["accounts"] = cached
            tt_block["error"] = status
    out["tiktok"] = tt_block

    return out


# ─────────────────────────────────────────────────────────────────────
# Cached-account fallback readers (V1 derived data, READ-ONLY)
# ─────────────────────────────────────────────────────────────────────
async def _cached_accounts_for_meta(db, user_id: str) -> list[dict]:
    """Distinct Meta accounts already seen in meta_ads_daily (V1)."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$account_id"}},
        {"$limit": 50},
    ]
    out: list[dict] = []
    try:
        async for row in db.meta_ads_daily.aggregate(pipeline):
            ext = row.get("_id")
            if not ext:
                continue
            out.append({
                "provider": "meta",
                "external_account_id": ext,
                "display_name": ext,
                "currency_native": "SAR",
                "timezone": "Asia/Riyadh",
                "organization_id": None,
                "organization_name": None,
            })
    except Exception:
        return []
    return out


async def _cached_accounts_for_snapchat(db, user_id: str) -> list[dict]:
    """Distinct Snapchat ad accounts from snapchat_ad_accounts (V1)."""
    out: list[dict] = []
    try:
        async for row in db.snapchat_ad_accounts.find(
            {"user_id": user_id}, {"_id": 0},
        ):
            ext = row.get("ad_account_id")
            if not ext:
                continue
            out.append({
                "provider": "snapchat",
                "external_account_id": ext,
                "display_name": row.get("name") or ext,
                "currency_native": row.get("currency_native") or "USD",
                "timezone": row.get("timezone") or "America/Los_Angeles",
                "organization_id": row.get("organization_id"),
                "organization_name": row.get("organization_name"),
            })
    except Exception:
        return []
    return out


async def _cached_accounts_for_tiktok(db, user_id: str) -> list[dict]:
    """Distinct TikTok advertisers from tiktok_ads_daily (V1, if any)."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$advertiser_id"}},
        {"$limit": 50},
    ]
    out: list[dict] = []
    try:
        async for row in db.tiktok_ads_daily.aggregate(pipeline):
            ext = row.get("_id")
            if not ext:
                continue
            out.append({
                "provider": "tiktok",
                "external_account_id": ext,
                "display_name": ext,
                "currency_native": "USD",
                "timezone": "Asia/Riyadh",
                "organization_id": None,
                "organization_name": None,
            })
    except Exception:
        return []
    return out
