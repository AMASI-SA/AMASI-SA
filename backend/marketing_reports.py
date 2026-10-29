"""Read-only reporting workspace. Snapchat uses the V2 account page's readers.

Provider conversions and Salla orders are separate observations. Only exact
commerce matches can support product/cost/profit detail. No provider counts
are distributed across Salla products.
"""
import asyncio
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from marketing_report_costs import number, order_costs, add_order_report_costs, finalize_report_costs

PROVIDERS = {"snapchat": "سناب شات", "tiktok": "تيك توك", "meta": "ميتا", "google": "إعلانات Google"}


def strict_sum(values):
    values = list(values)
    return round(sum(values), 2) if values and all(v is not None for v in values) else None


def selected_metrics(row, source):
    selected = row.get(source) or {}
    profit = row.get("profitability") or {}
    spend = number(row.get("spend_sar"))
    sales = number(selected.get("sales_sar"))
    orders = number(selected.get("orders"))
    # The provider aggregate does not identify purchased SKUs or their costs.
    cost_fields = ("product_cost_sar", "shipping_cost_sar", "payment_fees_sar", "net_profit_sar")
    costs = {k: number(profit.get(k)) if source == "salla" else None for k in cost_fields}
    return {"orders": orders, "sales_sar": sales, "spend_sar": spend, **costs,
            "roas": sales / spend if sales is not None and spend and spend > 0 else None,
            "cpa_sar": spend / orders if spend is not None and orders and orders > 0 else None,
            "net_margin_pct": costs["net_profit_sar"] / sales * 100 if costs["net_profit_sar"] is not None and sales and sales > 0 else None}


def summarize(rows, source):
    values = [selected_metrics(row, source) for row in rows]
    output = {key: strict_sum(v[key] for v in values) for key in
              ("orders", "sales_sar", "spend_sar", "product_cost_sar", "shipping_cost_sar", "payment_fees_sar", "net_profit_sar")}
    sales, spend, orders, net = (output[k] for k in ("sales_sar", "spend_sar", "orders", "net_profit_sar"))
    output.update(roas=sales / spend if sales is not None and spend else None,
                  cpa_sar=spend / orders if spend is not None and orders else None,
                  net_margin_pct=net / sales * 100 if net is not None and sales else None)
    return output


def summarize_totals(totals):
    rows = [{"spend_sar": t.get("spend_sar"), "salla": t, "profitability": t} for t in totals]
    return summarize(rows, "salla")


async def snapchat_report(db, user_id, start, end, account_id=None):
    from snapchat_v2.accounts import list_enabled_accounts, resolve_account
    from snapchat_v2.routes import _entity_performance_report, _add_sar_spend
    from snapchat_v2.salla_outcomes import load_salla_campaign_outcomes
    from integrations_control_center.snapchat_campaign_profitability import _new_campaign_bucket, _finalize_campaign

    available = await list_enabled_accounts(db, user_id)
    if account_id == "all":
        accounts = available
    else:
        account = await resolve_account(db, user_id, account_id)
        accounts = [account] if account else []
    if account_id and account_id != "all" and not accounts:
        raise HTTPException(404, "الحساب غير مرتبط")
    rows, totals, observed = [], [], []
    for account in accounts:
        aid = str(account["ad_account_id"])
        zone = account.get("timezone")
        if not zone:
            raise ValueError("account_timezone_unavailable")
        performance = await _entity_performance_report(db, user_id=user_id, account=account,
            date_from=start, date_to=end, timezone_name=zone, action_report_time="conversion",
            entity_type="campaign", include_stale=True)
        native_rows = performance["rows"]
        native_total = dict(performance["totals"])
        await _add_sar_spend(db, user_id=user_id, account=account, rows=native_rows, totals=native_total)
        commerce = await load_salla_campaign_outcomes(db, user_id, account_id=aid,
            date_from=start, date_to=end, timezone_name=zone,
            identities=[{"account_id": aid, "campaign_id": r["campaign_id"], "campaign_name": r["campaign_name"]} for r in native_rows],
            platform_purchases=int(native_total.get("purchases") or 0),
            campaign_spend_sar={r["campaign_id"]: r.get("spend_sar") for r in native_rows},
            include_report_costs=True)
        currency = account.get("currency")
        for item in native_rows:
            salla = commerce["by_campaign"].get(item["campaign_id"], {"orders": 0, "sales_sar": 0})
            spend = number(item.get("spend_sar"))
            profit = salla.get("profitability")
            if profit is None and not salla.get("orders"):
                raw = _new_campaign_bucket()
                profit = finalize_report_costs(_finalize_campaign(raw, spend_sar=spend or 0), raw, spend)
            # Use the source's exact native money/ROAS to derive the SAR value;
            # the existing account cost policy owns FX, never a hardcoded rate.
            native_spend = number(item.get("spend_native"))
            native_sales = number(item.get("purchase_value_native"))
            fx = number(item.get("exchange_rate_to_sar"))
            if not item.get("source_fact_count") and not salla.get("orders") and str(item.get("status") or "").upper() != "ACTIVE":
                continue
            rows.append({"id": f"snapchat:{aid}:{item['campaign_id']}", "provider": "snapchat",
                "campaign_id": item["campaign_id"], "name": item["campaign_name"], "account_id": aid,
                "account_name": account.get("display_name") or aid, "timezone": zone,
                "status": item.get("status"), "active": str(item.get("status") or "").upper() == "ACTIVE",
                "spend_sar": spend, "spend_native": native_spend, "currency": currency,
                "platform": {"orders": number(item.get("purchases")), "sales_sar": round(native_sales * fx, 2) if native_sales is not None and fx is not None else None,
                             "sales_native": native_sales},
                "salla": {"orders": salla.get("orders"), "sales_sar": salla.get("sales_sar")},
                "profitability": profit, "impressions": item.get("impressions"), "clicks": item.get("swipes")})
        totals.append({"account_id": aid, "purchases": native_total.get("purchases"),
                       "spend_native": native_total.get("spend_native"), "spend_sar": native_total.get("spend_sar"), "currency": currency,
                       "sales_sar": round(native_total["purchase_value_native"] * native_total["exchange_rate_to_sar"], 2) if native_total.get("purchase_value_native") is not None and native_total.get("exchange_rate_to_sar") else None,
                       "salla_orders": commerce["summary"].get("snapchat_attributed_orders"),
                       "unmatched_salla_orders": commerce["summary"].get("snapchat_attribution_gap_orders"),
                       "performance_sync_status": performance.get("performance_sync_status")})
        if account.get("last_sync_at"):
            observed.append(str(account["last_sync_at"]))
    return {"provider": "snapchat", "rows": rows, "accounts": [
        {"id": str(a["ad_account_id"]), "name": a.get("display_name"), "currency": a.get("currency"), "timezone": a.get("timezone")} for a in available],
        "selected_account_id": account_id or (str(accounts[0]["ad_account_id"]) if accounts else None),
        "source": "snapchat_v2_account_campaign_readers", "source_totals": totals,
        "last_sync_at": max(observed) if observed else None,
        "status": "available" if accounts else "not_connected"}


