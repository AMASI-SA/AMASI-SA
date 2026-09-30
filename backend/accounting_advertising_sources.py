"""Read-only adapters over actual V2 producers; analytics never authorizes posting."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from accounting_advertising_contract import decimal, digest, fail

SOURCES = {
    "snapchat": ("snapchat_ads", "mezan_snapchat_daily_projections_v2"),
    "meta": ("meta_ads", "mezan_meta_performance_daily_v2"),
    "tiktok": ("tiktok_ads", "mezan_tiktok_performance_daily_v2"),
    "google_ads": ("google_ads", "mezan_google_ads_performance_daily_v2"),
}
MODES = {"meta": "meta_marketing_reporting_v2", "tiktok": "tiktok_marketing_reporting_v2",
         "google_ads": "google_ads_reporting_v1"}
ACCOUNTS = "mezan_integration_accounts_v2"


def account_view(row):
    platform = next((p for p, (provider, _) in SOURCES.items() if row.get("provider") == provider), None)
    return dict(platform=platform, integration_account_id=row.get("mezan_integration_account_id"),
                platform_account_id=row.get("external_account_id"), display_name=row.get("display_name"),
                currency=row.get("currency"), status=row.get("connection_status"),
                provider_account_status=row.get("account_status"), timezone=row.get("timezone"))


async def require_account(db, owner, platform, integration_id):
    if platform not in SOURCES:
        fail("ad_platform_invalid")
    rows = await db[ACCOUNTS].find({"user_id": owner, "provider": SOURCES[platform][0],
        "mezan_integration_account_id": integration_id}, {"_id": 0}).to_list(2)
    if not rows:
        fail("ad_v2_integration_missing")
    if len(rows) != 1:
        fail("ad_v2_integration_ambiguous")
    row = rows[0]
    result = account_view(row)
    if not result["platform_account_id"] or not result["currency"] or not result["display_name"]:
        fail("ad_v2_identity_incomplete")
    if result["status"] != "connected":
        fail("ad_v2_integration_inactive")
    # Provider account status is not emitted by TikTok/Google projections.
    # Preserve unknown status; never invent a provider-active assertion.
    if row.get("account_status") is not None and str(row["account_status"]).upper() not in {"1", "ACTIVE", "ENABLED"}:
        fail("ad_provider_account_inactive")
    return result


def aware(value, field):
    try:
        # Mongo stores BSON datetime in UTC; the production Motor client returns
        # it naive. Naive timestamp *strings* remain invalid source evidence.
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        fail("ad_source_timestamp_invalid", field=field)


async def daily_source(db, owner, platform, integration_id, business_date):
    account = await require_account(db, owner, platform, integration_id)
    provider, collection = SOURCES[platform]
    try:
        zone = ZoneInfo(account.get("timezone") or "")
        day = date.fromisoformat(str(business_date))
    except (ValueError, ZoneInfoNotFoundError):
        fail("ad_source_timezone_or_date_missing")
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    if end > datetime.now(timezone.utc):
        fail("ad_source_day_not_closed")
    query = dict(user_id=owner, provider=provider, ad_account_id=account["platform_account_id"])
    if platform == "snapchat":
        query.update(report_date=day.isoformat(), projection_timezone=account["timezone"], action_report_time="conversion")
    else:
        query["date"] = day.isoformat()
    rows = await db[collection].find(query).to_list(2)
    if len(rows) != 1:
        fail("ad_daily_v2_fact_missing" if not rows else "ad_daily_v2_fact_ambiguous", collection=collection)
    row = rows[0]
    if platform == "snapchat":
        if row.get("amount_complete") is not True or row.get("data_state") != "confirmed_data":
            fail("ad_snapchat_daily_incomplete")
        if not row.get("source_sync_run_ids") or not row.get("source_fact_count"):
            fail("ad_source_provenance_missing")
        currency, amount = row.get("currency"), row.get("base_spend_native")
        source_mode = "snapchat_v2_daily_projection"
        observed = row.get("source_latest_updated_at")
        source_timezone = row.get("projection_timezone")
    else:
        if row.get("source_mode") != MODES[platform]:
            fail("ad_source_provenance_missing")
        if row.get("empty_provider_row") is True:
            fail("ad_daily_empty_provider_row")
        currency, amount = row.get("currency_native"), row.get("spend_native")
        source_mode = row["source_mode"]
        observed = row.get("observed_at")
        source_timezone = row.get("account_timezone")
    if source_timezone != account["timezone"]:
        fail("ad_source_timezone_mismatch")
    if currency != account["currency"] or amount is None:
        fail("ad_source_currency_or_amount_missing")
    original = decimal(amount)
    # Reporters synthesize zero for absent provider data. Zero is not a posting.
    if not original:
        fail("ad_zero_requires_separate_reconciliation")
    observed_at = aware(observed, "observed_at")
    if observed_at < end or observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        fail("ad_source_freshness_invalid")
    if not row.get("_id"):
        fail("ad_source_record_identity_missing")
    material = dict(platform=platform, integration_account_id=integration_id,
        platform_account_id=account["platform_account_id"], business_date=day.isoformat(),
        source_timezone=source_timezone, original_currency=currency,
        original_amount=format(original.normalize(), "f"), source_collection=collection,
        source_record_id=str(row["_id"]), source_mode=source_mode)
    # A refresh timestamp is not a financial revision. Material changes are.
    return {**material, "source_revision": digest(material), "source_observed_at": observed_at.isoformat(),
        "source_updated_at": aware(row.get("updated_at"), "updated_at").isoformat(),
        "source_sync_run_ids": row.get("source_sync_run_ids", []),
        "source_only": row.get("source_only"), "source_accounting_eligible": row.get("accounting_eligible"),
        "effective_at": datetime.combine(day, time.min, zone).astimezone(timezone.utc).isoformat(),
        "native_approval_required": True,
        "missing_contract_reason": "owner_confirmed_completeness_timezone_and_native_snapshot_required"}
