"""Read-only Salla order-source audit for the Snapchat Ads Manager.

The audit uses the same account-local date semantics and exact campaign matcher
as the Production campaign report. It never distributes direct, WhatsApp,
manual, gift, ambiguous, or otherwise unattributed orders across campaigns.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Callable

from auth import ensure_user_settings
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    meaningful_source_label,
)

from . import snapchat_account_timezone_manager as manager
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_campaign_created_order_semantics import is_cancelled_order
from .snapchat_campaign_result_source_routes import _match_order_campaign
from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    _collection,
    _utcnow,
)

MAX_AUDIT_ROWS = 500

AUDIT_ORDER_PROJECTION = {
    **SALLA_RAW_ATTRIBUTION_PROJECTION,
    "order_number": 1,
    "reference_id": 1,
    "order_id": 1,
    "id": 1,
    "created_at": 1,
    "order_created_at": 1,
    "created_at_utc": 1,
    "source_created_at": 1,
    "updated_at": 1,
    "order_date": 1,
    "order_date_inferred": 1,
    "order_status_native": 1,
    "status_native": 1,
    "order_status": 1,
    "status": 1,
    "total_amount": 1,
    "total": 1,
    "source_native": 1,
    "source": 1,
    "order_source": 1,
    "utm_source": 1,
    "utm_medium": 1,
    "utm_campaign": 1,
    "campaign_id": 1,
    "campaign_name": 1,
    "source_campaign_id": 1,
    "source_campaign_name": 1,
    "channel": 1,
    "platform": 1,
    "traffic_source": 1,
    "marketing_source": 1,
    "source_name": 1,
    "created_via": 1,
    "created_by_type": 1,
    "order_type": 1,
    "order_kind": 1,
    "type_of_order": 1,
    "is_gift": 1,
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def _matches_any(value: str, allowed: list[str]) -> bool:
    """Match configured statuses without importing the Dashboard route graph."""
    if not allowed:
        return True
    normalized = str(value or "").strip().casefold()
    return any(
        candidate
        and (
            candidate == normalized
            or candidate in normalized
            or normalized in candidate
        )
        for candidate in (str(item).strip().casefold() for item in allowed)
    )


def _first_text(order: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(order.get(field) or "").strip()
        if value:
            return value
    return ""


def classify_order_origin(order: dict[str, Any]) -> tuple[str, str]:
    """Classify the recorded order origin without changing attribution."""
    source_label = meaningful_source_label(order)
    order_type = _first_text(
        order,
        ("order_type", "order_kind", "type_of_order"),
    )
    haystack = _norm(" ".join(part for part in (source_label, order_type) if part))
    if any(token in haystack for token in ("whatsapp", "whats app", "واتساب", "واتس اب")):
        return "whatsapp", source_label or order_type or "واتساب"
    if any(token in haystack for token in ("manual", "manually", "يدوي", "يدويا", "يدويًا")):
        return "manual", source_label or order_type or "يدوي"
    if any(token in haystack for token in ("direct", "مباشر", "مباشرة")):
        return "direct", source_label or order_type or "مباشر"
    if any(token in haystack for token in ("gift", "هدية", "هديه", "إهداء", "اهداء")):
        return "gift", source_label or order_type or "هدية"
    if source_label or order_type:
        return "other", source_label or order_type
    return "unknown", "غير محدد"


def _order_number(order: dict[str, Any]) -> str:
    return _first_text(order, ("order_number", "reference_id", "order_id", "id"))


def _order_status(order: dict[str, Any]) -> str:
    return _first_text(
        order,
        ("order_status_native", "status_native", "order_status", "status"),
    )


def _order_amount(order: dict[str, Any]) -> float:
    return float(manager._number(order.get("total_amount") or order.get("total")) or 0.0)


def _is_gift(order: dict[str, Any]) -> bool:
    if order.get("is_gift") is True:
        return True
    value = _norm(_first_text(order, ("order_type", "order_kind", "type_of_order")))
    return any(token in value for token in ("gift", "هدية", "هديه", "إهداء", "اهداء"))


def platform_purchases_for_audit(
    account_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    *,
    requested_days: int,
) -> tuple[int, str]:
    """Use campaign-grain purchases for the campaign audit when available.

    Account-day projections can trail campaign rows during the current account
    day. The audit compares campaign attribution, so campaign rows are the
    authoritative grain. Account rows remain a safe fallback when campaign
    detail has not arrived yet.
    """
    source_rows = campaign_rows or account_rows
    source = "campaign_rows" if campaign_rows else "account_rows_fallback"
    summary = manager._aggregate_rows(source_rows, requested_days=requested_days)
    return int(summary.get("orders") or 0), source


def build_order_audit_rows(
    orders: list[dict[str, Any]],
    *,
    identities: list[dict[str, Any]],
    timezone_name: str,
    date_from: str,
    date_to: str,
    included_statuses: list[str],
    platform_attributed_purchases: int,
) -> dict[str, Any]:
    """Build deterministic summary and request-level audit rows."""
    id_lookup = manager._unique_lookup(identities, "campaign_id")
    name_lookup = manager._unique_lookup(identities, "campaign_name")
    identity_by_key = {
        (str(item.get("account_id") or ""), str(item.get("campaign_id") or "")): item
        for item in identities
    }
    zone = manager._timezone(timezone_name)
    counters: Counter[str] = Counter()
    origin_counts: Counter[str] = Counter()
    audit_rows: list[dict[str, Any]] = []
    total_financial_sales = 0.0
    matched_financial_sales = 0.0

    for order in orders:
        timestamp = manager._order_timestamp(order)
        if timestamp is not None:
            localized = timestamp.astimezone(zone)
            local_date = localized.date().isoformat()
            local_created_at = localized.isoformat()
            date_source = "created_at_localized"
        else:
            local_date = str(order.get("order_date") or "")[:10]
            local_created_at = local_date or None
            date_source = "order_date_fallback"
        if not local_date or local_date < date_from or local_date > date_to:
            continue

        counters["total_salla_created_orders"] += 1
        origin_category, source_label = classify_order_origin(order)
        origin_counts[origin_category] += 1
        financial = _matches_any(order.get("order_status", ""), included_statuses)
        amount = _order_amount(order)
        if financial:
            counters["total_financial_orders"] += 1
            total_financial_sales += amount

        key, match_method = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        campaign_id = None
        campaign_name = None
        if key is not None:
            classification = "matched"
            counters["campaign_matched_orders"] += 1
            identity = identity_by_key.get(key, {})
            campaign_id = key[1]
            campaign_name = str(identity.get("campaign_name") or campaign_id)
            if financial:
                counters["campaign_matched_financial_orders"] += 1
                matched_financial_sales += amount
        elif str(match_method).startswith("ambiguous"):
            classification = "ambiguous"
            counters["ambiguous_orders"] += 1
        else:
            classification = "non_campaign"
            counters["non_campaign_orders"] += 1

        audit_rows.append({
            "order_number": _order_number(order),
            "local_created_at": local_created_at,
            "local_date": local_date,
            "date_source": date_source,
            "timezone": timezone_name,
            "status": _order_status(order),
            "amount_sar": round(amount, 2),
            "financially_included": bool(financial),
            "cancelled": is_cancelled_order(order),
            "is_gift": _is_gift(order),
            "order_type": _first_text(order, ("order_type", "order_kind", "type_of_order")) or None,
            "source_label": source_label,
            "origin_category": origin_category,
            "classification": classification,
            "match_method": match_method,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
        })

    audit_rows.sort(
        key=lambda row: (
            str(row.get("local_created_at") or ""),
            str(row.get("order_number") or ""),
        ),
        reverse=True,
    )
    total = int(counters["total_salla_created_orders"])
    matched = int(counters["campaign_matched_orders"])
    non_campaign = int(counters["non_campaign_orders"])
    ambiguous = int(counters["ambiguous_orders"])
    return {
        "summary": {
            "total_salla_created_orders": total,
            "campaign_matched_orders": matched,
            "campaign_matched_financial_orders": int(counters["campaign_matched_financial_orders"]),
            "non_campaign_orders": non_campaign,
            "ambiguous_orders": ambiguous,
            "classified_orders": matched + non_campaign + ambiguous,
            "platform_attributed_purchases": int(platform_attributed_purchases or 0),
            "platform_minus_confirmed_campaign_orders": int(platform_attributed_purchases or 0) - matched,
            "total_financial_orders": int(counters["total_financial_orders"]),
            "total_financial_sales_sar": round(total_financial_sales, 2),
            "campaign_matched_financial_sales_sar": round(matched_financial_sales, 2),
            "origin_breakdown": dict(origin_counts),
            "date_timezone": timezone_name,
            "campaign_attribution_policy": "exact_campaign_id_or_unique_snapchat_campaign_name",
            "non_campaign_distribution_allowed": False,
        },
        "orders": audit_rows[:MAX_AUDIT_ROWS],
        "orders_total": len(audit_rows),
        "orders_returned": min(len(audit_rows), MAX_AUDIT_ROWS),
        "truncated": len(audit_rows) > MAX_AUDIT_ROWS,
    }


async def build_snapchat_order_source_audit(
    db: Any,
    user_id: str,
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    now: Callable = _utcnow,
) -> dict[str, Any]:
    current = manager._aware_now(now())
    selected_accounts = await _load_selected_accounts(db, user_id)
    if not selected_accounts:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "لا توجد حسابات Snapchat محددة داخل ميزان.",
            status_code=409,
        )
    requested_id = str(account_id or "").strip()
    selected = next(
        (
            row for row in selected_accounts
            if str(row.get("ad_account_id") or "").strip() == requested_id
        ),
        None,
    ) if requested_id else selected_accounts[0]
    if selected is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_not_selected",
            "الحساب الإعلاني المطلوب غير محدد داخل ميزان.",
            status_code=404,
        )

    selected_meta = manager._account_public_row(selected, now=current)
    selected_id = selected_meta["account_id"]
    timezone_name = selected_meta["timezone"]
    dates = manager.resolve_account_report_dates(
        from_date,
        to_date,
        timezone_name=timezone_name,
        now=current,
    )
    date_from_value = dates[0].isoformat()
    date_to_value = dates[-1].isoformat()
    date_query = {"$gte": date_from_value, "$lte": date_to_value}

    performance_rows = await manager._to_list(
        _collection(db, manager.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION).find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": selected_id,
                "entity_type": {"$in": ["ad_account", "campaign"]},
                "date": date_query,
                "date_timezone": timezone_name,
            },
            {"_id": 0},
        ),
        manager.MAX_REPORT_ROWS,
    )
    account_rows = [row for row in performance_rows if row.get("entity_type") == "ad_account"]
    campaign_rows = [row for row in performance_rows if row.get("entity_type") == "campaign"]
    platform_purchases, platform_purchase_source = platform_purchases_for_audit(
        account_rows,
        campaign_rows,
        requested_days=len(dates),
    )

    campaign_ids = sorted({
        str(row.get("campaign_id") or row.get("external_id") or "").strip()
        for row in campaign_rows
        if str(row.get("campaign_id") or row.get("external_id") or "").strip()
    })
    entity_rows = await manager._to_list(
        _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "ad_account_id": selected_id,
                "entity_type": "campaign",
                "external_id": {"$in": campaign_ids},
            },
            {"_id": 0, "external_id": 1, "display_name": 1},
        ),
        manager.MAX_ENTITY_ROWS,
    )
    name_by_id = {
        str(row.get("external_id") or "").strip(): str(row.get("display_name") or "").strip()
        for row in entity_rows
    }
    identities = [
        {
            "account_id": selected_id,
            "campaign_id": campaign_id,
            "campaign_name": name_by_id.get(campaign_id) or campaign_id,
        }
        for campaign_id in campaign_ids
    ]

    settings = await ensure_user_settings(db, user_id)
    included_statuses = settings.get("report_included_statuses") or []
    start = date.fromisoformat(date_from_value) - timedelta(days=1)
    end = date.fromisoformat(date_to_value) + timedelta(days=1)
    order_query: dict[str, Any] = {
        "user_id": user_id,
        "order_date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
    }
    if settings.get("hide_inferred_date_orders"):
        order_query["order_date_inferred"] = {"$ne": True}
    orders = await manager._to_list(
        db.unified_orders.find(order_query, AUDIT_ORDER_PROJECTION),
        100_000,
    )
    result = build_order_audit_rows(
        orders,
        identities=identities,
        timezone_name=timezone_name,
        date_from=date_from_value,
        date_to=date_to_value,
        included_statuses=included_statuses,
        platform_attributed_purchases=platform_purchases,
    )
    result["summary"]["platform_purchase_source"] = platform_purchase_source
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "account": selected_meta,
        "date_from": date_from_value,
        "date_to": date_to_value,
        **result,
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "policy": {
            "mode": "observe_only",
            "mutations_allowed": False,
            "non_campaign_distribution_allowed": False,
        },
    }


__all__ = [
    "MAX_AUDIT_ROWS",
    "build_order_audit_rows",
    "build_snapchat_order_source_audit",
    "classify_order_origin",
    "platform_purchases_for_audit",
]
