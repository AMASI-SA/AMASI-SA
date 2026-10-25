"""Fresh, complete entity snapshots for frozen Google action preconditions."""
from copy import deepcopy
import re

from fastapi import HTTPException

from .google_ads_client import MAX_ROWS, gaql_string, identifier, owned_resource, problem
from .google_ads_workspace import compatible_read


CONTROL_CHUNK_FIELDS = 60
MAX_CONTROL_FIELDS = 4000
MAX_CONTROL_QUERIES = 80
CONTROL_RESOURCES = {
    "campaign": ("campaign", "campaigns", ("resource_name", "id", "status", "campaign_budget")),
    "campaign_budget": ("campaignBudget", "campaignBudgets", ("resource_name", "period")),
    "campaign_criterion": ("campaignCriterion", "campaignCriteria", ("resource_name", "campaign", "type", "negative")),
    "ad_group": ("adGroup", "adGroups", ("resource_name", "id", "campaign", "status", "type")),
    "ad_group_ad": ("adGroupAd", "adGroupAds", ("resource_name", "ad_group", "status", "ad.id", "ad.type")),
    "ad_group_criterion": ("adGroupCriterion", "adGroupCriteria", ("resource_name", "ad_group", "type", "negative")),
    "campaign_asset": ("campaignAsset", "campaignAssets", ("resource_name", "campaign", "asset", "field_type", "status")),
    "ad_group_asset": ("adGroupAsset", "adGroupAssets", ("resource_name", "ad_group", "asset", "field_type", "status")),
    "asset_group": ("assetGroup", "assetGroups", ("resource_name", "id", "campaign", "status")),
    "asset_group_asset": ("assetGroupAsset", "assetGroupAssets", ("resource_name", "asset_group", "asset", "field_type", "status")),
    "asset_group_signal": ("assetGroupSignal", "assetGroupSignals", ("resource_name", "asset_group")),
    "asset_group_listing_group_filter": ("assetGroupListingGroupFilter", "assetGroupListingGroupFilters", ("resource_name", "asset_group", "type", "listing_source")),
    "asset": ("asset", "assets", ("resource_name", "type")),
}
CHILD_COLLECTIONS = {
    **{root: values[1] for root, values in CONTROL_RESOURCES.items()},
    "campaign_conversion_goal": "campaignConversionGoals",
    "conversion_goal_campaign_config": "conversionGoalCampaignConfigs",
}


def _merge_control(left, right):
    """Merge disjoint selected fields, rejecting contradictory overlapping reads."""
    result = deepcopy(left)
    for key, value in right.items():
        if key not in result:
            result[key] = deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_control(result[key], value)
        elif result[key] != value:
            raise ValueError("Provider settings changed between control reads.")
    return result


def _sort_snapshot(result):
    def reference(row):
        if not isinstance(row, dict):
            return ""
        if row.get("resourceName"):
            return row["resourceName"]
        return next((value.get("resourceName", "") for value in row.values() if isinstance(value, dict) and value.get("resourceName")), "")
    for field in ("groups", "ads", "criteria", "conversion_goals", "conversion_goal_config", "campaign_assets", "creative_assets"):
        if isinstance(result.get(field), list):
            result[field].sort(key=reference)
    for group in result.get("groups", []):
        for field in ("criteria", "assets", "signals", "listing_group_filters", "group_assets"):
            if isinstance(group.get(field), list):
                group[field].sort(key=reference)
    return result


