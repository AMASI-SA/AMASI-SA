"""Partition the synced Salla order universe once, independently of ad coverage.

Store comparisons use one Riyadh calendar and one status scope. They never
deduce direct traffic from the absence of a campaign, or add provider-reported
conversions to real orders. No order/customer payload is returned to the UI.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from salla_marketing_attribution import (
    canonical_ad_platform, order_source_candidates, campaign_id_candidates,
    campaign_name_candidates, is_salla_clickid_campaign_placeholder,
)

EXTRA_SOURCES = {"direct": "طلبات مباشرة", "unclassified": "غير مصنّفة لمنصة"}
AD_SOURCES = {"snapchat", "tiktok", "meta", "google"}
STORE_ZONE = "Asia/Riyadh"
MAX_ORDERS = 100_000


def _raw(order):
    return (order.get("raw_by_source") or {}).get("salla_direct") or {}


def classify_source(order):
    # Fresh explicit provider evidence wins over older normalized fields.
    platform = canonical_ad_platform(_raw(order)) or canonical_ad_platform(order)
    if platform:
        return platform
    explicit_direct = {"direct", "direct visit", "(direct)", "زيارة مباشرة", "زياره مباشره", "مباشر"}
    candidates = [" ".join(str(v).casefold().replace("_", " ").split()) for v in order_source_candidates(order)]
    # `store`, `website`, and `salla_direct` identify an ingestion/storefront
    # channel; none proves that the customer arrived directly.
    ingestion = {"store", "website", "salla direct", "salla", "make", "excel", "custom app", "المتجر"}
    meaningful = {v for v in candidates if v and v not in ingestion}
    return "direct" if meaningful and meaningful <= explicit_direct else "unclassified"


def unique_orders(orders):
    """Deduplicate identity aliases before date/status/source classification."""
    from snapchat_v2.salla_outcomes import _parse_datetime
    parent = list(range(len(orders)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    aliases = {}
    for i, order in enumerate(orders):
        keys = []
        if order.get("order_number") not in (None, ""):
            keys.append(("number", str(order["order_number"])))
        native_id = _raw(order).get("id") or order.get("salla_order_id")
        if native_id not in (None, ""):
            keys.append(("salla", str(native_id)))
        if not keys:
            raise ValueError("salla_order_identity_missing")
        for key in keys:
            if key in aliases:
                parent[root(i)] = root(aliases[key])
            aliases[key] = i
    chosen = {}
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    def version(order):
        return _parse_datetime(order.get("updated_at")) or minimum
    for i, order in enumerate(orders):
        key = root(i)
        if key not in chosen or version(order) > version(chosen[key]):
            chosen[key] = order
    return list(chosen.values())


def _cents(amount):
    return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _store_order_day(order, zone):
    from snapchat_v2.salla_outcomes import _localized_order_period_date
    # The merchant's Salla order register uses this explicitly provider-owned
    # calendar field. An advertising-account timestamp or refreshed raw
    # snapshot must not move those orders to another store reporting day.
    sources = order.get("field_sources") or {}
    day = order.get("order_date")
    if sources.get("order_date") == "salla_direct" and not order.get("order_date_inferred"):
        try:
            if isinstance(day, str) and date.fromisoformat(day).isoformat() == day:
                return day
        except ValueError:
            pass
    return _localized_order_period_date(order, zone=zone)[0]


def _residual(provider):
    return {"id": f"{provider}:store-unassigned", "provider": provider,
        "name": EXTRA_SOURCES.get(provider, "طلبات المنصة بدون حملة محددة"),
        "row_kind": "order_source", "campaign_id": None, "account_id": None,
        "status": "available", "active": True, "timezone": STORE_ZONE,
        "spend_sar": 0.0, "salla": {}, "platform": {}, "profitability": None}


async def apply_store_orders(db, user_id, start, end, reports, *, status_scope="all", account_id=None):
    from snapchat_v2.salla_outcomes import ORDER_PROJECTION, REPORT_COST_PROJECTION, _matches_any
    from snapchat_v2.period_candidates import bounded_period_cursor, period_candidate_query
    from integrations_control_center.snapchat_campaign_profitability import (
        _load_cost_context, _new_campaign_bucket, _order_cost_and_products, _add_order_to_campaign, _finalize_campaign)
    from shipping_cost_ssot import get_company_configs
    from order_currency import order_total_sar
    from marketing_report_costs import order_costs, add_order_report_costs, finalize_report_costs

    settings = await db.settings.find_one({"user_id": str(user_id)}, {"_id": 0}) or {}
    statuses = (settings.get("report_included_statuses") or []) if status_scope == "report" else []
    query = period_candidate_query(user_id, start, end)
    query["$and"] = [{"$or": [{"raw_by_source.salla_direct": {"$exists": True}},
        {"source": {"$in": ["salla", "salla_direct"]}}, {"salla_order_id": {"$exists": True}}]}]
    projection = {**ORDER_PROJECTION, **REPORT_COST_PROJECTION,
        "salla_order_id": 1, "raw_by_source.salla_direct.id": 1,
        "field_sources.order_date": 1,
        "utm_source": 1, "marketing_source": 1, "ad_platform_source": 1,
        "traffic_source": 1, "source_details": 1}
    orders = await bounded_period_cursor(db.unified_orders, query, projection, MAX_ORDERS).to_list(length=MAX_ORDERS + 1)
    if len(orders) > MAX_ORDERS:
        raise ValueError("salla_order_report_truncated")
    count_before = len(orders)
    orders = unique_orders(orders)
    duplicate_count = count_before - len(orders)
    context = await _load_cost_context(db, user_id)
    configs = await get_company_configs(db, user_id)
    report_by_provider = {p["provider"]: p for p in reports}
    residuals, ids, names = {}, defaultdict(list), defaultdict(list)
    for report in reports:
        provider = report["provider"]
        for row in report["rows"]:
            ids[(provider, str(row.get("campaign_id")))].append(row)
            names[(provider, str(row.get("name") or "").strip().casefold())].append(row)
        residuals[provider] = _residual(provider)
        report["rows"].append(residuals[provider])
    buckets = defaultdict(_new_campaign_bucket)
    sales_cents = defaultdict(int)
    universe_count, universe_cents, unknown_currency = 0, 0, 0
    assigned_count, assigned_cents, outside_selection = 0, 0, 0
    zone = ZoneInfo(STORE_ZONE)
    for order in orders:
        day = _store_order_day(order, zone)
        if not day or not start.isoformat() <= day <= end.isoformat():
            continue
        if statuses and (not order.get("order_status") or not _matches_any(order["order_status"], statuses)):
            continue
        amount = order_total_sar(order)
        cents = _cents(amount) if amount is not None else None
        universe_count += 1
        if cents is None:
            unknown_currency += 1
        else:
            universe_cents += cents
        provider = classify_source(order)
        if provider not in report_by_provider:
            outside_selection += 1
            continue
        raw = _raw(order)
        raw_candidates = campaign_id_candidates(raw) + campaign_name_candidates(raw)
        evidence = raw if any(not is_salla_clickid_campaign_placeholder(v) for v in raw_candidates) else order
        candidates = {r["id"]: r for value in campaign_id_candidates(evidence)
            if not is_salla_clickid_campaign_placeholder(value) for r in ids.get((provider, str(value)), [])}
        if not candidates:
            candidates = {r["id"]: r for value in campaign_name_candidates(evidence)
                if not is_salla_clickid_campaign_placeholder(value) for r in names.get((provider, str(value).strip().casefold()), [])}
        row = next(iter(candidates.values())) if len(candidates) == 1 else residuals[provider]
        if account_id and account_id != "all" and row.get("account_id") != account_id:
            outside_selection += 1
            continue
        bucket = buckets[row["id"]]
        # Preserve a legitimate zero instead of falling back to legacy total.
        priced = {**order, "total_amount": (cents or 0) / 100, "total": (cents or 0) / 100}
        result = _order_cost_and_products(priced, context)
        _add_order_to_campaign(bucket, result)
        add_order_report_costs(bucket, result, order_costs(order, settings, configs))
        assigned_count += 1
        if cents is None:
            bucket["unknown_currency"] = True
        else:
            sales_cents[row["id"]] += cents
            assigned_cents += cents
    for report in reports:
        for row in report["rows"]:
            bucket = buckets[row["id"]]
            bucket["sales_sar"] = sales_cents[row["id"]] / 100
            result = finalize_report_costs(_finalize_campaign(bucket, spend_sar=row.get("spend_sar") or 0), bucket, row.get("spend_sar"))
            if bucket.get("unknown_currency"):
                result.update(sales_sar=None, net_profit_sar=None, net_margin_pct=None, net_profit_available=False)
                for product in result["products"]:
                    product.update(sales_sar=None, allocated_ad_spend_sar=None, net_profit_sar=None, net_margin_pct=None, net_profit_available=False)
            row.update(profitability=result, salla={"orders": bucket["orders"], "sales_sar": result["sales_sar"]})
        report["commerce_available"] = True
    all_sources = set(report_by_provider) == AD_SOURCES | set(EXTRA_SOURCES)
    full_scope = all_sources and not account_id
    return {"reference": "synced_salla_orders", "timezone": STORE_ZONE,
        "status_scope": status_scope, "included_statuses": statuses,
        "orders": universe_count, "sales_sar": None if unknown_currency else universe_cents / 100,
        "assigned_orders": assigned_count, "outside_selection": outside_selection,
        "duplicates_removed": duplicate_count, "unknown_currency_orders": unknown_currency,
        "order_delta": universe_count - assigned_count if full_scope else None,
        "sales_delta_sar": (universe_cents - assigned_cents) / 100 if full_scope and not unknown_currency else None,
        "scope": "store" if full_scope else "selection",
        "provider_live_comparison_verified": False}
