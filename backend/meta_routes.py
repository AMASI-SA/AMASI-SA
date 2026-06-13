"""Meta (Facebook + Instagram) Marketing API integration.

The merchant pastes 4 credentials in Settings → Meta Ads:
- META_APP_ID
- META_APP_SECRET (kept private; never returned to UI in full)
- META_ACCESS_TOKEN (long-lived; never returned in full)
- META_AD_ACCOUNT_ID (e.g. 'act_123456789' or '123456789')

We then call:
    GET /v23.0/act_{AD_ACCOUNT_ID}/insights
    ?fields=campaign_name,campaign_id,adset_name,adset_id,ad_name,ad_id,
           spend,impressions,clicks,cpc,cpm,ctr,actions,action_values
    &time_range={"since":"YYYY-MM-DD","until":"YYYY-MM-DD"}
    &time_increment=1
    &level=campaign
    &access_token=...

Each day per campaign is upserted into `meta_ads_daily` so the existing
dashboard summary card and webhook flow continue to work.

Webhook (`POST /api/webhook/meta/:token`) already exists in
`webhook_routes.py` and writes to the same collection — they coexist.

Endpoints exposed by this router:
  GET    /api/meta/config           → returns the stored config (secrets masked)
  PUT    /api/meta/config           → save / update credentials
  DELETE /api/meta/config           → disconnect
  POST   /api/meta/sync             → manually trigger sync for a date range
  GET    /api/meta/sync-status      → last sync timestamp + summary
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta, date as _date
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
    _RIYADH_TZ = ZoneInfo("Asia/Riyadh")
except ImportError:  # pragma: no cover
    _RIYADH_TZ = timezone(timedelta(hours=3))


def _today_riyadh():
    """Saudi merchants — 'today' should mean today's date in Riyadh, NOT
    UTC. Otherwise a sync triggered at 02:00 AM Riyadh time would pull
    yesterday's data and the dashboard (which also reads Riyadh date)
    would show empty."""
    return datetime.now(_RIYADH_TZ).date()


META_API_BASE = "https://graph.facebook.com/v23.0"
INSIGHT_FIELDS = (
    "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "spend,impressions,clicks,cpc,cpm,ctr,actions,action_values"
)
logger = logging.getLogger("hesab.meta")


def _mask(value: Optional[str]) -> str:
    """Return only the last 4 chars (e.g. for showing the token)."""
    if not value:
        return ""
    if len(value) <= 6:
        return "•" * len(value)
    return f"{'•' * (len(value) - 4)}{value[-4:]}"


def _normalize_ad_account_id(raw: str) -> str:
    """Ensure the id is in the `act_xxx` format the API expects."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    return raw if raw.startswith("act_") else f"act_{raw}"


# ── Schemas ───────────────────────────────────────────────────────────────────
class MetaConfigIn(BaseModel):
    app_id: str = Field(min_length=1)
    # Secret + token are optional on UPDATE: if blank, we keep the existing
    # stored value (so the user doesn't have to re-paste them every time).
    app_secret: str = Field(default="")
    access_token: str = Field(default="")
    ad_account_id: str = Field(min_length=1)


class MetaSyncIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    from_date: Optional[str] = None  # YYYY-MM-DD (override `days`)
    to_date: Optional[str] = None


class MetaTokenExchangeIn(BaseModel):
    """Inputs for converting a short-lived Graph API Explorer token (1-2h)
    into a long-lived token (60 days). App ID + Secret can be blank if the
    user already saved them via `/meta/config` — we fall back to those."""
    short_lived_token: str = Field(default="")
    app_id: str = Field(default="")
    app_secret: str = Field(default="")
    ad_account_id: str = Field(default="")


# ── Helpers ───────────────────────────────────────────────────────────────────
# iter-50 — Meta Graph API returns the SAME conversion under multiple
# action_types (purchase + omni_purchase + offsite_conversion.fb_pixel_purchase
# + onsite_web_purchase + …). Summing across types gives 5-10× inflation
# when Pixel + Conversions API + Instagram/Facebook Shop are all wired
# up simultaneously (very common Saudi merchant setup).
#
# Meta's OFFICIAL deduplicated metric is `omni_purchase`, and `purchase`
# is the canonical Pixel event. We pick ONE of them — never sum.
#
# Priority (most-deduplicated first):
#   1. omni_purchase                            — Meta's official cross-channel dedup
#   2. purchase                                 — base Pixel event
#   3. offsite_conversion.fb_pixel_purchase     — Pixel-only attribution
#   4. onsite_web_purchase / onsite_conversion.purchase — Shop purchases
#
# Anything else (custom events, app purchases) is ignored because it
# is almost always a duplicate signal of one of the above.
_PURCHASE_TYPE_PRIORITY: tuple[str, ...] = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_web_purchase",
    "onsite_conversion.purchase",
)