class _ControlReader:
    def __init__(self, client, result):
        self.client = client
        self.result = result
        self.queries = 0
        self.fields = 0
        self.catalogs = {}
        self.gaps = []

    def gap(self, root, code, fields=None):
        item = {"resource": root, "code": code}
        if fields:
            item["fields"] = fields[:20]
        if item not in self.gaps:
            self.gaps.append(item)

    async def catalog(self, root):
        if root in self.catalogs:
            return self.catalogs[root]
        try:
            source = await self.client.fields_for_resource(root)
        except HTTPException as exc:
            self.gap(root, (exc.detail or {}).get("code", "google_ads_catalog_unavailable") if isinstance(exc.detail, dict) else "google_ads_catalog_unavailable")
            return None
        if not isinstance(source, dict) or not source or len(source) > 1000:
            self.gap(root, "google_ads_catalog_incomplete")
            return None
        names = sorted(source)
        if any(not re.fullmatch(re.escape(root) + r"\.[a-z][a-z0-9_.]*", name) or not isinstance(source[name], dict) or source[name].get("selectable") is not True or source[name].get("category") != "ATTRIBUTE" for name in names):
            self.gap(root, "google_ads_catalog_invalid")
            return None
        required = [root + "." + field for field in CONTROL_RESOURCES[root][2]]
        if missing := sorted(set(required) - set(names)):
            self.gap(root, "google_ads_control_identity_unavailable", missing)
            return None
        self.fields += len(names)
        if self.fields > MAX_CONTROL_FIELDS:
            self.gap(root, "google_ads_control_field_limit")
            return None
        self.catalogs[root] = names
        return names

    def validate_rows(self, rows, root, *, group_refs=None, asset_refs=None):
        payload, collection, _ = CONTROL_RESOURCES[root]
        output = {}
        campaign = self.result["campaign"]["resourceName"]
        for raw in rows:
            if not isinstance(raw, dict) or not isinstance(raw.get(payload), dict):
                raise problem("google_ads_snapshot_payload_missing", "هوية مورد القراءة غير مثبتة.")
            row = raw[payload]
            resource = owned_resource(row.get("resourceName"), self.client.customer_id, collection)
            if resource in output:
                raise problem("google_ads_snapshot_duplicate_resource", "قراءة المورد تحتوي هويات مكررة.")
            if root == "campaign" and resource != campaign:
                raise problem("google_ads_snapshot_parent_mismatch", "المورد لا يتبع الحملة المحددة.")
            if root == "campaign_budget" and resource != self.result["campaign"]["campaignBudget"]:
                raise problem("google_ads_snapshot_parent_mismatch", "الميزانية لا تتبع الحملة المحددة.")
            if root in {"ad_group", "asset_group", "campaign_criterion", "campaign_asset"} and row.get("campaign") != campaign:
                raise problem("google_ads_snapshot_parent_mismatch", "عنصر الحملة يتبع حملة مختلفة.")
            if root in {"campaign_criterion", "campaign_asset"} and resource.rsplit("/", 1)[-1].split("~", 1)[0] != campaign.rsplit("/", 1)[-1]:
                raise problem("google_ads_snapshot_parent_mismatch", "هوية العنصر لا تطابق الحملة.")
            if root in {"ad_group_ad", "ad_group_criterion", "ad_group_asset"}:
                parent = owned_resource(row.get("adGroup"), self.client.customer_id, "adGroups")
                if parent not in (group_refs or set()):
                    raise problem("google_ads_snapshot_parent_mismatch", "عنصر المجموعة يتبع حملة مختلفة.")
                if resource.rsplit("/", 1)[-1].split("~", 1)[0] != parent.rsplit("/", 1)[-1]:
                    raise problem("google_ads_snapshot_parent_mismatch", "هوية العنصر لا تطابق المجموعة.")
            if root in {"asset_group_asset", "asset_group_signal", "asset_group_listing_group_filter"}:
                parent = owned_resource(row.get("assetGroup"), self.client.customer_id, "assetGroups")
                if parent not in (group_refs or set()) or resource.rsplit("/", 1)[-1].split("~", 1)[0] != parent.rsplit("/", 1)[-1]:
                    raise problem("google_ads_snapshot_parent_mismatch", "هوية مادة المجموعة لا تطابق المجموعة.")
            if root in {"campaign_asset", "ad_group_asset", "asset_group_asset"}:
                owned_resource(row.get("asset"), self.client.customer_id, "assets")
            if root == "asset" and resource not in (asset_refs or set()):
                raise problem("google_ads_snapshot_parent_mismatch", "المادة ليست من مراجع الإعلان المقروءة.")
            output[resource] = row
        return output

    async def read(self, root, where, *, group_refs=None, asset_refs=None):
        names = await self.catalog(root)
        if names is None:
            return None
        identity = [root + "." + field for field in CONTROL_RESOURCES[root][2]]
        other = [name for name in names if name not in identity]
        chunks = [other[index:index + CONTROL_CHUNK_FIELDS] for index in range(0, len(other), CONTROL_CHUNK_FIELDS)] or [[]]
        output = None
        for chunk in chunks:
            self.queries += 1
            if self.queries > MAX_CONTROL_QUERIES:
                self.gap(root, "google_ads_control_query_limit")
                return None
            fields = identity + chunk
            try:
                rows, complete = await self.client.search("SELECT " + ", ".join(fields) + " FROM " + root + " WHERE " + where, max_rows=MAX_ROWS)
            except HTTPException as exc:
                code = (exc.detail or {}).get("code", "google_ads_control_read_rejected") if isinstance(exc.detail, dict) else "google_ads_control_read_rejected"
                self.gap(root, code, chunk)
                return None
            if not complete or not isinstance(rows, list) or len(rows) > MAX_ROWS:
                self.gap(root, "google_ads_control_read_incomplete")
                return None
            current = self.validate_rows(rows, root, group_refs=group_refs, asset_refs=asset_refs)
            if output is not None and set(output) != set(current):
                self.gap(root, "google_ads_control_resources_changed")
                return None
            try:
                output = current if output is None else {resource: _merge_control(output[resource], row) for resource, row in current.items()}
            except ValueError:
                self.gap(root, "google_ads_control_settings_changed")
                return None
        return list((output or {}).values())


