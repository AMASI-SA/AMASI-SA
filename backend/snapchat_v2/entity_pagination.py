"""Bounded Mongo-side pagination for the Snapchat V2 hierarchy.

The entity catalogue is the driving collection.  Performance facts are
aggregated inside MongoDB, and the filtered summary and requested page are
returned by one facet.  Python therefore materializes at most ``page_size``
entity rows plus scalar metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from .entities import SNAPCHAT_ENTITY_FACTS_COLLECTION
from .facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from .models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
    DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    SNAPCHAT_PROVIDER,
    build_attribution_key,
)
from .total_facts import SNAPCHAT_TOTAL_FACTS_COLLECTION

EntityType = Literal["campaign", "ad_squad", "ad"]
SortBy = Literal["default", "spend", "name"]
SortDirection = Literal["asc", "desc"]

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 100
READ_MAX_TIME_MS = 15_000

_ADDITIVE_FIELDS = (
    "spend_native",
    "impressions",
    "swipes",
    "video_views",
    "view_completion",
    "view_content",
    "add_to_cart",
    "start_checkout",
    "add_billing",
    "purchases",
    "purchase_value_native",
)


@dataclass(frozen=True)
class EntityPageSpec:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    search: str = ""
    active_only: bool = False
    sort_by: SortBy = "default"
    sort_direction: SortDirection = "desc"

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError("page must be at least 1")
        if self.page_size < 1 or self.page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if len(self.search) > MAX_SEARCH_LENGTH:
            raise ValueError(f"search cannot exceed {MAX_SEARCH_LENGTH} characters")
        if self.sort_by not in {"default", "spend", "name"}:
            raise ValueError("unsupported Snapchat entity sort")
        if self.sort_direction not in {"asc", "desc"}:
            raise ValueError("sort_direction must be asc or desc")


def _fact_match(
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: EntityType,
    source_collection: str,
    date_from: date,
    date_to: date,
    start_utc: datetime,
    end_utc: datetime,
    timezone_name: str,
    action_report_time: str,
) -> dict[str, Any]:
    action = str(action_report_time).strip().lower()
    attribution_key = build_attribution_key(
        action,
        {
            "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
            "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
        },
    )
    value: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": entity_type,
        "action_report_time": action,
        "attribution_key": attribution_key,
    }
    if source_collection == SNAPCHAT_TOTAL_FACTS_COLLECTION:
        value.update(
            {
                "report_date": {
                    "$gte": date_from.isoformat(),
                    "$lte": date_to.isoformat(),
                },
                "account_timezone": timezone_name,
            }
        )
    else:
        value["hour_start_utc"] = {"$gte": start_utc, "$lt": end_utc}
    return value


def _performance_lookup(
    *,
    fact_collection: str,
    fact_match: dict[str, Any],
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "_id": None,
        "source_fact_count": {"$sum": 1},
        "paid_reach_first": {"$first": "$paid_reach"},
        "paid_frequency_first": {"$first": "$paid_frequency"},
        "reach_frequency_scope_first": {"$first": "$reach_frequency_scope"},
    }
    for field in _ADDITIVE_FIELDS:
        group[field] = {"$sum": {"$ifNull": [f"${field}", 0]}}
    return {
        "$lookup": {
            "from": fact_collection,
            "let": {"entity_id": "$external_id"},
            "pipeline": [
                {
                    "$match": {
                        **fact_match,
                        "$expr": {"$eq": ["$external_id", "$$entity_id"]},
                    }
                },
                {"$group": group},
            ],
            "as": "performance_rows",
        }
    }


def _summary_group() -> dict[str, Any]:
    group: dict[str, Any] = {
        "_id": None,
        "entity_count": {"$sum": 1},
        "source_fact_count": {"$sum": "$performance.source_fact_count"},
    }
    for field in _ADDITIVE_FIELDS:
        group[field] = {"$sum": f"$performance.{field}"}
    return group


def _sort_spec(spec: EntityPageSpec) -> dict[str, int]:
    if spec.sort_by == "name":
        direction = 1 if spec.sort_direction == "asc" else -1
        return {
            "active": -1,
            "sort_name": direction,
            "external_id": direction,
        }
    if spec.sort_by == "spend":
        direction = 1 if spec.sort_direction == "asc" else -1
        return {
            "active": -1,
            "performance.spend_native": direction,
            "sort_name": 1,
            "external_id": 1,
        }
    return {
        "active": -1,
        "performance.spend_native": -1,
        "sort_name": 1,
        "external_id": 1,
    }


def build_entity_page_pipeline(
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: EntityType,
    source_collection: str,
    date_from: date,
    date_to: date,
    start_utc: datetime,
    end_utc: datetime,
    timezone_name: str,
    action_report_time: str,
    spec: EntityPageSpec,
    campaign_id: str | None = None,
    ad_squad_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build a stable page + filtered-summary pipeline.

    Parent constraints live in the first match, before lookup, sort, facet,
    skip, and limit.  Search and active filters are also applied before the
    inner page facet.
    """
    base_match: dict[str, Any] = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
        "entity_type": entity_type,
    }
    if campaign_id:
        base_match["campaign_id"] = str(campaign_id)
    if ad_squad_id:
        base_match["ad_squad_id"] = str(ad_squad_id)

    fact_match = _fact_match(
        user_id=user_id,
        ad_account_id=ad_account_id,
        entity_type=entity_type,
        source_collection=source_collection,
        date_from=date_from,
        date_to=date_to,
        start_utc=start_utc,
        end_utc=end_utc,
        timezone_name=timezone_name,
        action_report_time=action_report_time,
    )
    filtered_pipeline: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    if spec.active_only:
        filters.append({"active": True})
    needle = spec.search.strip()
    if needle:
        safe = re.escape(needle)
        filters.append(
            {
                "$or": [
                    {"name": {"$regex": safe, "$options": "i"}},
                    {"external_id": {"$regex": safe, "$options": "i"}},
                ]
            }
        )
    if filters:
        filtered_pipeline.append({"$match": {"$and": filters}})
    filtered_pipeline.append(
        {
            "$facet": {
                "items": [
                    {"$sort": _sort_spec(spec)},
                    {"$skip": (spec.page - 1) * spec.page_size},
                    {"$limit": spec.page_size},
                    {"$project": {"performance_rows": 0, "sort_name": 0}},
                ],
                "count": [{"$count": "value"}],
                "summary": [{"$group": _summary_group()}],
            }
        }
    )
    return [
        {"$match": base_match},
        _performance_lookup(
            fact_collection=source_collection,
            fact_match=fact_match,
        ),
        {
            "$set": {
                "performance": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$performance_rows", 0]},
                        {
                            "source_fact_count": 0,
                            **{field: 0 for field in _ADDITIVE_FIELDS},
                        },
                    ]
                },
                "sort_name": {"$toLower": {"$ifNull": ["$name", ""]}},
            }
        },
        {
            "$facet": {
                "catalog_count": [{"$count": "value"}],
                "filtered": filtered_pipeline,
            }
        },
    ]