def _pick_canonical_purchase_value(actions: Optional[list], *, value_key: str = "value") -> float:
    """Return the FIRST matching action_type's value, following the
    priority list. Treats missing/invalid numbers as 0. Returns a float
    that callers can cast to int (for purchase count) or keep as-is
    (for purchase_value).
    """
    if not actions:
        return 0.0
    by_type: dict[str, float] = {}
    for a in actions:
        atype = (a.get("action_type") or "").lower()
        if not atype:
            continue
        try:
            v = float(a.get(value_key) or 0)
        except (TypeError, ValueError):
            continue
        # If Meta lists the same action_type twice (rare — different
        # attribution windows), keep the LARGER value to be safe.
        if v > by_type.get(atype, 0.0):
            by_type[atype] = v
    for t in _PURCHASE_TYPE_PRIORITY:
        if t in by_type:
            return by_type[t]
    return 0.0


def _extract_purchases(actions: Optional[list]) -> int:
    """Number of purchases attributed to the ad — deduplicated.

    Replaces the previous "sum across all *purchase* action_types"
    behaviour that inflated counts 5-10× for merchants using both Pixel
    and Conversions API (every Saudi store with Salla → Meta Pixel +
    server-side CAPI).
    """
    return int(_pick_canonical_purchase_value(actions, value_key="value"))


def _extract_purchase_value(action_values: Optional[list]) -> float:
    """Revenue attributed to the ad — deduplicated, matches purchase count."""
    return float(_pick_canonical_purchase_value(action_values, value_key="value"))


def _classify_meta_error(error_text: str) -> tuple[str, str]:
    """Inspect a Meta API error string and return (status, friendly_arabic_message).

    Meta returns errors as JSON: {"error":{"code":190,"message":"Error validating access token: Session has expired..."}}
    or sometimes wrapped in HTTP 400/401 bodies. We pattern-match the most common
    failure modes so the UI never has to show raw JSON to the merchant.
    """
    s = (error_text or "").lower()
    # Code 190 = expired/invalid access token (most common). Group the
    # `access token + (expired|invalid)` substring check explicitly so the
    # operator-precedence reads unambiguously.
    if ("code\":190" in s
            or "code\": 190" in s
            or "session has expired" in s
            or "session expired" in s
            or ("access token" in s and ("expired" in s or "invalid" in s))
            or ("oauthexception" in s and "190" in s)):
        return ("expired",
                "انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الإعدادات.")
    # Code 200 = permission / capability issue
    if "code\":200" in s or "code\": 200" in s or ("permission" in s and "ads_read" in s):
        return ("permission_denied",
                "الـ Access Token لا يملك صلاحيات قراءة الإعلانات (ads_read). يرجى إعادة إنشاء التوكن بالصلاحيات الصحيحة.")
    # Ad account not found / invalid
    if "code\":100" in s or "code\": 100" in s or ("act_" in s and "not exist" in s):
        return ("invalid_account",
                "معرّف حساب الإعلانات (Ad Account ID) غير صحيح أو لا يملك التوكن صلاحية الوصول إليه.")
    # Rate limited
    if "code\":17" in s or "code\": 17" in s or "rate limit" in s or "too many" in s:
        return ("rate_limited",
                "تم تجاوز حد الطلبات لـ Meta API مؤقتاً. يرجى المحاولة بعد دقائق.")
    # Network/timeout
    if "network error" in s or "timeout" in s or "connection" in s:
        return ("network_error",
                "تعذّر الاتصال بـ Meta API. تحقق من اتصال الإنترنت ثم حاول مرة أخرى.")
    # Generic
    return ("error", "تعذّرت المزامنة مع Meta. تواصل مع الدعم إذا استمرت المشكلة.")