async def snapshot(client, campaign_id, *, children=False, duplicate_controls=False):
    campaign_id = identifier(campaign_id)
    children = children or duplicate_controls
    fields = ["campaign.resource_name", "campaign.id", "campaign.name", "campaign.status", "campaign.advertising_channel_type", "campaign.advertising_channel_sub_type", "campaign.campaign_budget", "campaign.bidding_strategy_type", "campaign.bidding_strategy", "campaign.brand_guidelines_enabled", "campaign.network_settings", "campaign.dynamic_search_ads_setting", "campaign.ai_max_setting", "campaign.asset_automation_settings", "campaign.shopping_setting", "campaign.app_campaign_setting", "campaign.selective_optimization", "campaign.hotel_setting", "campaign.start_date_time", "campaign.end_date_time", "campaign.local_services_campaign_settings", "campaign.hotel_property_asset_set", "campaign.travel_campaign_settings", "campaign.pmax_campaign_settings.local_services_enabled",
        "campaign.manual_cpc", "campaign.target_spend", "campaign.target_cpa", "campaign.target_roas", "campaign.maximize_conversions", "campaign.maximize_conversion_value", "campaign.target_impression_share", "campaign.manual_cpm", "campaign.target_cpm", "campaign.percent_cpc",
        "campaign_budget.resource_name", "campaign_budget.amount_micros", "campaign_budget.total_amount_micros", "campaign_budget.period", "campaign_budget.explicitly_shared", "campaign_budget.reference_count"]
    # GAQL exposes many message children rather than the parent message.
    fields += ["campaign.manual_cpc.enhanced_cpc_enabled", "campaign.target_spend.cpc_bid_ceiling_micros", "campaign.target_cpa.target_cpa_micros", "campaign.target_roas.target_roas", "campaign.maximize_conversions.target_cpa_micros", "campaign.maximize_conversion_value.target_roas", "campaign.shopping_setting.merchant_id", "campaign.shopping_setting.feed_label", "campaign.shopping_setting.campaign_priority", "campaign.app_campaign_setting.app_id", "campaign.app_campaign_setting.app_store", "campaign.app_campaign_setting.bidding_strategy_goal_type", "campaign.selective_optimization.conversion_actions", "campaign.hotel_setting.hotel_center_id", "campaign.network_settings.target_google_search", "campaign.network_settings.target_search_network", "campaign.network_settings.target_content_network", "campaign.network_settings.target_partner_search_network", "campaign.dynamic_search_ads_setting.domain_name", "campaign.dynamic_search_ads_setting.language_code", "campaign.ai_max_setting.enable_ai_max", "campaign.ai_max_setting.bundling_required", "campaign.percent_cpc.cpc_bid_ceiling_micros", "campaign.target_impression_share.location", "campaign.target_impression_share.location_fraction_micros", "campaign.target_impression_share.cpc_bid_ceiling_micros"]
    rows, complete, gaps = await compatible_read(client, fields, "campaign", f"campaign.id = {campaign_id}", required=("campaign.resource_name", "campaign.id", "campaign.status", "campaign.advertising_channel_type", "campaign.campaign_budget", "campaign_budget.resource_name", "campaign_budget.period"), limit=1)
    if not complete or len(rows) != 1 or str(rows[0].get("campaign", {}).get("id") or "") != campaign_id:
        raise problem("google_ads_campaign_not_found", "الحملة غير موجودة في الحساب المحدد.", 404)
    owned_resource(rows[0]["campaign"].get("resourceName"), client.customer_id, "campaigns")
    owned_resource(rows[0].get("campaignBudget", {}).get("resourceName"), client.customer_id, "campaignBudgets")
    result = {"campaign": rows[0].get("campaign") or {}, "campaign_budget": rows[0].get("campaignBudget") or {}, "groups": [], "ads": [], "criteria": [], "field_gaps": gaps}
    # GAQL may omit an empty standard Smart Bidding message when its optional
    # target is unset. Prove the scheme by its enum and that target's selected
    # child; an unread child or a portfolio reference is not an absent target.
    campaign = result["campaign"]
    optional_scheme = {
        "MAXIMIZE_CONVERSIONS": ("maximizeConversions", "campaign.maximize_conversions.target_cpa_micros"),
        "MAXIMIZE_CONVERSION_VALUE": ("maximizeConversionValue", "campaign.maximize_conversion_value.target_roas"),
    }.get(campaign.get("biddingStrategyType"))
    schemes = ("manualCpc", "percentCpc", "targetSpend", "targetCpa", "targetRoas", "maximizeConversions", "maximizeConversionValue", "targetImpressionShare", "manualCpm", "targetCpm")
    if optional_scheme and not campaign.get("biddingStrategy") and not any(key in campaign for key in schemes):
        field, target_child = optional_scheme
        if target_child not in gaps:
            campaign[field] = {}

    async def read(fields, root, payload, where, required):
        rows, complete, _ = await compatible_read(client, fields, root, where, required=required)
        if not complete:
            raise problem("google_ads_snapshot_incomplete", "عناصر الحملة تجاوزت حد القراءة الآمنة؛ ضيّق نطاق الإدارة.")
        output = [row.get(payload) or {} for row in rows]
        for value in output:
            reference = owned_resource(value.get("resourceName"), client.customer_id, CHILD_COLLECTIONS[root])
            if root in {"ad_group", "asset_group", "campaign_criterion", "campaign_conversion_goal", "conversion_goal_campaign_config"} and value.get("campaign") != result["campaign"]["resourceName"]:
                raise problem("google_ads_snapshot_parent_mismatch", "عنصر الحملة يتبع حملة مختلفة.")
            if root in {"campaign_criterion", "campaign_conversion_goal", "conversion_goal_campaign_config"} and reference.rsplit("/", 1)[-1].split("~", 1)[0] != campaign_id:
                raise problem("google_ads_snapshot_parent_mismatch", "هوية العنصر لا تطابق الحملة.")
            if root in {"ad_group_ad", "ad_group_criterion", "asset_group_signal", "asset_group_listing_group_filter"}:
                parent_field, collection = ("adGroup", "adGroups") if root.startswith("ad_group") else ("assetGroup", "assetGroups")
                parent = owned_resource(value.get(parent_field), client.customer_id, collection)
                if parent not in {group["resourceName"] for group in result["groups"]} or reference.rsplit("/", 1)[-1].split("~", 1)[0] != parent.rsplit("/", 1)[-1]:
                    raise problem("google_ads_snapshot_parent_mismatch", "عنصر المجموعة يتبع مجموعة مختلفة.")
        return output

    result["criteria"] = await read(["campaign_criterion.resource_name", "campaign_criterion.campaign", "campaign_criterion.type", "campaign_criterion.status", "campaign_criterion.negative", "campaign_criterion.location.geo_target_constant", "campaign_criterion.language.language_constant", "campaign_criterion.keyword.text", "campaign_criterion.keyword.match_type"], "campaign_criterion", "campaignCriterion", f"campaign.id = {campaign_id} AND campaign_criterion.status != 'REMOVED'", ("campaign_criterion.resource_name", "campaign_criterion.type"))
    result["conversion_goals"] = await read(["campaign_conversion_goal.resource_name", "campaign_conversion_goal.campaign", "campaign_conversion_goal.category", "campaign_conversion_goal.origin", "campaign_conversion_goal.biddable"], "campaign_conversion_goal", "campaignConversionGoal", f"campaign.id = {campaign_id}", ("campaign_conversion_goal.resource_name", "campaign_conversion_goal.category", "campaign_conversion_goal.origin", "campaign_conversion_goal.biddable"))
    result["conversion_goal_config"] = await read(["conversion_goal_campaign_config.resource_name", "conversion_goal_campaign_config.campaign", "conversion_goal_campaign_config.goal_config_level", "conversion_goal_campaign_config.custom_conversion_goal"], "conversion_goal_campaign_config", "conversionGoalCampaignConfig", f"campaign.id = {campaign_id}", ("conversion_goal_campaign_config.resource_name", "conversion_goal_campaign_config.goal_config_level", "conversion_goal_campaign_config.custom_conversion_goal"))
    if not children:
        return _sort_snapshot(result)
    if result["campaign"].get("advertisingChannelType") == "PERFORMANCE_MAX":
        result["groups"] = await read(["asset_group.resource_name", "asset_group.id", "asset_group.name", "asset_group.status", "asset_group.final_urls", "asset_group.campaign", "asset_group.path1", "asset_group.path2"], "asset_group", "assetGroup", f"campaign.id = {campaign_id} AND asset_group.status != 'REMOVED'", ("asset_group.resource_name", "asset_group.id", "asset_group.final_urls"))
        assets, complete, _ = await compatible_read(client, ["asset_group_asset.resource_name", "asset_group_asset.asset_group", "asset_group_asset.asset", "asset_group_asset.field_type", "asset_group_asset.status", "asset_group_asset.policy_summary.approval_status", "asset.text_asset.text"], "asset_group_asset", f"campaign.id = {campaign_id} AND asset_group_asset.status != 'REMOVED'", required=("asset_group_asset.resource_name", "asset_group_asset.asset", "asset_group_asset.field_type"))
        if not complete:
            raise problem("google_ads_snapshot_incomplete", "مواد المجموعة غير مكتملة.")
        group_refs = {group["resourceName"] for group in result["groups"]}
        for raw in assets:
            link = raw.get("assetGroupAsset") or {}
            reference = owned_resource(link.get("resourceName"), client.customer_id, "assetGroupAssets")
            parent = owned_resource(link.get("assetGroup"), client.customer_id, "assetGroups")
            owned_resource(link.get("asset"), client.customer_id, "assets")
            if parent not in group_refs or reference.rsplit("/", 1)[-1].split("~", 1)[0] != parent.rsplit("/", 1)[-1]:
                raise problem("google_ads_snapshot_parent_mismatch", "مادة المجموعة تتبع مجموعة مختلفة.")
        for group in result["groups"]:
            group["assets"] = [{**(row.get("assetGroupAsset") or {}), "textAsset": (row.get("asset") or {}).get("textAsset")} for row in assets if (row.get("assetGroupAsset") or {}).get("assetGroup") == group["resourceName"]]
            group_id = str(group["id"])
            group["signals"] = await read(["asset_group_signal.resource_name", "asset_group_signal.asset_group", "asset_group_signal.audience.audience", "asset_group_signal.search_theme.text", "asset_group_signal.local_services_id.service_id", "asset_group_signal.vertical_ads_item_group_rule_list.shared_set"], "asset_group_signal", "assetGroupSignal", f"asset_group.id = {group_id}", ("asset_group_signal.resource_name", "asset_group_signal.asset_group"))
            group["listing_group_filters"] = await read(["asset_group_listing_group_filter.resource_name", "asset_group_listing_group_filter.asset_group", "asset_group_listing_group_filter.type", "asset_group_listing_group_filter.listing_source", "asset_group_listing_group_filter.parent_listing_group_filter", "asset_group_listing_group_filter.case_value", "asset_group_listing_group_filter.case_value.product_item_id.value", "asset_group_listing_group_filter.case_value.product_brand.value", "asset_group_listing_group_filter.case_value.product_category.category_id", "asset_group_listing_group_filter.case_value.product_category.level", "asset_group_listing_group_filter.case_value.product_channel.channel", "asset_group_listing_group_filter.case_value.product_condition.condition", "asset_group_listing_group_filter.case_value.product_custom_attribute.index", "asset_group_listing_group_filter.case_value.product_custom_attribute.value", "asset_group_listing_group_filter.case_value.product_type.level", "asset_group_listing_group_filter.case_value.product_type.value", "asset_group_listing_group_filter.case_value.retail_filter_bundle.shared_set", "asset_group_listing_group_filter.case_value.webpage.conditions"], "asset_group_listing_group_filter", "assetGroupListingGroupFilter", f"asset_group.id = {group_id}", ("asset_group_listing_group_filter.resource_name", "asset_group_listing_group_filter.type", "asset_group_listing_group_filter.parent_listing_group_filter", "asset_group_listing_group_filter.case_value.product_item_id.value"))
        rows, complete, _ = await compatible_read(client, ["campaign_asset.resource_name", "campaign_asset.campaign", "campaign_asset.asset", "campaign_asset.field_type", "campaign_asset.status", "asset.resource_name", "asset.text_asset.text"], "campaign_asset", f"campaign.id = {campaign_id} AND campaign_asset.status != 'REMOVED'", required=("campaign_asset.resource_name", "campaign_asset.asset", "campaign_asset.field_type"))
        if not complete:
            raise problem("google_ads_snapshot_incomplete", "مواد العلامة التجارية غير مكتملة.")
        for raw in rows:
            link = raw.get("campaignAsset") or {}
            reference = owned_resource(link.get("resourceName"), client.customer_id, "campaignAssets")
            owned_resource(link.get("asset"), client.customer_id, "assets")
            if link.get("campaign") != result["campaign"]["resourceName"] or reference.rsplit("/", 1)[-1].split("~", 1)[0] != campaign_id:
                raise problem("google_ads_snapshot_parent_mismatch", "مادة العلامة التجارية تتبع حملة مختلفة.")
        result["campaign_assets"] = rows
    else:
        result["groups"] = await read(["ad_group.resource_name", "ad_group.id", "ad_group.name", "ad_group.status", "ad_group.type", "ad_group.campaign", "ad_group.cpc_bid_micros", "ad_group.target_cpa_micros", "ad_group.target_roas", "ad_group.percent_cpc_bid_micros", "ad_group.demand_gen_ad_group_settings", "ad_group.audience_setting.use_audience_grouped", "ad_group.ai_max_ad_group_setting.disable_search_term_matching"], "ad_group", "adGroup", f"campaign.id = {campaign_id} AND ad_group.status != 'REMOVED'", ("ad_group.resource_name", "ad_group.id", "ad_group.type", "ad_group.campaign"))
        for group in result["groups"]:
            group["criteria"] = await read(["ad_group_criterion.resource_name", "ad_group_criterion.ad_group", "ad_group_criterion.type", "ad_group_criterion.status", "ad_group_criterion.negative", "ad_group_criterion.location.geo_target_constant", "ad_group_criterion.language.language_constant", "ad_group_criterion.cpc_bid_micros", "ad_group_criterion.percent_cpc_bid_micros", "ad_group_criterion.webpage.criterion_name", "ad_group_criterion.webpage.conditions", "ad_group_criterion.keyword.text", "ad_group_criterion.keyword.match_type", "ad_group_criterion.audience.audience", "ad_group_criterion.user_list.user_list", "ad_group_criterion.listing_group.type", "ad_group_criterion.listing_group.parent_ad_group_criterion", "ad_group_criterion.listing_group.case_value"], "ad_group_criterion", "adGroupCriterion", f"ad_group.id = {group['id']} AND ad_group_criterion.status != 'REMOVED'", ("ad_group_criterion.resource_name", "ad_group_criterion.type"))
        fields = ["ad_group_ad.resource_name", "ad_group_ad.status", "ad_group_ad.ad_group", "ad_group_ad.ad.id", "ad_group_ad.ad.name", "ad_group_ad.ad.type", "ad_group_ad.ad.final_urls", "ad_group_ad.ad.final_app_urls", "ad_group_ad.policy_summary.approval_status", "ad_group_ad.ad.tracking_url_template", "ad_group_ad.ad.final_url_suffix", "ad_group_ad.ad.final_mobile_urls", "ad_group_ad.ad.url_custom_parameters", "ad_group_ad.ad.responsive_display_ad.control_spec", "ad_group_ad.ad.responsive_display_ad.main_color", "ad_group_ad.ad.responsive_display_ad.accent_color", "ad_group_ad.ad.responsive_display_ad.allow_flexible_color", "ad_group_ad.ad.responsive_display_ad.format_setting", "ad_group_ad.ad.responsive_display_ad.call_to_action_text", "ad_group_ad.ad.responsive_display_ad.square_logo_images", "ad_group_ad.ad.image_ad.image_asset.asset", "ad_group_ad.ad.display_upload_ad.display_upload_product_type", "ad_group_ad.ad.display_upload_ad.media_bundle.asset", "ad_group_ad.ad.expanded_dynamic_search_ad.description2"]
        for format_name in ("responsive_search_ad", "responsive_display_ad", "demand_gen_multi_asset_ad", "demand_gen_video_responsive_ad", "demand_gen_carousel_ad", "demand_gen_product_ad", "app_ad", "app_engagement_ad", "app_pre_registration_ad", "expanded_dynamic_search_ad", "image_ad", "display_upload_ad", "hotel_ad", "shopping_product_ad"):
            fields.append("ad_group_ad.ad." + format_name)
        for format_name in ("responsive_search_ad", "responsive_display_ad", "app_ad", "app_engagement_ad", "app_pre_registration_ad", "expanded_dynamic_search_ad", "demand_gen_multi_asset_ad", "demand_gen_video_responsive_ad", "demand_gen_carousel_ad", "demand_gen_product_ad"):
            for child in ("headlines", "descriptions", "long_headline", "business_name", "marketing_images", "square_marketing_images", "logo_images", "youtube_videos", "path1", "path2", "business_name", "logo_image", "headline", "description", "carousel_cards", "videos", "long_headlines", "portrait_marketing_images", "tall_portrait_marketing_images", "companion_banners", "call_to_action_text"):
                fields.append("ad_group_ad.ad." + format_name + "." + child)
        fields += ["ad_group_ad.ad.display_upload_ad.media_bundle", "ad_group_ad.ad.synthetic_content_info", "ad_group_ad.ad.app_ad.images", "ad_group_ad.ad.app_engagement_ad.images", "ad_group_ad.ad.app_pre_registration_ad.images", "ad_group_ad.ad.app_ad.html5_media_bundles", "ad_group_ad.ad.app_ad.app_deep_link", "ad_group_ad.ad.app_ad.mandatory_ad_text", "ad_group_ad.ad.responsive_display_ad.price_prefix", "ad_group_ad.ad.responsive_display_ad.promo_text", "ad_group_ad.ad.responsive_display_ad.control_spec.enable_asset_enhancements", "ad_group_ad.ad.responsive_display_ad.control_spec.enable_autogen_video"]
        result["ads"] = await read(fields, "ad_group_ad", "adGroupAd", f"campaign.id = {campaign_id} AND ad_group_ad.status != 'REMOVED'", ("ad_group_ad.resource_name", "ad_group_ad.ad.id", "ad_group_ad.ad.type", "ad_group_ad.ad_group"))
    await _read_carousel_assets(client, result)
    if duplicate_controls:
        await _duplicate_controls(client, campaign_id, result)
    return _sort_snapshot(result)