async def _aggregate_rows(
    collection: Any,
    pipeline: list[dict[str, Any]],
    *,
    length: int,
) -> list[dict[str, Any]]:
    try:
        cursor = collection.aggregate(
            pipeline,
            allowDiskUse=False,
            maxTimeMS=READ_MAX_TIME_MS,
        )
    except TypeError:
        cursor = collection.aggregate(pipeline)
    if hasattr(cursor, "to_list"):
        try:
            return list(await cursor.to_list(length=length))
        except TypeError:
            return list(await cursor.to_list(length))
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= length:
            break
    return rows


async def resolve_fact_source(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: EntityType,
    date_from: date,
    date_to: date,
    timezone_name: str,
    account_timezone: str,
    action_report_time: str,
    level_status: str,
) -> tuple[str, dict[str, Any]]:
    """Choose TOTAL only when every requested account-timezone date exists."""
    expected_dates = {
        (date_from.fromordinal(date_from.toordinal() + offset)).isoformat()
        for offset in range((date_to - date_from).days + 1)
    }
    if timezone_name != account_timezone or level_status != "complete":
        return SNAPCHAT_HOURLY_FACTS_COLLECTION, {
            "total_fact_coverage_complete": False,
            "reason": "account_timezone_or_sync_incomplete",
        }
    action = str(action_report_time).strip().lower()
    attribution_key = build_attribution_key(
        action,
        {
            "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
            "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
        },
    )
    pipeline = [
        {
            "$match": {
                "user_id": str(user_id),
                "provider": SNAPCHAT_PROVIDER,
                "ad_account_id": str(ad_account_id),
                "entity_type": entity_type,
                "report_date": {
                    "$gte": date_from.isoformat(),
                    "$lte": date_to.isoformat(),
                },
                "account_timezone": timezone_name,
                "action_report_time": action,
                "attribution_key": attribution_key,
            }
        },
        {"$group": {"_id": "$report_date"}},
        {"$limit": len(expected_dates) + 1},
    ]
    rows = await _aggregate_rows(
        db[SNAPCHAT_TOTAL_FACTS_COLLECTION],
        pipeline,
        length=len(expected_dates) + 1,
    )
    observed_dates = {str(row.get("_id") or "") for row in rows}
    complete = bool(expected_dates) and expected_dates.issubset(observed_dates)
    return (
        SNAPCHAT_TOTAL_FACTS_COLLECTION
        if complete
        else SNAPCHAT_HOURLY_FACTS_COLLECTION,
        {
            "total_fact_coverage_complete": complete,
            "expected_dates": len(expected_dates),
            "observed_dates": len(observed_dates & expected_dates),
            "reason": "complete" if complete else "missing_total_fact_dates",
        },
    )


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _metrics(value: dict[str, Any], *, exact_audience: bool = False) -> dict[str, Any]:
    spend = _number(value.get("spend_native"))
    purchases = int(_number(value.get("purchases")))
    purchase_value = _number(value.get("purchase_value_native"))
    impressions = int(_number(value.get("impressions")))
    swipes = int(_number(value.get("swipes")))
    result = {
        "spend_native": round(spend, 6),
        "impressions": impressions,
        "swipes": swipes,
        "video_views": int(_number(value.get("video_views"))),
        "view_completion": round(_number(value.get("view_completion")), 6),
        "view_content": int(_number(value.get("view_content"))),
        "add_to_cart": int(_number(value.get("add_to_cart"))),
        "start_checkout": int(_number(value.get("start_checkout"))),
        "add_billing": int(_number(value.get("add_billing"))),
        "purchases": purchases,
        "purchase_value_native": round(purchase_value, 6),
        "paid_reach": value.get("paid_reach_first") if exact_audience else None,
        "paid_frequency": (
            value.get("paid_frequency_first") if exact_audience else None
        ),
        "reach_frequency_scope": (
            "exact_one_day_total" if exact_audience else "exact_total_window_required"
        ),
        "roas": round(purchase_value / spend, 6) if spend > 0 else None,
        "ctr_pct": round(swipes / impressions * 100, 6) if impressions > 0 else None,
        "source_fact_count": int(_number(value.get("source_fact_count"))),
    }
    return result


