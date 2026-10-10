"""Tenant-scoped native TikTok hierarchy and daily reporting snapshots.

Only complete, validated provider pages replace a snapshot. Account totals
remain independent of campaign/adgroup/ad breakdowns (which may omit formats).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

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
MAX_ENTITIES = 10000
MAX_REPORT_ROWS = 100000
MAX_PAGES = 100
PAGE_SIZE = 1000


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


async def _pages(client, token, url, params, *, limit):
    rows, pages_expected, total_expected = [], None, None
    for page in range(1, MAX_PAGES + 1):
        response = await client.get(url, headers={"Access-Token": token},
                                    params={**params, "page": page, "page_size": PAGE_SIZE})
        data = _provider_data(response, "tiktok_hierarchy")
        batch, info = data.get("list"), data.get("page_info")
        if not isinstance(batch, list) or not isinstance(info, dict):
            raise _error("tiktok_hierarchy_pagination_missing")
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


async def sync_tiktok_hierarchy(db, user_id: str, days: list[date], *, observed_at: str):
    """All provider requests are GET; each kind/day is an atomic full snapshot."""
    token, accounts = await _credential(db, user_id), await _accounts(db, user_id)
    key = [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1)]
    await db[ENTITY_COLLECTION].create_index(key, unique=True, name="tiktok_entity_snapshot_unique")
    await db[DAILY_COLLECTION].create_index(key + [("date", 1)], unique=True,
                                            name="tiktok_entity_daily_snapshot_unique")
    counts, errors = {kind: 0 for kind in KINDS}, []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for account in accounts:
            account_id = account["ad_account_id"]
            for kind, (id_key, _, level) in KINDS.items():
                try:
                    metadata = await _pages(client, token,
                        f"https://business-api.tiktok.com/open_api/v1.3/{kind}/get/",
                        {"advertiser_id": account_id,
                         "filtering": json.dumps({"primary_status": "STATUS_ALL"})}, limit=MAX_ENTITIES)
                    entities = _entities(metadata, account_id, kind)
                    # stat_time_day requests support at most 30 days; a 31-day
                    # caller is split into independently validated chunks.
                    reports = []
                    for offset in range(0, len(days), 30):
                        chunk = days[offset:offset + 30]
                        reports.extend(await _pages(client, token, TIKTOK_REPORT_URL,
                            {"advertiser_id": account_id, "report_type": "BASIC", "data_level": level,
                             "dimensions": json.dumps([id_key, "stat_time_day"]),
                             "metrics": json.dumps(["spend", "impressions", "clicks", "conversion"]),
                             "filtering": json.dumps([{ "field_name": f"{kind}_status",
                                 "filter_type": "IN", "filter_value": json.dumps(["STATUS_ALL"])}]),
                             "start_date": chunk[0].isoformat(), "end_date": chunk[-1].isoformat()},
                            limit=MAX_REPORT_ROWS))
                    if len(reports) > MAX_REPORT_ROWS:
                        raise _error("tiktok_hierarchy_row_limit")
                    daily = _daily(reports, kind, [day.isoformat() for day in days])
                    known_ids = {entity["entity_id"] for entity in entities}
                    if any(row["entity_id"] not in known_ids for rows in daily.values() for row in rows):
                        raise _error("tiktok_hierarchy_report_identity_unmatched")
                    if any(len(rows) > MAX_ENTITIES for rows in daily.values()):
                        raise _error("tiktok_hierarchy_daily_row_limit")
                    identity = {"user_id": user_id, "ad_account_id": account_id, "entity_type": kind}
                    common = {"source_mode": SOURCE_MODE, "source_only": True,
                              "observed_at": observed_at, "complete": True}
                    for day, rows in daily.items():
                        await db[DAILY_COLLECTION].update_one({**identity, "date": day},
                            {"$set": {**identity, **common, "date": day,
                                      "row_count": len(rows), "rows": rows}}, upsert=True)
                    await db[ENTITY_COLLECTION].update_one(identity,
                        {"$set": {**identity, **common, "entities": entities}}, upsert=True)
                    counts[kind] += len(entities)
                except TikTokReportingError as exc:
                    if exc.code == "tiktok_needs_reauth":
                        raise
                    errors.append({"ad_account_id": account_id, "entity_type": kind, "code": exc.code})
                except (httpx.HTTPError, ValueError):
                    errors.append({"ad_account_id": account_id, "entity_type": kind,
                                   "code": "tiktok_hierarchy_transport_failed"})
    return {"status": "partial" if errors else "complete", "entity_counts": counts,
            "errors": errors, "errors_count": len(errors)}


async def _read(db, collection, query, maximum):
    if collection == DAILY_COLLECTION:
        headers = await db[collection].find(query, {"_id": 0, "rows": 0}).limit(maximum + 1).to_list(length=maximum + 1)
        if len(headers) > maximum or sum(int(row.get("row_count", MAX_REPORT_ROWS + 1)) for row in headers) > MAX_REPORT_ROWS:
            raise _error("tiktok_workspace_source_limit")
    rows = await db[collection].find(query, {"_id": 0}).limit(maximum + 1).to_list(length=maximum + 1)
    if len(rows) > maximum:
        raise _error("tiktok_workspace_source_limit")
    return rows


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
                           campaign_id=None, adgroup_id=None):
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
    scoped = {"user_id": user_id, "ad_account_id": {"$in": account_ids}}
    account_rows = await _read(db, TIKTOK_REPORTING_COLLECTION,
        {**scoped, "date": {"$in": days}}, len(accounts) * len(days))
    snapshots = await _read(db, ENTITY_COLLECTION, {**scoped, "entity_type": entity_type}, len(accounts))
    daily = await _read(db, DAILY_COLLECTION,
        {**scoped, "entity_type": entity_type, "date": {"$in": days}}, len(accounts) * len(days))
    if sum(len(row.get("rows") or []) for row in daily) > MAX_REPORT_ROWS:
        raise _error("tiktok_workspace_source_limit")
    account_output, entity_output, daily_output = [], [], []
    for account in accounts:
        account_id = account["ad_account_id"]
        rows = [row for row in account_rows if row["ad_account_id"] == account_id]
        complete = {row["date"] for row in rows} == set(days)
        fx, _ = _fx_to_sar(account.get("currency"))
        totals = _totals(rows, complete, fx)
        account_output.append({"account_id": account_id, "account_name": account.get("display_name") or account_id,
                               "currency": account.get("currency"), "timezone": account.get("timezone"), **totals})
        snapshot = next((row for row in snapshots if row["ad_account_id"] == account_id), {})
        observed = [row for row in daily if row["ad_account_id"] == account_id and row.get("complete")]
        entity_complete = {row["date"] for row in observed} == set(days)
        by_entity = {}
        for row in observed:
            for fact in row.get("rows") or []:
                by_entity.setdefault(fact["entity_id"], []).append(fact)
        for entity in snapshot.get("entities") or []:
            if campaign_id and entity["campaign_id"] != campaign_id:
                continue
            if adgroup_id and entity.get("adgroup_id") != adgroup_id:
                continue
            if query and query.casefold() not in (entity["entity_name"] + entity["entity_id"]).casefold():
                continue
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
    entity_output.sort(key=lambda row: (-(row["spend_sar"] or 0), row["account_id"], row["entity_id"]))
    total = len(entity_output)
    pages = math.ceil(total / limit)
    page = min(page, pages) if pages else 1
    identity_ready = len(snapshots) == len(accounts) and all(row.get("complete") for row in snapshots)
    return {"platform": "tiktok", "label": "تيك توك", "result_source": "platform",
            "range": {"date_from": start.isoformat(), "date_to": end.isoformat(), "timezone": "Asia/Riyadh"},
            "totals": totals, "accounts": account_output, "daily": daily_output, "hourly": [],
            "entities": entity_output[(page - 1) * limit:page * limit],
            "campaigns": [], "entity_type": entity_type,
            "campaign_pagination": {"page": page, "limit": limit, "total": total, "pages": pages},
            "source": {"source_mode": SOURCE_MODE, "performance_rows": len(account_rows),
                       "entity_rows": total, "row_limit_reached": False, "entity_limit_reached": False},
            "ai_readiness": {"report_ready": complete, "spend_ready": totals["spend_sar"] is not None,
                             "campaign_identity_ready": identity_ready, "orders_ready": False,
                             "sales_ready": False, "ratios_ready": False, "ai_analysis_ready": False,
                             "campaign_creation_enabled": False, "campaign_management_enabled": False},
            "insights": [{"code": "tiktok_conversions_are_not_orders", "severity": "info",
                          "title": "تحويلات TikTok", "detail": "التحويلات أحداث تبلغ عنها المنصة؛ مبيعات وطلبات سلة تحتاج ربطًا مستقلًا."}],
            "policy": {"mode": "observe_only", "mutations_allowed": False}}
