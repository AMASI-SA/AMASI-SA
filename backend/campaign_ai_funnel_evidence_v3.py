"""Full-funnel and creative evidence for Campaign AI Decision Intelligence V3.

Snapchat evidence is read from the same account-local conversion-time facts used
by Ads Manager.  Meta evidence is a bounded read-only Insights query.  Extended
video fields are best-effort: if Meta rejects a field, the collector falls back
to the core funnel request rather than making the entire AI cycle fail.

No metric pattern becomes a recommendation in this module.  It only computes
ratios and preserves provenance for OpenAI root-cause reasoning.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

from integrations_control_center.meta_campaign_reporting import _paged_get
from integrations_control_center.meta_native_reporting import (
    _accounts as _meta_accounts,
    _credential as _meta_credential,
    _fx_to_sar as _meta_fx_to_sar,
)
from integrations_control_center.meta_oauth_security import (
    meta_appsecret_proof,
    meta_graph_base,
)
from integrations_control_center.snapchat_account_timezone_manager import (
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
)
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
)


META_ENTITY_COLLECTION = "mezan_meta_entity_performance_daily_v1"
ACTION_REPORT_TIME = "conversion"
MAX_ROWS = 100_000

META_CORE_FIELDS = (
    "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "spend,impressions,clicks,actions,action_values,account_currency,"
    "date_start,date_stop"
)
META_EXTENDED_FIELDS = (
    META_CORE_FIELDS
    + ",frequency,cpm,cpc,ctr,outbound_clicks,"
    "video_3_sec_watched_actions,video_p25_watched_actions,"
    "video_p50_watched_actions,video_p75_watched_actions,"
    "video_p95_watched_actions,video_p100_watched_actions,"
    "video_avg_time_watched_actions"
)

ACTION_ALIASES = {
    "landing_page_views": {"landing_page_view"},
    "view_content": {"view_content", "offsite_conversion.fb_pixel_view_content"},
    "add_to_cart": {"add_to_cart", "offsite_conversion.fb_pixel_add_to_cart"},
    "initiate_checkout": {"initiate_checkout", "offsite_conversion.fb_pixel_initiate_checkout"},
    "add_payment_info": {"add_payment_info", "offsite_conversion.fb_pixel_add_payment_info"},
    "purchase": {
        "omni_purchase",
        "purchase",
        "offsite_conversion.fb_pixel_purchase",
        "onsite_conversion.purchase",
        "mobile_app_purchase",
    },
    "link_click": {"link_click"},
}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _sum_rows(rows: Any) -> float | None:
    if not isinstance(rows, list):
        return None
    values = []
    for item in rows:
        if isinstance(item, dict) and _number(item.get("value")) is not None:
            values.append(float(item["value"]))
    return sum(values) if values else None


def _action_map(actions: Any) -> dict[str, float | None]:
    by_type: dict[str, float] = {}
    if isinstance(actions, list):
        for item in actions:
            if not isinstance(item, dict):
                continue
            key = str(item.get("action_type") or "")
            value = _number(item.get("value"))
            if key and value is not None:
                by_type[key] = by_type.get(key, 0.0) + value
    output: dict[str, float | None] = {}
    for name, aliases in ACTION_ALIASES.items():
        matches = [by_type[key] for key in aliases if key in by_type]
        output[name] = sum(matches) if matches else None
    return output


def _video_value(row: dict[str, Any], field: str) -> float | None:
    return _sum_rows(row.get(field))


def _safe_ratio(numerator: Any, denominator: Any, *, percent: bool = True) -> float | None:
    n = _number(numerator)
    d = _number(denominator)
    if n is None or d in {None, 0}:
        return None
    value = n / d * (100.0 if percent else 1.0)
    return round(value, 4)


def _entity_key(provider: str, level: str, account_id: str, entity_id: str) -> str:
    return "|".join((provider, level, account_id, entity_id))


def _window_summary(rows: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    if not rows:
        return {"available": False, "source": source, "metrics": {}, "rates": {}, "video": {}, "limitations": ["no_rows"]}

    additive = defaultdict(float)
    seen = defaultdict(int)
    latest_non_additive: dict[str, float | None] = {}
    for row in rows:
        for key in (
            "spend_sar", "impressions", "clicks", "landing_page_views", "view_content",
            "add_to_cart", "initiate_checkout", "add_payment_info", "purchases",
            "purchase_value_sar", "video_views", "video_3s", "video_25", "video_50",
            "video_75", "video_95", "video_100",
        ):
            value = _number(row.get(key))
            if value is not None:
                additive[key] += value
                seen[key] += 1
        for key in ("frequency", "average_watch_time_seconds", "view_completion"):
            value = _number(row.get(key))
            if value is not None:
                latest_non_additive[key] = value

    metrics = {
        key: round(additive[key], 4) if seen[key] else None
        for key in (
            "spend_sar", "impressions", "clicks", "landing_page_views", "view_content",
            "add_to_cart", "initiate_checkout", "add_payment_info", "purchases",
            "purchase_value_sar",
        )
    }
    rates = {
        "ctr_pct": _safe_ratio(metrics["clicks"], metrics["impressions"]),
        "landing_page_rate_pct": _safe_ratio(metrics["landing_page_views"], metrics["clicks"]),
        "view_content_rate_pct": _safe_ratio(metrics["view_content"], metrics["clicks"]),
        "atc_rate_from_click_pct": _safe_ratio(metrics["add_to_cart"], metrics["clicks"]),
        "checkout_rate_from_atc_pct": _safe_ratio(metrics["initiate_checkout"], metrics["add_to_cart"]),
        "purchase_rate_from_checkout_pct": _safe_ratio(metrics["purchases"], metrics["initiate_checkout"]),
        "purchase_rate_from_click_pct": _safe_ratio(metrics["purchases"], metrics["clicks"]),
        "cpc_sar": (
            round(metrics["spend_sar"] / metrics["clicks"], 4)
            if metrics["spend_sar"] is not None and metrics["clicks"] not in {None, 0}
            else None
        ),
        "cpm_sar": (
            round(metrics["spend_sar"] * 1000 / metrics["impressions"], 4)
            if metrics["spend_sar"] is not None and metrics["impressions"] not in {None, 0}
            else None
        ),
    }
    video = {
        "video_views_2s_or_provider_view": round(additive["video_views"], 4) if seen["video_views"] else None,
        "video_views_3s": round(additive["video_3s"], 4) if seen["video_3s"] else None,
        "watched_25_pct": round(additive["video_25"], 4) if seen["video_25"] else None,
        "watched_50_pct": round(additive["video_50"], 4) if seen["video_50"] else None,
        "watched_75_pct": round(additive["video_75"], 4) if seen["video_75"] else None,
        "watched_95_pct": round(additive["video_95"], 4) if seen["video_95"] else None,
        "watched_100_pct": round(additive["video_100"], 4) if seen["video_100"] else None,
        "average_watch_time_seconds": latest_non_additive.get("average_watch_time_seconds"),
        "video_completion_rate": latest_non_additive.get("view_completion"),
        "frequency": latest_non_additive.get("frequency"),
    }
    return {
        "available": True,
        "source": source,
        "metrics": metrics,
        "rates": rates,
        "video": video,
        "limitations": [],
    }


async def _snapchat_rows(db: Any, user_id: str, candidate: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
    level = str(candidate.get("entity_level") or "")
    entity_type = {"campaign": "campaign", "ad_group": "ad_squad", "ad": "ad"}.get(level)
    if not entity_type:
        return []
    account_id = str(candidate.get("account_id") or "")
    entity_id = str(candidate.get("entity_id") or "")
    selector = {
        "user_id": user_id,
        "ad_account_id": account_id,
        "entity_type": entity_type,
        "external_id": entity_id,
        "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
    }
    collection = SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
    selector["action_report_time"] = ACTION_REPORT_TIME
    documents = await db[collection].find(selector, {"_id": 0}).limit(1000).to_list(length=1000)
    if not documents and level == "campaign":
        selector.pop("action_report_time", None)
        documents = await db[SNAPCHAT_PERFORMANCE_COLLECTION].find(selector, {"_id": 0}).limit(1000).to_list(length=1000)
    output = []
    for doc in documents:
        metrics = doc.get("metrics") if isinstance(doc.get("metrics"), dict) else {}
        funnel = doc.get("funnel_metrics") if isinstance(doc.get("funnel_metrics"), dict) else {}
        computed = doc.get("computed") if isinstance(doc.get("computed"), dict) else {}
        output.append({
            "date": doc.get("date"),
            "spend_sar": doc.get("spend_sar"),
            "purchase_value_sar": doc.get("purchase_value_sar"),
            "impressions": metrics.get("impressions"),
            "clicks": metrics.get("swipes"),
            "view_content": funnel.get("conversion_view_content"),
            "add_to_cart": funnel.get("conversion_add_cart"),
            "initiate_checkout": funnel.get("conversion_start_checkout"),
            "add_payment_info": funnel.get("conversion_add_billing"),
            "purchases": funnel.get("conversion_purchases"),
            "video_views": metrics.get("video_views"),
            "view_completion": metrics.get("view_completion"),
            "frequency": metrics.get("frequency"),
            "cpc": computed.get("cpc"),
            "cpm": computed.get("cpm"),
        })
    return output


async def _snapchat_creative_metadata(db: Any, user_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("provider") != "snapchat" or candidate.get("entity_level") != "ad":
        return {}
    row = await db[SNAPCHAT_ENTITY_COLLECTION].find_one(
        {"user_id": user_id, "entity_type": "ad", "external_id": str(candidate.get("entity_id") or "")},
        {"_id": 0, "creative_id": 1, "created_at_provider": 1, "updated_at_provider": 1, "provider_snapshot": 1},
        sort=[("updated_at", -1)],
    ) or {}
    snapshot = row.get("provider_snapshot") if isinstance(row.get("provider_snapshot"), dict) else {}
    web_view = snapshot.get("web_view_properties") if isinstance(snapshot.get("web_view_properties"), dict) else {}
    return {
        "creative_id": row.get("creative_id") or snapshot.get("creative_id"),
        "creative_type": snapshot.get("type") or snapshot.get("creative_type"),
        "media_id": snapshot.get("top_snap_media_id") or snapshot.get("media_id"),
        "destination_url": web_view.get("url"),
        "created_at": row.get("created_at_provider"),
        "last_major_edit_at": row.get("updated_at_provider"),
        "learning_or_delivery_status": candidate.get("effective_status") or candidate.get("status"),
    }


async def _meta_fetch_level(
    client: httpx.AsyncClient,
    token: str,
    account: dict[str, Any],
    *,
    start: date,
    end: date,
    level: str,
) -> tuple[list[dict[str, Any]], str]:
    account_id = str(account.get("ad_account_id") or "")
    base_params = {
        "access_token": token,
        "appsecret_proof": meta_appsecret_proof(token),
        "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}, separators=(",", ":")),
        "time_increment": 1,
        "level": level,
        "action_report_time": "conversion",
        "use_account_attribution_setting": "true",
        "use_unified_attribution_setting": "true",
        "limit": 500,
    }
    try:
        rows, _ = await _paged_get(
            client,
            f"{meta_graph_base()}/{account_id}/insights",
            {**base_params, "fields": META_EXTENDED_FIELDS},
            operation=f"meta_ai_v3_{level}_extended",
        )
        return rows, "meta_insights_v3_extended"
    except Exception:
        rows, _ = await _paged_get(
            client,
            f"{meta_graph_base()}/{account_id}/insights",
            {**base_params, "fields": META_CORE_FIELDS},
            operation=f"meta_ai_v3_{level}_core",
        )
        return rows, "meta_insights_v3_core_fallback"


def _meta_normalize_row(row: dict[str, Any], account: dict[str, Any], level: str) -> tuple[str, dict[str, Any]] | None:
    entity_id = _text(row.get("adset_id") if level == "adset" else row.get("ad_id"), 160)
    if not entity_id:
        return None
    action_map = _action_map(row.get("actions"))
    currency = _text(row.get("account_currency") or account.get("currency"), 12).upper()
    fx, _ = _meta_fx_to_sar(currency)
    spend_native = _number(row.get("spend"))
    purchase_values = _action_map(row.get("action_values"))
    purchase_value_native = purchase_values.get("purchase")
    return entity_id, {
        "date": row.get("date_start"),
        "spend_sar": round(spend_native * fx, 2) if spend_native is not None and fx else None,
        "purchase_value_sar": round(purchase_value_native * fx, 2) if purchase_value_native is not None and fx else None,
        "impressions": _number(row.get("impressions")),
        "clicks": _number(row.get("clicks")),
        "landing_page_views": action_map.get("landing_page_views"),
        "view_content": action_map.get("view_content"),
        "add_to_cart": action_map.get("add_to_cart"),
        "initiate_checkout": action_map.get("initiate_checkout"),
        "add_payment_info": action_map.get("add_payment_info"),
        "purchases": action_map.get("purchase"),
        "video_3s": _video_value(row, "video_3_sec_watched_actions"),
        "video_25": _video_value(row, "video_p25_watched_actions"),
        "video_50": _video_value(row, "video_p50_watched_actions"),
        "video_75": _video_value(row, "video_p75_watched_actions"),
        "video_95": _video_value(row, "video_p95_watched_actions"),
        "video_100": _video_value(row, "video_p100_watched_actions"),
        "average_watch_time_seconds": _video_value(row, "video_avg_time_watched_actions"),
        "frequency": _number(row.get("frequency")),
    }


async def _meta_evidence(db: Any, user_id: str, candidates: list[dict[str, Any]], start: date, end: date) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    wanted = {
        (str(row.get("account_id") or ""), str(row.get("entity_level") or ""), str(row.get("entity_id") or ""))
        for row in candidates if row.get("provider") == "meta" and row.get("entity_level") in {"ad_group", "ad"}
    }
    if not wanted:
        return {}, []
    current = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    try:
        token = await _meta_credential(db, user_id, current)
        accounts = await _meta_accounts(db, user_id)
    except Exception as exc:
        return {}, [f"meta_v3_evidence_unavailable:{type(exc).__name__}"]
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    limitations: list[str] = []
    async with httpx.AsyncClient(timeout=40.0) as client:
        for account in accounts:
            account_id = str(account.get("ad_account_id") or "")
            for level in ("adset", "ad"):
                entity_level = "ad_group" if level == "adset" else "ad"
                if not any(key[0] == account_id and key[1] == entity_level for key in wanted):
                    continue
                try:
                    rows, source = await _meta_fetch_level(client, token, account, start=start, end=end, level=level)
                except Exception as exc:
                    limitations.append(f"meta_v3_{level}_failed:{type(exc).__name__}")
                    continue
                if source.endswith("core_fallback"):
                    limitations.append(f"meta_v3_{level}_video_metrics_unavailable")
                for raw in rows[:20_000]:
                    if not isinstance(raw, dict):
                        continue
                    normalized = _meta_normalize_row(raw, account, level)
                    if not normalized:
                        continue
                    entity_id, fact = normalized
                    key_tuple = (account_id, entity_level, entity_id)
                    if key_tuple not in wanted:
                        continue
                    output[_entity_key("meta", entity_level, account_id, entity_id)].append({**fact, "source": source})
    return dict(output), limitations


async def build_funnel_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    *,
    end: date,
) -> dict[str, Any]:
    start = end - timedelta(days=29)
    meta_rows, limitations = await _meta_evidence(db, user_id, candidates, start, end)
    entities: dict[str, Any] = {}
    windows = {
        "today": (end, end),
        "yesterday": (end - timedelta(days=1), end - timedelta(days=1)),
        "day_minus_2": (end - timedelta(days=2), end - timedelta(days=2)),
        "baseline_7d": (end - timedelta(days=6), end),
        "baseline_30d": (start, end),
    }
    for candidate in candidates:
        key = _entity_key(
            str(candidate.get("provider") or ""),
            str(candidate.get("entity_level") or ""),
            str(candidate.get("account_id") or ""),
            str(candidate.get("entity_id") or ""),
        )
        if candidate.get("provider") == "snapchat":
            all_rows = await _snapchat_rows(db, user_id, candidate, start, end)
            source = "snapchat_ads_manager_conversion_time_daily_facts"
            creative = await _snapchat_creative_metadata(db, user_id, candidate)
        else:
            all_rows = meta_rows.get(key, [])
            source = "meta_insights_conversion_time_v3"
            creative = {
                "created_at": None,
                "last_major_edit_at": candidate.get("status_updated_at"),
                "learning_or_delivery_status": candidate.get("effective_status") or candidate.get("status"),
            }
        by_window = {}
        for label, (window_start, window_end) in windows.items():
            selected = [
                row for row in all_rows
                if window_start.isoformat() <= str(row.get("date") or "")[:10] <= window_end.isoformat()
            ]
            by_window[label] = _window_summary(selected, source=source)
        entities[key] = {
            "windows": by_window,
            "creative_metadata": creative,
            "framework": {
                "early_video_drop_is_hook_evidence_not_rule": True,
                "good_completion_low_ctr_is_offer_or_cta_evidence_not_rule": True,
                "good_ctr_low_atc_is_landing_or_product_evidence_not_rule": True,
                "good_atc_low_purchase_is_checkout_payment_shipping_evidence_not_rule": True,
                "video_views_alone_never_define_success": True,
            },
        }
    return {
        "schema_version": "campaign_ai_funnel_evidence_v3",
        "entities": entities,
        "limitations": limitations,
    }


__all__ = ["build_funnel_evidence"]