def _entity_row(
    document: dict[str, Any],
    *,
    entity_type: EntityType,
    account_id: str,
    source_collection: str,
    level_status: str,
) -> dict[str, Any]:
    performance = dict(document.get("performance") or {})
    source_fact_count = int(_number(performance.get("source_fact_count")))
    external_id = str(document.get("external_id") or "")
    display_name = str(document.get("name") or external_id)
    campaign_id = (
        external_id
        if entity_type == "campaign"
        else str(document.get("campaign_id") or "")
    )
    ad_squad_id = (
        external_id
        if entity_type == "ad_squad"
        else str(document.get("ad_squad_id") or "")
    )
    row = {
        "account_id": account_id,
        "entity_type": entity_type,
        "external_id": external_id,
        "name": display_name,
        "status": document.get("status"),
        "active": document.get("active") is True,
        "campaign_id": campaign_id or None,
        "campaign_name": (
            display_name
            if entity_type == "campaign"
            else document.get("campaign_name") or campaign_id or None
        ),
        "ad_squad_id": ad_squad_id or None,
        "ad_squad_name": ad_squad_id or None,
        **_metrics(
            performance,
            exact_audience=(
                source_collection == SNAPCHAT_TOTAL_FACTS_COLLECTION
                and source_fact_count == 1
                and performance.get("reach_frequency_scope_first")
                == "exact_one_day_total"
            ),
        ),
        "source_collection": source_collection,
        "performance_sync_status": (
            "complete"
            if source_fact_count and level_status == "complete"
            else "partial"
            if source_fact_count
            else "no_facts"
        ),
    }
    if entity_type == "ad_squad":
        row["ad_squad_name"] = row["name"]
    elif entity_type == "ad":
        row["ad_id"] = external_id
        row["ad_name"] = row["name"]
    return row


