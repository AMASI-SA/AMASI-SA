"""Tenant-scoped native TikTok hierarchy and daily reporting snapshots.

Only complete, validated provider pages replace a snapshot. Account totals
remain independent of campaign/adgroup/ad breakdowns (which may omit formats).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pymongo.errors import ExecutionTimeout
from resource_governor import governor

from .tiktok_native_reporting import (
    TIKTOK_REPORT_URL, TIKTOK_REPORTING_COLLECTION, TikTokReportingError,
    _accounts, _credential, _fx_to_sar, _provider_data,
)

ENTITY_COLLECTION = "mezan_tiktok_entities_v2"
DAILY_COLLECTION = "mezan_tiktok_entity_daily_v2"
SOURCE_MODE = "tiktok_native_hierarchy_v2"
KINDS = {"campaign": ("campaign_id", "campaign_name", "AUCTION_CAMPAIGN"),
         "adgroup": ("adgroup_id", "adgroup_name", "AUCTION_ADGROUP"),
         "ad": ("ad_id", "ad_name", "AUCTION_AD")}
MAX_ENTITIES = 5000
MAX_REPORT_ROWS = 5000
MAX_PAGES = 40
PAGE_SIZE = 500
QUERY_TIMEOUT_MS = 1500
HIERARCHY_CADENCE = timedelta(hours=1)
RETENTION_DAYS = 120
SYNC_TIME_BUDGET_SECONDS = 180
_hierarchy_slot = asyncio.Semaphore(1)
_workspace_slots = asyncio.Semaphore(2)


def _error(code: str) -> TikTokReportingError:
    return TikTokReportingError(code, "TikTok hierarchy returned incomplete or invalid data.",
                                status_code=502, retryable=True)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise _error("tiktok_hierarchy_invalid_metric")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise _error("tiktok_hierarchy_invalid_metric") from None
    if not math.isfinite(result) or result < 0:
        raise _error("tiktok_hierarchy_invalid_metric")
    return result


async def _pages(client, token, url, params, *, limit, deadline=None):
    rows, pages_expected, total_expected = [], None, None
    for page in range(1, MAX_PAGES + 1):
        if governor.peek()[0] in {"blocked", "cancel"}:
            raise _error("tiktok_hierarchy_resource_pressure")
        if deadline is not None and time.monotonic() >= deadline:
            raise _error("tiktok_hierarchy_time_budget")
        response = await client.get(url, headers={"Access-Token": token},
                                    params={**params, "page": page, "page_size": PAGE_SIZE})
        data = _provider_data(response, "tiktok_hierarchy")
        batch, info = data.get("list"), data.get("page_info")
        if not isinstance(batch, list) or not isinstance(info, dict):
            raise _error("tiktok_hierarchy_pagination_missing")
        if len(batch) > PAGE_SIZE:
            raise _error("tiktok_hierarchy_page_size_exceeded")
        try:
            current, pages, total = int(info["page"]), int(info["total_page"]), int(info["total_number"])
        except (KeyError, TypeError, ValueError):
            raise _error("tiktok_hierarchy_pagination_invalid") from None
        if current != page or pages < 0 or total < 0 or pages > MAX_PAGES or total > limit:
            raise _error("tiktok_hierarchy_limit_or_page_invalid")
        if pages_expected is not None and (pages, total) != (pages_expected, total_expected):
            raise _error("tiktok_hierarchy_pagination_changed")
        pages_expected, total_expected = pages, total
        if any(not isinstance(row, dict) for row in batch):
            raise _error("tiktok_hierarchy_invalid_row")
        rows.extend(batch)
        if len(rows) > limit:
            raise _error("tiktok_hierarchy_row_limit")
        if page >= max(1, pages):
            if len(rows) != total:
                raise _error("tiktok_hierarchy_pagination_incomplete")
            return rows
        if not batch:
            raise _error("tiktok_hierarchy_empty_intermediate_page")
    raise _error("tiktok_hierarchy_page_limit")


def _entities(rows, account_id, kind):
    id_key, name_key, _ = KINDS[kind]
    output, seen = [], set()
    for row in rows:
        entity_id = str(row.get(id_key) or "").strip()
        if isinstance(row.get(id_key), (float, bool, dict, list)):
            raise _error("tiktok_hierarchy_entity_identity_invalid")
        if not entity_id or entity_id in seen or str(row.get("advertiser_id") or account_id) != account_id:
            raise _error("tiktok_hierarchy_entity_identity_invalid")
        seen.add(entity_id)
        if kind != "campaign" and not row.get("campaign_id"):
            raise _error("tiktok_hierarchy_parent_missing")
        if kind == "ad" and not row.get("adgroup_id"):
            raise _error("tiktok_hierarchy_parent_missing")
        status = str(row.get("operation_status") or row.get("primary_status") or "unknown")
        output.append({"entity_id": entity_id, "entity_name": str(row.get(name_key) or entity_id)[:300],
                       "campaign_id": str(row.get("campaign_id") or entity_id),
                       "adgroup_id": str(row.get("adgroup_id") or "") or None,
                       "status": status, "delivery_status": str(row.get("secondary_status") or "") or None,
                       "objective": str(row.get("objective_type") or "") or None,
                       "budget_native": row.get("budget"), "budget_mode": row.get("budget_mode")})
        if kind == "ad":
            # In upgraded Smart+, /ad/get/ and ad_id reports identify a
            # creative. The Ads Manager Ad ID is its smart_plus_ad_id parent.
            # Keep both identities; never replace the report key with a parent
            # or infer a write target from an older, unclassified snapshot.
            automation = str(row.get("campaign_automation_type") or "")[:80] or None
            creative = automation == "UPGRADED_SMART_PLUS_CREATIVE"
            parent = row.get("smart_plus_ad_id")
            if creative and (isinstance(parent, (float, bool, dict, list))
                             or not str(parent or "").strip()):
                raise _error("tiktok_hierarchy_smart_plus_parent_invalid")
            regular = automation in {"MANUAL", "SMART_PLUS"}
            output[-1].update({
                "campaign_automation_type": automation,
                "identity_level": "creative" if creative else "ad" if regular else "unknown",
                "platform_ad_id": str(parent).strip() if creative else entity_id if regular else None,
            })
    return output


def _daily(rows, kind, days):
    id_key = KINDS[kind][0]
    grouped, seen = {day: [] for day in days}, set()
    for row in rows:
        dimensions, metrics = row.get("dimensions"), row.get("metrics")
        if not isinstance(dimensions, dict) or not isinstance(metrics, dict):
            raise _error("tiktok_hierarchy_report_row_invalid")
        entity_id = str(dimensions.get(id_key) or "").strip()
        if isinstance(dimensions.get(id_key), (float, bool, dict, list)):
            raise _error("tiktok_hierarchy_report_identity_invalid")
        day = str(dimensions.get("stat_time_day") or "")[:10]
        if not entity_id or day not in grouped or (day, entity_id) in seen:
            raise _error("tiktok_hierarchy_report_identity_invalid")
        seen.add((day, entity_id))
        grouped[day].append({"entity_id": entity_id,
                             "spend_native": _number(metrics.get("spend")),
                             "impressions": _number(metrics.get("impressions")),
                             "clicks": _number(metrics.get("clicks")),
                             "conversions": _number(metrics.get("conversion"))})
    return grouped


async def hierarchy_refresh_due(db, user_id, now):
    marker = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": "tiktok_ads"},
        {"_id": 0, "hierarchy_last_attempt_at": 1})
    try:
        last = datetime.fromisoformat((marker or {}).get("hierarchy_last_attempt_at", "").replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    return not now - HIERARCHY_CADENCE < last <= now + timedelta(minutes=5)


async def sync_tiktok_hierarchy(db, user_id: str, days: list[date], *, observed_at: str):
    # Refuse additional work rather than accumulate an unbounded task queue.
    reason = "tiktok_hierarchy_busy" if _hierarchy_slot.locked() else (
        "tiktok_hierarchy_resource_pressure" if governor.peek()[0] in {"blocked", "cancel"} else None)
    if reason:
        return {"status": "partial", "entity_counts": {kind: 0 for kind in KINDS},
                "errors": [{"code": reason}], "errors_count": 1}
    async with _hierarchy_slot:
        await db.mezan_integrations_v2.update_one(
            {"user_id": user_id, "provider": "tiktok_ads"},
            {"$set": {"hierarchy_last_attempt_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)
        return await _sync_tiktok_hierarchy(db, user_id, days, observed_at=observed_at)


async def _sync_tiktok_hierarchy(db, user_id: str, days: list[date], *, observed_at: str):
    """All provider requests are GET; each kind/day is an atomic full snapshot."""
    token, accounts = await _credential(db, user_id), await _accounts(db, user_id)
    key = [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1)]
    await db[ENTITY_COLLECTION].create_index(key, unique=True, name="tiktok_entity_snapshot_unique")
    await db[DAILY_COLLECTION].create_index(key + [("date", 1)], unique=True,
                                            name="tiktok_entity_daily_snapshot_unique")
    await db[DAILY_COLLECTION].create_index("expires_at", expireAfterSeconds=0,
                                            name="tiktok_entity_daily_retention")
    counts, errors = {kind: 0 for kind in KINDS}, []
    deadline = time.monotonic() + SYNC_TIME_BUDGET_SECONDS
    async with httpx.AsyncClient(timeout=30.0) as client:
        for account in accounts:
            account_id = account["ad_account_id"]
            for kind, (id_key, _, level) in KINDS.items():
                try:
                    metadata = await _pages(client, token,
                        f"https://business-api.tiktok.com/open_api/v1.3/{kind}/get/",
                        {"advertiser_id": account_id,
                         "fields": json.dumps([id_key, KINDS[kind][1], "advertiser_id",
                             "operation_status", "secondary_status"]
                             + (["objective_type", "budget", "budget_mode"] if kind == "campaign" else [])
                             + (["campaign_id", "budget", "budget_mode"] if kind == "adgroup" else [])
                             + (["campaign_id", "adgroup_id", "campaign_automation_type",
                                 "smart_plus_ad_id"] if kind == "ad" else [])),
                         "filtering": json.dumps({"primary_status": "STATUS_ALL"})},
                         limit=MAX_ENTITIES, deadline=deadline)
                    entities = _entities(metadata, account_id, kind)
                    del metadata
                    known_ids = {entity["entity_id"] for entity in entities}
                    identity = {"user_id": user_id, "ad_account_id": account_id, "entity_type": kind}
                    common = {"source_mode": SOURCE_MODE, "source_only": True,
                              "observed_at": observed_at, "complete": True}
                    # Bound raw batches by potential rows, not the backfill's
                    # duration. Large catalogues use one day; small catalogues
                    # share a request without fetching a large monthly payload.
                    chunk_size = max(1, min(30, MAX_REPORT_ROWS // max(1, len(entities))))
                    for offset in range(0, len(days), chunk_size):
                        chunk = days[offset:offset + chunk_size]
                        reports = await _pages(client, token, TIKTOK_REPORT_URL,
                            {"advertiser_id": account_id, "report_type": "BASIC", "data_level": level,
                             "dimensions": json.dumps([id_key, "stat_time_day"]),
                             "metrics": json.dumps(["spend", "impressions", "clicks", "conversion"]),
                             "filtering": json.dumps([{ "field_name": f"{kind}_status",
                                 "filter_type": "IN", "filter_value": json.dumps(["STATUS_ALL"])}]),
                             "start_date": chunk[0].isoformat(), "end_date": chunk[-1].isoformat()},
                            limit=MAX_REPORT_ROWS, deadline=deadline)
                        daily = _daily(reports, kind, [day.isoformat() for day in chunk])
                        del reports
                        if any(row["entity_id"] not in known_ids for rows in daily.values() for row in rows):
                            raise _error("tiktok_hierarchy_report_identity_unmatched")
                        for day_value in chunk:
                            day = day_value.isoformat()
                            rows = daily.pop(day)
                            expires_at = datetime.combine(day_value + timedelta(days=RETENTION_DAYS),
                                                          datetime.min.time(), timezone.utc)
                            await db[DAILY_COLLECTION].update_one({**identity, "date": day},
                                {"$set": {**identity, **common, "date": day,
                                          "row_count": len(rows), "rows": rows,
                                          "expires_at": expires_at}}, upsert=True)
                            del rows
                    await db[ENTITY_COLLECTION].update_one(identity,
                        {"$set": {**identity, **common, "entity_count": len(entities),
                                  "entities": entities}}, upsert=True)
                    counts[kind] += len(entities)
                except TikTokReportingError as exc:
                    if exc.code == "tiktok_needs_reauth":
                        raise
                    errors.append({"ad_account_id": account_id, "entity_type": kind, "code": exc.code})
                    if exc.code in {"tiktok_hierarchy_resource_pressure", "tiktok_hierarchy_time_budget"}:
                        return {"status": "partial", "entity_counts": counts,
                                "errors": errors, "errors_count": len(errors)}
                except (httpx.HTTPError, ValueError):
                    errors.append({"ad_account_id": account_id, "entity_type": kind,
                                   "code": "tiktok_hierarchy_transport_failed"})
    return {"status": "partial" if errors else "complete", "entity_counts": counts,
            "errors": errors, "errors_count": len(errors)}


async def _read(db, collection, query, maximum, projection=None):
    cursor = db[collection].find(query, projection or {"_id": 0})
    if hasattr(cursor, "max_time_ms"):
        cursor = cursor.max_time_ms(QUERY_TIMEOUT_MS)
    rows = await cursor.limit(maximum + 1).to_list(length=maximum + 1)
    if len(rows) > maximum:
        raise _error("tiktok_workspace_source_limit")
    return rows


async def _catalog_page(db, scoped, kind, *, page, limit, query, campaign_id, adgroup_id):
    """Paginate inside MongoDB; never return the full entity catalogue to Python."""
    match = {}
    if campaign_id:
        match["entities.campaign_id"] = campaign_id
    if adgroup_id:
        match["entities.adgroup_id"] = adgroup_id
    if query:
        escaped = re.escape(query[:120])
        match["$or"] = [{"entities.entity_name": {"$regex": escaped, "$options": "i"}},
                        {"entities.entity_id": {"$regex": escaped, "$options": "i"}}]
    pipeline = [{"$match": {**scoped, "entity_type": kind, "complete": True}},
                {"$unwind": "$entities"}]
    if match:
        pipeline.append({"$match": match})
    pipeline += [{"$sort": {"entities.status": -1, "ad_account_id": 1, "entities.entity_id": -1}},
                 {"$facet": {"count": [{"$count": "total"}], "entries": [
                     {"$skip": (page - 1) * limit}, {"$limit": limit},
                     {"$project": {"_id": 0, "ad_account_id": 1, "entity": "$entities"}}]}}]
    data = await db[ENTITY_COLLECTION].aggregate(pipeline, allowDiskUse=False,
                                                maxTimeMS=QUERY_TIMEOUT_MS).to_list(length=1)
    result = data[0] if data else {}
    total = int((result.get("count") or [{}])[0].get("total") or 0)
    pages = math.ceil(total / limit)
    if pages and page > pages:
        return await _catalog_page(db, scoped, kind, page=pages, limit=limit, query=query,
                                   campaign_id=campaign_id, adgroup_id=adgroup_id)
    return result.get("entries") or [], total, min(page, pages) if pages else 1


async def _page_facts(db, user_id, kind, days, entries):
    if not entries:
        return []
    accounts = list({row["ad_account_id"] for row in entries})
    selected = [row["ad_account_id"] + ":" + row["entity"]["entity_id"] for row in entries]
    pipeline = [{"$match": {"user_id": user_id, "ad_account_id": {"$in": accounts},
                            "entity_type": kind, "date": {"$in": days}, "complete": True}},
                {"$project": {"_id": 0, "ad_account_id": 1, "date": 1, "complete": 1,
                              "rows": {"$filter": {"input": "$rows", "as": "fact", "cond": {
                                  "$in": [{"$concat": ["$ad_account_id", ":", "$$fact.entity_id"]}, selected]}}}}}]
    return await db[DAILY_COLLECTION].aggregate(pipeline, allowDiskUse=False,
        maxTimeMS=QUERY_TIMEOUT_MS).to_list(length=len(accounts) * len(days))


def _totals(rows, complete, fx=1.0):
    keys = ("spend_native", "impressions", "clicks", "conversions")
    result = {key: round(sum(_number(row.get(key)) for row in rows), 6) if complete else None for key in keys}
    spend, clicks, impressions = result["spend_native"], result["clicks"], result["impressions"]
    sar = round(spend * fx, 2) if spend is not None and fx is not None else None
    return {**result, "spend_sar": sar, "swipes": clicks, "orders": None, "sales_sar": None,
            "roas": None, "cpa_sar": None, "cpc_sar": round(sar / clicks, 2) if sar is not None and clicks else None,
            "cpm_sar": round(sar * 1000 / impressions, 2) if sar is not None and impressions else None,
            "ctr_pct": round(clicks * 100 / impressions, 2) if clicks is not None and impressions else None,
            "data_complete": complete}


async def tiktok_workspace(db, user_id: str, *, from_date=None, to_date=None,
                           entity_type="campaign", page=1, limit=25, query="",
                           campaign_id=None, adgroup_id=None, account_id=None):
    if _workspace_slots.locked() or governor.peek()[0] in {"blocked", "cancel"}:
        raise TikTokReportingError("tiktok_workspace_busy", "بيانات TikTok مشغولة؛ حاول بعد قليل.",
                                  status_code=503, retryable=True)
    async with _workspace_slots:
        try:
            async with asyncio.timeout(6):
                return await _tiktok_workspace(db, user_id, from_date=from_date, to_date=to_date,
                    entity_type=entity_type, page=page, limit=limit, query=query,
                    campaign_id=campaign_id, adgroup_id=adgroup_id, account_id=account_id)
        except (TimeoutError, ExecutionTimeout):
            raise TikTokReportingError("tiktok_workspace_timeout", "انتهت مهلة بيانات TikTok؛ حاول بعد قليل.",
                                      status_code=503, retryable=True) from None


async def _tiktok_workspace(db, user_id: str, *, from_date=None, to_date=None,
                           entity_type="campaign", page=1, limit=25, query="",
                           campaign_id=None, adgroup_id=None, account_id=None):
    if not 1 <= limit <= 100 or page < 1:
        raise ValueError("invalid_tiktok_page")
    overview_only = entity_type == "overview"
    if overview_only:
        entity_type = "campaign"
    if entity_type not in KINDS:
        raise ValueError("invalid_tiktok_entity_type")
    accounts = await _accounts(db, user_id)
    now = datetime.now(timezone.utc)
    local_days = []
    for account in accounts:
        try:
            local_days.append(now.astimezone(ZoneInfo(account.get("timezone") or "Asia/Riyadh")).date())
        except ZoneInfoNotFoundError:
            local_days.append(now.astimezone(ZoneInfo("Asia/Riyadh")).date())
    today = min(local_days)
    start, end = date.fromisoformat(from_date or today.isoformat()), date.fromisoformat(to_date or today.isoformat())
    if end < start or (end - start).days >= 31:
        raise ValueError("invalid_tiktok_report_range")
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    account_ids = [account["ad_account_id"] for account in accounts]
    if account_id and account_id not in account_ids:
        raise TikTokReportingError("tiktok_account_not_connected", "الحساب غير مرتبط.", status_code=404)
    scoped = {"user_id": user_id, "ad_account_id": {"$in": account_ids}}
    account_rows = await _read(db, TIKTOK_REPORTING_COLLECTION,
        {**scoped, "date": {"$in": days}}, len(accounts) * len(days))
    snapshots = await _read(db, ENTITY_COLLECTION, {**scoped, "entity_type": entity_type}, len(accounts),
                            {"_id": 0, "entities": 0})
    if overview_only:
        entries, total = [], sum(int(row.get("entity_count") or 0) for row in snapshots)
    else:
        entries, total, page = await _catalog_page(db,
            {**scoped, **({"ad_account_id": account_id} if account_id else {})}, entity_type,
            page=page, limit=limit, query=query, campaign_id=campaign_id, adgroup_id=adgroup_id)
    daily = await _page_facts(db, user_id, entity_type, days, entries)
    account_output, entity_output, daily_output = [], [], []
    for account in accounts:
        account_id = account["ad_account_id"]
        rows = [row for row in account_rows if row["ad_account_id"] == account_id]
        complete = {row["date"] for row in rows} == set(days)
        fx, _ = _fx_to_sar(account.get("currency"))
        totals = _totals(rows, complete, fx)
        account_output.append({"account_id": account_id, "account_name": account.get("display_name") or account_id,
                               "currency": account.get("currency"), "timezone": account.get("timezone"), **totals})
        observed = [row for row in daily if row["ad_account_id"] == account_id and row.get("complete")]
        entity_complete = {row["date"] for row in observed} == set(days)
        by_entity = {}
        for row in observed:
            for fact in row.get("rows") or []:
                by_entity.setdefault(fact["entity_id"], []).append(fact)
        for item in entries:
            if item["ad_account_id"] != account_id:
                continue
            entity = item["entity"]
            entity_output.append({**entity, "entity_type": entity_type, "account_id": account_id,
                                  "account_name": account.get("display_name") or account_id,
                                  "currency": account.get("currency"),
                                  **_totals(by_entity.get(entity["entity_id"], []), entity_complete, fx)})
    for day in days:
        rows = [row for row in account_rows if row["date"] == day]
        complete = {row["ad_account_id"] for row in rows} == set(account_ids)
        metrics = _totals([{**row, "spend_native": row.get("spend_sar")} for row in rows],
                          complete and all(row.get("spend_sar") is not None for row in rows))
        daily_output.append({"date": day, **metrics})
    complete = all(row["data_complete"] for row in account_output)
    totals = _totals([{**row, "spend_native": row.get("spend_sar")} for row in account_output],
                     complete and all(row.get("spend_sar") is not None for row in account_output))
    pages = math.ceil(total / limit)
    identity_ready = len(snapshots) == len(accounts) and all(row.get("complete") for row in snapshots)
    return {"platform": "tiktok", "label": "تيك توك", "result_source": "platform",
            "range": {"date_from": start.isoformat(), "date_to": end.isoformat(), "timezone": "Asia/Riyadh"},
            "totals": totals, "accounts": account_output, "daily": daily_output, "hourly": [],
            "entities": entity_output,
            "campaigns": [], "entity_type": entity_type,
            "campaign_pagination": {"page": page, "limit": limit, "total": total, "pages": pages},
            "source": {"source_mode": SOURCE_MODE, "performance_rows": len(account_rows),
                       "entity_rows": total, "row_limit_reached": False, "entity_limit_reached": False},
            "ai_readiness": {"report_ready": complete, "spend_ready": totals["spend_sar"] is not None,
                             "campaign_identity_ready": identity_ready, "orders_ready": False,
                             "sales_ready": False, "ratios_ready": False,
                             "ai_analysis_ready": complete and identity_ready and bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                             "campaign_creation_enabled": False, "campaign_management_enabled": False},
            "insights": [{"code": "tiktok_conversions_are_not_orders", "severity": "info",
                          "title": "تحويلات TikTok", "detail": "التحويلات أحداث تبلغ عنها المنصة؛ مبيعات وطلبات سلة تحتاج ربطًا مستقلًا."}],
            "policy": {"mode": "observe_only", "mutations_allowed": False}}