def _carousel_references(result):
    references = set()
    for row in result.get("ads", []):
        for card in (row.get("ad") or {}).get("demandGenCarouselAd", {}).get("carouselCards", []):
            if not isinstance(card, dict):
                raise problem("google_ads_carousel_reference_invalid", "مرجع بطاقة الإعلان غير صالح.")
            references.add(card.get("asset"))
    return references


async def _read_carousel_assets(client, result):
    references = _carousel_references(result)
    if not references:
        return
    if len(references) > 100:
        result["creative_asset_gaps"] = [{"code": "google_ads_carousel_asset_limit"}]
        return
    for reference in references:
        owned_resource(reference, client.customer_id, "assets")
    fields = ["asset.resource_name", "asset.type", "asset.final_urls", "asset.final_mobile_urls", "asset.tracking_url_template", "asset.final_url_suffix", "asset.url_custom_parameters", "asset.demand_gen_carousel_card_asset.headline", "asset.demand_gen_carousel_card_asset.marketing_image_asset", "asset.demand_gen_carousel_card_asset.square_marketing_image_asset", "asset.demand_gen_carousel_card_asset.portrait_marketing_image_asset"]
    where = "asset.resource_name IN (" + ",".join(gaql_string(reference) for reference in sorted(references)) + ")"
    try:
        rows, complete, gaps = await compatible_read(client, fields, "asset", where, required=("asset.resource_name", "asset.type", "asset.final_urls", "asset.demand_gen_carousel_card_asset.headline"))
    except HTTPException as exc:
        result["creative_asset_gaps"] = [{"code": (exc.detail or {}).get("code", "google_ads_carousel_assets_unreadable") if isinstance(exc.detail, dict) else "google_ads_carousel_assets_unreadable"}]
        return
    if not complete or len(rows) != len(references):
        result["creative_asset_gaps"] = [{"code": "google_ads_carousel_assets_incomplete"}]
        return
    assets = []
    seen = set()
    for row in rows:
        asset = row.get("asset") or {}
        reference = owned_resource(asset.get("resourceName"), client.customer_id, "assets")
        if reference not in references or reference in seen or asset.get("type") != "DEMAND_GEN_CAROUSEL_CARD":
            raise problem("google_ads_carousel_asset_mismatch", "مادة البطاقة لا تطابق مرجع الإعلان.")
        seen.add(reference)
        assets.append(asset)
    result["creative_assets"] = assets
    if gaps:
        result["creative_asset_gaps"] = [{"code": "google_ads_carousel_control_fields_unavailable", "fields": gaps}]


