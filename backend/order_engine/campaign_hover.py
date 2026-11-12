"""Read-only daily campaign hover snapshot. Never calls an ad provider on hover.

Results are deliberately nullable when identity, coverage or conversion cannot
be proven; unknown metrics must not be represented as zero.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from fastapi import Depends, HTTPException, Query
from order_currency import salla_order_currency_fields
from salla_integration.abandoned_carts import parse_salla_datetime
from salla_marketing_attribution import canonical_ad_platform, campaign_id_candidates


MAX_CAMPAIGNS = 20
MAX_SALLA_ROWS = 4000
MAX_SPEND_ROWS = 500
PROVIDERS = {"snapchat", "meta", "tiktok", "google"}
CACHE_TTL = 180
_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_PENDING: dict[tuple, asyncio.Task] = {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _amount(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if parsed >= 0 and parsed != float("inf") and parsed == parsed else None
    except (TypeError, ValueError, OverflowError):
        return None


def _quantity(raw: dict) -> float | None:
    items = raw.get("items")
    if not isinstance(items, list):
        return None
    total = 0.0
    for item in items:
        if not isinstance(item, dict):
            return None
        # Source product lines only: a service or non-physical option isn't a piece.
        if _text(item.get("type")).lower() in {"service", "option"}:
            continue
        qty = _amount(item.get("quantity"))
        if qty is None:
            return None
        total += qty
    return total


def _window(now: datetime, timezone_name: str):
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    local_now = now.astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc), local_now.date().isoformat()


def _source(raw: dict, row: dict) -> dict:
    # Attach normalized attribution fields without altering stored provider facts.
    result = dict(raw)
    for key in ("campaign_id", "campaign_name", "utm_campaign", "utm_source",
                "source", "platform", "channel", "source_native"):
        if not result.get(key) and row.get(key):
            result[key] = row[key]
    return result


def _valid_user(user: Any) -> str:
    if not isinstance(user, dict) or (_text(user.get("role")).lower() != "owner" and user.get("is_owner") is not True):
        raise HTTPException(status_code=403, detail="owner_only")
    identity = _text(user.get("id"))
    if not identity:
        raise HTTPException(status_code=403, detail="owner_id_missing")
    return identity


async def read_campaign_hover(db: Any, user_id: str, campaign_ids: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    account_rows = await db.ads_v2_accounts.find(
        {"user_id": user_id, "soft_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "external_account_id": 1, "provider": 1,
         "timezone": 1, "currency_native": 1},
    ).limit(250).to_list(250)

    # Native connected accounts have the authoritative provider timezone; the
    # legacy ADS V2 catalogue can coexist with them under another internal ID.
    integrated = await db.mezan_integration_accounts_v2.find(
        {"user_id": user_id,
         "provider": {"$in": ["snapchat_ads", "meta_ads", "tiktok_ads", "google_ads"]}},
        {"_id": 0, "ad_account_id": 1, "external_account_id": 1,
         "provider": 1, "timezone": 1, "currency": 1,
         "connection_status": 1},
    ).limit(251).to_list(251)
    if len(integrated) > 250:
        integrated = []  # Never assume truncated account coverage.
    indexed_accounts = {
        (_text(row.get("provider")).lower().replace("_ads", ""),
         _text(row.get("external_account_id")).removeprefix("act_")): row
        for row in account_rows if row.get("external_account_id")
    }
    for row in integrated:
        provider = _text(row.get("provider")).lower().replace("_ads", "")
        external = _text(row.get("ad_account_id") or row.get("external_account_id"))
        if not external:
            continue
        key = (provider, external.removeprefix("act_"))
        existing = indexed_accounts.get(key)
        if existing is not None:
            # Connected ad-account metadata is authoritative for timezone.
            # Never silently use an outdated/missing legacy timezone.
            if row.get("timezone"):
                existing["timezone"] = row["timezone"]
            if row.get("currency"):
                existing["currency_native"] = row["currency"]
            continue
        account = {
            "id": external, "external_account_id": external,
            "provider": provider, "timezone": row.get("timezone"),
            "currency_native": row.get("currency"),
        }
        account_rows.append(account)
        indexed_accounts[key] = account

    accounts = []
    for account in account_rows:
        provider = _text(account.get("provider")).lower().replace("google_ads", "google")
        if provider not in PROVIDERS:
            continue
        zone = _window(now, account.get("timezone"))
        if zone is None:
            continue
        accounts.append((account, provider, zone[0], zone[1]))

    # Account-day raw spend is from locally stored campaigns, never ad API.
    dates = sorted({day for _, _, _, day in accounts})
    # Only use Riyadh facts from an American account to establish campaign
    # identity, never as spend for the American local day.
    riyadh_today = now.astimezone(ZoneInfo("Asia/Riyadh")).date()
    snapchat_identity_dates = sorted({
        riyadh_today.isoformat(),
        (riyadh_today - timedelta(days=1)).isoformat(),
        *dates,
    })
    raw_spends = []
    if dates:
        raw_spends = await db.ads_v2_spend_raw.find(
            {"user_id": user_id, "date": {"$in": dates},
             "granularity": "campaign_day",
             "dimension_keys.campaign_id": {"$in": campaign_ids}},
            {"_id": 0, "account_id": 1, "provider": 1, "date": 1,
             "source_run_id": 1, "fetched_at": 1, "dimension_keys": 1,
             "spend_native": 1, "currency_native": 1},
        ).sort("fetched_at", -1).limit(MAX_SPEND_ROWS + 1).to_list(MAX_SPEND_ROWS + 1)
    # Fallback to actual persisted native campaign facts. Snap native reports
    # are Riyadh-day buckets, so only Riyadh-timezone accounts qualify.
    # Meta's campaign insights are requested on the ad-account local date.
    native_spends = []
    if dates:
        for collection, query, label in (
            ("mezan_snapchat_performance_daily_v2",
             {"user_id": user_id, "entity_type": "campaign",
              "date": {"$in": snapchat_identity_dates},
              "campaign_id": {"$in": campaign_ids},
              "date_timezone": "Asia/Riyadh"}, "snapchat"),
            ("mezan_meta_campaign_performance_daily_v2",
             {"user_id": user_id, "campaign_id": {"$in": campaign_ids},
              "date": {"$in": dates},
              "source_mode": "meta_campaign_reporting_v2"}, "meta"),
        ):
            rows = await db[collection].find(
                query, {"_id": 0, "date": 1, "ad_account_id": 1,
                        "campaign_id": 1, "external_id": 1, "campaign_name": 1,
                        "spend_sar": 1, "updated_at": 1},
            ).limit(MAX_SPEND_ROWS + 1).to_list(MAX_SPEND_ROWS + 1)
            native_spends.extend((label, item) for item in rows)
    truncated_spend = (len(raw_spends) > MAX_SPEND_ROWS or
                       len(native_spends) > MAX_SPEND_ROWS)
    account_index = {_text(a.get("id")): (a, provider, start, day)
                     for a, provider, start, day in accounts}
    by_campaign: dict[str, list[dict]] = {cid: [] for cid in campaign_ids}
    seen = set()
    for row in raw_spends[:MAX_SPEND_ROWS]:
        cid = _text((row.get("dimension_keys") or {}).get("campaign_id"))
        account_id = _text(row.get("account_id"))
        account_tuple = account_index.get(account_id)
        if not account_tuple or cid not in by_campaign:
            continue
        account, provider, start, day = account_tuple
        if row.get("date") != day:
            continue
        key = (cid, account_id)
        if key in seen:
            continue
        seen.add(key)
        by_campaign[cid].append({"account": account, "provider": provider,
                                 "start": start, "date": day, "spend_row": row})

    external_lookup = {
        (provider, _text(account.get("external_account_id")).removeprefix("act_")):
        (account, provider, start, day)
        for account, provider, start, day in accounts
        if account.get("external_account_id")
    }
    for provider, row in native_spends[:MAX_SPEND_ROWS]:
        cid = _text(row.get("campaign_id") or row.get("external_id"))
        account_id = _text(row.get("ad_account_id")).removeprefix("act_")
        detail = external_lookup.get((provider, account_id))
        if not detail or cid not in by_campaign:
            continue
        account, _, start, day = detail
        is_account_day_aligned = (
            row.get("date") == day and
            (provider != "snapchat" or _text(account.get("timezone")) == "Asia/Riyadh")
        )
        if provider != "snapchat" and not is_account_day_aligned:
            continue
        if provider == "snapchat" and _text(account.get("timezone")) == "Asia/Riyadh" and row.get("date") != day:
            continue
        if provider == "snapchat" and not is_account_day_aligned and row.get("date") not in snapchat_identity_dates:
            continue
        key = (cid, _text(account.get("id")))
        if key in seen:
            continue
        seen.add(key)
        by_campaign[cid].append({
            "account": account, "provider": provider, "start": start,
            "date": day,
            "spend_alignment": None if is_account_day_aligned else "snapchat_account_day_not_aligned",
            "spend_row": {
                "spend_sar": row.get("spend_sar") if is_account_day_aligned else None,
                "fetched_at": row.get("updated_at"),
                "dimension_keys": {"campaign_name": row.get("campaign_name") or cid},
            },
        })

    qualified = {cid: entries[0] for cid, entries in by_campaign.items()
                 if len(entries) == 1 and not truncated_spend}
    # Surface useful source coverage reasons without claiming unavailable
    # Snap account-day spend is zero. In particular, Riyadh-day native facts
    # cannot represent the local-day spend for an American Snap account.
    unavailable_reason = {}
    for cid in campaign_ids:
        if cid in qualified:
            continue
        if truncated_spend:
            unavailable_reason[cid] = "spend_source_truncated"
        elif len(by_campaign[cid]) > 1:
            unavailable_reason[cid] = "ambiguous_account"
        elif any(provider == "snapchat"
                 and _text(row.get("campaign_id") or row.get("external_id")) == cid
                 for provider, row in native_spends):
            unavailable_reason[cid] = "snapchat_account_day_not_aligned"
        elif not accounts:
            unavailable_reason[cid] = "account_timezone_unavailable"
        else:
            unavailable_reason[cid] = "campaign_spend_not_available"
    min_start = min((v["start"] for v in qualified.values()), default=now)
    # Bound by indexed order_date plus campaign identifiers to avoid a full-day
    # hydration of unrelated Salla orders. No write or provider API call.
    scan_start = (min_start - timedelta(days=1)).date().isoformat()
    cursor = db.unified_orders.find(
        {"user_id": user_id, "raw_by_source.salla_direct": {"$type": "object"},
         "order_date": {"$gte": scan_start},
         "$or": [
             {"campaign_id": {"$in": list(qualified)}},
             {"utm_campaign": {"$in": list(qualified)}},
             {"raw_by_source.salla_direct.campaign_id": {"$in": list(qualified)}},
             {"raw_by_source.salla_direct.utm_campaign": {"$in": list(qualified)}},
             {"raw_by_source.salla_direct.source_details.campaign_id": {"$in": list(qualified)}},
         ]},
        {"_id": 0, "order_number": 1, "order_date": 1, "order_status": 1,
         "campaign_id": 1, "campaign_name": 1, "utm_campaign": 1,
         "utm_source": 1, "source": 1, "platform": 1, "channel": 1,
         "raw_by_source.salla_direct": 1},
    ).max_time_ms(1500).limit(MAX_SALLA_ROWS + 1)
    try:
        rows = await cursor.to_list(MAX_SALLA_ROWS + 1) if qualified else []
    except Exception:
        # A slow or unavailable database read must not block the order list.
        # Fail closed: never present a partial scan as a complete zero.
        rows = []
        order_read_failed = True
    else:
        order_read_failed = False
    truncated_orders = len(rows) > MAX_SALLA_ROWS
    totals = {cid: {"orders": 0, "pieces": 0.0, "sales": 0.0,
                    "pieces_complete": True, "sales_complete": True}
              for cid in qualified}
    seen_orders = set()
    for row in rows[:MAX_SALLA_ROWS]:
        order_no = _text(row.get("order_number"))
        if not order_no or order_no in seen_orders:
            continue
        seen_orders.add(order_no)
        raw = (row.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw, dict):
            continue
        order = _source(raw, row)
        cid_candidates = set(campaign_id_candidates(order))
        provider = canonical_ad_platform(order)
        event_time = (parse_salla_datetime(raw.get("date"))
                      or parse_salla_datetime(raw.get("created_at"))
                      or _timestamp(row.get("order_date")) )
        if event_time is None or event_time > now:
            continue
        status_text = _text(row.get("order_status") or raw.get("status")).lower()
        if any(x in status_text for x in ("cancel", "refund", "ملغ", "مسترج", "محذوف", "deleted")):
            continue
        for cid, detail in qualified.items():
            if cid not in cid_candidates or provider != detail["provider"] or event_time < detail["start"]:
                continue
            metric = totals[cid]
            metric["orders"] += 1
            pieces = _quantity(raw)
            if pieces is None:
                metric["pieces_complete"] = False
            else:
                metric["pieces"] += pieces
            money = salla_order_currency_fields(raw)
            sales = _amount(money.get("total_amount_sar"))
            if sales is None:
                metric["sales_complete"] = False
            else:
                metric["sales"] += sales

    # Resolve FX for all foreign-currency campaign facts in one bounded read.
    # Never execute one database query per campaign while loading the order page.
    fx_keys = {
        (_text(d["account"].get("id")), d["date"])
        for d in qualified.values()
        if _amount(d["spend_row"].get("spend_sar")) is None
        and _text(d["spend_row"].get("currency_native") or
                  d["account"].get("currency_native")).upper() not in {"", "SAR"}
        and _amount(d["spend_row"].get("spend_native")) is not None
    }
    fx_rows = []
    if fx_keys:
        try:
            fx_rows = await db.ads_v2_spend_daily.find(
                {"user_id": user_id,
                 "account_id": {"$in": sorted({k[0] for k in fx_keys})},
                 "date": {"$in": sorted({k[1] for k in fx_keys})}},
                {"_id": 0, "account_id": 1, "date": 1,
                 "fx_rate_to_sar": 1, "currency_native": 1},
            ).max_time_ms(1200).limit(251).to_list(251)
        except Exception:
            fx_rows = []
    fx_lookup = (
        {(_text(row.get("account_id")), _text(row.get("date"))): row for row in fx_rows}
        if len(fx_rows) <= 250 else {}
    )

    result = {}
    for cid in campaign_ids:
        entries = by_campaign[cid]
        detail = qualified.get(cid)
        record = {"campaign_id": cid, "orders": None, "pieces": None,
                  "sales_sar": None, "spend_sar": None, "cpa_sar": None,
                  "roas": None, "timezone": None, "date": None,
                  "updated_at": None, "status": "unavailable"}
        if not detail:
            record["status"] = unavailable_reason[cid]
            result[cid] = record
            continue
        account = detail["account"]
        record["timezone"] = account["timezone"]
        record["date"] = detail["date"]
        if detail.get("spend_alignment"):
            record["spend_reason"] = detail["spend_alignment"]
        spend_row = detail["spend_row"]
        record["updated_at"] = spend_row.get("fetched_at")
        record["campaign_name"] = _text((spend_row.get("dimension_keys") or {}).get("campaign_name"))
        currency = _text(spend_row.get("currency_native") or account.get("currency_native")).upper()
        amount = _amount(spend_row.get("spend_native"))
        # Already-converted native facts are read verbatim; don't convert twice.
        spend_sar = _amount(spend_row.get("spend_sar"))
        if spend_sar is None and currency == "SAR":
            spend_sar = amount
        if spend_sar is None and currency != "SAR" and amount is not None:
            daily = fx_lookup.get((_text(account.get("id")), detail["date"]))
            fx = _amount((daily or {}).get("fx_rate_to_sar"))
            if fx is not None and fx > 0 and _text((daily or {}).get("currency_native")).upper() == currency:
                spend_sar = amount * fx
        metric = totals[cid]
        record["orders"] = None if truncated_orders or order_read_failed else metric["orders"]
        record["pieces"] = (None if truncated_orders or order_read_failed or not metric["pieces_complete"]
                            else metric["pieces"])
        record["sales_sar"] = (None if truncated_orders or order_read_failed or not metric["sales_complete"]
                               else round(metric["sales"], 2))
        record["spend_sar"] = round(spend_sar, 2) if spend_sar is not None else None
        if record["orders"] and spend_sar is not None:
            record["cpa_sar"] = round(spend_sar / record["orders"], 2)
        if record["sales_sar"] is not None and spend_sar and spend_sar > 0:
            record["roas"] = round(record["sales_sar"] / spend_sar, 2)
        record["status"] = ("partial" if truncated_orders or order_read_failed or
                            record["pieces"] is None or record["sales_sar"] is None or
                            spend_sar is None else "available")
        result[cid] = record
    return {"as_of": now.isoformat(), "campaigns": result}



def _local_days_still_current(data: dict) -> bool:
    now = datetime.now(timezone.utc)
    for record in (data.get("campaigns") or {}).values():
        day = record.get("date")
        name = record.get("timezone")
        if not day or not name:
            continue
        window = _window(now, name)
        if not window or window[1] != day:
            return False
    return True


async def cached_campaign_hover(db: Any, user_id: str, campaign_ids: list[str]) -> dict:
    key = (user_id, tuple(sorted(campaign_ids)))
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL and _local_days_still_current(cached[1]):
        _CACHE.move_to_end(key)
        return cached[1]
    task = _PENDING.get(key)
    if task is None:
        task = asyncio.create_task(read_campaign_hover(db, user_id, campaign_ids))
        _PENDING[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _PENDING.get(key) is task:
            _PENDING.pop(key, None)
    _CACHE[key] = (time.monotonic(), result)
    _CACHE.move_to_end(key)
    while len(_CACHE) > 64:
        _CACHE.popitem(last=False)
    return result


def install_campaign_hover_route(router, db, current_user):
    @router.get("/campaign-hover")
    async def campaign_hover(
        campaign_ids: str = Query(..., min_length=1, max_length=1500),
        user: dict = Depends(current_user),
    ):
        user_id = _valid_user(user)
        ids = list(dict.fromkeys(x.strip() for x in campaign_ids.split(",") if x.strip()))
        if not ids or len(ids) > MAX_CAMPAIGNS or any(len(x) > 160 for x in ids):
            raise HTTPException(status_code=400, detail="invalid_campaign_ids")
        return await cached_campaign_hover(db, user_id, ids)