async def other_report(db, user_id, provider, start, end):
    from ads_manager.google_native_overview import GoogleAdsManagerService
    data = await GoogleAdsManagerService(db).overview(user_id, date_from=start.isoformat(),
        date_to=end.isoformat(), provider=provider, page=1, limit=10000)
    campaigns = data.get("campaigns") or []
    if (data.get("campaign_pagination") or {}).get("total", 0) > len(campaigns):
        raise ValueError("campaign_report_truncated")
    rows = [{"id": f"{provider}:{r.get('account_id')}:{r['campaign_id']}", "provider": provider,
        "campaign_id": r["campaign_id"], "name": r["campaign_name"], "account_id": r.get("account_id"),
        "account_name": r.get("account_id"), "timezone": "Asia/Riyadh", "status": r.get("status"),
        "active": str(r.get("status") or "").upper() in {"ACTIVE", "ENABLED", "ENABLE"},
        "spend_sar": number(r.get("spend_sar_equivalent")), "spend_native": number(r.get("spend_reported")),
        "currency": r.get("spend_currency"), "platform": {"orders": number(r.get("purchases")),
        "sales_sar": number(r.get("revenue_sar_equivalent"))}, "salla": {}, "profitability": None,
        "impressions": r.get("impressions"), "clicks": r.get("clicks")} for r in campaigns]
    await attach_commerce(db, user_id, provider, start, end, rows)
    summary = next((p for p in data.get("providers", []) if p.get("provider") == provider), {})
    return {"provider": provider, "rows": rows, "accounts": [], "source": "ads_manager_provider_readers",
            "last_sync_at": summary.get("last_sync_at"), "status": "available" if rows else "unavailable",
            "source_totals": summary.get("metrics") or {}, "coverage": summary.get("performance_coverage") or {}}


