"""Native Google entities and account-local facts shared by marketing and AI.

No accounting writes, legacy credentials or estimated profitability. Bounded
reads explicitly carry completeness, currency, timezone and source provenance.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

from .google_ads_client import MAX_ROWS, gaql_string, identifier, problem
from .google_ads_management import available_accounts, account_client, digest

FACTS = "mezan_google_ads_campaign_facts_v1"
FACTSETS = "mezan_google_ads_campaign_factsets_v1"


def period(from_date, to_date):
    try:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    except (ValueError, TypeError):
        raise problem("google_ads_invalid_period", "اختر تاريخ بداية ونهاية صالحين.", 422) from None
    if end < start or (end - start).days > 92:
        raise problem("google_ads_invalid_period", "نطاق التقرير يجب ألا يتجاوز 93 يومًا.", 422)
    return start.isoformat(), end.isoformat()


async def compatible_read(client, fields, root, where="", *, required=(), limit=MAX_ROWS + 1):
    metadata = await client.field_metadata(fields)
    selected = [field for field in fields if metadata.get(field, {}).get("selectable") is True]
    missing = [field for field in required if field not in selected]
    if missing or not selected:
        raise problem("google_ads_read_contract_unavailable", "إصدار جوجل لا يثبت الحقول اللازمة لهذه العملية.", 409, fields=missing)
    rows, complete = await client.search("SELECT " + ", ".join(selected) + " FROM " + root + (" WHERE " + where if where else "") + f" LIMIT {limit}")
    return rows, complete, [field for field in fields if field not in selected]


def native_metrics(row):
    raw = row.get("metrics") or {}
    def number(key, divisor=1):
        if key not in raw:
            return None
        try:
            value = Decimal(str(raw[key])) / divisor
            return str(value) if value.is_finite() else None
        except Exception:
            return None
    spend, value = number("costMicros", 1000000), number("conversionsValue")
    return {"spend_native": spend, "spend": spend, "impressions": number("impressions"), "clicks": number("clicks"),
        "conversions": number("conversions"), "conversion_value_native": value, "conversion_value": value,
        "ctr": number("ctr"), "cpc_native": number("averageCpc", 1000000),
        "roas": str(Decimal(value) / Decimal(spend)) if spend and value is not None and Decimal(spend) > 0 else None}


METRICS = ["metrics.cost_micros", "metrics.impressions", "metrics.clicks", "metrics.conversions", "metrics.conversions_value", "metrics.ctr", "metrics.average_cpc"]
CAMPAIGN_FIELDS = ["campaign.resource_name", "campaign.id", "campaign.name", "campaign.status", "campaign.advertising_channel_type", "campaign.advertising_channel_sub_type", "campaign.bidding_strategy_type", "campaign.primary_status", "campaign.primary_status_reasons", "campaign_budget.resource_name", "campaign_budget.amount_micros", "campaign_budget.total_amount_micros", "campaign_budget.period"]
CAMPAIGN_FIELDS += ["campaign.app_campaign_setting.app_id", "campaign.app_campaign_setting.app_store", "campaign.app_campaign_setting.bidding_strategy_goal_type", "campaign.selective_optimization.conversion_actions", "campaign.shopping_setting.merchant_id", "campaign.shopping_setting.feed_label", "campaign.shopping_setting.campaign_priority", "campaign.hotel_setting.hotel_center_id"]


def campaign_context(campaign):
    """Provider settings projected to the same typed draft used by every editor."""
    context = {}
    app = campaign.get("appCampaignSetting") or {}
    if app.get("appId") and app.get("appStore"):
        subtype = {"APP_CAMPAIGN_FOR_ENGAGEMENT": "ENGAGEMENT", "APP_CAMPAIGN_FOR_PRE_REGISTRATION": "PRE_REGISTRATION"}.get(campaign.get("advertisingChannelSubType"), "INSTALLS")
        context["app"] = {"subtype": subtype, "app_id": str(app["appId"]), "app_store": app["appStore"], "conversion_action_ids": list((campaign.get("selectiveOptimization") or {}).get("conversionActions") or [])}
        if app.get("biddingStrategyGoalType") not in {None, "UNKNOWN", "UNSPECIFIED"}:
            context["app"]["goal_type"] = app["biddingStrategyGoalType"]
    shopping = campaign.get("shoppingSetting") or {}
    if shopping.get("merchantId"):
        context["shopping"] = {"merchant_id": str(shopping["merchantId"]), "campaign_priority": int(shopping.get("campaignPriority") or 0)}
        if shopping.get("feedLabel"):
            context["shopping"]["feed_label"] = shopping["feedLabel"]
    hotel = campaign.get("hotelSetting") or {}
    if hotel.get("hotelCenterId"):
        context["hotel"] = {"hotel_center_id": str(hotel["hotelCenterId"])}
    return context


def campaign_item(row, metadata, observed_at):
    campaign, budget = row.get("campaign") or {}, row.get("campaignBudget") or {}
    total = budget.get("period") == "CUSTOM_PERIOD"
    amount = budget.get("totalAmountMicros") if total else budget.get("amountMicros")
    return {"id": str(campaign.get("id") or ""), "campaign_id": str(campaign.get("id") or ""), "account_id": metadata["account_id"],
        "name": campaign.get("name"), "status": campaign.get("status"), "effective_status": campaign.get("primaryStatus"), "delivery_reasons": campaign.get("primaryStatusReasons") or [],
        "campaign_type": "APP" if campaign.get("advertisingChannelType") == "MULTI_CHANNEL" else campaign.get("advertisingChannelType"), "campaign_subtype": campaign.get("advertisingChannelSubType"),
        "resource_name": campaign.get("resourceName"), "budget_native": str(Decimal(str(amount)) / 1000000) if amount is not None else None,
        "budget_mode": "TOTAL" if total else "DAILY", "bidding_strategy": campaign.get("biddingStrategyType"),
        "currency_code": metadata["currency_code"], "time_zone": metadata["time_zone"], "metrics": native_metrics(row),
        "source": "google_ads_api", "observed_at": observed_at, "raw_revision": digest(row), "campaign_context": campaign_context(campaign)}


async def read_google_workspace(db, actor, *, from_date, to_date, account_id=None, entity_type="campaigns", campaign_id=None, group_id=None, page=1, limit=25, campaign_query="", client_factory=None):
    from .google_ads_client import GoogleAdsClient
    from .google_ads_campaign_contract import campaign_capabilities
    start, end = period(from_date, to_date)
    accounts = await available_accounts(db, actor["user_id"])
    empty = {"accounts": accounts, "items": [], "campaigns": [], "totals": {}, "daily": [], "hourly": [], "permissions": {"owner": actor["owner"], "can_draft": actor["can_draft"]}, "capabilities": campaign_capabilities(), "range": {"from_date": start, "to_date": end}, "pagination": {"page": page, "has_more": False}, "complete": True, "source": "google_ads_api"}
    if not accounts:
        return empty
    if not account_id and len(accounts) > 1:
        # An unavailable first link must not poison another owned account's
        # workspace, nor may its totals be presented as an all-account report.
        # Readiness proves which links are readable; the UI then sends the
        # same explicit account for native management and the report.
        return {**empty, "account_selection_required": True, "complete": False}
    account_id = identifier(account_id or accounts[0]["account_id"])
    client, _ = await account_client(db, actor["user_id"], account_id, client_factory=client_factory or GoogleAdsClient)
    try:
        metadata = await client.account_metadata()
        for account in accounts:
            if account["account_id"] == account_id:
                account.update(metadata, currency=metadata["currency_code"], timezone=metadata["time_zone"])
        observed = datetime.now(timezone.utc).isoformat()
        date_where = f"segments.date BETWEEN '{start}' AND '{end}'"
        filter_campaign = f" AND campaign.id = {identifier(campaign_id)}" if campaign_id else ""
        rows, complete, gaps = await compatible_read(client, CAMPAIGN_FIELDS + METRICS, "campaign", "campaign.status != 'REMOVED' AND " + date_where + filter_campaign, required=("campaign.id", "campaign.name", "campaign.status", "campaign.advertising_channel_type", *METRICS[:5]))
        campaigns = [campaign_item(row, metadata, observed) for row in rows]
        all_campaigns = campaigns
        if campaign_query:
            campaigns = [item for item in campaigns if campaign_query.casefold() in (item["name"] or "").casefold()]
        items = campaigns
        if entity_type in {"groups", "ads"} or entity_type == "assets" and campaign_id and group_id:
            if not campaign_id:
                raise problem("google_ads_parent_required", "اختر حملة قبل عرض مجموعاتها وإعلاناتها.", 422)
            snapshot = await client.campaign_snapshot(campaign_id, children=True)
            is_pmax = snapshot["campaign"].get("advertisingChannelType") == "PERFORMANCE_MAX"
            root, payload = ("asset_group", "assetGroup") if is_pmax else ("ad_group", "adGroup")
            if entity_type in {"ads", "assets"} and is_pmax:
                items = [{"id": asset.get("resourceName"), "resource_name": asset.get("resourceName"), "group_id": str(group.get("id")), "name": asset.get("fieldType"), "type": asset.get("fieldType"), "asset": asset.get("asset"), "status": asset.get("status"), "policy_status": (asset.get("policySummary") or {}).get("approvalStatus"), "campaign_id": campaign_id, "is_asset_group_asset": True} for group in snapshot["groups"] if not group_id or str(group.get("id")) == str(group_id) for asset in group.get("assets", [])]
            else:
                if entity_type == "ads":
                    root, payload = "ad_group_ad", "adGroupAd"
                    fields = ["ad_group_ad.resource_name", "ad_group_ad.status", "ad_group_ad.ad.id", "ad_group_ad.ad.name", "ad_group_ad.ad.type", "ad_group_ad.ad.final_urls", "ad_group_ad.ad.final_app_urls", "ad_group_ad.policy_summary.approval_status", "ad_group_ad.policy_summary.review_status", "ad_group_ad.policy_summary.policy_topic_entries", "ad_group_ad.ad_strength", "ad_group.id"]
                else:
                    fields = [f"{root}.resource_name", f"{root}.id", f"{root}.name", f"{root}.status", f"{root}.primary_status", f"{root}.primary_status_reasons"]
                filters = f"campaign.id = {identifier(campaign_id)} AND {root}.status != 'REMOVED' AND " + date_where
                if group_id:
                    filters += f" AND {'asset_group' if is_pmax else 'ad_group'}.id = {identifier(group_id)}"
                entity_rows, entity_complete, entity_gaps = await compatible_read(client, fields + METRICS, root, filters, required=(f"{root}.resource_name", f"{root}.status"))
                complete = complete and entity_complete
                gaps += entity_gaps
                items = []
                for row in entity_rows:
                    raw = row.get(payload) or {}
                    ad = raw.get("ad") or {}
                    item_id = str(ad.get("id") or raw.get("id") or "")
                    items.append({"id": item_id, "group_id": str((row.get("adGroup") or {}).get("id") or raw.get("id") or ""), "campaign_id": campaign_id, "name": ad.get("name") or raw.get("name") or item_id, "resource_name": raw.get("resourceName"), "ad_group_ad_resource": raw.get("resourceName") if entity_type == "ads" else None,
                        "status": raw.get("status"), "type": ad.get("type"), "final_urls": ad.get("finalUrls") or [], "policy_status": (raw.get("policySummary") or {}).get("approvalStatus"), "policy_issues": (raw.get("policySummary") or {}).get("policyTopicEntries") or [], "ad_strength": raw.get("adStrength"), "effective_status": raw.get("primaryStatus"), "delivery_reasons": raw.get("primaryStatusReasons") or [], "metrics": native_metrics(row), "is_asset_group": is_pmax})
            from .google_ads_creative_projection import project_google_group
            from .google_ads_client import owned_resource
            for item in items:
                item.update(account_id=account_id, currency_code=metadata["currency_code"], time_zone=metadata["time_zone"], campaign_type="APP" if snapshot["campaign"].get("advertisingChannelType") == "MULTI_CHANNEL" else snapshot["campaign"].get("advertisingChannelType"), app=snapshot["campaign"].get("appCampaignSetting"), shopping=snapshot["campaign"].get("shoppingSetting"))
                item["campaign_context"] = campaign_context(snapshot["campaign"])
                owned_resource(item.get("resource_name"), account_id)
                if not item.get("is_asset_group_asset"):
                    projection = project_google_group(snapshot, account_id, item["group_id"], ad_resource=item.get("ad_group_ad_resource"))
                    item.update(projection)
                    item["creative_supported"] = projection["editable"]
                    if entity_type == "ads" and item.get("name") == item["id"]:
                        creative = projection.get("creative") or {}
                        item["name"] = next((text for text in [*(creative.get("headlines") or []), *(creative.get("long_headlines") or [])] if isinstance(text, str) and text.strip()), "إعلان جوجل")
        elif entity_type == "assets":
            items, asset_complete = await client.assets()
            complete = complete and asset_complete
        elif entity_type != "campaigns":
            raise problem("google_ads_entity_type_invalid", "مستوى العرض غير صالح.", 422)
        totals = {}
        for key in ("spend_native", "impressions", "clicks", "conversions", "conversion_value_native"):
            values = [item["metrics"].get(key) for item in all_campaigns]
            totals[key] = str(sum((Decimal(value) for value in values if value is not None), Decimal(0))) if complete and all(value is not None for value in values) else None
        totals.update(spend=totals.get("spend_native"), currency_code=metadata["currency_code"], time_zone=metadata["time_zone"])
        daily_rows, daily_complete, _ = await compatible_read(client, ["segments.date", *METRICS[:5]], "customer", date_where, required=("segments.date", *METRICS[:5]))
        daily = [{"date": row.get("segments", {}).get("date"), **native_metrics(row), "currency_code": metadata["currency_code"], "time_zone": metadata["time_zone"]} for row in daily_rows]
        # A cache of source-backed, account/period facts; AI and cross-platform
        # readers reuse these facts, never an independently calculated total.
        await db[FACTSETS].create_index([("user_id", 1), ("account_id", 1), ("date_from", 1), ("date_to", 1)], unique=True, name="google_campaign_factset_scope")
        if complete and not campaign_id:
            scope = {"user_id": actor["user_id"], "account_id": account_id, "date_from": start, "date_to": end}
            # Publish the full set atomically, including an empty set. A deleted
            # campaign must not survive in a dashboard assembled from old rows.
            await db[FACTSETS].update_one(scope, {"$set": {**scope, "campaigns": all_campaigns, "complete": True, "source": "google_ads_api", "observed_at": observed}}, upsert=True)
        offset = (page - 1) * limit
        return {**empty, "account_id": account_id, "currency_code": metadata["currency_code"], "time_zone": metadata["time_zone"], "items": items[offset:offset + limit], "campaigns": all_campaigns, "totals": totals, "daily": daily, "complete": complete and daily_complete, "field_gaps": sorted(set(gaps)), "observed_at": observed,
            "pagination": {"page": page, "limit": limit, "total": len(items) if complete else None, "has_more": len(items) > offset + limit, "truncated": not complete}}
    finally:
        await client.close()


async def google_lookup(client, kind, *, query="", merchant_id=None):
    """Human labels with provider identities; never fabricate linked resources."""
    if kind == "geo":
        query = {"السعودية": "Saudi Arabia", "المملكة العربية السعودية": "Saudi Arabia", "اليمن": "Yemen", "قطر": "Qatar", "الإمارات": "United Arab Emirates", "الامارات": "United Arab Emirates", "الكويت": "Kuwait", "البحرين": "Bahrain", "عمان": "Oman", "الرياض": "Riyadh", "جدة": "Jeddah", "مكة": "Mecca", "الدمام": "Dammam", "الدوحة": "Doha"}.get(query.strip(), query)
        if len(query.strip()) < 2:
            return {"items": [], "complete": True}
        rows, complete, _ = await compatible_read(client, ["geo_target_constant.id", "geo_target_constant.resource_name", "geo_target_constant.name", "geo_target_constant.canonical_name", "geo_target_constant.country_code", "geo_target_constant.target_type"], "geo_target_constant", "geo_target_constant.status = 'ENABLED' AND geo_target_constant.name LIKE " + gaql_string("%" + query.replace("%", "").replace("_", "")[:100] + "%"), required=("geo_target_constant.id", "geo_target_constant.name"), limit=101)
        values = [row.get("geoTargetConstant") or {} for row in rows]
        return {"items": [{"id": str(row.get("id")), "name": row.get("canonicalName") or row.get("name"), "resource_name": row.get("resourceName"), "country_code": row.get("countryCode"), "target_type": row.get("targetType")} for row in values[:100]], "complete": complete and len(values) <= 100}
    config = {
        "languages": ("language_constant", "languageConstant", ["id", "resource_name", "name", "code"], ""),
        "audiences": ("audience", "audience", ["id", "resource_name", "name", "status"], "audience.status != 'REMOVED'"),
        "user_lists": ("user_list", "userList", ["id", "resource_name", "name", "membership_status"], ""),
        "conversion_actions": ("conversion_action", "conversionAction", ["id", "resource_name", "name", "status", "category", "type", "primary_for_goal"], "conversion_action.status = 'ENABLED'"),
        "merchants": ("product_link", "productLink", ["resource_name", "merchant_center.merchant_center_id", "type"], "product_link.type = 'MERCHANT_CENTER'"),
        "hotels": ("product_link_invitation", "productLinkInvitation", ["resource_name", "hotel_center.hotel_center_id", "type", "status"], "product_link_invitation.type = 'HOTEL_CENTER' AND product_link_invitation.status = 'ACCEPTED'"),
        "products": ("shopping_product", "shoppingProduct", ["resource_name", "merchant_center_id", "item_id", "title", "feed_label", "channel", "language_code", "status", "availability", "product_image_uri"], f"shopping_product.merchant_center_id = {identifier(merchant_id)}" if merchant_id else ""),
        "apps": ("campaign", "campaign", ["id", "name", "app_campaign_setting.app_id", "app_campaign_setting.app_store"], "campaign.advertising_channel_type = 'MULTI_CHANNEL' AND campaign.status != 'REMOVED'"),
    }
    if kind == "conversion_goals":
        rows, complete, _ = await compatible_read(client, ["customer_conversion_goal.resource_name", "customer_conversion_goal.category", "customer_conversion_goal.origin", "customer_conversion_goal.biddable"], "customer_conversion_goal", required=("customer_conversion_goal.category", "customer_conversion_goal.origin", "customer_conversion_goal.biddable"))
        return {"items": [{"category": row.get("customerConversionGoal", {}).get("category"), "origin": row.get("customerConversionGoal", {}).get("origin"), "biddable": row.get("customerConversionGoal", {}).get("biddable", False), "name": str(row.get("customerConversionGoal", {}).get("category") or "") + " · " + str(row.get("customerConversionGoal", {}).get("origin") or "")} for row in rows], "complete": complete}
    if kind not in config:
        raise problem("google_ads_lookup_invalid", "دليل جوجل المطلوب غير متاح.", 404)
    root, payload, names, where = config[kind]
    fields = [root + "." + name for name in names]
    required = tuple(field for field in fields if field.rsplit(".", 1)[-1] not in {"title", "product_image_uri"}) if kind == "products" else (fields[0],)
    rows, complete, gaps = await compatible_read(client, fields, root, where, required=required)
    items = []
    for row in rows:
        raw = row.get(payload) or {}
        if kind == "products" and (not isinstance(raw, dict) or not raw.get("resourceName") or not raw.get("itemId")):
            raise problem("google_ads_product_identity_unproved", "ورد منتج جوجل دون هوية كاملة؛ لا يمكن اعتماد اكتمال التغذية.", 409)
        item = {"id": str(raw.get("id") or ""), "name": raw.get("name") or raw.get("title"), "resource_name": raw.get("resourceName"), "type": raw.get("type"), "status": raw.get("status")}
        if kind == "merchants":
            item.update(id=str((raw.get("merchantCenter") or {}).get("merchantCenterId") or ""), merchant_id=str((raw.get("merchantCenter") or {}).get("merchantCenterId") or ""))
        elif kind == "hotels":
            item.update(id=str((raw.get("hotelCenter") or {}).get("hotelCenterId") or ""), hotel_center_id=str((raw.get("hotelCenter") or {}).get("hotelCenterId") or ""), eligibility="provider_validate_only_required", reason="يتطلب حسابًا مؤهلًا وموافقة جوجل؛ الدعوة المقبولة لا تثبت جاهزية الخلاصة والتسعير.")
        elif kind == "products":
            item.update(id=raw.get("itemId"), offer_id=raw.get("itemId"), title=raw.get("title"), feed_label=raw.get("feedLabel"), channel=raw.get("channel"), language_code=raw.get("languageCode"), merchant_id=str(raw.get("merchantCenterId") or ""), preview_url=raw.get("productImageUri"), availability=raw.get("availability"), approved=raw.get("status") in {"ELIGIBLE", "ELIGIBLE_LIMITED"})
        elif kind == "apps":
            settings = raw.get("appCampaignSetting") or {}
            item.update(id=settings.get("appId"), app_id=settings.get("appId"), app_store=settings.get("appStore"))
        elif kind == "languages":
            item.update(code=raw.get("code"), language_code=raw.get("code"))
        item["name"] = item["name"] or item["id"] or kind
        if item["id"] or item["resource_name"]:
            items.append(item)
    return {"items": items, "complete": complete, "field_gaps": gaps, "source": "google_ads_api"}
