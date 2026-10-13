"""Extend shared Ads Manager using the same Google daily source as Dashboard.

Campaign facts remain account-local and separately identified. Only proven
Riyadh-day amounts join the shared SAR comparison; all conversion types are
not relabelled as purchase orders or Salla sales.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math

from .service import AdsManagerService
from integrations_control_center.google_ads_reporting import GOOGLE_ADS_DAILY_COLLECTION, GOOGLE_ADS_REPORTING_SOURCE_MODE
from integrations_control_center.google_ads_workspace import FACTS, FACTSETS


def amount(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        parsed = Decimal(str(value))
        return float(parsed) if parsed.is_finite() and parsed >= 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


async def augment_google_overview(db, user_id, result, *, provider="all", campaign_query=None, page=1, limit=50):
    if provider not in {"all", "google"}:
        return result
    start, end = result["range"]["date_from"], result["range"]["date_to"]
    requested_days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1
    accounts = await db.mezan_integration_accounts_v2.find({"user_id": user_id, "provider": "google_ads", "connection_status": "connected", "connection_provenance": "api_connection"}, {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "mezan_selected": 1}).to_list(length=101)
    selected = [row for row in accounts if row.get("mezan_selected") is True] or accounts
    ids = {str(row.get("ad_account_id") or row.get("external_account_id") or "") for row in selected}
    connection = await db.mezan_integrations_v2.find_one({"user_id": user_id, "provider": "google_ads", "connection_provenance": "api_connection"}, {"_id": 0, "connection_status": 1, "last_sync_at": 1}) or {}
    rows = await db[GOOGLE_ADS_DAILY_COLLECTION].find({"user_id": user_id, "provider": "google_ads", "ad_account_id": {"$in": sorted(ids)}, "date": {"$gte": start, "$lte": end}, "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE}, {"_id": 0}).to_list(length=10001)
    aligned = []
    for row in rows[:10000]:
        proof = row.get("source_close_proof") or {}
        if row.get("account_timezone") == "Asia/Riyadh" and proof.get("complete") is True and proof.get("account_id") == str(row.get("ad_account_id")) and proof.get("business_date") == row.get("date") and amount(row.get("spend_sar")) is not None:
            aligned.append(row)
    pairs = {(row["ad_account_id"], row["date"]) for row in aligned}
    complete = bool(ids) and len(accounts) <= 100 and len(rows) <= 10000 and len(pairs) == len(aligned) == len(ids) * requested_days
    daily = {}
    for row in aligned:
        daily[row["date"]] = daily.get(row["date"], 0) + amount(row["spend_sar"])
    spend = round(sum(daily.values()), 6) if complete else None
    latest = max((str(row.get("updated_at") or row.get("observed_at") or "") for row in rows), default=None)
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    sets = await db[FACTSETS].find({"user_id": user_id, "account_id": {"$in": sorted(ids)}, "date_from": start, "date_to": end, "complete": True, "observed_at": {"$gte": cutoff}}, {"_id": 0}).to_list(length=101)
    facts = [row for snapshot in sets[:100] for row in snapshot.get("campaigns", []) if row.get("account_id") == snapshot.get("account_id") and row.get("source") == "google_ads_api"]
    campaigns = []
    for row in facts[:2000]:
        native = row.get("metrics") or {}
        sar = amount(native.get("spend_native")) if row.get("currency_code") == "SAR" and row.get("time_zone") == "Asia/Riyadh" else None
        campaigns.append({"provider": "google", "provider_label": "Google Ads", "account_id": row["account_id"], "campaign_id": row["campaign_id"], "campaign_name": row.get("name") or row["campaign_id"], "status": row.get("status"), "delivery_status": row.get("effective_status"), "objective": row.get("campaign_type"), "budget": {"currency": row.get("currency_code"), "daily_native": amount(row.get("budget_native")) if row.get("budget_mode") == "DAILY" else None, "lifetime_native": amount(row.get("budget_native")) if row.get("budget_mode") == "TOTAL" else None},
            "spend_reported": amount(native.get("spend_native")), "spend_currency": row.get("currency_code"), "spend_sar_equivalent": sar, "revenue_reported": None, "revenue_sar_equivalent": None, "purchases": None, "impressions": int(native["impressions"]) if native.get("impressions") is not None else None, "clicks": int(native["clicks"]) if native.get("clicks") is not None else None, "roas": None, "last_observed_date": end, "data_source": FACTSETS, "currency_evidence": "google_ads_api_account_currency_and_timezone"})
    summary = {"provider": "google", "provider_label": "Google Ads", "integration_provider": "google_ads", "connection_status": connection.get("connection_status") or "not_connected", "connection_provenance": "api_connection", "health_status": "healthy" if complete else "unknown", "last_sync_at": connection.get("last_sync_at"), "metrics": {"provider_reported_spend_sar": spend},
        "freshness": {"last_observed_at": latest, "observed_days": len({row["date"] for row in aligned}), "requested_days": requested_days, "status": "unknown" if rows else "unavailable"},
        "performance_coverage": {"status": "complete" if complete else "partial" if rows else "unavailable", "eligible_for_ratios": False, "observed_days": len(daily), "requested_days": requested_days, "coverage_pct": len(daily) / requested_days * 100, "reasons": [] if complete else ["incomplete_spend"], "detail": "مصدر جوجل نفسه المستخدم في لوحة التحكم؛ يتطلب جمع الريال تطابق أيام الرياض وإثبات اكتمال الحسابات."},
        "campaign_coverage": {"status": "available" if campaigns else "aggregate_only" if rows else "unavailable", "campaign_count": len(campaigns), "source_rows": len(facts), "detail": "تفاصيل الحملات من قراءة جوجل الأصلية المحفوظة لنفس الفترة؛ حدّث صفحة جوجل لتحديثها."},
        "reconciliation": {"status": "not_comparable", "comparison_basis": "unavailable", "severity": "info", "detail": "بيانات منصة جوجل لا تثبت مبيعات سلة أو الربح المحاسبي."},
        "metric_availability": {"provider_spend": spend is not None, "provider_spend_period_complete": complete, "booked_expense": False, "revenue": False, "purchases": False, "impressions": False, "clicks": False, "campaigns": bool(campaigns)}}
    if provider == "google":
        result.update(providers=[], campaigns=[], metrics={}, insights=[])
        result["coverage"] = {"providers_total": 0}
        result["range"]["provider"] = "google"
        for point in result["daily_spend"]:
            point.update(snapchat=None, tiktok=None, meta=None, booked_ad_expense_sar=None)
    result["providers"].append(summary)
    if spend is not None:
        old = result["metrics"].get("provider_reported_spend_sar")
        result["metrics"]["provider_reported_spend_sar"] = round((old or 0) + spend, 6)
    for key in ("platform_roas", "platform_cpa_sar", "platform_cpc_sar", "platform_cpm_sar", "platform_ctr_pct"):
        result["metrics"][key] = None
    coverage = result["coverage"]
    coverage.update(providers_total=len(result["providers"]), provider_spend_is_partial=not complete or coverage.get("provider_spend_is_partial", False), revenue_is_partial=True, booked_expense_is_partial=True)
    coverage["provider_spend_providers"] = coverage.get("provider_spend_providers", 0) + int(complete)
    coverage["campaign_detail_providers"] = coverage.get("campaign_detail_providers", 0) + int(bool(campaigns))
    for point in result["daily_spend"]:
        point["google"] = daily.get(point["date"]) if complete else None
    query = (campaign_query or "").casefold()
    combined = result["campaigns"] + [row for row in campaigns if not query or query in row["campaign_name"].casefold() or query in row["campaign_id"]]
    total = len(combined)
    combined.sort(key=lambda row: row.get("spend_sar_equivalent") or row.get("spend_reported") or 0, reverse=True)
    result["campaigns"] = combined[(page - 1) * limit:page * limit]
    result["campaign_pagination"] = {"page": page, "limit": limit, "total": total, "pages": math.ceil(total / limit)}
    result["sources"].append({"key": GOOGLE_ADS_DAILY_COLLECTION, "role": "provider_reported", "grain": "account_day", "authoritative_for": ["google_provider_spend_sar"]})
    return result


class GoogleAdsManagerService(AdsManagerService):
    async def overview(self, user_id, *, provider="all", **kwargs):
        # Read the complete bounded original campaign set once before merging
        # the Google rows and applying a shared page, avoiding double paging.
        page, limit = kwargs.get("page", 1), kwargs.get("limit", 50)
        if provider in {"all", "google"}:
            original_kwargs = {**kwargs, "page": 1, "limit": 10000}
            result = await super().overview(user_id, provider="all", **original_kwargs)
            return await augment_google_overview(self.db, user_id, result, provider=provider, campaign_query=kwargs.get("campaign_query"), page=page, limit=limit)
        return await super().overview(user_id, provider=provider, **kwargs)