async def attach_commerce(db, user_id, provider, start, end, rows):
    """Exact IDs, or a unique name with explicit same-platform evidence."""
    from snapchat_v2.salla_outcomes import ORDER_PROJECTION, REPORT_COST_PROJECTION, _localized_order_period_date, _matches_any
    from snapchat_v2.period_candidates import period_candidate_query, bounded_period_cursor
    from salla_marketing_attribution import canonical_ad_platform, campaign_id_candidates, campaign_name_candidates
    from integrations_control_center.snapchat_campaign_profitability import (
        _load_cost_context, _new_campaign_bucket, _order_cost_and_products, _add_order_to_campaign, _finalize_campaign)
    from shipping_cost_ssot import get_company_configs
    from order_currency import order_total_sar
    if not rows:
        return
    settings = await db.settings.find_one({"user_id": user_id}, {"_id": 0}) or {}
    context = await _load_cost_context(db, user_id)
    configs = await get_company_configs(db, user_id)
    lookup, names = defaultdict(list), defaultdict(list)
    for row in rows:
        lookup[str(row["campaign_id"])].append(row)
        names[str(row["name"]).strip().casefold()].append(row)
    query = period_candidate_query(user_id, start, end, hide_inferred=settings.get("hide_inferred_date_orders"))
    orders = await bounded_period_cursor(db.unified_orders, query, {**ORDER_PROJECTION, **REPORT_COST_PROJECTION}, 100000).to_list(length=100001)
    if len(orders) > 100000:
        raise ValueError("commerce_report_truncated")
    buckets, counts = defaultdict(_new_campaign_bucket), defaultdict(int)
    for order in orders:
        platform = canonical_ad_platform(order)
        if platform and platform != provider:
            continue
        candidates = {r["id"]: r for value in campaign_id_candidates(order) for r in lookup.get(str(value), [])}
        if not candidates and platform == provider:
            candidates = {r["id"]: r for value in campaign_name_candidates(order) for r in names.get(str(value).strip().casefold(), [])}
        if len(candidates) != 1:
            continue
        row = next(iter(candidates.values()))
        day, _, _ = _localized_order_period_date(order, zone=ZoneInfo(row["timezone"]))
        if not day or not start.isoformat() <= day <= end.isoformat():
            continue
        counts[row["id"]] += 1
        if not _matches_any(order.get("order_status"), settings.get("report_included_statuses") or []):
            continue
        amount = order_total_sar(order)
        if amount is None:
            buckets[row["id"]]["unknown_currency"] = True
        result = _order_cost_and_products({**order, "total_amount": amount or 0}, context)
        bucket = buckets[row["id"]]
        _add_order_to_campaign(bucket, result)
        add_order_report_costs(bucket, result, order_costs(order, settings, configs))
    for row in rows:
        raw = buckets[row["id"]]
        result = finalize_report_costs(_finalize_campaign(raw, spend_sar=row["spend_sar"] or 0), raw, row["spend_sar"])
        if raw.get("unknown_currency"):
            result.update(sales_sar=None, net_profit_sar=None, net_margin_pct=None, net_profit_available=False)
        row["profitability"] = result
        row["salla"] = {"orders": counts[row["id"]], "sales_sar": result["sales_sar"]}


def make_marketing_reports_router(db, current_user):
    router = APIRouter(prefix="/marketing-reports", tags=["marketing-reports"])

    @router.get("")
    async def report(provider: Literal["all", "snapchat", "tiktok", "meta", "google"] = "snapchat",
        source: Literal["salla", "platform"] = "platform",
        date_from: date | None = None, date_to: date | None = None,
        account_id: str | None = Query(default=None, max_length=128), user=Depends(current_user)):
        from integrations_control_center.routes import _require_owner as require_owner
        owner = require_owner(user)
        user_id = str(owner["id"])
        zone = "Asia/Riyadh"
        if provider == "snapchat" and account_id != "all":
            from snapchat_v2.accounts import resolve_account
            account = await resolve_account(db, user_id, account_id)
            zone = (account or {}).get("timezone") or zone
        today = datetime.now(ZoneInfo(zone)).date()
        start, end = date_from or today, date_to or date_from or today
        if end < start or (end - start).days >= 62 or end > today:
            raise HTTPException(422, "اختر فترة صحيحة لا تتجاوز 62 يومًا ولا تشمل المستقبل")
        async def read(p):
            try:
                async with asyncio.timeout(45):
                    return await (snapchat_report(db, user_id, start, end, "all" if provider == "all" else account_id)
                        if p == "snapchat" else other_report(db, user_id, p, start, end))
            except HTTPException:
                raise
            except Exception as exc:
                return {"provider": p, "rows": [], "status": "unavailable", "reason": type(exc).__name__}
        reports = await asyncio.gather(*(read(p) for p in (PROVIDERS if provider == "all" else [provider])))
        for item in reports:
            item["label"] = PROVIDERS[item["provider"]]
            for row in item["rows"]:
                row["metrics"] = selected_metrics(row, source)
            item["totals"] = summarize(item["rows"], source)
            if item["provider"] == "snapchat" and item.get("source_totals"):
                total = item["totals"]
                total["spend_sar"] = strict_sum(number(t.get("spend_sar")) for t in item["source_totals"])
                if source == "platform":
                    total["orders"] = strict_sum(number(t.get("purchases")) for t in item["source_totals"])
                    total["sales_sar"] = strict_sum(number(t.get("sales_sar")) for t in item["source_totals"])
                total["roas"] = total["sales_sar"] / total["spend_sar"] if total["sales_sar"] is not None and total["spend_sar"] else None
                total["cpa_sar"] = total["spend_sar"] / total["orders"] if total["spend_sar"] is not None and total["orders"] else None
        return {"provider": provider, "source": source, "currency": "SAR",
                "range": {"date_from": start.isoformat(), "date_to": end.isoformat(), "timezone": zone},
                "reports": reports, "generated_at": datetime.now(timezone.utc).isoformat(),
                "totals": summarize_totals([p["totals"] for p in reports]),
                "complete": all(p["status"] == "available" for p in reports),
                "profit_scope": "after_product_advertising_shipping_payment_before_operating_expenses"}
    return router