async def _fetch_meta_insights(
    ad_account_id: str,
    access_token: str,
    since: str,
    until: str,
) -> tuple[list[dict], list[str]]:
    """Call Meta Marketing API for daily campaign-level insights.

    Returns (rows, errors). Each row corresponds to one (date, campaign).
    """
    params = {
        "fields": INSIGHT_FIELDS,
        "time_range": f'{{"since":"{since}","until":"{until}"}}',
        "time_increment": 1,
        "level": "campaign",
        "limit": 500,
        "access_token": access_token,
    }
    url = f"{META_API_BASE}/{ad_account_id}/insights"
    rows: list[dict] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as http:
        while url:
            try:
                resp = await http.get(url, params=params if url.endswith("/insights") else None)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Surface Meta's error body (it carries the useful message)
                body = exc.response.text[:300]
                errors.append(f"HTTP {exc.response.status_code}: {body}")
                break
            except httpx.HTTPError as exc:
                errors.append(f"Network error: {exc}")
                break
            data = resp.json()
            for row in data.get("data", []) or []:
                rows.append(row)
            # Pagination
            url = ((data.get("paging") or {}).get("next")) or ""
            params = None  # subsequent calls embed everything in the URL
    return rows, errors


async def _verify_meta_credentials(
    ad_account_id: str,
    access_token: str,
) -> tuple[bool, str, dict]:
    """Lightweight credential check used by /meta/test-connection.

    Calls the ad account endpoint (NOT /insights — that requires time_range
    and is slower). Returns (ok, message, account_info_or_empty).
    """
    url = f"{META_API_BASE}/{ad_account_id}"
    params = {"fields": "id,name,account_status,currency,timezone_name",
              "access_token": access_token}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(url, params=params)
            if resp.status_code >= 400:
                _, msg = _classify_meta_error(resp.text)
                return (False, msg, {})
            data = resp.json()
            return (True, "تم الاتصال بنجاح ✓", data)
    except httpx.HTTPError as exc:
        _, msg = _classify_meta_error(f"network error: {exc}")
        return (False, msg, {})


async def _exchange_short_for_long_lived(
    app_id: str,
    app_secret: str,
    short_lived_token: str,
) -> tuple[bool, str, dict]:
    """Call Meta's fb_exchange_token grant. Per the official docs
    (https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/):

        GET /oauth/access_token?
            grant_type=fb_exchange_token
            &client_id={app-id}
            &client_secret={app-secret}
            &fb_exchange_token={short-lived-token}

    Returns (ok, friendly_arabic_msg, payload). On success payload contains:
        access_token, token_type ("bearer"), expires_in (seconds, ~5184000 = 60d).

    Long-lived user tokens last 60 days. If the input is already a long-lived
    or system-user token, Meta still returns a fresh token (re-extending it
    by 60d if eligible) — so calling this on a "valid" token is a no-op
    refresh, not an error.
    """
    if not app_id or not app_secret:
        return (False,
                "Meta App ID و App Secret مطلوبان للتحويل. احفظهما أولاً ثم أعد المحاولة.",
                {})
    url = f"{META_API_BASE}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(url, params=params)
            if resp.status_code >= 400:
                _, friendly = _classify_meta_error(resp.text)
                logger.warning("Meta token exchange failed (%s): %s",
                               resp.status_code, resp.text[:240])
                return (False, friendly, {})
            data = resp.json() or {}
            if "access_token" not in data:
                return (False,
                        "استجابة غير متوقعة من Meta — لم يصل التوكن الجديد.",
                        {})
            return (True, "تم تحويل التوكن بنجاح ✓", data)
    except httpx.HTTPError as exc:
        _, friendly = _classify_meta_error(f"network error: {exc}")
        return (False, friendly, {})


