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

        spend = round(total_micro / 1_000_000, 2)
        return {"date": date, "ad_account_id": ad_id, "spend": spend, "currency": data.get("currency") or "—"}

    return router


def attach_snapchat_routes(parent_router: APIRouter, db) -> None:
    parent_router.include_router(_build_router(db))
