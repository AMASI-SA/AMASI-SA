"""Ads V2 — Phase 1 sync adapters.

Each adapter fetches a single (account, date) tuple from the provider
and returns a normalized row:

    {
      "spend_native":   float,
      "currency_native": str,
      "impressions":    int,
      "clicks":         int,
      "purchases":      int,
      "raw_excerpt":    dict,   # for audit only
    }

If the API call fails, the adapter returns (None, status_dict). Status
codes used:
  • token_invalid       → access_token rejected (401)
  • not_found           → account does not exist or no access
  • rate_limited        → 429
  • http_error          → other 4xx/5xx
  • exception           → network / parsing error
  • empty               → API returned no data for that day

Adapters NEVER mutate V1 collections.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("ads_v2.sync.adapters")


# ─────────────────────────────────────────────────────────────────────
# Meta
# ─────────────────────────────────────────────────────────────────────
META_BASE = "https://graph.facebook.com/v23.0"


async def fetch_meta_day(
    access_token: str, external_account_id: str, date_iso: str,
) -> tuple[Optional[dict], dict]:
    """Fetch one day of spend for a Meta ad account."""
    if not access_token or not external_account_id:
        return None, {"code": "missing_input"}
    if not external_account_id.startswith("act_"):
        external_account_id = f"act_{external_account_id}"

    url = f"{META_BASE}/{external_account_id}/insights"
    # Important Meta API params for accuracy:
    #   • level=account → includes ALL campaigns (active+paused+deleted+archived)
    #   • use_account_attribution_setting=true → uses the account's chosen
    #       attribution window so numbers match Ads Manager exactly.
    #   • use_unified_attribution_setting=true → Meta's newer unified window.
    #   • time_increment=1 + time_range(since==until) → single daily bucket.
    #   • Date is interpreted in the account timezone Meta has configured.
    params = {
        "fields": (
            "spend,impressions,clicks,actions,action_values,"
            "account_currency,date_start,date_stop"
        ),
        "time_range": '{"since":"' + date_iso + '","until":"' + date_iso + '"}',
        "time_increment": 1,
        "level": "account",
        "use_account_attribution_setting": "true",
        "use_unified_attribution_setting": "true",
        "limit": 500,
        "access_token": access_token,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, params=params)
            if resp.status_code == 401:
                return None, {"code": "token_invalid",
                                "body": resp.text[:200]}
            if resp.status_code == 429:
                return None, {"code": "rate_limited"}
            if resp.status_code >= 400:
                return None, {"code": "http_error",
                                "status": resp.status_code,
                                "body": resp.text[:200]}
            data = resp.json() or {}
            rows = data.get("data") or []
            if not rows:
                # Empty day → register as zero-spend row so we have a
                # daily marker (helps detect "missing data" anomalies).
                return {
                    "spend_native":     0.0,
                    "currency_native":  "SAR",
                    "impressions":      0,
                    "clicks":           0,
                    "purchases":        0,
                    "raw_excerpt":      {"empty": True},
                }, {"code": "empty"}
            r = rows[0]
            spend = float(r.get("spend") or 0)
            impr = int(r.get("impressions") or 0)
            clk = int(r.get("clicks") or 0)
            account_currency = r.get("account_currency") or "SAR"
            purchases = 0
            for act in (r.get("actions") or []):
                if act.get("action_type") == "purchase":
                    purchases = int(float(act.get("value") or 0))
                    break
            return {
                "spend_native":    spend,
                "currency_native": account_currency,
                "impressions":     impr,
                "clicks":          clk,
                "purchases":       purchases,
                "raw_excerpt": {
                    "actions_count": len(r.get("actions") or []),
                    "date_start":    r.get("date_start"),
                    "date_stop":     r.get("date_stop"),
                    "account_currency": account_currency,
                    "attribution": "use_account_attribution_setting+unified",
                },
            }, {"code": "ok"}
    except Exception as exc:
        return None, {"code": "exception", "message": str(exc)[:200]}


# ─────────────────────────────────────────────────────────────────────
# Snapchat
# ─────────────────────────────────────────────────────────────────────
SNAP_BASE = "https://adsapi.snapchat.com/v1"


async def fetch_snapchat_day(
    access_token: str, external_account_id: str, date_iso: str,
    account_timezone: str = "Asia/Riyadh",
) -> tuple[Optional[dict], dict]:
    """Fetch one day of spend for a Snap ad account.

    Uses TOTAL granularity over a 24-hour window anchored to the
    account's timezone (start = date_iso 00:00 local, end = +24h).
    """
    if not access_token or not external_account_id:
        return None, {"code": "missing_input"}

    # Build 24h ISO window in the account timezone.
    # Snapchat accepts ISO8601 with offset (e.g. 2026-06-23T00:00:00.000-07:00).
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime, timedelta as _td
        d = datetime.fromisoformat(date_iso).replace(
            tzinfo=ZoneInfo(account_timezone))
        end = d + _td(days=1)
        start_iso = d.isoformat(timespec="milliseconds")
        end_iso = end.isoformat(timespec="milliseconds")
    except Exception as exc:
        return None, {"code": "tz_error", "message": str(exc)[:200]}

    headers = {"Authorization": f"Bearer {access_token}"}
    url = (
        f"{SNAP_BASE}/adaccounts/{external_account_id}/stats"
        f"?granularity=TOTAL&start_time={start_iso}&end_time={end_iso}"
        f"&fields=spend,impressions,swipes"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, headers=headers)
            if resp.status_code == 401:
                return None, {"code": "token_invalid"}
            if resp.status_code == 404:
                return None, {"code": "not_found"}
            if resp.status_code == 429:
                return None, {"code": "rate_limited"}
            if resp.status_code >= 400:
                return None, {"code": "http_error",
                                "status": resp.status_code,
                                "body": resp.text[:200]}
            data = resp.json() or {}
            stats = (data.get("total_stats") or [])
            if not stats:
                return {
                    "spend_native":    0.0,
                    "currency_native": "USD",
                    "impressions":     0,
                    "clicks":          0,
                    "purchases":       0,
                    "raw_excerpt":     {"empty": True},
                }, {"code": "empty"}
            row = (stats[0] or {}).get("total_stat") or {}
            stat = row.get("stats") or {}
            # Snap spend is in micro-currency-units (1e6 = 1 USD)
            spend_micros = float(stat.get("spend") or 0)
            spend = spend_micros / 1_000_000.0
            return {
                "spend_native":    spend,
                "currency_native": "USD",  # Snap reports in USD
                "impressions":     int(stat.get("impressions") or 0),
                "clicks":          int(stat.get("swipes") or 0),
                "purchases":       0,  # Snap conversion fetching is separate
                "raw_excerpt":     {"granularity": "TOTAL",
                                     "spend_micros": spend_micros},
            }, {"code": "ok"}
    except Exception as exc:
        return None, {"code": "exception", "message": str(exc)[:200]}


# ─────────────────────────────────────────────────────────────────────
# TikTok
# ─────────────────────────────────────────────────────────────────────
TIKTOK_BASE = "https://business-api.tiktok.com/open_api/v1.3"


async def fetch_tiktok_day(
    access_token: str, external_account_id: str, date_iso: str,
) -> tuple[Optional[dict], dict]:
    """Fetch one day of spend for a TikTok advertiser account."""
    if not access_token or not external_account_id:
        return None, {"code": "missing_input"}

    headers = {"Access-Token": access_token}
    url = f"{TIKTOK_BASE}/report/integrated/get/"
    params = {
        "advertiser_id":    external_account_id,
        "report_type":      "BASIC",
        "data_level":       "AUCTION_ADVERTISER",
        "dimensions":       '["advertiser_id"]',
        "metrics":          '["spend","impressions","clicks","conversion"]',
        "start_date":       date_iso,
        "end_date":         date_iso,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                return None, {"code": "token_invalid"}
            if resp.status_code >= 400:
                return None, {"code": "http_error",
                                "status": resp.status_code,
                                "body": resp.text[:200]}
            data = resp.json() or {}
            if data.get("code") not in (0, "0", None):
                # TikTok puts errors in body even with 200
                if data.get("code") in (40103, 40105):
                    return None, {"code": "token_invalid",
                                    "tt_code": data.get("code")}
                return None, {"code": "http_error",
                                "tt_code": data.get("code"),
                                "message": data.get("message", "")[:200]}
            rows = ((data.get("data") or {}).get("list") or [])
            if not rows:
                return {
                    "spend_native":    0.0,
                    "currency_native": "USD",
                    "impressions":     0,
                    "clicks":          0,
                    "purchases":       0,
                    "raw_excerpt":     {"empty": True},
                }, {"code": "empty"}
            r = rows[0]
            metrics = r.get("metrics") or {}
            return {
                "spend_native":    float(metrics.get("spend") or 0),
                "currency_native": "USD",
                "impressions":     int(metrics.get("impressions") or 0),
                "clicks":          int(metrics.get("clicks") or 0),
                "purchases":       int(float(metrics.get("conversion") or 0)),
                "raw_excerpt":     {"tt_metrics": True},
            }, {"code": "ok"}
    except Exception as exc:
        return None, {"code": "exception", "message": str(exc)[:200]}


# ─────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────
async def fetch_day(
    provider: str, access_token: str, external_account_id: str,
    date_iso: str, account_timezone: str = "Asia/Riyadh",
) -> tuple[Optional[dict], dict]:
    if provider == "meta":
        return await fetch_meta_day(access_token, external_account_id, date_iso)
    if provider == "snapchat":
        return await fetch_snapchat_day(
            access_token, external_account_id, date_iso, account_timezone,
        )
    if provider == "tiktok":
        return await fetch_tiktok_day(
            access_token, external_account_id, date_iso,
        )
    return None, {"code": "unknown_provider"}