# ── Router ────────────────────────────────────────────────────────────────────
def attach_meta_routes(parent_router, db):
    """Mount /meta/* routes onto the given parent router."""
    from auth import get_current_user_from_db

    router = APIRouter(prefix="/meta", tags=["meta"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _get_conn(user_id: str) -> Optional[dict]:
        return await db.meta_connections.find_one({"user_id": user_id}, {"_id": 0})

    async def _set_status(user_id: str, status: str, last_error: Optional[str] = None) -> None:
        """Persist the latest connection status (ok/expired/error) + a friendly
        Arabic error so the dashboard can render a clear banner without ever
        showing raw JSON to the user."""
        update_doc = {
            "connection_status": status,
            "last_error_message": last_error if last_error else None,
            "last_error_at": datetime.now(timezone.utc).isoformat() if status != "ok" else None,
        }
        await db.meta_connections.update_one(
            {"user_id": user_id},
            {"$set": update_doc},
        )

    @router.get("/config")
    async def get_config(user: dict = Depends(current_user)):
        conn = await _get_conn(user["id"])
        if not conn:
            return {"connected": False}
        return {
            "connected": True,
            "app_id": conn.get("app_id", ""),
            "app_secret_masked": _mask(conn.get("app_secret")),
            "access_token_masked": _mask(conn.get("access_token")),
            "ad_account_id": conn.get("ad_account_id", ""),
            "last_sync_at": conn.get("last_sync_at"),
            "last_sync_summary": conn.get("last_sync_summary"),
            "connection_status": conn.get("connection_status", "ok"),
            "last_error_message": conn.get("last_error_message"),
            "last_error_at": conn.get("last_error_at"),
            "token_expires_at": conn.get("token_expires_at"),
            "token_exchanged_at": conn.get("token_exchanged_at"),
        }

    @router.put("/config")
    async def put_config(payload: MetaConfigIn, user: dict = Depends(current_user)):
        normalized = _normalize_ad_account_id(payload.ad_account_id)
        if not re.match(r"^act_\d+$", normalized):
            raise HTTPException(status_code=400,
                                detail="Ad Account ID يجب أن يكون رقماً (مع/بدون act_)")
        existing = await _get_conn(user["id"])
        # On first save both secret + token are required. On update, blanks
        # mean "keep existing".
        new_secret = (payload.app_secret or "").strip()
        new_token = (payload.access_token or "").strip()
        if not existing:
            if not new_secret or not new_token:
                raise HTTPException(status_code=400,
                                    detail="App Secret و Access Token مطلوبان عند الربط لأول مرة")
            final_secret = new_secret
            final_token = new_token
        else:
            final_secret = new_secret or existing.get("app_secret", "")
            final_token = new_token or existing.get("access_token", "")
        await db.meta_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "user_id": user["id"],
                "app_id": payload.app_id.strip(),
                "app_secret": final_secret,
                "access_token": final_token,
                "ad_account_id": normalized,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
             "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return {"ok": True}

    @router.post("/test-connection")
    async def test_connection(payload: MetaConfigIn, user: dict = Depends(current_user)):
        """Verify the supplied credentials against Meta API WITHOUT committing
        them unless the test passes. UI uses this from a dedicated "اختبار الاتصال"
        button so the merchant can validate a freshly-pasted token before
        clicking Save."""
        normalized = _normalize_ad_account_id(payload.ad_account_id)
        if not re.match(r"^act_\d+$", normalized):
            raise HTTPException(status_code=400,
                                detail="Ad Account ID يجب أن يكون رقماً (مع/بدون act_)")
        existing = await _get_conn(user["id"])
        new_token = (payload.access_token or "").strip()
        # If the merchant left the token blank in the test form, fall back to
        # the stored token (lets them validate an existing connection without
        # re-typing 200+ characters).
        token_to_test = new_token or (existing or {}).get("access_token", "")
        if not token_to_test:
            raise HTTPException(status_code=400,
                                detail="الرجاء إدخال Access Token للاختبار")

        ok, msg, account_info = await _verify_meta_credentials(normalized, token_to_test)
        if not ok:
            # Test failed → do NOT save the new token. Surface the friendly
            # Arabic message returned by _classify_meta_error.
            raise HTTPException(status_code=400, detail=msg)

        # Test passed → persist (replaces only the fields the user provided;
        # blank app_secret/app_id keep the existing values).
        new_secret = (payload.app_secret or "").strip()
        final_secret = new_secret or (existing or {}).get("app_secret", "")
        final_token = new_token or token_to_test
        if not existing and not new_secret:
            # First-time save still requires app_secret (we can't test-only-save).
            raise HTTPException(status_code=400,
                                detail="App Secret مطلوب عند الربط لأول مرة")
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.meta_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "user_id": user["id"],
                "app_id": payload.app_id.strip(),
                "app_secret": final_secret,
                "access_token": final_token,
                "ad_account_id": normalized,
                "updated_at": now_iso,
                "connection_status": "ok",
                "last_error_message": None,
                "last_error_at": None,
            },
             "$setOnInsert": {"created_at": now_iso}},
            upsert=True,
        )
        return {
            "ok": True,
            "message": msg,
            "saved": True,
            "account": {
                "id": account_info.get("id"),
                "name": account_info.get("name"),
                "currency": account_info.get("currency"),
                "timezone": account_info.get("timezone_name"),
            },
        }

    @router.delete("/config")
    async def delete_config(user: dict = Depends(current_user)):
        await db.meta_connections.delete_one({"user_id": user["id"]})
        return {"ok": True}

    @router.post("/exchange-token")
    async def exchange_token(payload: MetaTokenExchangeIn, user: dict = Depends(current_user)):
        """Convert a short-lived Graph API Explorer token (1-2h lifetime)
        into a long-lived token (60 days) and persist it. Workflow:

          1. Merchant opens https://developers.facebook.com/tools/explorer/
          2. Selects ads_read + business_management permissions
          3. Copies the short-lived token
          4. Pastes it into Settings → presses "تحويل إلى Long-lived"
          5. We call Meta's fb_exchange_token grant with the stored app
             id/secret and save the new token + expires_at.

        App ID/Secret/Ad Account ID can be omitted if already stored
        (typical update flow). If first-time setup, all 4 are required.
        """
        existing = await _get_conn(user["id"])

        # Validate short-lived token presence/length manually so we return
        # a friendly Arabic message instead of Pydantic's raw English JSON.
        sl_token = (payload.short_lived_token or "").strip()
        if len(sl_token) < 20:
            raise HTTPException(
                status_code=400,
                detail="Short-lived token قصير جداً أو فارغ — انسخ التوكن كاملاً من Graph API Explorer.",
            )

        # Resolve final credentials — prefer payload, fall back to existing.
        new_app_id = payload.app_id.strip() or (existing or {}).get("app_id", "")
        new_secret = payload.app_secret.strip() or (existing or {}).get("app_secret", "")
        new_ad_account = payload.ad_account_id.strip() or (existing or {}).get("ad_account_id", "")
        if not new_app_id or not new_secret:
            raise HTTPException(
                status_code=400,
                detail="Meta App ID و App Secret مطلوبان للتحويل. احفظهما أولاً (أو ضمّنهما في هذا الطلب).",
            )
        if not new_ad_account:
            raise HTTPException(
                status_code=400,
                detail="Ad Account ID مطلوب للتحويل.",
            )
        normalized = _normalize_ad_account_id(new_ad_account)
        if not re.match(r"^act_\d+$", normalized):
            raise HTTPException(
                status_code=400,
                detail="Ad Account ID يجب أن يكون رقماً (مع/بدون act_).",
            )

        ok, msg, data = await _exchange_short_for_long_lived(
            app_id=new_app_id,
            app_secret=new_secret,
            short_lived_token=sl_token,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=msg)

        long_lived_token = data["access_token"]
        # Meta returns expires_in in seconds. Long-lived tokens are ~60 days.
        # Some token types (system user) come back without expires_in → null.
        expires_in = data.get("expires_in")
        token_expires_at = None
        token_expires_in_days = None
        if expires_in and isinstance(expires_in, (int, float)):
            expires_at_dt = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            token_expires_at = expires_at_dt.isoformat()
            token_expires_in_days = round(int(expires_in) / 86400, 1)

        # Persist — replaces token + clears any prior expired status.
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.meta_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "user_id": user["id"],
                "app_id": new_app_id,
                "app_secret": new_secret,
                "access_token": long_lived_token,
                "ad_account_id": normalized,
                "token_expires_at": token_expires_at,
                "token_exchanged_at": now_iso,
                "updated_at": now_iso,
                "connection_status": "ok",
                "last_error_message": None,
                "last_error_at": None,
            },
             "$setOnInsert": {"created_at": now_iso}},
            upsert=True,
        )

        # Mask the long-lived token for the response (we NEVER return the
        # full token to the browser; it's already saved server-side).
        masked = _mask(long_lived_token)
        return {
            "ok": True,
            "message": msg,
            "access_token_masked": masked,
            "token_expires_at": token_expires_at,
            "token_expires_in_days": token_expires_in_days,
            "token_type": data.get("token_type", "bearer"),
        }

    @router.post("/sync")
    async def sync(payload: MetaSyncIn, user: dict = Depends(current_user)):
        """Pull Meta insights for the requested date range and upsert each
        (date, campaign_id) row into `meta_ads_daily`. Used by the
        "مزامنة Meta الآن" button and by the daily background job."""
        conn = await _get_conn(user["id"])
        if not conn:
            raise HTTPException(status_code=400,
                                detail="Meta Ads غير مربوط — افتح الإعدادات وأضف بيانات الحساب أولاً")

        today = _today_riyadh()
        if payload.from_date or payload.to_date:
            try:
                start_d = _date.fromisoformat(payload.from_date) if payload.from_date else today
                end_d = _date.fromisoformat(payload.to_date) if payload.to_date else today
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format; use YYYY-MM-DD")
        else:
            start_d = today - timedelta(days=payload.days - 1)
            end_d = today
        if end_d < start_d:
            raise HTTPException(status_code=400, detail="to_date < from_date")
        if (end_d - start_d).days > 90:
            raise HTTPException(status_code=400, detail="Range too wide (max 90 days)")

        rows, errors = await _fetch_meta_insights(
            ad_account_id=conn["ad_account_id"],
            access_token=conn["access_token"],
            since=start_d.isoformat(),
            until=end_d.isoformat(),
        )

        # If Meta returned any error, classify and persist the status so the
        # dashboard can show a friendly banner. CRITICAL: do NOT clear or
        # overwrite existing spend rows when token expires — user keeps seeing
        # historical data while we surface the expired-link warning.
        if errors:
            err_text = " | ".join(errors)
            status, friendly_msg = _classify_meta_error(err_text)
            await _set_status(user["id"], status, friendly_msg)
            now_iso = datetime.now(timezone.utc).isoformat()
            await db.meta_connections.update_one(
                {"user_id": user["id"]},
                {"$set": {"last_sync_at": now_iso}},
            )
            # Map to HTTP status: 401 for expired so frontend can route to settings
            http_status = 401 if status == "expired" else 400
            raise HTTPException(status_code=http_status,
                                detail={"message": friendly_msg,
                                        "status": status,
                                        "raw": err_text[:200]})

        # No errors → mark connection healthy.
        await _set_status(user["id"], "ok", None)

        upserted = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for row in rows:
            date_str = row.get("date_start") or row.get("date_stop")
            if not date_str:
                continue
            doc = {
                "user_id": user["id"],
                "platform": "meta",
                "date": date_str,
                "account_id": conn["ad_account_id"],
                "campaign_id": str(row.get("campaign_id") or "_default"),
                "campaign_name": row.get("campaign_name") or "",
                "adset_id": str(row.get("adset_id") or ""),
                "adset_name": row.get("adset_name") or "",
                "ad_id": str(row.get("ad_id") or ""),
                "ad_name": row.get("ad_name") or "",
                "spend": round(float(row.get("spend") or 0), 2),
                "impressions": int(float(row.get("impressions") or 0)),
                "clicks": int(float(row.get("clicks") or 0)),
                "cpc": round(float(row.get("cpc") or 0), 4),
                "cpm": round(float(row.get("cpm") or 0), 4),
                "ctr": round(float(row.get("ctr") or 0), 4),
                "purchases": _extract_purchases(row.get("actions")),
                "purchase_value": round(_extract_purchase_value(row.get("action_values")), 2),
                "updated_at": now_iso,
            }
            await db.meta_ads_daily.update_one(
                {"user_id": user["id"], "date": doc["date"],
                 "campaign_id": doc["campaign_id"]},
                {"$set": doc,
                 "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            upserted += 1

        summary = {
            "range": {"from": start_d.isoformat(), "to": end_d.isoformat()},
            "rows": len(rows),
            "upserted": upserted,
            "errors": errors,
        }
        await db.meta_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {"last_sync_at": now_iso, "last_sync_summary": summary}},
        )
        # Iter-172 — push fresh Meta spend into ad_account_ledger so the
        # ad-account cards + executive profit panel pick it up.
        try:
            from ad_account_routes import _run_sync_for_all
            await _run_sync_for_all(
                db, user["id"], start_d.isoformat(),
                end_d.isoformat(), force=True)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, **summary}

    @router.post("/auto-sync-if-stale")
    async def auto_sync_if_stale(user: dict = Depends(current_user)):
        """Called silently by the dashboard. If Meta is connected and the
        last sync was >23h ago, trigger a 7-day sync. Returns whether a
        sync was actually performed so the UI can refresh the card."""
        conn = await _get_conn(user["id"])
        if not conn:
            return {"connected": False, "synced": False}
        last_sync_at = conn.get("last_sync_at")
        if last_sync_at:
            try:
                last_dt = datetime.fromisoformat(str(last_sync_at).replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < 23 * 3600:
                    return {"connected": True, "synced": False,
                            "last_sync_at": last_sync_at,
                            "reason": "recently synced"}
            except Exception:
                pass

        # Trigger a 7-day sync silently. Reuse the sync logic by calling
        # _fetch_meta_insights directly (avoids HTTP roundtrip).
        today = _today_riyadh()
        start_d = today - timedelta(days=6)
        rows, errors = await _fetch_meta_insights(
            ad_account_id=conn["ad_account_id"],
            access_token=conn["access_token"],
            since=start_d.isoformat(),
            until=today.isoformat(),
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        # Silent failure: persist status for the dashboard banner but don't
        # raise (background job — never alarm the user mid-page-load).
        if errors:
            status, friendly_msg = _classify_meta_error(" | ".join(errors))
            await _set_status(user["id"], status, friendly_msg)
            await db.meta_connections.update_one(
                {"user_id": user["id"]},
                {"$set": {"last_sync_at": now_iso}},
            )
            return {"connected": True, "synced": False,
                    "connection_status": status,
                    "error": friendly_msg}
        await _set_status(user["id"], "ok", None)
        upserted = 0
        for row in rows:
            date_str = row.get("date_start") or row.get("date_stop")
            if not date_str:
                continue
            doc = {
                "user_id": user["id"], "platform": "meta", "date": date_str,
                "account_id": conn["ad_account_id"],
                "campaign_id": str(row.get("campaign_id") or "_default"),
                "campaign_name": row.get("campaign_name") or "",
                "adset_id": str(row.get("adset_id") or ""),
                "adset_name": row.get("adset_name") or "",
                "ad_id": str(row.get("ad_id") or ""),
                "ad_name": row.get("ad_name") or "",
                "spend": round(float(row.get("spend") or 0), 2),
                "impressions": int(float(row.get("impressions") or 0)),
                "clicks": int(float(row.get("clicks") or 0)),
                "cpc": round(float(row.get("cpc") or 0), 4),
                "cpm": round(float(row.get("cpm") or 0), 4),
                "ctr": round(float(row.get("ctr") or 0), 4),
                "purchases": _extract_purchases(row.get("actions")),
                "purchase_value": round(_extract_purchase_value(row.get("action_values")), 2),
                "updated_at": now_iso,
            }
            await db.meta_ads_daily.update_one(
                {"user_id": user["id"], "date": doc["date"],
                 "campaign_id": doc["campaign_id"]},
                {"$set": doc, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
            upserted += 1
        summary = {"range": {"from": start_d.isoformat(), "to": today.isoformat()},
                   "rows": len(rows), "upserted": upserted, "errors": errors}
        await db.meta_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {"last_sync_at": now_iso, "last_sync_summary": summary}},
        )
        # Iter-172 — same write-through to ad_account_ledger as the
        # manual sync, so the silent auto-sync also refreshes cards.
        try:
            from ad_account_routes import _run_sync_for_all
            await _run_sync_for_all(
                db, user["id"], start_d.isoformat(),
                today.isoformat(), force=True)
        except Exception:  # noqa: BLE001
            pass
        return {"connected": True, "synced": True, **summary}

    # ── Iter-159l — Diagnose Meta billing API permissions ────────────────
    @router.get("/diagnose-billing-permissions")
    async def diagnose_billing_permissions(user: dict = Depends(current_user)):
        conn = await _get_conn(user["id"])
        if not conn or not conn.get("access_token"):
            return {
                "connected": False,
                "checks": [],
                "platform": "meta",
                "summary": "حساب Meta غير مربوط. اربطه أولاً من الإعدادات.",
            }
        token = conn["access_token"]
        checks: list[dict] = []
        ad_account_id: Optional[str] = None

        async with httpx.AsyncClient(timeout=20.0) as http:
            # 1) Token's granted scopes via /debug_token (best source of truth)
            try:
                resp = await http.get(
                    f"{META_API_BASE}/debug_token",
                    params={"input_token": token, "access_token": token},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    scopes = data.get("scopes", []) or []
                    has_read = "ads_read" in scopes
                    has_mgmt = "ads_management" in scopes
                    checks.append({
                        "name": "صلاحيات التوكن (debug_token)",
                        "endpoint": "/debug_token",
                        "status": (f"✅ {len(scopes)} صلاحية"
                                   if scopes else "⚠ بلا صلاحيات معلنة"),
                        "ok": True,
                        "scopes": scopes,
                        "has_ads_read": has_read,
                        "has_ads_management": has_mgmt,
                    })
                else:
                    checks.append({
                        "name": "صلاحيات التوكن (debug_token)",
                        "status": f"❌ HTTP {resp.status_code}",
                        "detail": resp.text[:200],
                        "ok": False,
                    })
            except Exception as e:
                checks.append({"name": "صلاحيات التوكن",
                                "status": f"❌ خطأ: {e}", "ok": False})

            # 2) List ad accounts (requires ads_read)
            try:
                resp = await http.get(
                    f"{META_API_BASE}/me/adaccounts",
                    params={"access_token": token, "limit": 5,
                            "fields": "id,name,account_status,balance,"
                                       "amount_spent,currency"},
                )
                if resp.status_code == 200:
                    accts = resp.json().get("data", []) or []
                    checks.append({
                        "name": "قراءة الحسابات الإعلانية",
                        "endpoint": "/me/adaccounts",
                        "status": f"✅ مسموح ({len(accts)} حساب)",
                        "ok": True,
                        "count": len(accts),
                    })
                    if accts:
                        ad_account_id = accts[0].get("id")
                elif resp.status_code in (400, 401, 403):
                    checks.append({
                        "name": "قراءة الحسابات الإعلانية",
                        "endpoint": "/me/adaccounts",
                        "status": f"❌ ممنوع (HTTP {resp.status_code})",
                        "detail": resp.text[:200],
                        "ok": False,
                        "missing_scope": "ads_read",
                    })
                else:
                    checks.append({
                        "name": "قراءة الحسابات الإعلانية",
                        "status": f"⚠ HTTP {resp.status_code}",
                        "ok": False,
                    })
            except Exception as e:
                checks.append({"name": "قراءة الحسابات الإعلانية",
                                "status": f"❌ خطأ: {e}", "ok": False})

            # 3) Billing / spend cap fields (uses ads_management for some)
            if ad_account_id:
                try:
                    resp = await http.get(
                        f"{META_API_BASE}/{ad_account_id}",
                        params={"access_token": token,
                                "fields": "balance,amount_spent,spend_cap,"
                                           "currency,funding_source_details"},
                    )
                    if resp.status_code == 200:
                        d = resp.json()
                        checks.append({
                            "name": "قراءة الرصيد والمصروف (billing fields)",
                            "endpoint": "/{ad_account_id}",
                            "status": "✅ مسموح",
                            "ok": True,
                            "sample": {
                                "balance": d.get("balance"),
                                "amount_spent": d.get("amount_spent"),
                                "currency": d.get("currency"),
                            },
                        })
                    else:
                        checks.append({
                            "name": "قراءة الرصيد والمصروف",
                            "status": f"❌ HTTP {resp.status_code}",
                            "detail": resp.text[:200],
                            "ok": False,
                        })
                except Exception as e:
                    checks.append({"name": "قراءة الرصيد والمصروف",
                                    "status": f"❌ خطأ: {e}", "ok": False})

        # Build summary
        ads_read_ok = any(
            c.get("name", "").startswith("قراءة الحسابات") and c.get("ok")
            for c in checks)
        billing_ok = any(
            c.get("name", "").startswith("قراءة الرصيد") and c.get("ok")
            for c in checks)
        if billing_ok:
            summary = ("✅ صلاحياتك كاملة على Meta — يمكنك مزامنة "
                       "المديونيات (الرصيد والمصروف) من Meta API.")
            level = "ok"
        elif ads_read_ok:
            summary = ("⚠ تستطيع قراءة الحسابات لكن ليس الرصيد التفصيلي. "
                       "تأكد من أن التوكن مُولَّد عبر تطبيق Business "
                       "بصلاحية ads_management.")
            level = "partial"
        else:
            summary = ("❌ التوكن الحالي لا يحتوي صلاحية ads_read. "
                       "أعد توليد التوكن من Meta Business Manager → "
                       "Apps → Permissions and Features → فعّل ads_read.")
            level = "missing_scope"

        return {
            "connected": True,
            "platform": "meta",
            "checks": checks,
            "summary": summary,
            "level": level,
        }

    parent_router.include_router(router)
