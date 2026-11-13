"""Read-only, bounded campaign-day snapshots. Hover only displays prefetched data.

Salla commerce and provider spend have independent availability. Campaign
identity comes from explicit identifiers, never names or spend availability.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, Query
from order_currency import salla_order_currency_fields
from salla_integration.abandoned_carts import parse_salla_datetime
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION, canonical_ad_platform, campaign_id_candidates,
)

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
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
        return parsed if 0 <= parsed < float("inf") else None
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
        if _text(item.get("type")).lower() in {"service", "option"}:
            continue
        qty = _amount(item.get("quantity"))
        if qty is None:
            return None
        total += qty
    return total


def _window(now: datetime, timezone_name: str):
    try:
        local = now.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), local.date().isoformat()


def _provider(value):
    return _text(value).lower().removesuffix("_ads")


def _external(value):
    return _text(value).removeprefix("act_")


def _valid_user(user: Any) -> str:
    if not isinstance(user, dict) or (_text(user.get("role")).lower() != "owner" and user.get("is_owner") is not True):
        raise HTTPException(status_code=403, detail="owner_only")
    identity = _text(user.get("id"))
    if not identity:
        raise HTTPException(status_code=403, detail="owner_id_missing")
    return identity


async def _read(db, collection, query, projection, limit=MAX_SPEND_ROWS):
    """A safety limit is not a completeness proof; overflow is explicit."""
    try:
        rows = await db[collection].find(query, projection).max_time_ms(1500).limit(limit + 1).to_list(limit + 1)
    except Exception:
        return [], False
    return (rows, True) if len(rows) <= limit else ([], False)


def _blank(cid, reason="campaign_account_unavailable"):
    return {"campaign_id": cid, "orders": None, "pieces": None, "sales_sar": None,
            "spend_sar": None, "cpa_sar": None, "roas": None, "timezone": None,
            "date": None, "updated_at": None, "status": reason}


def _source(raw, row):
    result = dict(raw)
    for key in ("campaign_id", "campaign_name", "utm_campaign", "utm_source", "source",
                "platform", "channel", "source_native", "ad_platform_source", "marketing_source"):
        if not result.get(key) and row.get(key):
            result[key] = row[key]
    return result


def _salla_query(user_id, campaign_ids, start, now):
    # Derive the predicate from the existing safe attribution schema, so raw
    # source_details/UTM orders are included even with NO campaign spend row.
    leaves = {"campaign_id", "source_campaign_id", "utm_campaign_id", "ad_campaign_id", "utm_campaign", "campaign"}
    paths = {key for key in SALLA_RAW_ATTRIBUTION_PROJECTION
             if key.split(".")[-1] in leaves or key.endswith((".campaign.id", ".campaign.external_id"))}
    paths.update(leaves)
    values = list(campaign_ids)
    values.extend(int(cid) for cid in campaign_ids if cid.isdigit() and len(cid) < 19)
    return {"user_id": user_id, "raw_by_source.salla_direct": {"$type": "object"},
            "order_date": {"$gte": (start - timedelta(days=1)).date().isoformat(),
                           "$lt": (now + timedelta(days=2)).date().isoformat()},
            "$or": [{key: {"$in": values}} for key in sorted(paths)]}


async def read_campaign_hover(db: Any, user_id: str, campaign_ids: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    result = {cid: _blank(cid) for cid in campaign_ids}
    legacy, old_ok = await _read(db, "ads_v2_accounts",
        {"user_id": user_id, "soft_deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "external_account_id": 1, "provider": 1, "timezone": 1, "currency_native": 1}, 250)
    native, native_ok = await _read(db, "mezan_integration_accounts_v2",
        {"user_id": user_id, "provider": {"$in": ["snapchat_ads", "meta_ads", "tiktok_ads", "google_ads"]}},
        {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "provider": 1, "timezone": 1, "currency": 1}, 250)
    if not old_ok or not native_ok:
        return {"as_of": now.isoformat(), "campaigns": {cid: _blank(cid, "account_read_incomplete") for cid in campaign_ids}}
    accounts = {}
    internal = {}
    for row in legacy:
        provider = _provider(row.get("provider"))
        external = _external(row.get("external_account_id") or row.get("id"))
        if provider in PROVIDERS and external:
            key = (provider, external)
            accounts[key] = {**row, "external_account_id": external, "provider": provider}
            internal[_text(row.get("id"))] = key
    for row in native:
        key = (_provider(row.get("provider")), _external(row.get("ad_account_id") or row.get("external_account_id")))
        if key[0] not in PROVIDERS or not key[1]:
            continue
        prior = accounts.get(key, {"id": key[1]})
        accounts[key] = {**prior, "external_account_id": key[1], "provider": key[0],
                         "timezone": row.get("timezone") or prior.get("timezone"),
                         "currency_native": row.get("currency") or prior.get("currency_native")}
        internal.setdefault(key[1], key)
    windows = {key: _window(now, account.get("timezone")) for key, account in accounts.items()}
    dates = sorted({value[1] for value in windows.values() if value})
    if not dates:
        return {"as_of": now.isoformat(), "campaigns": {cid: _blank(cid, "account_timezone_unavailable") for cid in campaign_ids}}
    identities = {cid: set() for cid in campaign_ids}
    names = {}
    spends = {}

    def identify(cid, key, name=None):
        if cid in identities and key in accounts:
            identities[cid].add(key)
            if name:
                names.setdefault(cid, _text(name))
            return True
        return False

    def offer(cid, key, row, rank):
        stamp = _timestamp(row.get("fetched_at") or row.get("updated_at"))
        sort_key = (rank, stamp or datetime.min.replace(tzinfo=timezone.utc))
        existing = spends.get((cid, key))
        if existing is None or sort_key > existing[0]:
            spends[(cid, key)] = (sort_key, row)

    # Identity is independent of today's spend; this resolves the user's two
    # Snapchat accounts even for a campaign with Salla sales but no spend fact.
    entities, catalog_ok = await _read(db, "mezan_snapchat_entities_v2",
        {"user_id": user_id, "entity_type": "campaign", "external_id": {"$in": campaign_ids}},
        {"_id": 0, "ad_account_id": 1, "external_id": 1, "name": 1})
    for row in entities:
        identify(_text(row.get("external_id")), ("snapchat", _external(row.get("ad_account_id"))), row.get("name"))

    raw, raw_ok = await _read(db, "ads_v2_spend_raw",
        {"user_id": user_id, "date": {"$in": dates}, "granularity": "campaign_day",
         "dimension_keys.campaign_id": {"$in": campaign_ids}},
        {"_id": 0, "account_id": 1, "provider": 1, "date": 1, "fetched_at": 1,
         "dimension_keys": 1, "spend_native": 1, "currency_native": 1})
    for row in raw:
        cid = _text((row.get("dimension_keys") or {}).get("campaign_id"))
        key = internal.get(_text(row.get("account_id")))
        if not identify(cid, key, (row.get("dimension_keys") or {}).get("campaign_name")):
            continue
        if windows.get(key) and row.get("date") == windows[key][1]:
            offer(cid, key, row, 1)

    ry = now.astimezone(ZoneInfo("Asia/Riyadh")).date()
    identity_dates = sorted({*dates, ry.isoformat(), (ry - timedelta(days=1)).isoformat()})
    native_sources = (
        ("mezan_snapchat_performance_daily_v2", "snapchat", 2,
         {"entity_type": "campaign", "date": {"$in": identity_dates}, "date_timezone": "Asia/Riyadh"}),
        ("mezan_meta_campaign_performance_daily_v2", "meta", 2,
         {"date": {"$in": dates}, "source_mode": "meta_campaign_reporting_v2"}),
        ("mezan_snapchat_campaign_account_day_v2", "snapchat", 3,
         {"date": {"$in": dates}, "source_mode": "snapchat_native_account_day_v2"}),
    )
    source_health = []
    for collection, provider, rank, extra in native_sources:
        rows, complete = await _read(db, collection,
            {"user_id": user_id, "campaign_id": {"$in": campaign_ids}, **extra},
            {"_id": 0, "date": 1, "ad_account_id": 1, "campaign_id": 1, "external_id": 1,
             "campaign_name": 1, "spend_native": 1, "currency_native": 1,
             "spend_sar": 1, "updated_at": 1, "account_timezone": 1,
             "coverage_status": 1, "coverage_complete": 1, "coverage_start_utc": 1,
             "coverage_end_utc": 1, "hours_observed": 1, "missing_hours": 1})
        source_health.append(complete)
        for row in rows:
            cid = _text(row.get("campaign_id") or row.get("external_id"))
            key = (provider, _external(row.get("ad_account_id")))
            if not identify(cid, key, row.get("campaign_name")) or not windows.get(key):
                continue
            start, day = windows[key]
            zone = accounts[key].get("timezone")
            if row.get("date") != day:
                continue
            value = {**row, "fetched_at": row.get("updated_at")}
            if rank == 3:
                if row.get("account_timezone") != zone:
                    continue
                through = _timestamp(row.get("coverage_end_utc"))
                coverage_valid = (row.get("coverage_complete") is True and
                                  row.get("coverage_status") == "provisional_hourly" and
                                  _timestamp(row.get("coverage_start_utc")) == start and
                                  through is not None and start < through <= now)
                value["spend_coverage"] = row.get("coverage_status")
                if not coverage_valid:
                    value.update(spend_sar=None, spend_native=None, spend_reason="snapchat_hours_incomplete")
            elif provider == "snapchat" and zone != "Asia/Riyadh":
                value.update(spend_sar=None, spend_native=None, spend_reason="snapchat_account_day_not_aligned")
            offer(cid, key, value, rank)

    usable = {cid: next(iter(keys)) for cid, keys in identities.items() if len(keys) == 1}
    # All configured accounts, including invalid-timezone ones, participate in
    # uniqueness. An unreadable second account must not look like one account.
    by_provider = {provider: [key for key in accounts if key[0] == provider] for provider in PROVIDERS}
    starts = [window[0] for window in windows.values() if window]
    projection = {**SALLA_RAW_ATTRIBUTION_PROJECTION,
        "order_number": 1, "order_date": 1, "order_status": 1, "campaign_id": 1,
        "campaign_name": 1, "utm_campaign": 1, "utm_source": 1, "source": 1,
        "platform": 1, "channel": 1, "source_native": 1, "ad_platform_source": 1,
        "marketing_source": 1}
    for field in ("date", "created_at", "status", "amounts.total", "currency", "total_amount",
                  "total", "exchange_rate", "items.quantity", "items.type"):
        projection["raw_by_source.salla_direct." + field] = 1
    orders, commerce_ok = await _read(db, "unified_orders",
        _salla_query(user_id, campaign_ids, min(starts), now), projection, MAX_SALLA_ROWS)
    totals = {cid: {"orders": 0, "pieces": 0.0, "sales": 0.0, "pieces_ok": True, "sales_ok": True, "time_ok": True} for cid in campaign_ids}
    seen = set()
    for row_index, row in enumerate(orders):
        if row_index and row_index % 32 == 0:
            await asyncio.sleep(0)
        number = _text(row.get("order_number"))
        if not number or number in seen:
            continue
        seen.add(number)
        raw_order = (row.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw_order, dict):
            continue
        order = _source(raw_order, row)
        provider = canonical_ad_platform(order)
        cids = set(campaign_id_candidates(order)).intersection(campaign_ids)
        at = parse_salla_datetime(raw_order.get("date")) or parse_salla_datetime(raw_order.get("created_at")) or _timestamp(row.get("order_date"))
        status = _text(row.get("order_status") or raw_order.get("status")).lower()
        if at is None:
            for cid in cids:
                totals[cid]["time_ok"] = False
            continue
        if at > now or any(token in status for token in ("cancel", "refund", "ملغ", "مسترج", "محذوف", "deleted")):
            continue
        for cid in cids:
            if len(identities[cid]) > 1:
                continue
            key = usable.get(cid)
            if key is None and provider in by_provider and len(by_provider[provider]) == 1:
                key = by_provider[provider][0]
                usable[cid] = key
            if key is None or (provider is not None and key[0] != provider) or not windows.get(key) or at < windows[key][0]:
                continue
            metric = totals[cid]
            metric["orders"] += 1
            pieces = _quantity(raw_order)
            sales = _amount(salla_order_currency_fields(raw_order).get("total_amount_sar"))
            if pieces is None:
                metric["pieces_ok"] = False
            else:
                metric["pieces"] += pieces
            if sales is None:
                metric["sales_ok"] = False
            else:
                metric["sales"] += sales

    fx_rows, fx_ok = await _read(db, "ads_v2_spend_daily",
        {"user_id": user_id, "date": {"$in": dates}, "account_id": {"$in": [_text(a.get("id")) for a in accounts.values()]}},
        {"_id": 0, "account_id": 1, "date": 1, "currency_native": 1, "fx_rate_to_sar": 1}, 250)
    fx = {(_text(row.get("account_id")), row.get("date")): row for row in fx_rows}
    for cid in campaign_ids:
        key = usable.get(cid)
        record = result[cid]
        if len(identities[cid]) > 1:
            record["status"] = "ambiguous_account"
            continue
        if key is None or not windows.get(key):
            record["status"] = "account_timezone_unavailable" if key else "campaign_account_unavailable"
            continue
        account = accounts[key]
        start, day = windows[key]
        spend = spends.get((cid, key), (None, {}))[1]
        amount = _amount(spend.get("spend_sar"))
        native_amount = _amount(spend.get("spend_native"))
        currency = _text(spend.get("currency_native") or account.get("currency_native")).upper()
        if amount is None and native_amount is not None and not spend.get("spend_reason"):
            if currency == "SAR":
                amount = native_amount
            else:
                fx_row = fx.get((_text(account.get("id")), day), {})
                rate = _amount(fx_row.get("fx_rate_to_sar"))
                if fx_ok and rate and _text(fx_row.get("currency_native")).upper() == currency:
                    amount = native_amount * rate
        metric = totals[cid]
        record.update(timezone=account["timezone"], date=day, campaign_name=names.get(cid, ""),
            account_id=key[1], provider=key[0], period_start=start.isoformat(),
            orders_as_of=now.isoformat(), updated_at=spend.get("fetched_at"),
            orders=metric["orders"] if commerce_ok and metric["time_ok"] else None,
            pieces=metric["pieces"] if commerce_ok and metric["time_ok"] and metric["pieces_ok"] else None,
            sales_sar=round(metric["sales"], 2) if commerce_ok and metric["time_ok"] and metric["sales_ok"] else None,
            spend_sar=round(amount, 2) if amount is not None else None,
            spend_reason=spend.get("spend_reason") or ("campaign_spend_not_available" if amount is None else None),
            spend_coverage=spend.get("spend_coverage"), spend_as_of=spend.get("coverage_end_utc"),
            hours_observed=spend.get("hours_observed"), missing_hours=spend.get("missing_hours"))
        if record["orders"] and amount is not None:
            record["cpa_sar"] = round(amount / record["orders"], 2)
        if record["sales_sar"] is not None and amount is not None and amount > 0:
            record["roas"] = round(record["sales_sar"] / amount, 2)
        record["status"] = "available" if all(record[k] is not None for k in ("orders", "pieces", "sales_sar", "spend_sar")) else "partial"
    return {"as_of": now.isoformat(), "campaigns": result,
            "sources_complete": {"campaign_catalog": catalog_ok, "raw_spend": raw_ok,
                                 "native_spend": all(source_health), "salla": commerce_ok}}


def _local_days_still_current(data: dict) -> bool:
    now = datetime.now(timezone.utc)
    for record in (data.get("campaigns") or {}).values():
        if record.get("date") and record.get("timezone"):
            window = _window(now, record["timezone"])
            if not window or window[1] != record["date"]:
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
        if len(_PENDING) >= 8:
            raise HTTPException(status_code=503, detail="campaign_snapshot_busy")
        task = asyncio.create_task(asyncio.wait_for(read_campaign_hover(db, user_id, campaign_ids), timeout=10))
        _PENDING[key] = task
        def finished(done):
            if _PENDING.get(key) is done:
                _PENDING.pop(key, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    result = await asyncio.shield(task)
    _CACHE[key] = (time.monotonic(), result)
    _CACHE.move_to_end(key)
    while len(_CACHE) > 64:
        _CACHE.popitem(last=False)
    return result


def install_campaign_hover_route(router, db, current_user):
    @router.get("/campaign-hover")
    async def campaign_hover(campaign_ids: str = Query(..., min_length=1, max_length=1500),
                             user: dict = Depends(current_user)):
        user_id = _valid_user(user)
        ids = list(dict.fromkeys(x.strip() for x in campaign_ids.split(",") if x.strip()))
        if not ids or len(ids) > MAX_CAMPAIGNS or any(len(x) > 160 for x in ids):
            raise HTTPException(status_code=400, detail="invalid_campaign_ids")
        try:
            return await cached_campaign_hover(db, user_id, ids)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="campaign_snapshot_timeout")
