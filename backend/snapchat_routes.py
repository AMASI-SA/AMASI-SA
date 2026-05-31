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
            raise HTTPException(status_code=400, detail="اختر حساب إعلانات سناب أولاً")

        # Resolve the ad account's timezone — Snapchat's DAY granularity stats
        # REQUIRE start_time and end_time to align with the ad account's local
        # day boundary (00:00:00 in its own timezone). Sending UTC midnight
        # when the account is in Asia/Riyadh (UTC+3) triggers the error:
        # "Timeseries queries with DAY granularity must have a start time that
        #  is the start of the day."
        try:
            from zoneinfo import ZoneInfo  # py3.9+
        except ImportError:  # pragma: no cover
            ZoneInfo = None  # type: ignore

        async with httpx.AsyncClient(timeout=20.0) as http:
            tz_name = await _resolve_ad_account_timezone(http, access_token, ad_id, conn)
            try:
                tzinfo = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
            except Exception:
                tzinfo = timezone.utc

            start_local = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tzinfo)
            end_local = start_local + timedelta(days=1)
            # Snapchat accepts ISO-8601 with offset; format with explicit offset like "+03:00"
            def _iso(dt: datetime) -> str:
                s = dt.isoformat(timespec="seconds")
                # Python emits "+03:00"; Snapchat handles that. For UTC tz, ensure "+00:00".
                if s.endswith("+00:00"):
                    return s
                return s

            params = {
                "start_time": _iso(start_local),
                "end_time": _iso(end_local),
                "granularity": "DAY",
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
        # Snapchat returns spend in micro-currency (1/1,000,000 of currency unit).
        total_micro = 0
        # Response shape: { "timeseries_stats": [ { "timeseries_stat": { "id":..., "type":"AD_ACCOUNT",
        #   "granularity":"DAY", "start_time":..., "end_time":..., "timeseries":[{"start_time":..,"end_time":..,"stats":{"spend":N}}]}}]}
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
        # Convert to SAR if the ad account is denominated in another currency.
        async with httpx.AsyncClient(timeout=10.0) as http2:
            currency = await _resolve_ad_account_currency(http2, access_token, ad_id, conn)
        spend_sar, fx_rate = _to_sar(spend_native, currency)
        return {
            "date": date,
            "ad_account_id": ad_id,
            "spend": spend_sar,
            "spend_native": spend_native,
            "native_currency": currency,
            "fx_rate": fx_rate,
            "currency": "SAR",
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

            # Build the list of dates to fetch. If the user passed explicit
            # from_date/to_date (range mode), honor those bounds; otherwise
            # use the last N days ending today.
            today_local = datetime.now(tzinfo).date()
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
                start_local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tzinfo)
                end_local = start_local + timedelta(days=1)
                params = {
                    "start_time": start_local.isoformat(timespec="seconds"),
                    "end_time": end_local.isoformat(timespec="seconds"),
                    "granularity": "DAY",
                    # Pull spend + Pixel-attributed conversions/revenue. Snapchat
                    # exposes these only when the ad-account has a working Snap
                    # Pixel reporting conversion events.
                    "fields": "spend,conversion_purchases,conversion_purchases_value",
                }
                try:
                    resp = await http.get(
                        f"{SNAPCHAT_API_BASE}/adaccounts/{ad_id}/stats",
                        headers={"Authorization": f"Bearer {access_token}",
                                 "Accept": "application/json"},
                        params=params,
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    errors.append({"date": d.isoformat(),
                                   "error": exc.response.text[:200]})
                    continue
                except httpx.HTTPError as exc:
                    errors.append({"date": d.isoformat(), "error": str(exc)[:200]})
                    continue

                data = resp.json()
                total_micro = 0
                total_purchases = 0
                total_purchases_value_micro = 0
                for ts in data.get("timeseries_stats", []) or []:
                    stat = ts.get("timeseries_stat", ts) if isinstance(ts, dict) else {}
                    for point in stat.get("timeseries", []) or []:
                        s = point.get("stats") or {}
                        try:
                            total_micro += int(s.get("spend", 0) or 0)
                        except (TypeError, ValueError):
                            pass
                        try:
                            total_purchases += int(s.get("conversion_purchases", 0) or 0)
                        except (TypeError, ValueError):
                            pass
                        try:
                            total_purchases_value_micro += int(s.get("conversion_purchases_value", 0) or 0)
                        except (TypeError, ValueError):
                            pass
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
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    return router


def attach_snapchat_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
