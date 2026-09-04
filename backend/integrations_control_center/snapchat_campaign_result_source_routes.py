"""Snapchat campaign report with selectable Salla or provider results.

Spend, delivery, impressions, clicks and budgets always remain provider facts.
Only commercial outcomes change with ``result_source``:

* ``salla`` (default): orders and sales from financially included Salla orders;
* ``platform``: purchases and purchase value reported by Snapchat.

The route is read-only and never writes to Snapchat, Salla, accounting or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from salla_marketing_attribution import (
    attribution_containers,
    canonical_ad_platform,
)

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_campaign_report_routes import read_snapchat_campaign_report
from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    _collection,
)

RESULT_SOURCE_SALLA = "salla"
RESULT_SOURCE_PLATFORM = "platform"
SUPPORTED_RESULT_SOURCES = (RESULT_SOURCE_SALLA, RESULT_SOURCE_PLATFORM)
MAX_ROWS = 100_000
DEFAULT_USD_TO_SAR = 3.7544


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    """Legacy display/search normalizer; never used for attribution joins."""
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _sum(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_number(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return round(sum(present), 6) if present else None


def _metric_sum(rows: list[dict[str, Any]], key: str) -> float | None:
    present: list[float] = []
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = _number(metrics.get(key))
        if value is not None:
            present.append(value)
    return round(sum(present), 6) if present else None


def _ratio(numerator: float | None, denominator: float | None, multiplier: float = 1.0) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round((numerator / denominator) * multiplier, 6)


def _source_is_snapchat(order: dict[str, Any]) -> bool:
    """Legacy source classifier retained for non-attribution consumers."""
    return canonical_ad_platform(order) == "snapchat"


def _unique_lookup(rows: list[dict[str, Any]], field: str) -> dict[str, tuple[str, str] | None]:
    """Build a literal identity lookup.

    Campaign attribution is an identifier join, not a search feature.  Case,
    underscores and whitespace are therefore significant and may not be
    normalized into a match.
    """
    grouped: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        value = row.get(field)
        if not isinstance(value, str) or not value:
            continue
        key = (_text(row.get("account_id")), _text(row.get("campaign_id")))
        if value and all(key):
            grouped[value].add(key)
    return {
        value: next(iter(keys)) if len(keys) == 1 else None
        for value, keys in grouped.items()
    }


def _match_order_campaign(
    order: dict[str, Any],
    *,
    id_lookup: dict[str, tuple[str, str] | None],
    name_lookup: dict[str, tuple[str, str] | None],
) -> tuple[tuple[str, str] | None, str]:
    # SNAP-REPORT-1 deliberately recognizes only the literal Salla UTM
    # Campaign ID.  Names, promoted aliases and normalized strings are not
    # authoritative identifiers and can silently attribute an order to the
    # wrong Snapchat campaign.
    del name_lookup
    candidates: list[str] = []
    for container in attribution_containers(order):
        candidate = container.get("utm_campaign_id")
        if isinstance(candidate, str) and candidate and candidate not in candidates:
            candidates.append(candidate)
    for literal in candidates:
        if literal in id_lookup:
            key = id_lookup[literal]
            return (key, "campaign_id") if key else (None, "ambiguous_id")
    return None, "unmatched"


async def _to_list(cursor: Any, length: int = MAX_ROWS) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _campaign_native_rows(
    db: Any,
    user_id: str,
    *,
    account_ids: list[str],
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    cursor = _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": account_ids},
            "entity_type": "campaign",
            "date": {"$gte": date_from, "$lte": date_to},
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "campaign_id": 1,
            "external_id": 1,
            "date": 1,
            "currency": 1,
            "spend_native": 1,
            "spend_sar": 1,
            "purchase_value_native": 1,
            "purchase_value_sar": 1,
            "purchases": 1,
            "metrics": 1,
        },
    )
    return await _to_list(cursor)


async def _campaign_identities(
    db: Any,
    user_id: str,
    *,
    account_ids: list[str],
    performance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": account_ids},
            "entity_type": "campaign",
        },
        {
            "_id": 0,
            "ad_account_id": 1,
            "external_id": 1,
            "display_name": 1,
        },
    )
    entities = await _to_list(entity_cursor, 50_000)
    names = {
        (_text(row.get("ad_account_id")), _text(row.get("external_id"))): _text(row.get("display_name"))
        for row in entities
        if _text(row.get("ad_account_id")) and _text(row.get("external_id"))
    }
    keys = {
        (
            _text(row.get("ad_account_id")),
            _text(row.get("campaign_id") or row.get("external_id")),
        )
        for row in performance_rows
        if _text(row.get("ad_account_id"))
        and _text(row.get("campaign_id") or row.get("external_id"))
    }
    return [
        {
            "account_id": account_id,
            "campaign_id": campaign_id,
            "campaign_name": names.get((account_id, campaign_id)) or campaign_id,
        }
        for account_id, campaign_id in sorted(keys)
    ]


async def _salla_outcomes(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    identities: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    from dashboard_v2_routes import _filtered_orders

    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    by_campaign: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    by_account: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    by_date: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    matched_by_id = 0
    matched_by_name = 0
    ambiguous = 0
    unmatched_snapchat = 0
    for order in orders:
        key, match_kind = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            if match_kind.startswith("ambiguous"):
                ambiguous += 1
            elif _source_is_snapchat(order):
                unmatched_snapchat += 1
            continue
        if match_kind == "campaign_id":
            matched_by_id += 1
        elif match_kind == "campaign_name":
            matched_by_name += 1
        amount = _number(order.get("total_amount") or order.get("total")) or 0.0
        order_date = _text(order.get("order_date"))[:10]
        by_campaign[key]["orders"] += 1
        by_campaign[key]["sales_sar"] += amount
        by_account[key[0]]["orders"] += 1
        by_account[key[0]]["sales_sar"] += amount
        if order_date:
            by_date[order_date]["orders"] += 1
            by_date[order_date]["sales_sar"] += amount

    for container in (by_campaign, by_account, by_date):
        for value in container.values():
            value["sales_sar"] = round(float(value["sales_sar"]), 2)
    coverage = {
        "eligible_salla_orders": len(orders),
        "matched_orders": matched_by_id + matched_by_name,
        "matched_by_campaign_id": matched_by_id,
        "matched_by_campaign_name": matched_by_name,
        "ambiguous_orders": ambiguous,
        "unattributed_snapchat_orders": unmatched_snapchat,
        "provider_conversion_sales_excluded": True,
    }
    return dict(by_campaign), dict(by_account), dict(by_date), coverage


def _native_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    spend_native = _sum(rows, "spend_native")
    spend_sar = _sum(rows, "spend_sar")
    sales_native = _sum(rows, "purchase_value_native")
    sales_sar = _sum(rows, "purchase_value_sar")
    purchases = _sum(rows, "purchases")
    impressions = _metric_sum(rows, "impressions")
    swipes = _metric_sum(rows, "swipes")
    return {
        "spend_native": spend_native,
        "spend_sar": spend_sar,
        "sales_native": sales_native,
        "sales_sar": sales_sar,
        "orders": int(round(purchases)) if purchases is not None else None,
        "cpa_native": _ratio(spend_native, purchases),
        "cpc_native": _ratio(spend_native, swipes),
        "cpm_native": _ratio(spend_native, impressions, 1000.0),
    }


def _effective_currency(account: dict[str, Any], setting: dict[str, Any] | None) -> tuple[str, float]:
    currency = _text((setting or {}).get("native_currency") or account.get("currency")).upper()
    if currency not in {"SAR", "USD"}:
        currency = "SAR"
    default_rate = 1.0 if currency == "SAR" else DEFAULT_USD_TO_SAR
    rate = _number((setting or {}).get("exchange_rate_to_sar")) or default_rate
    return currency, 1.0 if currency == "SAR" else rate


def _selected_metrics(
    *,
    result_source: str,
    platform: dict[str, Any],
    salla: dict[str, Any],
    spend_sar: float | None,
    spend_native: float | None,
    rate: float,
) -> dict[str, Any]:
    outcomes = salla if result_source == RESULT_SOURCE_SALLA else platform
    orders = int(outcomes.get("orders") or 0)
    sales_sar = _number(outcomes.get("sales_sar")) or 0.0
    sales_native = (
        round(sales_sar / rate, 6)
        if result_source == RESULT_SOURCE_SALLA and rate > 0
        else _number(outcomes.get("sales_native"))
    )
    return {
        "orders": orders,
        "sales_sar": round(sales_sar, 2),
        "sales_native": sales_native,
        "roas": _ratio(sales_sar, spend_sar),
        "cpa_sar": _ratio(spend_sar, orders),
        "cpa_native": _ratio(spend_native, orders),
    }


async def build_snapchat_result_source_report(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    campaign_query: str | None,
    page: int,
    limit: int,
    result_source: str,
) -> dict[str, Any]:
    payload = await read_snapchat_campaign_report(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
        campaign_query=campaign_query,
        page=page,
        limit=limit,
    )
    date_from = _text(payload.get("date_from"))
    date_to = _text(payload.get("date_to"))
    selected_accounts = await _load_selected_accounts(db, user_id)
    account_ids = [_text(row.get("ad_account_id")) for row in selected_accounts if _text(row.get("ad_account_id"))]
    account_by_id = {_text(row.get("ad_account_id")): row for row in selected_accounts}
    performance_rows = await _campaign_native_rows(
        db,
        user_id,
        account_ids=account_ids,
        date_from=date_from,
        date_to=date_to,
    )
    identities = await _campaign_identities(
        db,
        user_id,
        account_ids=account_ids,
        performance_rows=performance_rows,
    )
    salla_by_campaign, salla_by_account, salla_by_date, coverage = await _salla_outcomes(
        db,
        user_id,
        date_from=date_from,
        date_to=date_to,
        identities=identities,
    )
    from ads_manager.account_cost_settings import list_account_cost_settings

    settings_payload = await list_account_cost_settings(db, user_id)
    setting_by_account = {
        _text(item.get("external_account_id")): item
        for item in settings_payload.get("items") or []
    }
    native_by_campaign: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in performance_rows:
        key = (
            _text(row.get("ad_account_id")),
            _text(row.get("campaign_id") or row.get("external_id")),
        )
        if all(key):
            native_by_campaign[key].append(row)

    campaigns = []
    for campaign in payload.get("campaigns") or []:
        key = (_text(campaign.get("account_id")), _text(campaign.get("campaign_id")))
        native = _native_summary(native_by_campaign.get(key, []))
        account = account_by_id.get(key[0], {})
        currency, rate = _effective_currency(account, setting_by_account.get(key[0]))
        spend_sar = _number(campaign.get("spend_sar"))
        spend_native = _number(native.get("spend_native"))
        if spend_native is None and spend_sar is not None and rate > 0:
            spend_native = round(spend_sar / rate, 6)
        platform = {
            "orders": campaign.get("orders"),
            "sales_sar": campaign.get("sales_sar"),
            "sales_native": native.get("sales_native"),
        }
        salla = salla_by_campaign.get(key, {"orders": 0, "sales_sar": 0.0})
        selected = _selected_metrics(
            result_source=result_source,
            platform=platform,
            salla=salla,
            spend_sar=spend_sar,
            spend_native=spend_native,
            rate=rate,
        )
        campaigns.append({
            **campaign,
            **selected,
            "result_source": result_source,
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "spend_native": spend_native,
            "cpc_native": native.get("cpc_native"),
            "cpm_native": native.get("cpm_native"),
            "platform_results": platform,
            "salla_results": salla,
        })
    payload["campaigns"] = campaigns

    for account_row in payload.get("accounts") or []:
        account_id = _text(account_row.get("account_id"))
        account = account_by_id.get(account_id, {})
        currency, rate = _effective_currency(account, setting_by_account.get(account_id))
        native_rows = [row for row in performance_rows if _text(row.get("ad_account_id")) == account_id]
        native = _native_summary(native_rows)
        platform = {
            "orders": account_row.get("orders"),
            "sales_sar": account_row.get("sales_sar"),
            "sales_native": native.get("sales_native"),
        }
        salla = salla_by_account.get(account_id, {"orders": 0, "sales_sar": 0.0})
        selected = _selected_metrics(
            result_source=result_source,
            platform=platform,
            salla=salla,
            spend_sar=_number(account_row.get("spend_sar")),
            spend_native=_number(native.get("spend_native")),
            rate=rate,
        )
        account_row.update({
            **selected,
            "result_source": result_source,
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "spend_native": native.get("spend_native"),
        })

    for daily_row in payload.get("daily") or []:
        date_value = _text(daily_row.get("date"))
        if result_source == RESULT_SOURCE_SALLA:
            outcomes = salla_by_date.get(date_value, {"orders": 0, "sales_sar": 0.0})
            orders = int(outcomes.get("orders") or 0)
            sales_sar = _number(outcomes.get("sales_sar")) or 0.0
            spend_sar = _number(daily_row.get("spend_sar"))
            daily_row.update({
                "orders": orders,
                "sales_sar": round(sales_sar, 2),
                "roas": _ratio(sales_sar, spend_sar),
                "cpa_sar": _ratio(spend_sar, orders),
                "result_source": result_source,
            })

    if result_source == RESULT_SOURCE_SALLA:
        total_orders = sum(int(value.get("orders") or 0) for value in salla_by_campaign.values())
        total_sales = round(sum(float(value.get("sales_sar") or 0) for value in salla_by_campaign.values()), 2)
        spend_sar = _number((payload.get("totals") or {}).get("spend_sar"))
        payload["totals"].update({
            "orders": total_orders,
            "sales_sar": total_sales,
            "roas": _ratio(total_sales, spend_sar),
            "cpa_sar": _ratio(spend_sar, total_orders),
        })
    payload["totals"]["result_source"] = result_source
    payload["result_source"] = result_source
    payload["supported_result_sources"] = list(SUPPORTED_RESULT_SOURCES)
    payload.setdefault("source", {}).update({
        "result_source": result_source,
        "spend_source": "snapchat_native_selected_accounts",
        "commercial_results_source": (
            "unified_orders:salla_financially_included"
            if result_source == RESULT_SOURCE_SALLA
            else "snapchat_conversion_reporting"
        ),
        "salla_attribution": coverage,
    })
    payload.setdefault("policy", {}).update({
        "source_selection_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    })
    return payload


def attach_snapchat_campaign_result_source_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/campaign-report",
        name="get_snapchat_campaign_report",
    )
    async def campaign_report(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        result_source: str = Query(default=RESULT_SOURCE_SALLA, pattern="^(salla|platform)$"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_snapchat_result_source_report(
                db,
                str(owner["id"]),
                from_date=from_date,
                to_date=to_date,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
                result_source=result_source,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "provider_write_reached": False,
                    "campaign_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "RESULT_SOURCE_PLATFORM",
    "RESULT_SOURCE_SALLA",
    "SUPPORTED_RESULT_SOURCES",
    "attach_snapchat_campaign_result_source_routes",
    "build_snapchat_result_source_report",
]