async def read_entity_page(
    db: Any,
    *,
    user_id: str,
    ad_account_id: str,
    entity_type: EntityType,
    source_collection: str,
    date_from: date,
    date_to: date,
    start_utc: datetime,
    end_utc: datetime,
    timezone_name: str,
    action_report_time: str,
    level_status: str,
    spec: EntityPageSpec,
    campaign_id: str | None = None,
    ad_squad_id: str | None = None,
) -> dict[str, Any]:
    pipeline = build_entity_page_pipeline(
        user_id=user_id,
        ad_account_id=ad_account_id,
        entity_type=entity_type,
        source_collection=source_collection,
        date_from=date_from,
        date_to=date_to,
        start_utc=start_utc,
        end_utc=end_utc,
        timezone_name=timezone_name,
        action_report_time=action_report_time,
        spec=spec,
        campaign_id=campaign_id,
        ad_squad_id=ad_squad_id,
    )
    roots = await _aggregate_rows(
        db[SNAPCHAT_ENTITY_FACTS_COLLECTION],
        pipeline,
        length=1,
    )
    root = roots[0] if roots else {}
    catalog_count = list(root.get("catalog_count") or [])
    filtered_root = list(root.get("filtered") or [])
    filtered = filtered_root[0] if filtered_root else {}
    count_rows = list(filtered.get("count") or [])
    summary_rows = list(filtered.get("summary") or [])
    total = int((catalog_count[0] if catalog_count else {}).get("value") or 0)
    filtered_total = int((count_rows[0] if count_rows else {}).get("value") or 0)
    summary = dict(summary_rows[0] if summary_rows else {})
    summary.pop("_id", None)
    rows = [
        _entity_row(
            dict(document),
            entity_type=entity_type,
            account_id=str(ad_account_id),
            source_collection=source_collection,
            level_status=level_status,
        )
        for document in list(filtered.get("items") or [])[: spec.page_size]
    ]
    totals = {
        **_metrics(summary),
        "source_collection": source_collection,
        "entity_count": int(summary.get("entity_count") or filtered_total),
    }
    pages = (filtered_total + spec.page_size - 1) // spec.page_size
    return {
        "rows": rows,
        "totals": totals,
        "performance_sync_status": level_status,
        "source_collection": source_collection,
        "pagination": {
            "page": spec.page,
            "page_size": spec.page_size,
            "total": total,
            "filtered_total": filtered_total,
            "pages": pages,
            "has_more": spec.page < pages,
            "sort": {
                "by": spec.sort_by,
                "direction": spec.sort_direction,
                "stable_tiebreaker": "external_id",
            },
            "filters": {
                "search": spec.search.strip(),
                "active_only": spec.active_only,
                "campaign_id": campaign_id,
                "ad_squad_id": ad_squad_id,
            },
        },
        "read_diagnostics": {
            "mongo_commands": 1,
            "python_entity_rows_materialized": len(rows),
            "python_summary_rows_materialized": 1 if summary_rows else 0,
            "max_entity_rows_materialized": spec.page_size,
            "parent_filter_applied": bool(campaign_id or ad_squad_id),
        },
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "EntityPageSpec",
    "build_entity_page_pipeline",
    "read_entity_page",
    "resolve_fact_source",
]
