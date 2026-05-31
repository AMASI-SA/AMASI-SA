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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_purchases(actions: Optional[list]) -> int:
    """Sum Facebook 'purchase' actions across types (web, omni, offsite_conversion)."""
    if not actions:
        return 0
    total = 0
    for a in actions:
        atype = (a.get("action_type") or "").lower()
        if "purchase" in atype:
            try:
                total += int(float(a.get("value") or 0))
            except (TypeError, ValueError):
                pass
    return total


def _extract_purchase_value(action_values: Optional[list]) -> float:
    if not action_values:
        return 0.0
    total = 0.0
    for a in action_values:
        atype = (a.get("action_type") or "").lower()
        if "purchase" in atype:
            try:
                total += float(a.get("value") or 0)
            except (TypeError, ValueError):
                pass
    return total


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
        return {"connected": True, "synced": True, **summary}

    parent_router.include_router(router)