async def _duplicate_controls(client, campaign_id, result):
    """Read every selected ATTRIBUTE from the fixed-version provider catalog."""
    result["duplicate_controls_complete"] = False
    reader = _ControlReader(client, result)
    version = getattr(client, "api_version", None) or str(getattr(client, "base", "")).rsplit("/", 1)[-1]
    if version != "v25" or not callable(getattr(client, "fields_for_resource", None)):
        reader.gap("catalog", "google_ads_control_version_unproved")
        result["duplicate_control_gaps"] = reader.gaps
        return
    scoped = f"campaign.id = {campaign_id}"
    campaign_rows = await reader.read("campaign", scoped)
    if campaign_rows is not None:
        if len(campaign_rows) != 1:
            reader.gap("campaign", "google_ads_control_read_incomplete")
        else:
            try:
                result["campaign"] = _merge_control(result["campaign"], campaign_rows[0])
            except ValueError:
                reader.gap("campaign", "google_ads_control_settings_changed")
    budget_rows = await reader.read("campaign_budget", "campaign_budget.resource_name = " + gaql_string(result["campaign"]["campaignBudget"]))
    if budget_rows is not None:
        if len(budget_rows) != 1:
            reader.gap("campaign_budget", "google_ads_control_read_incomplete")
        else:
            try:
                result["campaign_budget"] = _merge_control(result["campaign_budget"], budget_rows[0])
            except ValueError:
                reader.gap("campaign_budget", "google_ads_control_settings_changed")
    criteria = await reader.read("campaign_criterion", scoped + " AND campaign_criterion.status != 'REMOVED'")
    if criteria is not None:
        result["criteria"] = criteria
    pmax = result["campaign"].get("advertisingChannelType") == "PERFORMANCE_MAX"
    root = "asset_group" if pmax else "ad_group"
    groups = await reader.read(root, scoped + f" AND {root}.status != 'REMOVED'")
    if groups is not None:
        previous = {row["resourceName"]: row for row in result["groups"]}
        if set(previous) != {row["resourceName"] for row in groups}:
            reader.gap(root, "google_ads_control_resources_changed")
        else:
            merged = []
            for row in groups:
                try:
                    merged.append(_merge_control(previous[row["resourceName"]], row))
                except ValueError:
                    reader.gap(root, "google_ads_control_settings_changed")
            if len(merged) == len(groups):
                result["groups"] = merged
    group_refs = {row["resourceName"] for row in result["groups"]}
    campaign_assets = await reader.read("campaign_asset", scoped + " AND campaign_asset.status != 'REMOVED'")
    if campaign_assets is not None:
        old_assets = {row.get("campaignAsset", row).get("resourceName"): row for row in result.get("campaign_assets", [])}
        result["campaign_assets"] = [{**old_assets.get(row["resourceName"], {}), "campaignAsset": row} for row in campaign_assets]
    if pmax:
        assets = await reader.read("asset_group_asset", scoped + " AND asset_group_asset.status != 'REMOVED'", group_refs=group_refs)
        signals = await reader.read("asset_group_signal", scoped, group_refs=group_refs)
        filters = await reader.read("asset_group_listing_group_filter", scoped, group_refs=group_refs)
        for group in result["groups"]:
            if assets is not None:
                text_assets = {row["resourceName"]: row.get("textAsset") for row in group.get("assets", [])}
                group["assets"] = [{**row, "textAsset": text_assets.get(row["resourceName"])} for row in assets if row["assetGroup"] == group["resourceName"]]
            if signals is not None:
                group["signals"] = [row for row in signals if row["assetGroup"] == group["resourceName"]]
            if filters is not None:
                group["listing_group_filters"] = [row for row in filters if row["assetGroup"] == group["resourceName"]]
    else:
        ads = await reader.read("ad_group_ad", scoped + " AND ad_group_ad.status != 'REMOVED'", group_refs=group_refs)
        if ads is not None:
            previous_ads = {row["resourceName"]: row for row in result["ads"]}
            if set(previous_ads) != {row["resourceName"] for row in ads}:
                reader.gap("ad_group_ad", "google_ads_control_resources_changed")
            else:
                merged = []
                for row in ads:
                    try:
                        merged.append(_merge_control(previous_ads[row["resourceName"]], row))
                    except ValueError:
                        reader.gap("ad_group_ad", "google_ads_control_settings_changed")
                if len(merged) == len(ads):
                    result["ads"] = merged
        criteria = await reader.read("ad_group_criterion", scoped + " AND ad_group_criterion.status != 'REMOVED'", group_refs=group_refs)
        assets = await reader.read("ad_group_asset", scoped + " AND ad_group_asset.status != 'REMOVED'", group_refs=group_refs)
        for group in result["groups"]:
            if criteria is not None:
                group["criteria"] = [row for row in criteria if row["adGroup"] == group["resourceName"]]
            if assets is not None:
                group["group_assets"] = [row for row in assets if row["adGroup"] == group["resourceName"]]
        # Deep reads may reveal additional carousel cards omitted by the base
        # snapshot. Prove the full immutable asset bodies against those refs.
        references = _carousel_references(result)
        if len(references) > 100:
            reader.gap("asset", "google_ads_carousel_asset_limit")
        elif references:
            for reference in references:
                owned_resource(reference, client.customer_id, "assets")
            asset_rows = await reader.read("asset", "asset.resource_name IN (" + ",".join(gaql_string(reference) for reference in sorted(references)) + ")", asset_refs=references)
            if asset_rows is None or {row["resourceName"] for row in asset_rows} != references:
                reader.gap("asset", "google_ads_carousel_assets_incomplete")
            else:
                result["creative_assets"] = asset_rows
                result.pop("creative_asset_gaps", None)
    result["duplicate_control_gaps"] = reader.gaps
    result["duplicate_controls_complete"] = not reader.gaps
