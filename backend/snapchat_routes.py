"""Snapchat Marketing API OAuth + data-fetch routes.

Per-user integration: each user supplies their own App ID + App Secret and
connects via OAuth. We store refresh_token and short-lived access_token in
MongoDB and proactively refresh.

Endpoints (all under /api/snapchat):
- GET  /config              → return saved config (without secret)
- POST /config              → upsert client_id / client_secret / redirect_uri
- DELETE /config            → disconnect (delete config + tokens)
- GET  /authorize-url       → returns Snapchat OAuth authorization URL
- GET  /oauth/callback      → OAuth redirect target → exchanges code → stores tokens → redirects to frontend
- GET  /adaccounts          → list Snapchat ad accounts for connected user
- GET  /daily-spend         → fetch spend for a given date range / ad account
"""
import os
import jwt
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SNAPCHAT_AUTH_URL = "https://accounts.snapchat.com/login/oauth2/authorize"
SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
SNAPCHAT_SCOPE = "snapchat-marketing-api"

JWT_ALGORITHM = "HS256"


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")


# ── Schemas ────────────────────────────────────────────────────────────────
class SnapchatConfigIn(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)


# ── Helpers ────────────────────────────────────────────────────────────────
def _build_router(db) -> APIRouter:
    from auth import get_current_user_from_db

    router = APIRouter(prefix="/snapchat", tags=["snapchat"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _get_conn(user_id: str) -> Optional[dict]:
        return await db.snapchat_connections.find_one({"user_id": user_id}, {"_id": 0})

    async def _refresh_access_token(conn: dict) -> str:
        """Use stored refresh_token to mint a new access_token; persist it."""
        if not conn.get("refresh_token"):
            raise HTTPException(status_code=400, detail="لا يوجد Refresh Token. أعد الربط مع سناب.")
        data = {
            "refresh_token": conn["refresh_token"],
            "client_id": conn["client_id"],
            "client_secret": conn["client_secret"],
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=20.0) as http:
            try:
                resp = await http.post(SNAPCHAT_TOKEN_URL, data=data)
                if resp.status_code == 400 and "invalid_client" in (resp.text or "").lower():
                    # Retry with HTTP Basic Auth
                    logger.info("Snapchat refresh invalid_client via form body; retrying with Basic Auth")
                    resp = await http.post(
                        SNAPCHAT_TOKEN_URL,
                        data={"refresh_token": conn["refresh_token"], "grant_type": "refresh_token"},
                        auth=(conn["client_id"], conn["client_secret"]),
                    )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error("Snapchat refresh failed: %s", exc.response.text)
                raise HTTPException(status_code=502, detail=f"فشل تحديث رمز سناب: {exc.response.text}")
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"خطأ شبكة مع سناب: {exc}")

        token = resp.json()
        access_token = token.get("access_token")
        new_refresh = token.get("refresh_token") or conn["refresh_token"]
        expires_in = int(token.get("expires_in", 3600))
        if not access_token:
            raise HTTPException(status_code=502, detail="رد سناب لا يحتوي access_token")
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        await db.snapchat_connections.update_one(
            {"user_id": conn["user_id"]},
            {"$set": {
                "access_token": access_token,
                "refresh_token": new_refresh,
                "access_token_expires_at": expires_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return access_token

    async def _ensure_access_token(user_id: str) -> tuple[str, dict]:
        conn = await _get_conn(user_id)
        if not conn or not conn.get("refresh_token"):
            raise HTTPException(status_code=400, detail="حساب سناب غير مربوط. اربطه من الإعدادات.")
        # Use cached token if still valid for at least 60s
        try:
            expires_at = datetime.fromisoformat(conn.get("access_token_expires_at", ""))
        except Exception:
            expires_at = None
        if (
            conn.get("access_token")
            and expires_at
            and expires_at > datetime.now(timezone.utc) + timedelta(seconds=60)
        ):
            return conn["access_token"], conn
        token = await _refresh_access_token(conn)
        conn["access_token"] = token
        return token, conn

    def _encode_state(user_id: str) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "purpose": "snapchat_oauth_state",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
            },
            get_jwt_secret(),
            algorithm=JWT_ALGORITHM,
        )

    def _decode_state(state: str) -> str:
        try:
            payload = jwt.decode(state, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
            if payload.get("purpose") != "snapchat_oauth_state":
                raise ValueError("invalid purpose")
            return payload["sub"]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"رمز state غير صالح: {e}")

    # ── Routes ─────────────────────────────────────────────────────────────
    @router.get("/config")
    async def get_config(user: dict = Depends(current_user)):
        conn = await _get_conn(user["id"])
        if not conn:
            return {
                "connected": False,
                "has_credentials": False,
                "client_id": "",
                "redirect_uri": "",
                "ad_account_id": "",
                "ad_account_name": "",
            }
        return {
            "connected": bool(conn.get("refresh_token")),
            "has_credentials": True,
            "client_id": conn.get("client_id", ""),
            "redirect_uri": conn.get("redirect_uri", ""),
            "ad_account_id": conn.get("ad_account_id", ""),
            "ad_account_name": conn.get("ad_account_name", ""),
            "updated_at": conn.get("updated_at"),
        }

    @router.post("/config")
    async def save_config(payload: SnapchatConfigIn, user: dict = Depends(current_user)):
        await db.snapchat_connections.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "user_id": user["id"],
                "client_id": payload.client_id.strip(),
                "client_secret": payload.client_secret.strip(),
                "redirect_uri": payload.redirect_uri.strip(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
        return {"ok": True}

    @router.delete("/config")
    async def disconnect(user: dict = Depends(current_user)):
        await db.snapchat_connections.delete_one({"user_id": user["id"]})
        return {"ok": True}

    class SelectAdAccountIn(BaseModel):
        ad_account_id: str
        ad_account_name: Optional[str] = ""
        timezone: Optional[str] = ""  # IANA name e.g. "Asia/Riyadh" from /adaccounts response
        currency: Optional[str] = ""

    @router.post("/select-adaccount")
    async def select_adaccount(payload: SelectAdAccountIn, user: dict = Depends(current_user)):
        update: dict = {
            "ad_account_id": payload.ad_account_id.strip(),
            "ad_account_name": (payload.ad_account_name or "").strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if payload.timezone:
            update["ad_account_timezone"] = payload.timezone.strip()
        if payload.currency:
            update["ad_account_currency"] = payload.currency.strip()
        res = await db.snapchat_connections.update_one(
            {"user_id": user["id"]},
            {"$set": update},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=400, detail="لم يتم العثور على حساب سناب مربوط")
        return {"ok": True}

    @router.get("/authorize-url")
    async def authorize_url(user: dict = Depends(current_user)):
        conn = await _get_conn(user["id"])
        if not conn or not conn.get("client_id") or not conn.get("redirect_uri"):
            raise HTTPException(status_code=400, detail="احفظ App ID و App Secret و Redirect URI أولاً")
        state = _encode_state(user["id"])
        params = {
            "response_type": "code",
            "client_id": conn["client_id"],
            "redirect_uri": conn["redirect_uri"],
            "scope": SNAPCHAT_SCOPE,
            "state": state,
        }
        return {"authorize_url": f"{SNAPCHAT_AUTH_URL}?{urlencode(params)}"}

    @router.get("/oauth/callback")
    async def oauth_callback(
        code: Optional[str] = Query(None),
        state: Optional[str] = Query(None),
        error: Optional[str] = Query(None),
        error_description: Optional[str] = Query(None),
    ):
        frontend = _frontend_url()
        if error:
            return RedirectResponse(
                url=f"{frontend}/settings?snapchat=error&msg={error_description or error}"
            )
        if not code or not state:
            return RedirectResponse(url=f"{frontend}/settings?snapchat=error&msg=missing_code_or_state")

        user_id = _decode_state(state)
        conn = await _get_conn(user_id)
        if not conn:
            return RedirectResponse(url=f"{frontend}/settings?snapchat=error&msg=config_missing")

        # Snapchat token endpoint expects credentials in form body. Some installations
        # additionally require HTTP Basic Auth — if the first call fails with
        # invalid_client, we retry with Basic Auth before giving up.
        form_data = {
            "code": code,
            "client_id": conn["client_id"],
            "client_secret": conn["client_secret"],
            "grant_type": "authorization_code",
            "redirect_uri": conn["redirect_uri"],
        }
        basic_data = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": conn["redirect_uri"],
        }
        async with httpx.AsyncClient(timeout=20.0) as http:
            try:
                resp = await http.post(SNAPCHAT_TOKEN_URL, data=form_data)
                if resp.status_code == 400 and "invalid_client" in (resp.text or "").lower():
                    # Retry with HTTP Basic Auth (RFC 6749 §2.3.1)
                    logger.info("Snapchat invalid_client via form body; retrying with Basic Auth")
                    resp = await http.post(
                        SNAPCHAT_TOKEN_URL,
                        data=basic_data,
                        auth=(conn["client_id"], conn["client_secret"]),
                    )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Snapchat token exchange failed (status=%s): %s",
                    exc.response.status_code, exc.response.text,
                )
                msg = (exc.response.text or "exchange_failed")[:300]
                return RedirectResponse(url=f"{frontend}/settings?snapchat=error&msg={msg}")
            except httpx.HTTPError:
                return RedirectResponse(url=f"{frontend}/settings?snapchat=error&msg=network_error")

        token = resp.json()
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_in = int(token.get("expires_in", 3600))
        if not access_token or not refresh_token:
            return RedirectResponse(url=f"{frontend}/settings?snapchat=error&msg=missing_tokens")

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        await db.snapchat_connections.update_one(
            {"user_id": user_id},
            {"$set": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "access_token_expires_at": expires_at,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return RedirectResponse(url=f"{frontend}/settings?snapchat=success")

    @router.get("/adaccounts")
    async def list_adaccounts(user: dict = Depends(current_user)):
        access_token, _ = await _ensure_access_token(user["id"])
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=20.0) as http:
            try:
                resp = await http.get(
                    f"{SNAPCHAT_API_BASE}/me/organizations",
                    headers=headers,
                    params={"with_ad_accounts": "true"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error("Snapchat adaccounts failed: %s", exc.response.text)
                raise HTTPException(status_code=502, detail=f"فشل جلب الحسابات: {exc.response.text[:200]}")
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"خطأ شبكة مع سناب: {exc}")

        data = resp.json()
        out = []
        # Snapchat returns organizations under `organizations` as list of wrappers:
        # [{ "organization": {... "ad_accounts": [{...}] }}]
        for wrap in data.get("organizations", []) or []:
            org = wrap.get("organization") if isinstance(wrap, dict) and "organization" in wrap else wrap
            org_id = org.get("id") if isinstance(org, dict) else None
            org_name = org.get("name") if isinstance(org, dict) else None
            accounts = (org or {}).get("ad_accounts") or (org or {}).get("adaccounts") or []
            for acc_wrap in accounts:
                acc = acc_wrap.get("adaccount") if isinstance(acc_wrap, dict) and "adaccount" in acc_wrap else acc_wrap
                if not isinstance(acc, dict):
                    continue
                out.append({
                    "organization_id": org_id,
                    "organization_name": org_name,
                    "ad_account_id": acc.get("id"),
                    "name": acc.get("name"),
                    "currency": acc.get("currency"),
                    "status": acc.get("status"),
                    "timezone": acc.get("timezone"),
                })
        return {"adaccounts": out}

    async def _resolve_ad_account_timezone(http: httpx.AsyncClient, access_token: str, ad_id: str, conn: dict) -> str:
        """Return IANA timezone (e.g. 'Asia/Riyadh') for the ad account.
        Falls back to UTC if not resolvable.
        """
        tz_name = (conn.get("ad_account_timezone") or "").strip()
        if tz_name:
            return tz_name
        try:
            r = await http.get(
                f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            wrap = (data.get("adaccounts") or [{}])[0]
            acc = wrap.get("adaccount") if isinstance(wrap, dict) and "adaccount" in wrap else wrap
            tz_name = (acc or {}).get("timezone") or ""
            if tz_name:
                # Cache for future calls
                await db.snapchat_connections.update_one(
                    {"user_id": conn["user_id"]},
                    {"$set": {"ad_account_timezone": tz_name}},
                )
                return tz_name
        except Exception as exc:
            logger.warning("Could not resolve ad account timezone, falling back to UTC: %s", exc)
        return "UTC"

    def _validate_iso_date(value: str) -> "date":
        """Strict YYYY-MM-DD parser. Rejects anything else (date picker output guard)."""
        import re
        from datetime import date as _date
        if not value or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            raise HTTPException(
                status_code=400,
                detail=f"صيغة التاريخ يجب أن تكون YYYY-MM-DD (المُرسَل: {value!r})",
            )
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"تاريخ غير صالح: {value!r}")

    # SAR is pegged to USD at 3.75 by the Saudi Central Bank (SAMA) since 1986.
    # This is constant and reliable, so we use it directly instead of calling
    # a third-party FX API (which would add latency and a possible failure mode).
    USD_TO_SAR = 3.75

    async def _resolve_ad_account_currency(http: httpx.AsyncClient, access_token: str, ad_id: str, conn: dict) -> str:
        """Return the ISO currency code of the ad account (e.g. 'USD', 'SAR').
        Cached on conn.ad_account_currency once resolved.
        """
        cur = (conn.get("ad_account_currency") or "").strip().upper()
        if cur:
            return cur
        try:
            r = await http.get(
                f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            wrap = (data.get("adaccounts") or [{}])[0]
            acc = wrap.get("adaccount") if isinstance(wrap, dict) and "adaccount" in wrap else wrap
            cur = ((acc or {}).get("currency") or "").upper()
            if cur:
                await db.snapchat_connections.update_one(
                    {"user_id": conn["user_id"]},
                    {"$set": {"ad_account_currency": cur}},
                )
        except Exception as exc:
            logger.warning("Could not resolve ad account currency, defaulting to SAR: %s", exc)
            cur = "SAR"
        return cur or "SAR"

    def _to_sar(amount: float, source_currency: str) -> tuple[float, float]:
        """Convert an amount in the ad account's currency to SAR.
        Returns (sar_amount, exchange_rate_applied).
        For SAR → 1:1. For USD → multiply by 3.75 (SAMA peg). For other
        currencies we don't recognise, return as-is (rate=1) so the user sees
        the raw number and can intervene.
        """
        cur = (source_currency or "").upper().strip()
        if cur in ("SAR", "ر.س", ""):
            return round(amount, 2), 1.0
        if cur == "USD":
            return round(amount * USD_TO_SAR, 2), USD_TO_SAR
        # Unknown currency: pass through with rate=1, but log so we notice.
        logger.warning("Unknown Snapchat ad account currency %r; leaving amount unchanged.", cur)
        return round(amount, 2), 1.0

    @router.get("/daily-spend")
    async def daily_spend(
        date: str = Query(..., description="YYYY-MM-DD"),
        ad_account_id: Optional[str] = Query(None),
        user: dict = Depends(current_user),
    ):
        # Validate date format FIRST so we fail fast on bad input
        # without consuming a token refresh.
        day = _validate_iso_date(date)

        access_token, conn = await _ensure_access_token(user["id"])
        ad_id = ad_account_id or conn.get("ad_account_id")
        if not ad_id:
            raise HTTPException(status_code=400, detail="حساب سناب غير مربوط. اربطه من الإعدادات.")

        # ── Riyadh-day boundary ──────────────────────────────────────────────
        # Per merchant requirement: "business day" is ALWAYS 00:00-23:59 Asia/Riyadh,
        # NOT the ad account's native timezone (which is often Pacific/PST).
        # Snapchat's DAY granularity REQUIRES start_time to be midnight in the
        # ad account's TZ — so we instead use HOUR granularity (which has no
        # such constraint) and sum the 24 hourly points overlapping the
        # Riyadh day. This guarantees the merchant sees "today's spend"
        # exactly as Riyadh experiences it, regardless of Snap's internal TZ.
        try:
            from zoneinfo import ZoneInfo  # py3.9+
        except ImportError:  # pragma: no cover
            ZoneInfo = None  # type: ignore

        riyadh_tz = ZoneInfo("Asia/Riyadh") if ZoneInfo else timezone(timedelta(hours=3))
        # Use the existing strict YYYY-MM-DD validator declared earlier in
        # this closure — guarantees the date passed by the caller is a real
        # ISO date and rejects free-text input.
        day = _validate_iso_date(date)
        start_riyadh = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=riyadh_tz)
        end_riyadh = start_riyadh + timedelta(days=1)

        # Snapchat HOUR granularity still wants start/end aligned to the hour
        # in the ad-account TZ. Riyadh midnight is always on an hour boundary
        # in any IANA tz, so the conversion is safe. We pass the ISO strings
        # in Riyadh offset (e.g. "+03:00"); Snapchat accepts ISO-8601 with any
        # offset for HOUR granularity.
        async with httpx.AsyncClient(timeout=20.0) as http:
            # Still resolve the ad-account TZ so we can show it in the UI
            # for diagnostic purposes (the banner says "yes we know Snap is
            # in PT, but we're aggregating in Riyadh time anyway").
            tz_name = await _resolve_ad_account_timezone(http, access_token, ad_id, conn)

            def _iso(dt: datetime) -> str:
                return dt.isoformat(timespec="seconds")

            params = {
                "start_time": _iso(start_riyadh),
                "end_time": _iso(end_riyadh),
                "granularity": "HOUR",
                "fields": "spend",
            }
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            try:
                resp = await http.get(
                    f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}/stats",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error("Snapchat stats failed: %s", exc.response.text)
                raise HTTPException(status_code=502, detail=f"فشل جلب الصرف: {exc.response.text[:200]}")
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502, detail=f"خطأ شبكة مع سناب: {exc}")

        data = resp.json()
        # Sum spend across all 24 hourly points falling inside the Riyadh day.
        # Snapchat returns each hourly bucket with start_time/end_time already
        # in the requested timezone, so we just sum all spend values.
        total_micro = 0
        for ts in data.get("timeseries_stats", []) or []:
            stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
            for point in stat.get("timeseries", []) or []:
                spend_val = (point.get("stats") or {}).get("spend", 0)
                try:
                    total_micro += int(spend_val)
                except (TypeError, ValueError):
                    pass
        # Fallback for older "stats" array
        if total_micro == 0:
            for entry in data.get("stats", []) or []:
                try:
                    total_micro += int(entry.get("spend", 0) or 0)
                except (TypeError, ValueError):
                    pass

        spend_native = round(total_micro / 1_000_000, 2)
        async with httpx.AsyncClient(timeout=10.0) as http2:
            currency = await _resolve_ad_account_currency(http2, access_token, ad_id, conn)
        spend_sar, fx_rate = _to_sar(spend_native, currency)

        return {
            "date": date,  # Always the Riyadh business date the merchant requested.
            "ad_account_id": ad_id,
            "spend": spend_sar,
            "spend_native": spend_native,
            "native_currency": currency,
            "fx_rate": fx_rate,
            "currency": "SAR",
            # Diagnostics — let the UI show "we aggregated in Riyadh time"
            # while also revealing the ad-account's native TZ.
            "ad_account_timezone": tz_name,
            "business_timezone": "Asia/Riyadh",
            "snap_day_start_riyadh": start_riyadh.strftime("%Y-%m-%d %H:%M"),
            "snap_day_end_riyadh": end_riyadh.strftime("%Y-%m-%d %H:%M"),
            "aggregation_method": "hourly_riyadh",
        }

    class BulkSpendIn(BaseModel):
        days: int = Field(default=7, ge=1, le=62)
        ad_account_id: Optional[str] = None
        from_date: Optional[str] = None  # YYYY-MM-DD (overrides `days`)
        to_date: Optional[str] = None    # YYYY-MM-DD (defaults to today)

    @router.post("/daily-spend/bulk")
    async def daily_spend_bulk(payload: BulkSpendIn, user: dict = Depends(current_user)):
        """Fetch Snapchat spend for a date range and upsert each into
        `daily_costs.snapchat_ads` so the dashboard reflects them immediately.

        Two modes:
          - Default: last N days ending today (payload.days)
          - Range: payload.from_date → payload.to_date (inclusive). Used by
            the "تحديث الشهر الحالي" button on the dashboard.

        Returns {saved, items:[...], errors:[{date, error}]}.
        """
        access_token, conn = await _ensure_access_token(user["id"])
        ad_id = payload.ad_account_id or conn.get("ad_account_id")
        if not ad_id:
            raise HTTPException(status_code=400, detail="اختر حساب إعلانات سناب أولاً")

        try:
            from zoneinfo import ZoneInfo
        except ImportError:  # pragma: no cover
            ZoneInfo = None  # type: ignore

        from datetime import date as _date
        saved: list[dict] = []
        errors: list[dict] = []

        async with httpx.AsyncClient(timeout=20.0) as http:
            tz_name = await _resolve_ad_account_timezone(http, access_token, ad_id, conn)
            # Resolve currency once for the whole batch (saves N redundant API
            # calls).
            ad_currency = await _resolve_ad_account_currency(http, access_token, ad_id, conn)
            try:
                tzinfo = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
            except Exception:
                tzinfo = timezone.utc

            # Riyadh-based business day boundary (per merchant requirement:
            # "today" is ALWAYS 00:00-23:59 Asia/Riyadh, never the ad-account's
            # native TZ). Used to enumerate dates AND to compute start/end of
            # each day. Snapchat HOUR granularity has no TZ alignment
            # requirement (unlike DAY), so we can request 24 hourly buckets
            # starting at Riyadh midnight verbatim.
            try:
                from zoneinfo import ZoneInfo as _ZI
                riyadh_tz = _ZI("Asia/Riyadh")
            except ImportError:  # pragma: no cover
                riyadh_tz = timezone(timedelta(hours=3))
            today_local = datetime.now(riyadh_tz).date()
            if payload.from_date or payload.to_date:
                try:
                    start_d = _date.fromisoformat(payload.from_date) if payload.from_date else today_local
                    end_d = _date.fromisoformat(payload.to_date) if payload.to_date else today_local
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date format; use YYYY-MM-DD")
                if end_d < start_d:
                    raise HTTPException(status_code=400, detail="to_date < from_date")
                span = (end_d - start_d).days + 1
                if span > 62:
                    raise HTTPException(status_code=400, detail="Range too wide (max 62 days)")
                dates: list[_date] = [start_d + timedelta(days=i) for i in range(span)]
            else:
                dates = [today_local - timedelta(days=i) for i in range(payload.days)]
                dates.reverse()  # ascending

            for d in dates:
                # Riyadh business-day window: 00:00 → 24:00 Asia/Riyadh
                start_local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=riyadh_tz)
                end_local = start_local + timedelta(days=1)
                base_headers = {"Authorization": f"Bearer {access_token}",
                                "Accept": "application/json"}
                stats_url = f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}/stats"

                # ── 1. SPEND (hourly, summed over the Riyadh day) ───────────
                # Using granularity=HOUR (instead of DAY) frees us from
                # Snapchat's "start_time must be midnight of ad-account TZ"
                # constraint, so we can request the Riyadh day directly.
                spend_params = {
                    "start_time": start_local.isoformat(timespec="seconds"),
                    "end_time": end_local.isoformat(timespec="seconds"),
                    "granularity": "HOUR",
                    "fields": "spend",
                }
                try:
                    resp = await http.get(stats_url, headers=base_headers, params=spend_params)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # Parse Snapchat's friendly debug_message if present.
                    body = exc.response.text or ""
                    try:
                        import json as _json
                        j = _json.loads(body)
                        snap_msg = j.get("debug_message") or j.get("request_status") or body
                    except Exception:
                        snap_msg = body
                    errors.append({"date": d.isoformat(),
                                   "error": str(snap_msg)[:240]})
                    continue
                except httpx.HTTPError as exc:
                    errors.append({"date": d.isoformat(), "error": str(exc)[:200]})
                    continue

                data = resp.json()
                total_micro = 0
                for ts in data.get("timeseries_stats", []) or []:
                    stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
                    for point in stat.get("timeseries", []) or []:
                        s = point.get("stats") or {}
                        try:
                            total_micro += int(s.get("spend", 0) or 0)
                        except (TypeError, ValueError):
                            pass

                # ── 2. CONVERSIONS (best-effort) ────────────────────────────
                # These require attribution windows; if Snapchat rejects the
                # query (Unsupported Stats Query for accounts without active
                # Pixel events), we silently fall back to purchases=0 and let
                # the dashboard read store-side orders instead.
                total_purchases = 0
                total_purchases_value_micro = 0
                conv_params = {
                    "start_time": start_local.isoformat(timespec="seconds"),
                    "end_time": end_local.isoformat(timespec="seconds"),
                    "granularity": "HOUR",
                    "fields": "conversion_purchases,conversion_purchases_value",
                    "swipe_up_attribution_window": "28_DAY",
                    "view_attribution_window": "1_DAY",
                }
                try:
                    conv_resp = await http.get(stats_url, headers=base_headers, params=conv_params)
                    if conv_resp.status_code < 400:
                        cdata = conv_resp.json()
                        for ts in cdata.get("timeseries_stats", []) or []:
                            stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
                            for point in stat.get("timeseries", []) or []:
                                s = point.get("stats") or {}
                                try:
                                    total_purchases += int(s.get("conversion_purchases", 0) or 0)
                                except (TypeError, ValueError):
                                    pass
                                try:
                                    total_purchases_value_micro += int(s.get("conversion_purchases_value", 0) or 0)
                                except (TypeError, ValueError):
                                    pass
                    else:
                        # Conversions not available → log once at debug level
                        # and move on. Spend (the must-have) is already saved.
                        logger.info("Snap conversions skipped for %s: HTTP %s",
                                    d.isoformat(), conv_resp.status_code)
                except httpx.HTTPError as exc:
                    logger.info("Snap conversions skipped for %s: %s",
                                d.isoformat(), exc)

                spend_native = round(total_micro / 1_000_000, 2)
                revenue_native = round(total_purchases_value_micro / 1_000_000, 2)
                spend, fx_rate = _to_sar(spend_native, ad_currency)
                revenue, _ = _to_sar(revenue_native, ad_currency)

                # Upsert into daily_costs, preserving other fields (snapchat_ads_2,
                # tiktok, etc) if the row already exists.
                date_str = d.isoformat()
                existing = await db.daily_costs.find_one(
                    {"user_id": user["id"], "date": date_str}, {"_id": 0}
                )
                if existing:
                    await db.daily_costs.update_one(
                        {"user_id": user["id"], "date": date_str},
                        {"$set": {"snapchat_ads": spend,
                                  "updated_at": datetime.now(timezone.utc).isoformat()}},
                    )
                else:
                    import uuid as _uuid
                    await db.daily_costs.insert_one({
                        "id": str(_uuid.uuid4()),
                        "user_id": user["id"],
                        "date": date_str,
                        "snapchat_ads": spend,
                        "snapchat_ads_2": 0.0,
                        "tiktok_ads": 0.0,
                        "instagram_ads": 0.0,
                        "google_ads": 0.0,
                        "product_costs": 0.0,
                        "notes": f"auto from Snapchat ({ad_currency}→SAR ×{fx_rate})" if fx_rate != 1 else "auto from Snapchat",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })

                # Separately persist Snapchat-reported conversions (Pixel data)
                # so the dashboard can read attributed orders + revenue from
                # the ad platform itself rather than from unified_orders.
                await db.snapchat_daily_stats.update_one(
                    {"user_id": user["id"], "date": date_str},
                    {"$set": {
                        "user_id": user["id"],
                        "date": date_str,
                        "spend": spend,
                        "spend_native": spend_native,
                        "revenue": revenue,
                        "revenue_native": revenue_native,
                        "purchases": total_purchases,
                        "currency_native": ad_currency,
                        "fx_rate": fx_rate,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }},
                    upsert=True,
                )

                saved.append({
                    "date": date_str,
                    "spend": spend,
                    "spend_native": spend_native,
                    "revenue": revenue,
                    "purchases": total_purchases,
                    "native_currency": ad_currency,
                    "fx_rate": fx_rate,
                })

        return {
            "saved": len(saved),
            "items": saved,
            "errors": errors,
            "native_currency": ad_currency,
            "currency": "SAR",
            "ad_account_timezone": tz_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Multi-Account Selection (NEW — iteration 15) ─────────────────────
    # Merchants with multiple Snapchat ad accounts (e.g. one per brand, or
    # one for SA market + one for AE) want to enable several at once and
    # see the AGGREGATED spend on the dashboard plus a per-account drill-down
    # page. We keep the legacy single `ad_account_id` field on
    # snapchat_connections for back-compat, but the source of truth for
    # "which accounts are active" is now the `snapchat_ad_accounts`
    # collection (one doc per (user_id, ad_account_id)).

    class _SelectedAccount(BaseModel):
        ad_account_id: str = Field(min_length=1)
        name: str = Field(default="")
        currency: str = Field(default="")
        timezone: str = Field(default="")
        organization_id: Optional[str] = ""
        organization_name: Optional[str] = ""
        status: Optional[str] = ""

    class _SelectedAccountsIn(BaseModel):
        accounts: list[_SelectedAccount] = Field(default_factory=list)

    @router.get("/selected-accounts")
    async def get_selected_accounts(user: dict = Depends(current_user)):
        """Return the list of Snapchat ad accounts the merchant has explicitly
        enabled. Each returned doc carries the native currency + timezone +
        last_sync_at so the UI can render badges without an extra API call."""
        rows = await db.snapchat_ad_accounts.find(
            {"user_id": user["id"], "enabled": True}, {"_id": 0, "user_id": 0},
        ).sort("name", 1).to_list(50)
        return {"accounts": rows, "count": len(rows)}

    @router.put("/selected-accounts")
    async def set_selected_accounts(
        payload: _SelectedAccountsIn, user: dict = Depends(current_user),
    ):
        """Replace the enabled-account set. Accounts in the payload are
        upserted as `enabled=True`; any previously-enabled accounts NOT
        present in this payload get `enabled=False` (we keep the document
        with its history so the merchant can re-enable later without
        losing sync metadata)."""
        uid = user["id"]
        now_iso = datetime.now(timezone.utc).isoformat()
        incoming_ids: set[str] = set()
        for acc in payload.accounts:
            ad_id = acc.ad_account_id.strip()
            if not ad_id:
                continue
            incoming_ids.add(ad_id)
            doc = {
                "user_id": uid,
                "ad_account_id": ad_id,
                "name": (acc.name or "").strip(),
                "currency_native": (acc.currency or "").strip().upper(),
                "timezone": (acc.timezone or "").strip(),
                "organization_id": (acc.organization_id or "").strip(),
                "organization_name": (acc.organization_name or "").strip(),
                "status": (acc.status or "").strip(),
                "enabled": True,
                "updated_at": now_iso,
            }
            await db.snapchat_ad_accounts.update_one(
                {"user_id": uid, "ad_account_id": ad_id},
                {"$set": doc, "$setOnInsert": {"created_at": now_iso}},
                upsert=True,
            )
        # Disable accounts that are no longer in the selected set.
        if incoming_ids:
            await db.snapchat_ad_accounts.update_many(
                {"user_id": uid, "ad_account_id": {"$nin": list(incoming_ids)}},
                {"$set": {"enabled": False, "updated_at": now_iso}},
            )
        else:
            # Empty payload → disable all.
            await db.snapchat_ad_accounts.update_many(
                {"user_id": uid},
                {"$set": {"enabled": False, "updated_at": now_iso}},
            )
        # Keep the legacy single-account field in sync: point it at the
        # FIRST enabled account so existing per-account endpoints keep
        # working without a connected merchant noticing a break.
        first_enabled = await db.snapchat_ad_accounts.find_one(
            {"user_id": uid, "enabled": True},
            {"_id": 0, "ad_account_id": 1, "name": 1, "currency_native": 1, "timezone": 1},
            sort=[("name", 1)],
        )
        legacy_patch: dict = {"updated_at": now_iso}
        if first_enabled:
            legacy_patch.update({
                "ad_account_id": first_enabled["ad_account_id"],
                "ad_account_name": first_enabled.get("name", ""),
                "ad_account_currency": first_enabled.get("currency_native", ""),
                "ad_account_timezone": first_enabled.get("timezone", ""),
            })
        await db.snapchat_connections.update_one(
            {"user_id": uid}, {"$set": legacy_patch},
        )
        enabled_count = await db.snapchat_ad_accounts.count_documents(
            {"user_id": uid, "enabled": True},
        )
        return {"ok": True, "enabled_count": enabled_count}

    async def _sync_one_account(
        http: httpx.AsyncClient,
        access_token: str,
        uid: str,
        account_doc: dict,
        dates: list,
        riyadh_tz,
    ) -> tuple[int, list]:
        """Sync ONE Snapchat ad account for a range of Riyadh dates.

        Writes per-(account, date) rows into `snapchat_account_daily` AND
        accumulates daily totals (across all accounts of this user) so the
        caller can upsert the SUM into legacy `daily_costs.snapchat_ads`.

        Returns (saved_count, errors[]). The caller is responsible for
        aggregating daily_costs.snapchat_ads after iterating over all
        accounts.
        """
        ad_id = account_doc["ad_account_id"]
        ad_currency = (account_doc.get("currency_native") or "SAR").upper() or "SAR"
        ad_name = account_doc.get("name") or ""
        ad_tz = account_doc.get("timezone") or ""
        base_headers = {"Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"}
        stats_url = f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}/stats"
        saved = 0
        errors: list = []
        for d in dates:
            start_local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=riyadh_tz)
            end_local = start_local + timedelta(days=1)
            # ── Spend (HOUR granularity to bypass Snap's DAY TZ constraint) ──
            spend_params = {
                "start_time": start_local.isoformat(timespec="seconds"),
                "end_time": end_local.isoformat(timespec="seconds"),
                "granularity": "HOUR",
                "fields": "spend",
            }
            try:
                resp = await http.get(stats_url, headers=base_headers, params=spend_params)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body = (exc.response.text or "")[:240]
                try:
                    import json as _json
                    j = _json.loads(body)
                    snap_msg = j.get("debug_message") or j.get("request_status") or body
                except Exception:
                    snap_msg = body
                errors.append({"ad_account_id": ad_id, "date": d.isoformat(),
                               "error": str(snap_msg)[:240]})
                continue
            except httpx.HTTPError as exc:
                errors.append({"ad_account_id": ad_id, "date": d.isoformat(),
                               "error": str(exc)[:200]})
                continue

            data = resp.json()
            total_micro = 0
            for ts in data.get("timeseries_stats", []) or []:
                stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
                for point in stat.get("timeseries", []) or []:
                    try:
                        total_micro += int((point.get("stats") or {}).get("spend", 0) or 0)
                    except (TypeError, ValueError):
                        pass

            # Conversions — best-effort (won't fail spend save).
            total_purchases = 0
            total_purchases_value_micro = 0
            conv_params = {
                "start_time": start_local.isoformat(timespec="seconds"),
                "end_time": end_local.isoformat(timespec="seconds"),
                "granularity": "HOUR",
                "fields": "conversion_purchases,conversion_purchases_value",
                "swipe_up_attribution_window": "28_DAY",
                "view_attribution_window": "1_DAY",
            }
            try:
                conv_resp = await http.get(stats_url, headers=base_headers, params=conv_params)
                if conv_resp.status_code < 400:
                    cdata = conv_resp.json()
                    for ts in cdata.get("timeseries_stats", []) or []:
                        stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
                        for point in stat.get("timeseries", []) or []:
                            s = point.get("stats") or {}
                            try:
                                total_purchases += int(s.get("conversion_purchases", 0) or 0)
                            except (TypeError, ValueError):
                                pass
                            try:
                                total_purchases_value_micro += int(s.get("conversion_purchases_value", 0) or 0)
                            except (TypeError, ValueError):
                                pass
            except httpx.HTTPError:
                pass

            spend_native = round(total_micro / 1_000_000, 2)
            revenue_native = round(total_purchases_value_micro / 1_000_000, 2)
            spend_sar, fx_rate = _to_sar(spend_native, ad_currency)
            revenue_sar, _ = _to_sar(revenue_native, ad_currency)
            date_str = d.isoformat()

            await db.snapchat_account_daily.update_one(
                {"user_id": uid, "ad_account_id": ad_id, "date": date_str},
                {"$set": {
                    "user_id": uid,
                    "ad_account_id": ad_id,
                    "account_name": ad_name,
                    "date": date_str,
                    "spend_native": spend_native,
                    "currency_native": ad_currency,
                    "fx_rate": fx_rate,
                    "spend_sar": spend_sar,
                    "spend": spend_sar,  # alias for legacy reads
                    "purchases": total_purchases,
                    "revenue_native": revenue_native,
                    "revenue_sar": revenue_sar,
                    "business_timezone": "Asia/Riyadh",
                    "ad_account_timezone": ad_tz,
                    "snap_day_start_riyadh": start_local.strftime("%Y-%m-%d %H:%M"),
                    "snap_day_end_riyadh": end_local.strftime("%Y-%m-%d %H:%M"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                 "$setOnInsert": {
                     "created_at": datetime.now(timezone.utc).isoformat(),
                 }},
                upsert=True,
            )
            saved += 1

        # Mark this account's last_sync_at.
        await db.snapchat_ad_accounts.update_one(
            {"user_id": uid, "ad_account_id": ad_id},
            {"$set": {"last_sync_at": datetime.now(timezone.utc).isoformat()}},
        )
        return saved, errors

    class _SyncAllIn(BaseModel):
        days: int = Field(default=7, ge=1, le=62)
        from_date: Optional[str] = None
        to_date: Optional[str] = None

    @router.post("/sync-all-accounts")
    async def sync_all_accounts(payload: _SyncAllIn, user: dict = Depends(current_user)):
        """Sync EVERY enabled Snapchat ad account for the given Riyadh-date
        range. Writes per-(account, date) rows into `snapchat_account_daily`
        and updates the AGGREGATED total into legacy `daily_costs.snapchat_ads`
        so the existing dashboard card + reports continue to show the
        cross-account total without any other code change.

        Body: {days|from_date|to_date}. Returns {accounts_synced, items[],
        errors[]} per-account.
        """
        uid = user["id"]
        access_token, _ = await _ensure_access_token(uid)
        enabled = await db.snapchat_ad_accounts.find(
            {"user_id": uid, "enabled": True}, {"_id": 0},
        ).to_list(50)
        if not enabled:
            raise HTTPException(status_code=400,
                                detail="لم يتم تفعيل أي حساب Snapchat بعد. اختر حساباً واحداً أو أكثر من الإعدادات.")

        # Riyadh-anchored date enumeration (00:00 → 23:59 Asia/Riyadh — per
        # merchant requirement: SA timezone is the source of truth, NOT PDT).
        try:
            from zoneinfo import ZoneInfo as _ZI
            riyadh_tz = _ZI("Asia/Riyadh")
        except ImportError:  # pragma: no cover
            riyadh_tz = timezone(timedelta(hours=3))
        from datetime import date as _date
        today_local = datetime.now(riyadh_tz).date()
        if payload.from_date or payload.to_date:
            try:
                start_d = _date.fromisoformat(payload.from_date) if payload.from_date else today_local
                end_d = _date.fromisoformat(payload.to_date) if payload.to_date else today_local
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format; use YYYY-MM-DD")
            if end_d < start_d:
                raise HTTPException(status_code=400, detail="to_date < from_date")
            span = (end_d - start_d).days + 1
            if span > 62:
                raise HTTPException(status_code=400, detail="Range too wide (max 62 days)")
            dates = [start_d + timedelta(days=i) for i in range(span)]
        else:
            dates = [today_local - timedelta(days=i) for i in range(payload.days)]
            dates.reverse()

        accounts_summary: list[dict] = []
        all_errors: list[dict] = []
        async with httpx.AsyncClient(timeout=30.0) as http:
            for account_doc in enabled:
                saved, errs = await _sync_one_account(
                    http, access_token, uid, account_doc, dates, riyadh_tz,
                )
                accounts_summary.append({
                    "ad_account_id": account_doc["ad_account_id"],
                    "name": account_doc.get("name"),
                    "currency_native": account_doc.get("currency_native"),
                    "rows_saved": saved,
                    "errors": len(errs),
                })
                all_errors.extend(errs)

        # ── Aggregate per-date totals across ALL accounts and write back to
        # legacy `daily_costs.snapchat_ads` so the dashboard card + existing
        # reports continue to render the cross-account total without any
        # other code change. Also update `snapchat_daily_stats` (used by the
        # snapchat-summary card for orders+revenue) with cross-account
        # aggregates.
        for d in dates:
            date_str = d.isoformat()
            account_rows = await db.snapchat_account_daily.find(
                {"user_id": uid, "date": date_str},
                {"_id": 0, "spend_sar": 1, "revenue_sar": 1, "purchases": 1,
                 "spend_native": 1, "revenue_native": 1, "currency_native": 1, "fx_rate": 1},
            ).to_list(50)
            sum_spend = round(sum(float(r.get("spend_sar") or 0) for r in account_rows), 2)
            sum_revenue = round(sum(float(r.get("revenue_sar") or 0) for r in account_rows), 2)
            sum_purchases = sum(int(r.get("purchases") or 0) for r in account_rows)

            # Legacy daily_costs upsert (preserves other fields).
            existing_dc = await db.daily_costs.find_one(
                {"user_id": uid, "date": date_str}, {"_id": 0},
            )
            if existing_dc:
                await db.daily_costs.update_one(
                    {"user_id": uid, "date": date_str},
                    {"$set": {"snapchat_ads": sum_spend,
                              "updated_at": datetime.now(timezone.utc).isoformat()}},
                )
            else:
                import uuid as _uuid
                await db.daily_costs.insert_one({
                    "id": str(_uuid.uuid4()),
                    "user_id": uid,
                    "date": date_str,
                    "snapchat_ads": sum_spend,
                    "snapchat_ads_2": 0.0,
                    "tiktok_ads": 0.0,
                    "instagram_ads": 0.0,
                    "google_ads": 0.0,
                    "product_costs": 0.0,
                    "notes": f"auto from Snapchat ({len(account_rows)} حساب)",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            # Aggregate Pixel stats too so the dashboard card shows the
            # cross-account orders+revenue.
            await db.snapchat_daily_stats.update_one(
                {"user_id": uid, "date": date_str},
                {"$set": {
                    "user_id": uid,
                    "date": date_str,
                    "spend": sum_spend,
                    "revenue": sum_revenue,
                    "purchases": sum_purchases,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )

        return {
            "accounts_synced": len(accounts_summary),
            "items": accounts_summary,
            "errors": all_errors,
            "currency": "SAR",
            "business_timezone": "Asia/Riyadh",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @router.get("/accounts-summary")
    async def accounts_summary(user: dict = Depends(current_user)):
        """Per-account spend breakdown for the new "حسابات Snapchat" detail
        page. Returns today/month/30-day spend in BOTH native currency and
        SAR for each enabled account, plus the FX rate that was applied.

        All windows use Asia/Riyadh boundaries (matches the merchant's
        business day). Empty `accounts` list with `total_*` zeros if no
        account is enabled — UI shows a friendly empty-state.
        """
        uid = user["id"]
        try:
            from zoneinfo import ZoneInfo as _ZI
            riyadh_tz = _ZI("Asia/Riyadh")
        except ImportError:  # pragma: no cover
            riyadh_tz = timezone(timedelta(hours=3))
        today_d = datetime.now(riyadh_tz).date()
        today_str = today_d.isoformat()
        month_start_str = today_str[:8] + "01"
        d30_start_str = (today_d - timedelta(days=29)).isoformat()

        enabled = await db.snapchat_ad_accounts.find(
            {"user_id": uid, "enabled": True}, {"_id": 0, "user_id": 0},
        ).sort("name", 1).to_list(50)

        out: list = []
        total_today_sar = total_today_native = 0.0
        total_month_sar = 0.0
        total_30d_sar = 0.0
        for acc in enabled:
            ad_id = acc["ad_account_id"]
            rows = await db.snapchat_account_daily.find(
                {"user_id": uid, "ad_account_id": ad_id,
                 "date": {"$gte": d30_start_str, "$lte": today_str}},
                {"_id": 0, "date": 1, "spend_sar": 1, "spend_native": 1,
                 "currency_native": 1, "fx_rate": 1, "purchases": 1,
                 "revenue_sar": 1, "revenue_native": 1},
            ).to_list(60)
            by_date = {r["date"]: r for r in rows}
            spend_today_sar = round(float((by_date.get(today_str) or {}).get("spend_sar") or 0), 2)
            spend_today_nat = round(float((by_date.get(today_str) or {}).get("spend_native") or 0), 2)
            spend_month_sar = round(sum(float(r.get("spend_sar") or 0)
                                        for k, r in by_date.items() if k >= month_start_str), 2)
            spend_30d_sar = round(sum(float(r.get("spend_sar") or 0)
                                      for r in by_date.values()), 2)
            cur_native = acc.get("currency_native") or "SAR"
            # Pick the freshest fx_rate (most recent row); fall back to 1 if
            # SAR-native account.
            fx_rate = 1.0
            if rows:
                fx_rate = float(rows[-1].get("fx_rate") or 1.0)
            elif cur_native == "USD":
                fx_rate = 3.75
            out.append({
                "ad_account_id": ad_id,
                "name": acc.get("name") or ad_id,
                "currency_native": cur_native,
                "currency_display": "SAR",
                "fx_rate": fx_rate,
                "timezone": acc.get("timezone"),
                "business_timezone": "Asia/Riyadh",
                "last_sync_at": acc.get("last_sync_at"),
                "today": {"spend_sar": spend_today_sar, "spend_native": spend_today_nat},
                "month": {"start": month_start_str, "spend_sar": spend_month_sar},
                "last_30d": {"start": d30_start_str, "spend_sar": spend_30d_sar},
            })
            total_today_sar += spend_today_sar
            total_today_native += spend_today_nat if cur_native == "SAR" else 0
            total_month_sar += spend_month_sar
            total_30d_sar += spend_30d_sar

        return {
            "accounts": out,
            "count": len(out),
            "today": {"date": today_str, "spend_sar": round(total_today_sar, 2)},
            "month": {"start": month_start_str, "spend_sar": round(total_month_sar, 2)},
            "last_30d": {"start": d30_start_str, "spend_sar": round(total_30d_sar, 2)},
            "business_timezone": "Asia/Riyadh",
            "currency": "SAR",
        }

    return router


def attach_snapchat_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
