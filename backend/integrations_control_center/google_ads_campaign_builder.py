"""Pure v25 GoogleAdsService mutation builder for governed Mezan actions.

The caller must use one GoogleAdsService.Mutate request with partialFailure=false,
first with validateOnly=true, then after approval with validateOnly=false. The
builder neither authenticates nor writes, and provider eligibility/image metadata
checks remain mandatory. All new campaigns are PAUSED; children are ENABLED so
a separately approved campaign enable can activate the verified hierarchy.

References checked 2026-10-13: Google Developers /google-ads/api/docs/{
responsive-search-ads/create-responsive-search-ads,dynamic-search-ads/
create-dynamic-search-ads,responsive-display-ads/create-responsive-display-ads,
display-upload-ads/create-display-upload-ad,shopping-ads/create-campaign,
demand-gen/create-campaign,demand-gen/product-ads,app-campaigns/create-campaign,
app-campaigns/create-ad-group,hotel-ads/create-campaign,hotel-ads/
create-listing-groups,campaigns/budgets/create-budgets} and /performance-max/{
create-campaign,asset-requirements,listing-groups}, plus v25 RPC definitions.
"""
from __future__ import annotations

from decimal import Decimal
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from .google_ads_campaign_contract import (
    Digits, NativeAmount, ShortName, GoogleBiddingInput, GoogleCampaignDraft,
    GoogleCampaignGroupInput, GoogleConversionGoalInput, GoogleKeywordInput, SUPPORTED_FORMATS, SUPPORTED_STRATEGIES,
    TOTAL_BUDGET_TYPES, UNSUPPORTED_CREATE, campaign_capabilities,
    owned_resource, positive_amount, validate_group_references,
)


class GoogleCampaignBuildError(ValueError):
    def __init__(self, code: str, message: str, *, details: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []


def native_to_micros(value: str) -> str:
    """Exact int64 JSON string; never round a float or silently truncate money."""
    valid = TypeAdapter(NativeAmount).validate_python(value)
    positive_amount(valid)
    return str(int(Decimal(valid) * 1_000_000))


def _customer_id(value: str) -> str:
    valid = TypeAdapter(Digits).validate_python(value)
    if not int(valid):
        raise ValueError("Google customer id must be positive")
    return valid


def _draft(value: GoogleCampaignDraft | dict[str, Any], customer_id: str | None = None) -> GoogleCampaignDraft:
    # Revalidate nested mutable lists and model_copy/model_construct inputs too.
    result = GoogleCampaignDraft.model_validate(value.model_dump(mode="json") if isinstance(value, GoogleCampaignDraft) else value)
    if customer_id is not None and result.account_id != _customer_id(customer_id):
        raise GoogleCampaignBuildError("google_campaign_account_mismatch", "The draft belongs to another Google customer.")
    return result


def _format(campaign_type: str, group: GoogleCampaignGroupInput) -> str:
    return SUPPORTED_FORMATS.get(campaign_type, ("AUTO",))[0] if group.ad_format == "AUTO" else group.ad_format


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in text)


def _app_goal(draft: GoogleCampaignDraft) -> str:
    assert draft.app is not None
    if draft.app.goal_type:
        return draft.app.goal_type
    if draft.app.subtype == "PRE_REGISTRATION":
        return "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME"
    if draft.app.subtype == "ENGAGEMENT":
        return "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST"
    if draft.bidding.strategy == "TARGET_ROAS":
        return "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND"
    return "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"


def validate_draft_readiness(value: GoogleCampaignDraft | dict[str, Any], customer_id: str | None = None) -> list[dict[str, str]]:
    """Return editable, field-specific gaps. [] is not a provider acceptance claim."""
    draft = _draft(value, customer_id)
    issues: list[dict[str, str]] = []

    def gap(field: str, message: str, code: str = "required_input") -> None:
        issues.append({"code": code, "field": field, "message": message})

    kind = draft.campaign_type
    if kind in UNSUPPORTED_CREATE:
        gap("campaign_type", UNSUPPORTED_CREATE[kind], "provider_creation_unsupported")
        return issues
    if draft.bidding.strategy not in SUPPORTED_STRATEGIES[kind]:
        gap("bidding.strategy", f"{draft.bidding.strategy} is not implemented for {kind}.", "unsupported_bidding_strategy")
    if draft.bidding.strategy == "TARGET_CPA" and draft.bidding.target_cpa_native is None:
        gap("bidding.target_cpa_native", "Target CPA requires a positive target in the account currency.")
    if draft.bidding.strategy == "TARGET_ROAS" and draft.bidding.target_roas is None:
        gap("bidding.target_roas", "Target ROAS requires a positive ratio; 3 means 300%.")
    if draft.budget_mode == "TOTAL":
        if kind not in TOTAL_BUDGET_TYPES:
            gap("budget_mode", "Total budgets are implemented only for Search, Shopping, Performance Max and Demand Gen.", "unsupported_budget_mode")
        if not draft.start_date or not draft.end_date:
            gap("start_date", "A campaign total budget requires both start_date and end_date.")
    if not draft.groups:
        gap("groups", "At least one complete ad group or asset group is required.")
    if not draft.geo_target_ids:
        gap("geo_target_ids", "Choose at least one provider geographic target; empty targeting must not silently become worldwide.")
    if not draft.language_ids and kind != "HOTEL":
        gap("language_ids", "Choose at least one provider language target.")
    for field, allowed in (("shopping", {"SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN"}), ("app", {"APP"}), ("hotel", {"HOTEL"}), ("search", {"SEARCH"}), ("ai_max", {"SEARCH"})):
        if getattr(draft, field) is not None and kind not in allowed:
            gap(field, f"{field} settings do not apply to {kind}.", "incompatible_input")
    if kind == "SHOPPING" and not draft.shopping:
        gap("shopping", "A linked Merchant Center account is required.")
    if kind == "HOTEL" and not draft.hotel:
        gap("hotel", "An allowlisted account with a linked Hotel Center and valid feed/pricing is required.")
    if kind == "APP":
        if draft.conversion_goals:
            gap("conversion_goals", "App campaigns use selective optimization; campaign conversion-goal overrides are a separate non-App contract.", "incompatible_input")
        if not draft.app:
            gap("app", "Select the app, store, subtype and conversion settings.")
        else:
            goal = _app_goal(draft)
            needs_conversions = draft.app.subtype == "ENGAGEMENT" or goal in {
                "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_INSTALL_COST", "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
            }
            if needs_conversions and not draft.app.conversion_action_ids:
                gap("app.conversion_action_ids", "This App goal requires existing eligible conversion actions; value bidding requires Firebase conversion actions.")
            if draft.app.subtype == "PRE_REGISTRATION":
                if draft.app.conversion_action_ids:
                    gap("app.conversion_action_ids", "Pre-registration conversion actions are managed automatically by Google.", "incompatible_input")
                if goal != "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME" or draft.bidding.strategy != "TARGET_CPA":
                    gap("app.goal_type", "Pre-registration requires its pre-registration volume goal and Target CPA.", "incompatible_input")
            elif draft.app.subtype == "ENGAGEMENT" and (goal != "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST" or draft.bidding.strategy != "TARGET_CPA"):
                gap("app.goal_type", "App engagement requires the in-app conversion cost goal and Target CPA.", "incompatible_input")
            elif draft.app.subtype == "INSTALLS":
                if goal == "OPTIMIZE_PRE_REGISTRATION_CONVERSION_VOLUME":
                    gap("app.goal_type", "Pre-registration is a separate App subtype.", "incompatible_input")
                if (goal == "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND") != (draft.bidding.strategy == "TARGET_ROAS"):
                    gap("app.goal_type", "The return-on-ad-spend App goal and Target ROAS must be selected together.", "incompatible_input")
    if draft.search and draft.search.use_supplied_urls_only:
        gap("search.use_supplied_urls_only", "Restricting DSA to a page feed requires a linked PAGE_FEED AssetSet; this specialized link is not implemented.", "prerequisite_not_implemented")
    if draft.conversion_goals and not any(goal.biddable for goal in draft.conversion_goals):
        gap("conversion_goals", "Choose at least one existing biddable conversion goal.")
    if kind == "PERFORMANCE_MAX" and draft.brand_guidelines_enabled and draft.groups:
        brand = draft.groups[0]
        if any(group.business_name != brand.business_name or group.logo_assets != brand.logo_assets or group.landscape_logo_assets != brand.landscape_logo_assets for group in draft.groups):
            gap("groups", "Brand guidelines require one consistent campaign-level business name and logo set.", "incompatible_brand_assets")
        if len(brand.logo_assets) + len(brand.landscape_logo_assets) > 5:
            gap("groups.0.logo_assets", "Campaign brand guidelines allow at most five logo and landscape-logo assets combined.")

    for index, group in enumerate(draft.groups):
        path = f"groups.{index}"
        fmt = _format(kind, group)
        if fmt not in SUPPORTED_FORMATS[kind]:
            gap(f"{path}.ad_format", f"{fmt} is not an implemented ad format for {kind}.", "unsupported_ad_format")
            continue

        def count(field: str, minimum: int, maximum: int) -> None:
            number = len(getattr(group, field))
            if not minimum <= number <= maximum:
                gap(f"{path}.{field}", f"{fmt} requires {minimum} to {maximum} {field.replace('_', ' ')}.")

        def width(field: str, maximum: int) -> None:
            for position, text in enumerate(getattr(group, field)):
                if _display_width(text) > maximum:
                    gap(f"{path}.{field}.{position}", f"Maximum display width is {maximum}; wide CJK characters count twice.", "creative_text_too_long")

        if draft.bidding.strategy == "MANUAL_CPC" and not (group.cpc_bid_native or draft.bidding.max_cpc_native):
            gap(f"{path}.cpc_bid_native", "Manual CPC requires an ad-group CPC bid or the campaign draft default max_cpc_native.")
        if group.cpc_bid_native is not None and draft.bidding.strategy != "MANUAL_CPC":
            gap(f"{path}.cpc_bid_native", "Ad-group CPC is only implemented for Manual CPC.", "incompatible_input")
        if group.percent_cpc_bid is not None and not (kind == "HOTEL" and draft.bidding.strategy == "PERCENT_CPC"):
            gap(f"{path}.percent_cpc_bid", "Fractional Percent CPC bids apply only to a Percent CPC Hotel campaign.", "incompatible_input")
        if group.keywords and kind != "SEARCH":
            gap(f"{path}.keywords", "Keyword creation is implemented for Search ad groups only.", "incompatible_input")
        if group.audience_ids and kind not in {"PERFORMANCE_MAX", "DEMAND_GEN", "APP"}:
            gap(f"{path}.audience_ids", "Reusable Audience resources apply to Performance Max, Demand Gen and App; use user-list segments for Search or Display.", "incompatible_input")
        if group.user_list_ids and not (kind in {"SEARCH", "DISPLAY"} or kind == "APP" and draft.app and draft.app.subtype == "ENGAGEMENT"):
            gap(f"{path}.user_list_ids", "User-list segment criteria are not implemented for this campaign subtype.", "incompatible_input")
        if group.product_offer_ids and not (draft.shopping and kind in {"SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN"}):
            gap(f"{path}.product_offer_ids", "Product offer filters require a Merchant Center product-feed campaign.", "incompatible_input")
        if group.hotel_ids and kind != "HOTEL":
            gap(f"{path}.hotel_ids", "Hotel listing identifiers apply only to Hotel campaigns.", "incompatible_input")
        if group.channels and kind != "DEMAND_GEN":
            gap(f"{path}.channels", "Channel controls are implemented for Demand Gen only.", "incompatible_input")
        if group.search_themes and kind != "PERFORMANCE_MAX":
            gap(f"{path}.search_themes", "Search-theme signals are supported only in Performance Max asset groups.", "incompatible_input")
        if any(not theme.strip() or any(ord(character) < 32 for character in theme) for theme in group.search_themes):
            gap(f"{path}.search_themes", "Search themes must be nonblank without control characters.")
        if group.disable_ai_max_search_term_matching and not (kind == "SEARCH" and draft.ai_max and draft.ai_max.enabled):
            gap(f"{path}.disable_ai_max_search_term_matching", "AI Max search term matching controls require an AI Max Search campaign.", "incompatible_input")
        if group.carousel_cards and not (kind == "DEMAND_GEN" and fmt == "CAROUSEL"):
            gap(f"{path}.carousel_cards", "Carousel cards apply only to Demand Gen carousel ads.", "incompatible_input")
        if group.media_bundle_asset and not (kind == "DISPLAY" and fmt == "UPLOADED_HTML5"):
            gap(f"{path}.media_bundle_asset", "An uploaded media bundle applies only to an uploaded HTML5 Display ad.", "incompatible_input")

        if fmt == "RSA":
            count("headlines", 3, 15); count("descriptions", 2, 4)
            width("headlines", 30); width("descriptions", 90)
            if not group.final_urls:
                gap(f"{path}.final_urls", "An RSA requires a landing URL.")
            if not any(not keyword.negative for keyword in group.keywords) and not (draft.ai_max and draft.ai_max.enabled):
                gap(f"{path}.keywords", "Add a positive Search keyword or explicitly enable AI Max search term matching.")
        elif fmt == "DSA":
            count("descriptions", 1, 2); width("descriptions", 90)
            if group.headlines or group.long_headlines:
                gap(f"{path}.headlines", "DSA headlines are generated by Google and cannot be supplied.", "incompatible_input")
            if not draft.search or not draft.search.dynamic_search_domain or not draft.search.dynamic_search_language_code:
                gap("search.dynamic_search_domain", "DSA requires the campaign website domain and language code.")
            if draft.ai_max and draft.ai_max.enabled:
                gap("ai_max", "Combined AI Max and legacy DSA groups are not implemented as a verified creation path.", "unsupported_combination")
            if any(not keyword.negative for keyword in group.keywords):
                gap(f"{path}.keywords", "DSA uses webpage targets; positive keyword targets belong in an RSA group.", "incompatible_input")
            if draft.search:
                for position, url in enumerate(group.final_urls):
                    if urlsplit(url).hostname != draft.search.dynamic_search_domain:
                        gap(f"{path}.final_urls.{position}", "A DSA webpage URL must be within the configured website domain.")
        elif kind == "DISPLAY":
            if not group.final_urls:
                gap(f"{path}.final_urls", "A Display ad requires a landing URL.")
            if fmt == "RESPONSIVE_DISPLAY":
                count("headlines", 1, 5); count("descriptions", 1, 5); count("long_headlines", 1, 1)
                width("headlines", 30); width("long_headlines", 90); width("descriptions", 90)
                count("image_assets", 1, 15); count("square_image_assets", 1, 15)
                if len(group.image_assets) + len(group.square_image_assets) > 15:
                    gap(f"{path}.image_assets", "Responsive Display allows at most 15 marketing images across both aspect ratios.")
                if not group.business_name:
                    gap(f"{path}.business_name", "Responsive Display requires the advertiser business name.")
                if len(group.logo_assets) + len(group.landscape_logo_assets) > 5:
                    gap(f"{path}.logo_assets", "Responsive Display allows at most five logo assets combined.")
                if len(group.video_assets) > 5:
                    gap(f"{path}.video_assets", "Responsive Display allows at most five YouTube videos.")
            elif fmt == "UPLOADED_IMAGE":
                count("image_assets", 1, 1)
            elif fmt == "UPLOADED_HTML5" and not group.media_bundle_asset:
                gap(f"{path}.media_bundle_asset", "An existing HTML5 MEDIA_BUNDLE asset and account eligibility are required.")
        elif kind == "PERFORMANCE_MAX":
            # A rich complete asset group is required even for retail; no empty feed container.
            count("headlines", 3, 15); count("descriptions", 2, 5); count("long_headlines", 1, 5)
            width("headlines", 30); width("long_headlines", 90); width("descriptions", 90)
            count("image_assets", 1, 20); count("square_image_assets", 1, 20); count("logo_assets", 1, 5)
            if not group.business_name:
                gap(f"{path}.business_name", "Performance Max requires one advertiser business name.")
            if not group.final_urls:
                gap(f"{path}.final_urls", "Performance Max requires an asset-group landing URL.")
        elif kind == "DEMAND_GEN":
            if not group.final_urls:
                gap(f"{path}.final_urls", "A Demand Gen ad requires a landing URL.")
            if not group.business_name:
                gap(f"{path}.business_name", "Demand Gen requires the advertiser business name.")
            count("logo_assets", 1, 5)
            width("descriptions", 90)
            if fmt == "MULTI_ASSET":
                count("headlines", 1, 5); count("descriptions", 1, 5); width("headlines", 30)
                images = len(group.image_assets) + len(group.square_image_assets) + len(group.portrait_image_assets) + len(group.tall_portrait_image_assets)
                if not (group.image_assets or group.square_image_assets) or images > 20:
                    gap(f"{path}.image_assets", "Demand Gen needs a landscape or square image, with at most 20 marketing images combined.")
            elif fmt == "VIDEO":
                count("headlines", 1, 5); count("descriptions", 1, 5); count("long_headlines", 1, 5)
                width("headlines", 40); width("long_headlines", 90); count("video_assets", 1, 5)
                if len(group.image_assets) > 1:
                    gap(f"{path}.image_assets", "Demand Gen video accepts at most one optional companion banner.")
            elif fmt in {"PRODUCT", "CAROUSEL"}:
                count("headlines", 1, 1); count("descriptions", 1, 1); count("logo_assets", 1, 1); width("headlines", 40)
                if fmt == "PRODUCT" and not draft.shopping:
                    gap("shopping", "A Demand Gen product ad requires a linked Merchant Center account.")
                if fmt == "CAROUSEL":
                    if draft.shopping:
                        gap("shopping", "Demand Gen carousel is not a supported product-feed ad type.", "incompatible_input")
                    count("carousel_cards", 2, 10)
                    for position, card in enumerate(group.carousel_cards):
                        if not card.image_asset and not card.square_image_asset:
                            gap(f"{path}.carousel_cards.{position}", "Each carousel card needs an existing landscape or square image.")
                        if _display_width(card.headline) > 40:
                            gap(f"{path}.carousel_cards.{position}.headline", "Carousel card headline maximum display width is 40.")
        elif kind == "APP":
            count("headlines", 1, 5); count("descriptions", 1, 5); width("headlines", 30); width("descriptions", 90)
            if draft.app and draft.app.subtype == "ENGAGEMENT":
                if not group.user_list_ids:
                    gap(f"{path}.user_list_ids", "App engagement requires an eligible existing app-user remarketing list.")
                if not group.final_urls:
                    gap(f"{path}.final_urls", "App engagement requires an http(s) universal/app link for the in-app destination.")
                if group.audience_ids:
                    gap(f"{path}.audience_ids", "App engagement uses user-list criteria rather than grouped audience signals.", "incompatible_input")
            elif draft.app and draft.app.subtype == "PRE_REGISTRATION" and group.audience_ids:
                gap(f"{path}.audience_ids", "Audience signals are implemented for App installs only.", "incompatible_input")
            if draft.app and draft.app.subtype != "ENGAGEMENT" and group.final_urls:
                gap(f"{path}.final_urls", "App installs and pre-registration use the selected app's store destination; custom final URLs apply only to App engagement.", "incompatible_input")
        elif kind == "HOTEL" and draft.bidding.strategy == "PERCENT_CPC" and not group.percent_cpc_bid:
            gap(f"{path}.percent_cpc_bid", "A Percent CPC Hotel ad group requires a fractional price bid, for example 0.1 for 10%.")

        # Reject creative inputs that this format would otherwise silently discard.
        if kind == "SHOPPING" or kind == "HOTEL":
            for field in ("headlines", "descriptions", "long_headlines", "image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets", "final_urls", "business_name"):
                if getattr(group, field):
                    gap(f"{path}.{field}", "Product/Hotel ads derive creative and destinations from the approved feed.", "incompatible_input")
        if kind == "SEARCH":
            for field in ("portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets", "business_name", "long_headlines"):
                if getattr(group, field) and not (fmt == "DSA" and field == "long_headlines"):
                    gap(f"{path}.{field}", "This creative field is not used by the implemented Search format.", "incompatible_input")
        if kind == "DISPLAY" and (group.portrait_image_assets or group.tall_portrait_image_assets):
            gap(f"{path}.portrait_image_assets", "Responsive Display does not use these Demand Gen portrait fields.", "incompatible_input")
        if kind == "APP":
            for field in ("long_headlines", "logo_assets", "landscape_logo_assets", "tall_portrait_image_assets", "business_name"):
                if getattr(group, field):
                    gap(f"{path}.{field}", "This field is not an App ad creative field.", "incompatible_input")
            if len(group.image_assets) + len(group.square_image_assets) + len(group.portrait_image_assets) > 20:
                gap(f"{path}.image_assets", "App ads allow at most 20 image assets combined.")
            if len(group.video_assets) > 20:
                gap(f"{path}.video_assets", "App ads allow at most 20 YouTube video assets.")
        if group.business_name and _display_width(group.business_name) > 25:
            gap(f"{path}.business_name", "Business name maximum display width is 25.", "creative_text_too_long")
        if kind == "PERFORMANCE_MAX" and group.tall_portrait_image_assets:
            gap(f"{path}.tall_portrait_image_assets", "Tall portrait images are a Demand Gen field, not a supported Performance Max asset-group field.", "incompatible_input")
        unused: set[str] = set()
        creative_fields = {"headlines", "descriptions", "long_headlines", "business_name", "image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets", "landscape_logo_assets", "video_assets"}
        if kind == "DISPLAY" and fmt == "UPLOADED_IMAGE":
            unused = creative_fields - {"image_assets"}
        elif kind == "DISPLAY" and fmt == "UPLOADED_HTML5":
            unused = creative_fields
        elif kind == "DEMAND_GEN":
            allowed = {
                "MULTI_ASSET": {"headlines", "descriptions", "business_name", "image_assets", "square_image_assets", "portrait_image_assets", "tall_portrait_image_assets", "logo_assets"},
                "VIDEO": {"headlines", "descriptions", "long_headlines", "business_name", "logo_assets", "video_assets", "image_assets"},
                "PRODUCT": {"headlines", "descriptions", "business_name", "logo_assets"},
                "CAROUSEL": {"headlines", "descriptions", "business_name", "logo_assets"},
            }[fmt]
            unused = creative_fields - allowed
        for field in sorted(unused):
            if getattr(group, field):
                gap(f"{path}.{field}", f"This creative field is not used by {fmt}; choose its correct ad format.", "incompatible_input")
    return issues


class _Operations:
    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        self.next_id = -1
        self.items: list[dict[str, Any]] = []

    def resource(self, collection: str, parent: str | None = None) -> str:
        identifier = self.next_id
        self.next_id -= 1
        suffix = str(identifier) if parent is None else f"{parent.rsplit('/', 1)[-1]}~{identifier}"
        return f"customers/{self.customer_id}/{collection}/{suffix}"

    def create(self, operation: str, fields: dict[str, Any]) -> None:
        self.items.append({operation: {"create": fields}})

    def text_asset(self, text: str) -> str:
        resource = self.resource("assets")
        self.create("assetOperation", {"resourceName": resource, "textAsset": {"text": text}})
        return resource


def _bidding_fields(bidding: GoogleBiddingInput, campaign_type: str) -> dict[str, Any]:
    strategy = bidding.strategy
    if strategy == "MANUAL_CPC":
        return {"manualCpc": {}}
    if strategy == "PERCENT_CPC":
        return {"percentCpc": {"cpcBidCeilingMicros": native_to_micros(bidding.max_cpc_native)} if bidding.max_cpc_native else {}}
    if strategy == "MAXIMIZE_CLICKS":
        return {"targetSpend": {"cpcBidCeilingMicros": native_to_micros(bidding.max_cpc_native)} if bidding.max_cpc_native else {}}
    if strategy == "MAXIMIZE_CONVERSIONS" or strategy == "TARGET_CPA" and campaign_type in {"SEARCH", "PERFORMANCE_MAX"}:
        return {"maximizeConversions": {"targetCpaMicros": native_to_micros(bidding.target_cpa_native)} if bidding.target_cpa_native else {}}
    if strategy == "MAXIMIZE_CONVERSION_VALUE" or strategy == "TARGET_ROAS" and campaign_type in {"SEARCH", "PERFORMANCE_MAX"}:
        return {"maximizeConversionValue": {"targetRoas": float(Decimal(bidding.target_roas))} if bidding.target_roas else {}}
    if strategy == "TARGET_CPA":
        return {"targetCpa": {"targetCpaMicros": native_to_micros(bidding.target_cpa_native)}}
    if strategy == "TARGET_ROAS":
        return {"targetRoas": {"targetRoas": float(Decimal(bidding.target_roas))}}
    raise GoogleCampaignBuildError("google_bidding_not_supported", "The bidding strategy is not implemented.")


def _text(values: list[str]) -> list[dict[str, str]]:
    return [{"text": value} for value in values]


def _assets(values: list[str]) -> list[dict[str, str]]:
    return [{"asset": value} for value in values]


def _targeting(operations: _Operations, parent: str, geo_ids: list[str], language_ids: list[str], *, group_level: bool = False) -> None:
    operation = "adGroupCriterionOperation" if group_level else "campaignCriterionOperation"
    parent_key = "adGroup" if group_level else "campaign"
    for identifier in geo_ids:
        operations.create(operation, {parent_key: parent, "negative": False, "location": {"geoTargetConstant": f"geoTargetConstants/{identifier}"}})
    for identifier in language_ids:
        operations.create(operation, {parent_key: parent, "negative": False, "language": {"languageConstant": f"languageConstants/{identifier}"}})


def _listing_tree(operations: _Operations, parent: str, item_ids: list[str], *, pmax: bool = False, hotel: bool = False, cpc_bid_native: str | None = None, percent_cpc_bid: str | None = None) -> None:
    """Complete root/units/Other tree, excluding feed items not selected."""
    operation = "assetGroupListingGroupFilterOperation" if pmax else "adGroupCriterionOperation"
    collection = "assetGroupListingGroupFilters" if pmax else "adGroupCriteria"
    dimension_key = "hotelId" if hotel else "productItemId"
    root = operations.resource(collection, parent)

    def node(resource: str, node_type: str, *, parent_node: str | None = None, item: str | None = None, other: bool = False, excluded: bool = False) -> None:
        dimension = {dimension_key: {} if other else {"value": item}} if parent_node else None
        if pmax:
            fields: dict[str, Any] = {"resourceName": resource, "assetGroup": parent, "type": node_type, "listingSource": "SHOPPING"}
            if parent_node:
                fields["parentListingGroupFilter"] = parent_node
                fields["caseValue"] = dimension
        else:
            listing: dict[str, Any] = {"type": node_type}
            if parent_node:
                listing["parentAdGroupCriterion"] = parent_node
                listing["caseValue"] = dimension
            fields = {"resourceName": resource, "adGroup": parent, "negative": excluded, "listingGroup": listing}
            if node_type == "UNIT" and not excluded:
                if percent_cpc_bid:
                    fields["percentCpcBidMicros"] = native_to_micros(percent_cpc_bid)
                elif cpc_bid_native:
                    fields["cpcBidMicros"] = native_to_micros(cpc_bid_native)
        operations.create(operation, fields)

    if not item_ids:
        node(root, "UNIT_INCLUDED" if pmax else "UNIT")
        return
    node(root, "SUBDIVISION")
    for item in item_ids:
        node(operations.resource(collection, parent), "UNIT_INCLUDED" if pmax else "UNIT", parent_node=root, item=item)
    node(operations.resource(collection, parent), "UNIT_EXCLUDED" if pmax else "UNIT", parent_node=root, other=True, excluded=True)


def _ad(operations: _Operations, draft: GoogleCampaignDraft, group: GoogleCampaignGroupInput, ad_group: str, *, status: str = "ENABLED") -> None:
    fmt = _format(draft.campaign_type, group)
    ad: dict[str, Any] = {}
    if group.final_urls and fmt != "DSA" and draft.campaign_type != "APP":
        ad["finalUrls"] = list(group.final_urls)
    if fmt == "RSA":
        ad["responsiveSearchAd"] = {"headlines": _text(group.headlines), "descriptions": _text(group.descriptions)}
    elif fmt == "DSA":
        ad["expandedDynamicSearchAd"] = {"description": group.descriptions[0]}
        if len(group.descriptions) > 1:
            ad["expandedDynamicSearchAd"]["description2"] = group.descriptions[1]
    elif fmt == "RESPONSIVE_DISPLAY":
        content: dict[str, Any] = {
            "headlines": _text(group.headlines), "longHeadline": {"text": group.long_headlines[0]},
            "descriptions": _text(group.descriptions), "businessName": group.business_name,
            "marketingImages": _assets(group.image_assets), "squareMarketingImages": _assets(group.square_image_assets),
        }
        for field, values in (("squareLogoImages", group.logo_assets), ("logoImages", group.landscape_logo_assets), ("youtubeVideos", group.video_assets)):
            if values:
                content[field] = _assets(values)
        ad["responsiveDisplayAd"] = content
    elif fmt == "UPLOADED_IMAGE":
        ad.update({"name": group.name, "imageAd": {"imageAsset": {"asset": group.image_assets[0]}}})
    elif fmt == "UPLOADED_HTML5":
        ad.update({"name": group.name, "displayUploadAd": {"displayUploadProductType": "HTML5_UPLOAD_AD", "mediaBundle": {"asset": group.media_bundle_asset}}})
    elif draft.campaign_type == "SHOPPING":
        ad["shoppingProductAd"] = {}
    elif draft.campaign_type == "HOTEL":
        ad["hotelAd"] = {}
    elif draft.campaign_type == "DEMAND_GEN":
        ad["name"] = group.name
        if fmt == "MULTI_ASSET":
            content = {"headlines": _text(group.headlines), "descriptions": _text(group.descriptions), "businessName": group.business_name, "logoImages": _assets(group.logo_assets)}
            for field, values in (("marketingImages", group.image_assets), ("squareMarketingImages", group.square_image_assets), ("portraitMarketingImages", group.portrait_image_assets), ("tallPortraitMarketingImages", group.tall_portrait_image_assets)):
                if values:
                    content[field] = _assets(values)
            ad["demandGenMultiAssetAd"] = content
        elif fmt == "VIDEO":
            ad["demandGenVideoResponsiveAd"] = {
                "headlines": _text(group.headlines), "descriptions": _text(group.descriptions), "longHeadlines": _text(group.long_headlines),
                "businessName": {"text": group.business_name}, "logoImages": _assets(group.logo_assets), "videos": _assets(group.video_assets),
            }
            if group.image_assets:
                ad["demandGenVideoResponsiveAd"]["companionBanners"] = _assets(group.image_assets)
        elif fmt == "PRODUCT":
            ad["demandGenProductAd"] = {
                "headline": {"text": group.headlines[0]}, "description": {"text": group.descriptions[0]},
                "businessName": {"text": group.business_name}, "logoImage": {"asset": group.logo_assets[0]},
            }
        elif fmt == "CAROUSEL":
            cards = []
            for card in group.carousel_cards:
                resource = operations.resource("assets")
                card_asset: dict[str, Any] = {"headline": card.headline}
                for field, ref in (("marketingImageAsset", card.image_asset), ("squareMarketingImageAsset", card.square_image_asset), ("portraitMarketingImageAsset", card.portrait_image_asset)):
                    if ref:
                        card_asset[field] = ref
                operations.create("assetOperation", {"resourceName": resource, "finalUrls": [card.final_url], "demandGenCarouselCardAsset": card_asset})
                cards.append({"asset": resource})
            ad["demandGenCarouselAd"] = {
                "headline": {"text": group.headlines[0]}, "description": {"text": group.descriptions[0]},
                "businessName": group.business_name, "logoImage": {"asset": group.logo_assets[0]}, "carouselCards": cards,
            }
    elif draft.campaign_type == "APP":
        assert draft.app is not None
        content = {"headlines": _text(group.headlines), "descriptions": _text(group.descriptions)}
        images = group.image_assets + group.square_image_assets + group.portrait_image_assets
        if images:
            content["images"] = _assets(images)
        if group.video_assets:
            content["videos" if draft.app.subtype == "ENGAGEMENT" else "youtubeVideos"] = _assets(group.video_assets)
        if draft.app.subtype == "ENGAGEMENT":
            ad["finalAppUrls"] = [{"osType": "ANDROID" if draft.app.app_store == "GOOGLE_APP_STORE" else "IOS", "url": url} for url in group.final_urls]
        field = {"INSTALLS": "appAd", "ENGAGEMENT": "appEngagementAd", "PRE_REGISTRATION": "appPreRegistrationAd"}[draft.app.subtype]
        ad[field] = content
    operations.create("adGroupAdOperation", {"adGroup": ad_group, "status": status, "ad": ad})


def _pmax_group(operations: _Operations, draft: GoogleCampaignDraft, group: GoogleCampaignGroupInput, campaign: str) -> None:
    resource = operations.resource("assetGroups")
    operations.create("assetGroupOperation", {"resourceName": resource, "name": group.name, "campaign": campaign, "status": "ENABLED", "finalUrls": list(group.final_urls)})
    for field_type, texts in (("HEADLINE", group.headlines), ("LONG_HEADLINE", group.long_headlines), ("DESCRIPTION", group.descriptions)):
        for text in texts:
            asset = operations.text_asset(text)
            operations.create("assetGroupAssetOperation", {"assetGroup": resource, "asset": asset, "fieldType": field_type})
    for field_type, assets in (("MARKETING_IMAGE", group.image_assets), ("SQUARE_MARKETING_IMAGE", group.square_image_assets), ("PORTRAIT_MARKETING_IMAGE", group.portrait_image_assets), ("YOUTUBE_VIDEO", group.video_assets)):
        for asset in assets:
            operations.create("assetGroupAssetOperation", {"assetGroup": resource, "asset": asset, "fieldType": field_type})
    if not draft.brand_guidelines_enabled:
        asset = operations.text_asset(group.business_name)
        operations.create("assetGroupAssetOperation", {"assetGroup": resource, "asset": asset, "fieldType": "BUSINESS_NAME"})
        for field_type, assets in (("LOGO", group.logo_assets), ("LANDSCAPE_LOGO", group.landscape_logo_assets)):
            for asset in assets:
                operations.create("assetGroupAssetOperation", {"assetGroup": resource, "asset": asset, "fieldType": field_type})
    for audience in group.audience_ids:
        operations.create("assetGroupSignalOperation", {"assetGroup": resource, "audience": {"audience": owned_resource(audience, draft.account_id, "audiences", allow_id=True)}})
    for theme in group.search_themes:
        operations.create("assetGroupSignalOperation", {"assetGroup": resource, "searchTheme": {"text": theme}})
    if draft.shopping:
        _listing_tree(operations, resource, group.product_offer_ids, pmax=True)


def _ordinary_group(operations: _Operations, draft: GoogleCampaignDraft, group: GoogleCampaignGroupInput, campaign: str) -> None:
    kind = draft.campaign_type
    customer_id = operations.customer_id
    ad_group = operations.resource("adGroups")
    fields: dict[str, Any] = {"resourceName": ad_group, "name": group.name, "campaign": campaign, "status": "ENABLED"}
    fmt = _format(kind, group)
    if kind in {"SEARCH", "DISPLAY", "SHOPPING", "HOTEL"}:
        fields["type"] = {"SEARCH": "SEARCH_DYNAMIC_ADS" if fmt == "DSA" else "SEARCH_STANDARD", "DISPLAY": "DISPLAY_STANDARD", "SHOPPING": "SHOPPING_PRODUCT_ADS", "HOTEL": "HOTEL_ADS"}[kind]
    default_cpc = (group.cpc_bid_native or draft.bidding.max_cpc_native) if draft.bidding.strategy == "MANUAL_CPC" else None
    if default_cpc:
        fields["cpcBidMicros"] = native_to_micros(default_cpc)
    if kind == "HOTEL" and group.percent_cpc_bid:
        fields["percentCpcBidMicros"] = native_to_micros(group.percent_cpc_bid)
    if group.audience_ids:
        fields["audienceSetting"] = {"useAudienceGrouped": True}
    if kind == "DEMAND_GEN" and group.channels:
        fields["demandGenAdGroupSettings"] = {"channelControls": {"selectedChannels": {
            "youtubeInStream": "YOUTUBE_IN_STREAM" in group.channels, "youtubeInFeed": "YOUTUBE_IN_FEED" in group.channels,
            "youtubeShorts": "YOUTUBE_SHORTS" in group.channels, "discover": "DISCOVER" in group.channels,
            "gmail": "GMAIL" in group.channels, "display": "DISPLAY" in group.channels,
        }}}
    if group.disable_ai_max_search_term_matching:
        fields["aiMaxAdGroupSetting"] = {"disableSearchTermMatching": True}
    operations.create("adGroupOperation", fields)
    if kind == "DEMAND_GEN":
        _targeting(operations, ad_group, draft.geo_target_ids, draft.language_ids, group_level=True)
    for keyword in group.keywords:
        criterion = {"adGroup": ad_group, "negative": keyword.negative, "keyword": {"text": keyword.text, "matchType": keyword.match_type}}
        if not keyword.negative:
            criterion["status"] = "ENABLED"
        operations.create("adGroupCriterionOperation", criterion)
    for ref in group.audience_ids:
        operations.create("adGroupCriterionOperation", {"adGroup": ad_group, "negative": False, "audience": {"audience": owned_resource(ref, customer_id, "audiences", allow_id=True)}})
    for ref in group.user_list_ids:
        operations.create("adGroupCriterionOperation", {"adGroup": ad_group, "negative": False, "userList": {"userList": owned_resource(ref, customer_id, "userLists", allow_id=True)}})
    if kind == "SEARCH":
        for ref in group.image_assets + group.square_image_assets:
            operations.create("adGroupAssetOperation", {"adGroup": ad_group, "asset": ref, "fieldType": "AD_IMAGE"})
        if fmt == "DSA":
            targets = group.final_urls or [None]
            for index, url in enumerate(targets):
                webpage = {"criterionName": f"{group.name[:100]} page {index + 1}", "conditions": [{"operand": "URL", "operator": "EQUALS", "argument": url}] if url else []}
                operations.create("adGroupCriterionOperation", {"adGroup": ad_group, "negative": False, "webpage": webpage})
    if kind in {"SHOPPING", "HOTEL"} or kind == "DEMAND_GEN" and draft.shopping:
        _listing_tree(operations, ad_group, group.hotel_ids if kind == "HOTEL" else group.product_offer_ids, hotel=kind == "HOTEL", cpc_bid_native=default_cpc, percent_cpc_bid=group.percent_cpc_bid)
    _ad(operations, draft, group, ad_group)


def build_campaign_operations(value: GoogleCampaignDraft | dict[str, Any], customer_id: str) -> list[dict[str, Any]]:
    draft = _draft(value, customer_id)
    gaps = validate_draft_readiness(draft, customer_id)
    if gaps:
        raise GoogleCampaignBuildError("google_campaign_draft_incomplete", "The campaign draft cannot be sent until its requirements are complete.", details=gaps)
    operations = _Operations(customer_id)
    budget = operations.resource("campaignBudgets")
    campaign = operations.resource("campaigns")
    budget_fields = {"resourceName": budget, "deliveryMethod": "STANDARD", "explicitlyShared": False, "period": "CUSTOM_PERIOD" if draft.budget_mode == "TOTAL" else "DAILY"}
    budget_fields["totalAmountMicros" if draft.budget_mode == "TOTAL" else "amountMicros"] = native_to_micros(draft.budget_native)
    operations.create("campaignBudgetOperation", budget_fields)
    kind = draft.campaign_type
    campaign_fields: dict[str, Any] = {
        "resourceName": campaign, "name": draft.name, "status": "PAUSED", "campaignBudget": budget,
        "advertisingChannelType": "MULTI_CHANNEL" if kind == "APP" else kind,
        "containsEuPoliticalAdvertising": "CONTAINS_EU_POLITICAL_ADVERTISING" if draft.eu_political_advertising else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        **_bidding_fields(draft.bidding, kind),
    }
    if draft.start_date:
        campaign_fields["startDateTime"] = f"{draft.start_date} 00:00:00"
    if draft.end_date:
        campaign_fields["endDateTime"] = f"{draft.end_date} 23:59:59"
    if kind == "SEARCH":
        campaign_fields["networkSettings"] = {"targetGoogleSearch": True, "targetSearchNetwork": draft.search.target_search_network if draft.search else True, "targetContentNetwork": draft.search.target_content_network if draft.search else False}
        if any(_format(kind, group) == "DSA" for group in draft.groups):
            campaign_fields["dynamicSearchAdsSetting"] = {"domainName": draft.search.dynamic_search_domain, "languageCode": draft.search.dynamic_search_language_code}
        if draft.ai_max:
            campaign_fields["aiMaxSetting"] = {"enableAiMax": draft.ai_max.enabled}
            campaign_fields["assetAutomationSettings"] = [
                {"assetAutomationType": "TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN" if draft.ai_max.text_customization else "OPTED_OUT"},
                {"assetAutomationType": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN" if draft.ai_max.final_url_expansion else "OPTED_OUT"},
            ]
    if draft.shopping:
        shopping: dict[str, Any] = {"merchantId": draft.shopping.merchant_id}
        if draft.shopping.feed_label:
            shopping["feedLabel"] = draft.shopping.feed_label
        if kind == "SHOPPING":
            shopping.update({"campaignPriority": draft.shopping.campaign_priority, "enableLocal": draft.shopping.enable_local})
        elif draft.shopping.enable_local or draft.shopping.campaign_priority:
            raise GoogleCampaignBuildError("google_shopping_setting_incompatible", "Shopping priority/local product controls are implemented for standard Shopping only.")
        campaign_fields["shoppingSetting"] = shopping
    if kind == "PERFORMANCE_MAX":
        campaign_fields["brandGuidelinesEnabled"] = draft.brand_guidelines_enabled
    if kind == "APP":
        assert draft.app is not None
        campaign_fields["advertisingChannelSubType"] = {"INSTALLS": "APP_CAMPAIGN", "ENGAGEMENT": "APP_CAMPAIGN_FOR_ENGAGEMENT", "PRE_REGISTRATION": "APP_CAMPAIGN_FOR_PRE_REGISTRATION"}[draft.app.subtype]
        campaign_fields["appCampaignSetting"] = {"appId": draft.app.app_id, "appStore": draft.app.app_store, "biddingStrategyGoalType": _app_goal(draft)}
        if draft.app.conversion_action_ids:
            campaign_fields["selectiveOptimization"] = {"conversionActions": [owned_resource(ref, customer_id, "conversionActions", allow_id=True) for ref in draft.app.conversion_action_ids]}
        if draft.app.subtype == "PRE_REGISTRATION":
            campaign_fields["optimizationGoalSetting"] = {"optimizationGoalTypes": ["APP_PRE_REGISTRATION"]}
    if kind == "HOTEL":
        campaign_fields["hotelSetting"] = {"hotelCenterId": draft.hotel.hotel_center_id}
        campaign_fields["networkSettings"] = {"targetGoogleSearch": True}
    operations.create("campaignOperation", campaign_fields)
    for goal in draft.conversion_goals:
        resource = f"customers/{customer_id}/campaignConversionGoals/{campaign.rsplit('/', 1)[-1]}~{goal.category}~{goal.origin}"
        operations.items.append({"campaignConversionGoalOperation": {"update": {"resourceName": resource, "biddable": goal.biddable}, "updateMask": "biddable"}})
    if kind != "DEMAND_GEN":
        _targeting(operations, campaign, draft.geo_target_ids, draft.language_ids)
    if kind == "PERFORMANCE_MAX" and draft.brand_guidelines_enabled:
        brand = draft.groups[0]
        asset = operations.text_asset(brand.business_name)
        operations.create("campaignAssetOperation", {"campaign": campaign, "asset": asset, "fieldType": "BUSINESS_NAME"})
        for field_type, assets in (("LOGO", brand.logo_assets), ("LANDSCAPE_LOGO", brand.landscape_logo_assets)):
            for asset in assets:
                operations.create("campaignAssetOperation", {"campaign": campaign, "asset": asset, "fieldType": field_type})
    for group in draft.groups:
        if kind == "PERFORMANCE_MAX":
            _pmax_group(operations, draft, group, campaign)
        else:
            _ordinary_group(operations, draft, group, campaign)
    # GoogleAdsService has a 10,000 mutate-operation request limit. Do not split
    # a dependent graph: splitting would break atomicity and orphan resources.
    if len(operations.items) > 10_000:
        raise GoogleCampaignBuildError("google_campaign_operation_limit", "The complete campaign exceeds Google's atomic mutation limit; reduce groups or product filters.")
    return operations.items


def _snapshot_campaign(before: dict[str, Any], customer_id: str) -> tuple[dict[str, Any], str]:
    campaign = before.get("campaign")
    if not isinstance(campaign, dict):
        raise GoogleCampaignBuildError("google_campaign_snapshot_required", "A fresh provider campaign snapshot is required.")
    owned_resource(str(campaign.get("resourceName") or ""), customer_id, "campaigns")
    kind = str(campaign.get("advertisingChannelType") or "")
    if kind == "MULTI_CHANNEL" and campaign.get("advertisingChannelSubType") in {"APP_CAMPAIGN", "APP_CAMPAIGN_FOR_ENGAGEMENT", "APP_CAMPAIGN_FOR_PRE_REGISTRATION"}:
        kind = "APP"
    if kind not in SUPPORTED_FORMATS or campaign.get("localServicesCampaignSettings") or before.get("campaign_type") == "LOCAL_SERVICES":
        raise GoogleCampaignBuildError("google_campaign_management_unsupported", "This campaign type or Local Services subtype has no implemented write contract.")
    if campaign.get("hotelPropertyAssetSet") or campaign.get("travelCampaignSettings"):
        raise GoogleCampaignBuildError("google_campaign_management_unsupported", "Performance Max for travel goals requires a specialized contract; ordinary Performance Max operations cannot be used.")
    if campaign.get("status") == "REMOVED":
        raise GoogleCampaignBuildError("google_campaign_removed", "A removed campaign cannot be edited or re-enabled.")
    return campaign, kind


def _change_keys(changes: dict[str, Any], allowed: set[str], required: set[str] | None = None) -> None:
    if not isinstance(changes, dict) or set(changes) - allowed or (required or set()) - set(changes):
        raise GoogleCampaignBuildError("google_action_changes_invalid", "The action has missing or unsupported semantic change fields.")


def _name(value: Any) -> str:
    valid = TypeAdapter(ShortName).validate_python(value)
    if not valid.strip() or any(ord(character) < 32 for character in valid):
        raise ValueError("name must be nonblank without control characters")
    return valid


def _update(operation: str, resource: str, fields: dict[str, Any], mask: list[str]) -> dict[str, Any]:
    return {operation: {"update": {"resourceName": resource, **fields}, "updateMask": ",".join(mask)}}


def _bidding_mask(fields: dict[str, Any]) -> list[str]:
    key, values = next(iter(fields.items()))
    if values:
        return [f"{key}.{field}" for field in values]
    # Field masks for empty messages with mutable subfields must name a
    # subfield, not the parent message (FIELD_HAS_SUBFIELDS in Google's API).
    return [{"manualCpc": "manualCpc.enhancedCpcEnabled", "targetSpend": "targetSpend.cpcBidCeilingMicros", "percentCpc": "percentCpc.cpcBidCeilingMicros", "maximizeConversions": "maximizeConversions.targetCpaMicros", "maximizeConversionValue": "maximizeConversionValue.targetRoas"}[key]]


def _row(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoogleCampaignBuildError("google_child_snapshot_invalid", "Invalid provider child snapshot.")
    for key in keys:
        if isinstance(value.get(key), dict):
            return value[key]
    return value


def _group_snapshot(before: dict[str, Any], customer_id: str, campaign: dict[str, Any], kind: str, group_id: str) -> dict[str, Any]:
    identifier = TypeAdapter(Digits).validate_python(group_id)
    collection = "assetGroups" if kind == "PERFORMANCE_MAX" else "adGroups"
    resource = owned_resource(identifier, customer_id, collection, allow_id=True)
    for source in before.get("groups", []) + before.get("asset_groups", []):
        group = _row(source, "adGroup", "assetGroup")
        if group.get("resourceName") == resource:
            if group.get("campaign") != campaign["resourceName"] or group.get("status") == "REMOVED":
                raise GoogleCampaignBuildError("google_group_campaign_mismatch", "The group does not belong to this campaign or has been removed.")
            # Preserve parent-owned evidence arrays if supplied beside the raw resource.
            return {**group, **{key: source[key] for key in ("criteria", "assets", "signals", "listing_group_filters") if key in source}}
    raise GoogleCampaignBuildError("google_group_snapshot_required", "A fresh child-group snapshot belonging to the selected campaign is required.")


def _ad_snapshot(before: dict[str, Any], customer_id: str, campaign: dict[str, Any], kind: str, resource: Any) -> dict[str, Any]:
    if kind == "PERFORMANCE_MAX":
        raise GoogleCampaignBuildError("google_ad_action_unsupported", "Performance Max uses asset groups, not ad-group ads.")
    reference = owned_resource(str(resource or ""), customer_id, "adGroupAds", composite=True)
    for source in before.get("ads", []):
        ad = _row(source, "adGroupAd")
        if ad.get("resourceName") == reference:
            group_ref = str(ad.get("adGroup") or "")
            owned_resource(group_ref, customer_id, "adGroups")
            group_id = group_ref.rsplit("/", 1)[-1]
            _group_snapshot(before, customer_id, campaign, kind, group_id)
            if reference.rsplit("/", 1)[-1].split("~", 1)[0] != group_id or ad.get("status") == "REMOVED":
                raise GoogleCampaignBuildError("google_ad_group_mismatch", "The ad reference does not belong to this group or has been removed.")
            return ad
    raise GoogleCampaignBuildError("google_ad_snapshot_required", "A fresh ad snapshot belonging to this campaign is required.")


def _native_from_micros(value: Any) -> str:
    if not re.fullmatch(r"[0-9]+", str(value)):
        raise GoogleCampaignBuildError("google_snapshot_money_invalid", "Provider micros are unavailable or invalid.")
    result = format(Decimal(str(value)) / 1_000_000, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _snapshot_bidding(campaign: dict[str, Any]) -> dict[str, Any]:
    if "manualCpc" in campaign:
        return {"strategy": "MANUAL_CPC"}
    if "percentCpc" in campaign:
        result = {"strategy": "PERCENT_CPC"}
        if int(campaign["percentCpc"].get("cpcBidCeilingMicros") or 0) > 0:
            result["max_cpc_native"] = _native_from_micros(campaign["percentCpc"]["cpcBidCeilingMicros"])
        return result
    if "targetSpend" in campaign:
        result = {"strategy": "MAXIMIZE_CLICKS"}
        if int(campaign["targetSpend"].get("cpcBidCeilingMicros") or 0) > 0:
            result["max_cpc_native"] = _native_from_micros(campaign["targetSpend"]["cpcBidCeilingMicros"])
        return result
    for field, strategy, parameter, target in (
        ("targetCpa", "TARGET_CPA", "targetCpaMicros", "target_cpa_native"),
        ("targetRoas", "TARGET_ROAS", "targetRoas", "target_roas"),
        ("maximizeConversions", "MAXIMIZE_CONVERSIONS", "targetCpaMicros", "target_cpa_native"),
        ("maximizeConversionValue", "MAXIMIZE_CONVERSION_VALUE", "targetRoas", "target_roas"),
    ):
        if field in campaign:
            result = {"strategy": strategy}
            number = campaign[field].get(parameter)
            if number is not None and Decimal(str(number)) > 0:
                result[target] = _native_from_micros(number) if parameter.endswith("Micros") else format(Decimal(str(number)), "f")
            return result
    raise GoogleCampaignBuildError("google_bidding_snapshot_required", "The current provider bidding scheme is required for this action.")


def _criteria_ids(criteria: list[dict[str, Any]], field: str, constant: str) -> list[str]:
    values = []
    for source in criteria:
        row = _row(source, "campaignCriterion", "adGroupCriterion")
        if row.get("negative") or row.get("status") == "REMOVED":
            continue
        resource = row.get(field, {}).get(constant)
        if resource:
            identifier = str(resource).rsplit("/", 1)[-1]
            TypeAdapter(Digits).validate_python(identifier)
            if identifier not in values:
                values.append(identifier)
    return values


def _context_draft(before: dict[str, Any], customer_id: str, campaign: dict[str, Any], kind: str, group: GoogleCampaignGroupInput, *, source_group: dict[str, Any] | None = None) -> GoogleCampaignDraft:
    """Translate existing campaign context only; never invent budgets or targets."""
    budget = before.get("campaign_budget")
    if not isinstance(budget, dict) or budget.get("resourceName") != campaign.get("campaignBudget"):
        raise GoogleCampaignBuildError("google_budget_snapshot_required", "A fresh budget bound to this campaign is required.")
    owned_resource(budget["resourceName"], customer_id, "campaignBudgets")
    mode = "TOTAL" if budget.get("period") == "CUSTOM_PERIOD" else "DAILY"
    criteria = before.get("criteria", [])
    if kind == "DEMAND_GEN":
        if source_group is not None:
            criteria = source_group.get("criteria", [])
        elif before.get("groups"):
            criteria = _row(before["groups"][0], "adGroup").get("criteria", before["groups"][0].get("criteria", []))
    data: dict[str, Any] = {
        "account_id": customer_id, "campaign_type": kind, "name": campaign["name"],
        "budget_native": _native_from_micros(budget.get("totalAmountMicros" if mode == "TOTAL" else "amountMicros")),
        "budget_mode": mode, "bidding": _snapshot_bidding(campaign), "groups": [group.model_dump(mode="json")],
        "geo_target_ids": _criteria_ids(criteria, "location", "geoTargetConstant"),
        "language_ids": _criteria_ids(criteria, "language", "languageConstant"),
        "brand_guidelines_enabled": bool(campaign.get("brandGuidelinesEnabled", False)),
        "eu_political_advertising": campaign.get("containsEuPoliticalAdvertising") == "CONTAINS_EU_POLITICAL_ADVERTISING",
    }
    for source, field in (("startDateTime", "start_date"), ("endDateTime", "end_date")):
        if campaign.get(source):
            data[field] = str(campaign[source]).split(" ", 1)[0]
    if campaign.get("shoppingSetting"):
        shopping = campaign["shoppingSetting"]
        data["shopping"] = {"merchant_id": str(shopping["merchantId"]), "feed_label": shopping.get("feedLabel"), "campaign_priority": int(shopping.get("campaignPriority") or 0), "enable_local": bool(shopping.get("enableLocal", False))}
    if kind == "SEARCH":
        dynamic = campaign.get("dynamicSearchAdsSetting", {})
        network = campaign.get("networkSettings", {})
        data["search"] = {"target_search_network": bool(network.get("targetSearchNetwork", False)), "target_content_network": bool(network.get("targetContentNetwork", False)), "dynamic_search_domain": dynamic.get("domainName"), "dynamic_search_language_code": dynamic.get("languageCode"), "use_supplied_urls_only": bool(dynamic.get("useSuppliedUrlsOnly", False))}
        if campaign.get("aiMaxSetting"):
            automation = {setting.get("assetAutomationType"): setting.get("assetAutomationStatus") == "OPTED_IN" for setting in campaign.get("assetAutomationSettings", [])}
            data["ai_max"] = {"enabled": bool(campaign["aiMaxSetting"].get("enableAiMax", False)), "text_customization": automation.get("TEXT_ASSET_AUTOMATION", False), "final_url_expansion": automation.get("FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", False)}
    if kind == "APP":
        setting = campaign.get("appCampaignSetting", {})
        data["app"] = {"subtype": {"APP_CAMPAIGN": "INSTALLS", "APP_CAMPAIGN_FOR_ENGAGEMENT": "ENGAGEMENT", "APP_CAMPAIGN_FOR_PRE_REGISTRATION": "PRE_REGISTRATION"}[campaign["advertisingChannelSubType"]], "app_id": setting.get("appId"), "app_store": setting.get("appStore"), "goal_type": setting.get("biddingStrategyGoalType"), "conversion_action_ids": campaign.get("selectiveOptimization", {}).get("conversionActions", [])}
    if kind == "HOTEL":
        data["hotel"] = {"hotel_center_id": str(campaign.get("hotelSetting", {}).get("hotelCenterId") or "")}
    return GoogleCampaignDraft.model_validate(data)


def _group_ready(draft: GoogleCampaignDraft, *, ad_only: bool = False) -> None:
    gaps = validate_draft_readiness(draft, draft.account_id)
    ignored = {"geo_target_ids", "language_ids", "start_date", "budget_mode", "bidding.strategy", "bidding.target_cpa_native", "bidding.target_roas"}
    if draft.campaign_type == "DEMAND_GEN" and not ad_only:
        ignored -= {"geo_target_ids", "language_ids"}
    if ad_only:
        ignored.add("groups.0.keywords")
        ignored.add("groups.0.cpc_bid_native")
        ignored.add("groups.0.percent_cpc_bid")
    gaps = [gap for gap in gaps if gap["field"] not in ignored]
    if gaps:
        raise GoogleCampaignBuildError("google_campaign_draft_incomplete", "The new group or ad does not meet its format requirements.", details=gaps)


def _check_existing_brand(before: dict[str, Any], group: GoogleCampaignGroupInput, campaign: dict[str, Any], customer_id: str) -> None:
    if not campaign.get("brandGuidelinesEnabled"):
        return
    if not isinstance(before.get("campaign_assets"), list):
        raise GoogleCampaignBuildError("google_brand_snapshot_required", "Read the campaign's current brand assets before changing an asset group.")
    names: list[str] = []
    logos: set[str] = set()
    landscape_logos: set[str] = set()
    for source in before["campaign_assets"]:
        link = _row(source, "campaignAsset")
        if link.get("campaign") and link["campaign"] != campaign["resourceName"]:
            raise GoogleCampaignBuildError("google_brand_campaign_mismatch", "Brand asset evidence belongs to another campaign.")
        resource = source.get("asset") if isinstance(source.get("asset"), dict) else {}
        ref = resource.get("resourceName") or link.get("asset")
        if ref:
            owned_resource(ref, customer_id, "assets")
        if link.get("fieldType") == "BUSINESS_NAME":
            name = resource.get("textAsset", {}).get("text") or source.get("textAsset", {}).get("text")
            if name:
                names.append(name)
        elif link.get("fieldType") == "LOGO" and ref:
            logos.add(ref)
        elif link.get("fieldType") == "LANDSCAPE_LOGO" and ref:
            landscape_logos.add(ref)
    if names != [group.business_name] or set(group.logo_assets) != logos or set(group.landscape_logo_assets) != landscape_logos:
        raise GoogleCampaignBuildError("google_brand_assets_mismatch", "The asset group must use the campaign's existing business name and campaign-level logos; brand changes require a separate action.")


def _checked_link(resource: str, customer_id: str, collection: str, parent: str, *, field_type: bool = False) -> str:
    suffix = rf"{re.escape(parent.rsplit('/', 1)[-1])}~[1-9][0-9]*(?:~[A-Z_]+)" if field_type else rf"{re.escape(parent.rsplit('/', 1)[-1])}~[1-9][0-9]*"
    if not re.fullmatch(rf"customers/{re.escape(customer_id)}/{collection}/{suffix}", str(resource)):
        raise GoogleCampaignBuildError("google_link_parent_mismatch", "The child-resource evidence does not belong to the selected account and group.")
    return resource


def _replace_pmax_assets(before: dict[str, Any], customer_id: str, campaign: dict[str, Any], source_group: dict[str, Any], draft: GoogleCampaignDraft, group: GoogleCampaignGroupInput) -> list[dict[str, Any]]:
    _check_existing_brand(before, group, campaign, customer_id)
    target = source_group["resourceName"]
    for key in ("assets", "signals"):
        if not isinstance(source_group.get(key), list):
            raise GoogleCampaignBuildError("google_asset_group_snapshot_required", "Read the asset group's complete existing assets and signals before replacing them.")
    removals: list[dict[str, Any]] = []
    for source in source_group["assets"]:
        link = _row(source, "assetGroupAsset")
        if link.get("fieldType") not in {"HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO", "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE", "YOUTUBE_VIDEO"}:
            raise GoogleCampaignBuildError("google_asset_group_reconstruction_unsupported", "The existing asset group contains a creative role not expressible in this editor; a full supported reconstruction is required before replacement.")
        if link.get("assetGroup") != target:
            raise GoogleCampaignBuildError("google_link_parent_mismatch", "An asset link belongs to another asset group.")
        ref = _checked_link(link.get("resourceName", ""), customer_id, "assetGroupAssets", target, field_type=True)
        removals.append({"assetGroupAssetOperation": {"remove": ref}})
    for source in source_group["signals"]:
        signal = _row(source, "assetGroupSignal")
        if (not signal.get("audience") and not signal.get("searchTheme")) or signal.get("localServicesId") or signal.get("verticalAdsItemGroupRuleList"):
            raise GoogleCampaignBuildError("google_asset_group_reconstruction_unsupported", "The existing asset group contains a specialized signal not expressible in this editor.")
        if signal.get("assetGroup") != target:
            raise GoogleCampaignBuildError("google_link_parent_mismatch", "An audience signal belongs to another asset group.")
        ref = _checked_link(signal.get("resourceName", ""), customer_id, "assetGroupSignals", target)
        removals.append({"assetGroupSignalOperation": {"remove": ref}})
    if draft.shopping:
        if not isinstance(source_group.get("listing_group_filters"), list):
            raise GoogleCampaignBuildError("google_listing_tree_snapshot_required", "Read the complete existing retail listing tree before replacing it.")
        filters = [_row(source, "assetGroupListingGroupFilter") for source in source_group["listing_group_filters"]]
        by_resource = {row["resourceName"]: row for row in filters}
        for row in filters:
            _checked_link(row["resourceName"], customer_id, "assetGroupListingGroupFilters", target)
            if row.get("assetGroup") != target or row.get("listingSource") != "SHOPPING":
                raise GoogleCampaignBuildError("google_link_parent_mismatch", "A listing filter belongs to another asset group.")

        def depth(row: dict[str, Any]) -> int:
            visited = set()
            parent = row.get("parentListingGroupFilter")
            while parent:
                if parent in visited or parent not in by_resource:
                    raise GoogleCampaignBuildError("google_listing_tree_snapshot_invalid", "The listing-tree snapshot is incomplete or cyclic.")
                visited.add(parent)
                parent = by_resource[parent].get("parentListingGroupFilter")
            return len(visited)

        for row in sorted(filters, key=depth, reverse=True):
            removals.append({"assetGroupListingGroupFilterOperation": {"remove": row["resourceName"]}})
    operations = _Operations(customer_id)
    _pmax_group(operations, draft, group, campaign["resourceName"])
    temporary = operations.items[0]["assetGroupOperation"]["create"]["resourceName"]

    def bind(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, str):
            if value == temporary:
                return target
            # Listing-group resource names include the asset group id before ~.
            prefix = f"customers/{customer_id}/assetGroupListingGroupFilters/{temporary.rsplit('/', 1)[-1]}~"
            if value.startswith(prefix):
                return f"customers/{customer_id}/assetGroupListingGroupFilters/{target.rsplit('/', 1)[-1]}~{value.split('~', 1)[1]}"
        return value

    result = [_update("assetGroupOperation", target, {"name": group.name, "finalUrls": list(group.final_urls)}, ["name", "finalUrls"])]
    return result + removals + [bind(item) for item in operations.items[1:]]


def build_update_operations(action: str, customer_id: str, before: dict[str, Any], changes: dict[str, Any]) -> list[dict[str, Any]]:
    """Build allowlisted semantic writes from fresh, parent-owned provider facts.

    before has raw camelCase campaign/campaign_budget, campaign criteria and
    groups/ads. Resource ownership AND campaign parenthood are checked again.
    The caller owns approval, revision/concurrency, role and spending policies.
    """
    customer_id = _customer_id(customer_id)
    campaign, kind = _snapshot_campaign(before, customer_id)
    resource = campaign["resourceName"]
    if action == "rename":
        _change_keys(changes, {"name"}, {"name"})
        return [_update("campaignOperation", resource, {"name": _name(changes["name"])}, ["name"])]
    if action in {"pause", "enable"}:
        _change_keys(changes, set())
        return [_update("campaignOperation", resource, {"status": "PAUSED" if action == "pause" else "ENABLED"}, ["status"])]
    if action == "remove":
        _change_keys(changes, {"ad_group_ad_resource"})
        if changes.get("ad_group_ad_resource"):
            ad = _ad_snapshot(before, customer_id, campaign, kind, changes["ad_group_ad_resource"])
            return [{"adGroupAdOperation": {"remove": ad["resourceName"]}}]
        return [{"campaignOperation": {"remove": resource}}]
    if action == "set_budget":
        _change_keys(changes, {"budget_native"}, {"budget_native"})
        budget = before.get("campaign_budget")
        if not isinstance(budget, dict) or budget.get("resourceName") != campaign.get("campaignBudget"):
            raise GoogleCampaignBuildError("google_budget_snapshot_required", "A fresh budget bound to this campaign is required.")
        reference = owned_resource(budget["resourceName"], customer_id, "campaignBudgets")
        if budget.get("explicitlyShared") is not False or int(budget.get("referenceCount") or 1) > 1:
            raise GoogleCampaignBuildError("google_shared_budget_requires_scope", "This action cannot change a shared budget affecting other campaigns; approve the complete shared-budget scope separately.")
        period = budget.get("period", "DAILY")
        if period not in {"DAILY", "CUSTOM_PERIOD"}:
            raise GoogleCampaignBuildError("google_budget_period_unsupported", "The provider budget period is unsupported.")
        field = "totalAmountMicros" if period == "CUSTOM_PERIOD" else "amountMicros"
        return [_update("campaignBudgetOperation", reference, {field: native_to_micros(changes["budget_native"])}, [field])]
    if action == "set_bidding":
        _change_keys(changes, {"bidding"}, {"bidding"})
        bidding = GoogleBiddingInput.model_validate(changes["bidding"])
        if bidding.strategy not in SUPPORTED_STRATEGIES[kind]:
            raise GoogleCampaignBuildError("google_bidding_not_supported", "The selected bidding strategy is not implemented for this campaign.")
        if bidding.strategy == "TARGET_CPA" and not bidding.target_cpa_native or bidding.strategy == "TARGET_ROAS" and not bidding.target_roas:
            raise GoogleCampaignBuildError("google_bidding_target_required", "The selected strategy requires a positive CPA or ROAS target.")
        if kind == "APP":
            current = _snapshot_bidding(campaign)
            if bidding.strategy != current["strategy"]:
                raise GoogleCampaignBuildError("google_app_bidding_goal_change_unsupported", "Changing an App bidding strategy also changes its goal contract; only its current strategy's target is editable here.")
        fields = _bidding_fields(bidding, kind)
        result = [_update("campaignOperation", resource, fields, _bidding_mask(fields))]
        if bidding.strategy == "MANUAL_CPC":
            if not bidding.max_cpc_native:
                raise GoogleCampaignBuildError("google_manual_cpc_bid_required", "Specify a default CPC bid when switching to Manual CPC.")
            for source in before.get("groups", []):
                row = _row(source, "adGroup")
                group = _group_snapshot(before, customer_id, campaign, kind, row["resourceName"].rsplit("/", 1)[-1])
                if kind in {"SHOPPING", "HOTEL"}:
                    manual_before = {**before, "campaign": {**campaign, "manualCpc": {}}}
                    result.extend(build_update_operations("set_group_bidding", customer_id, manual_before, {"group_id": group["resourceName"].rsplit("/", 1)[-1], "cpc_bid_native": bidding.max_cpc_native}))
                else:
                    result.append(_update("adGroupOperation", group["resourceName"], {"cpcBidMicros": native_to_micros(bidding.max_cpc_native)}, ["cpcBidMicros"]))
        return result
    if action == "set_conversion_goals":
        _change_keys(changes, {"conversion_goals"}, {"conversion_goals"})
        if kind == "APP":
            raise GoogleCampaignBuildError("google_conversion_goal_action_unsupported", "App campaigns use selective optimization.")
        goals = TypeAdapter(list[GoogleConversionGoalInput]).validate_python(changes["conversion_goals"])
        if not goals or len(goals) > 100 or not any(goal.biddable for goal in goals) or len({(goal.category, goal.origin) for goal in goals}) != len(goals):
            raise GoogleCampaignBuildError("google_conversion_goals_invalid", "Provide distinct existing conversion-goal pairs with at least one biddable goal.")
        existing = {(row["category"], row["origin"]): row for row in before.get("conversion_goals", [])}
        if not existing or any((goal.category, goal.origin) not in existing for goal in goals):
            raise GoogleCampaignBuildError("google_conversion_goal_snapshot_required", "Read the existing campaign conversion-goal pairs before changing them.")
        result = []
        for goal in goals:
            ref = existing[(goal.category, goal.origin)]["resourceName"]
            expected = f"customers/{customer_id}/campaignConversionGoals/{resource.rsplit('/', 1)[-1]}~{goal.category}~{goal.origin}"
            if ref != expected:
                raise GoogleCampaignBuildError("google_conversion_goal_campaign_mismatch", "Conversion-goal evidence belongs to another campaign.")
            result.append(_update("campaignConversionGoalOperation", ref, {"biddable": goal.biddable}, ["biddable"]))
        return result
    if action == "set_targeting":
        _change_keys(changes, {"geo_target_ids", "language_ids", "group_id"})
        dimensions = {key for key in changes if key != "group_id"}
        if not dimensions:
            raise GoogleCampaignBuildError("google_targeting_inputs_required", "Choose geographic targets or languages to change.")
        values = {key: TypeAdapter(list[Digits]).validate_python(changes[key]) for key in dimensions}
        for key, identifiers in values.items():
            if not identifiers or len(identifiers) > (100 if key == "geo_target_ids" else 50) or len(identifiers) != len(set(identifiers)):
                raise GoogleCampaignBuildError("google_targeting_inputs_invalid", "Target identifiers must be nonempty, distinct and within the supported count.")
        parents: list[dict[str, Any]]
        if kind == "DEMAND_GEN":
            if changes.get("group_id"):
                parents = [_group_snapshot(before, customer_id, campaign, kind, changes["group_id"])]
            else:
                parents = [_group_snapshot(before, customer_id, campaign, kind, _row(source, "adGroup")["resourceName"].rsplit("/", 1)[-1]) for source in before.get("groups", [])]
            if not parents:
                raise GoogleCampaignBuildError("google_group_snapshot_required", "Demand Gen requires its existing ad groups to change geographic/language targeting.")
        else:
            if "group_id" in changes:
                raise GoogleCampaignBuildError("google_targeting_level_unsupported", "This campaign uses campaign-level geographic/language targeting.")
            parents = [{"resourceName": resource, "criteria": before.get("criteria")}]
        operations = _Operations(customer_id)
        for parent in parents:
            if not isinstance(parent.get("criteria"), list):
                raise GoogleCampaignBuildError("google_targeting_snapshot_required", "Read the complete current targeting criteria before replacing them.")
            group_level = kind == "DEMAND_GEN"
            collection = "adGroupCriteria" if group_level else "campaignCriteria"
            operation = "adGroupCriterionOperation" if group_level else "campaignCriterionOperation"
            touched = {"location" if key == "geo_target_ids" else "language" for key in dimensions}
            for source in parent["criteria"]:
                criterion = _row(source, "campaignCriterion", "adGroupCriterion")
                if criterion.get("negative") or criterion.get("status") == "REMOVED" or not any(criterion.get(field) for field in touched):
                    continue
                ref = _checked_link(criterion.get("resourceName", ""), customer_id, collection, parent["resourceName"])
                if criterion.get("adGroup" if group_level else "campaign", parent["resourceName"]) != parent["resourceName"]:
                    raise GoogleCampaignBuildError("google_targeting_parent_mismatch", "A targeting criterion belongs to another campaign/group.")
                operations.items.append({operation: {"remove": ref}})
            _targeting(operations, parent["resourceName"], values.get("geo_target_ids", []), values.get("language_ids", []), group_level=group_level)
        return operations.items
    if action == "set_keywords":
        _change_keys(changes, {"group_id", "keywords"}, {"group_id", "keywords"})
        if kind != "SEARCH":
            raise GoogleCampaignBuildError("google_keyword_action_unsupported", "Keyword-set editing is implemented for Search groups only.")
        group = _group_snapshot(before, customer_id, campaign, kind, changes["group_id"])
        keywords = TypeAdapter(list[GoogleKeywordInput]).validate_python(changes["keywords"])
        if len(keywords) > 500 or len({(keyword.text, keyword.match_type, keyword.negative) for keyword in keywords}) != len(keywords):
            raise GoogleCampaignBuildError("google_keywords_invalid", "Provide no more than 500 distinct keyword/match-type/negative combinations.")
        if group.get("type") == "SEARCH_DYNAMIC_ADS":
            if any(not keyword.negative for keyword in keywords):
                raise GoogleCampaignBuildError("google_keywords_invalid", "DSA groups use webpage targets and accept only negative keyword exclusions in this editor.")
        elif group.get("type") == "SEARCH_STANDARD":
            ai_matching = campaign.get("aiMaxSetting", {}).get("enableAiMax") and not group.get("aiMaxAdGroupSetting", {}).get("disableSearchTermMatching")
            if not any(not keyword.negative for keyword in keywords) and not ai_matching:
                raise GoogleCampaignBuildError("google_keywords_invalid", "An RSA group needs positive keywords or explicitly enabled AI Max search term matching.")
        else:
            raise GoogleCampaignBuildError("google_keyword_group_unsupported", "This Search group subtype has no keyword-set editing contract.")
        if not isinstance(group.get("criteria"), list):
            raise GoogleCampaignBuildError("google_keywords_snapshot_required", "Read the complete existing group criteria before replacing keywords.")
        operations = _Operations(customer_id)
        for source in group["criteria"]:
            criterion = _row(source, "adGroupCriterion")
            if not criterion.get("keyword") or criterion.get("status") == "REMOVED":
                continue
            reference = _checked_link(criterion.get("resourceName", ""), customer_id, "adGroupCriteria", group["resourceName"])
            if criterion.get("adGroup", group["resourceName"]) != group["resourceName"]:
                raise GoogleCampaignBuildError("google_keywords_parent_mismatch", "Keyword evidence belongs to another group.")
            operations.items.append({"adGroupCriterionOperation": {"remove": reference}})
        for keyword in keywords:
            criterion = {"adGroup": group["resourceName"], "negative": keyword.negative, "keyword": {"text": keyword.text, "matchType": keyword.match_type}}
            if not keyword.negative:
                criterion["status"] = "ENABLED"
            operations.create("adGroupCriterionOperation", criterion)
        return operations.items
    if action == "set_channels":
        _change_keys(changes, {"group_id", "channels"}, {"group_id", "channels"})
        if kind != "DEMAND_GEN":
            raise GoogleCampaignBuildError("google_channel_action_unsupported", "Channel controls are implemented for Demand Gen only.")
        group = _group_snapshot(before, customer_id, campaign, kind, changes["group_id"])
        channels = GoogleCampaignGroupInput.model_validate({"name": group.get("name") or "Channel selection", "channels": changes["channels"]}).channels
        if not channels:
            raise GoogleCampaignBuildError("google_channel_inputs_required", "Select at least one Demand Gen channel.")
        selected = {"youtubeInStream": "YOUTUBE_IN_STREAM" in channels, "youtubeInFeed": "YOUTUBE_IN_FEED" in channels, "youtubeShorts": "YOUTUBE_SHORTS" in channels, "discover": "DISCOVER" in channels, "gmail": "GMAIL" in channels, "display": "DISPLAY" in channels}
        return [_update("adGroupOperation", group["resourceName"], {"demandGenAdGroupSettings": {"channelControls": {"selectedChannels": selected}}}, [f"demandGenAdGroupSettings.channelControls.selectedChannels.{field}" for field in selected])]
    if action in {"rename_group", "pause_group", "enable_group", "remove_group", "set_group_bidding"}:
        allowed = {"group_id", "name"} if action == "rename_group" else {"group_id", "cpc_bid_native", "percent_cpc_bid", "target_cpa_native", "target_roas"} if action == "set_group_bidding" else {"group_id"}
        _change_keys(changes, allowed, {"group_id", "name"} if action == "rename_group" else {"group_id"})
        group = _group_snapshot(before, customer_id, campaign, kind, changes["group_id"])
        operation = "assetGroupOperation" if kind == "PERFORMANCE_MAX" else "adGroupOperation"
        if action == "remove_group":
            return [{operation: {"remove": group["resourceName"]}}]
        if action == "rename_group":
            return [_update(operation, group["resourceName"], {"name": _name(changes["name"])}, ["name"])]
        if action in {"pause_group", "enable_group"}:
            return [_update(operation, group["resourceName"], {"status": "PAUSED" if action == "pause_group" else "ENABLED"}, ["status"])]
        if kind == "PERFORMANCE_MAX":
            raise GoogleCampaignBuildError("google_asset_group_bidding_unsupported", "Performance Max bidding is controlled at the campaign level.")
        bid_fields = set(changes) - {"group_id"}
        if len(bid_fields) != 1:
            raise GoogleCampaignBuildError("google_group_bid_invalid", "Exactly one supported group bid is required.")
        field = next(iter(bid_fields))
        current = _snapshot_bidding(campaign)
        target = {"cpc_bid_native": "cpcBidMicros", "percent_cpc_bid": "percentCpcBidMicros", "target_cpa_native": "targetCpaMicros", "target_roas": "targetRoas"}[field]
        if field == "cpc_bid_native" and current["strategy"] != "MANUAL_CPC" or field == "percent_cpc_bid" and not (kind == "HOTEL" and current["strategy"] == "PERCENT_CPC"):
            raise GoogleCampaignBuildError("google_group_bid_strategy_mismatch", "The group bid does not match the campaign's bidding strategy.")
        if field == "target_cpa_native" and (current["strategy"] not in {"TARGET_CPA", "MAXIMIZE_CONVERSIONS"} or not current.get("target_cpa_native") or kind == "APP" and campaign.get("advertisingChannelSubType") != "APP_CAMPAIGN_FOR_ENGAGEMENT"):
            raise GoogleCampaignBuildError("google_group_bid_strategy_mismatch", "This campaign/subtype does not allow an ad-group Target CPA override.")
        if field == "target_roas" and not (kind == "SHOPPING" and current["strategy"] == "TARGET_ROAS"):
            raise GoogleCampaignBuildError("google_group_bid_strategy_mismatch", "Target ROAS ad-group overrides are implemented for standard Shopping only.")
        number = positive_amount(TypeAdapter(NativeAmount).validate_python(changes[field]))
        if field == "percent_cpc_bid" and Decimal(number) >= 1:
            raise GoogleCampaignBuildError("google_group_bid_invalid", "Percent CPC is a fraction below one, for example 0.1 for 10%.")
        value: Any = float(Decimal(number)) if field == "target_roas" else native_to_micros(number)
        result = [_update(operation, group["resourceName"], {target: value}, [target])]
        if kind in {"SHOPPING", "HOTEL"} and field in {"cpc_bid_native", "percent_cpc_bid"}:
            if not isinstance(group.get("criteria"), list):
                raise GoogleCampaignBuildError("google_listing_tree_snapshot_required", "Read included listing units so their bid overrides can be updated with the group bid.")
            for source in group["criteria"]:
                criterion = _row(source, "adGroupCriterion")
                if criterion.get("negative") or criterion.get("listingGroup", {}).get("type") != "UNIT":
                    continue
                ref = _checked_link(criterion.get("resourceName", ""), customer_id, "adGroupCriteria", group["resourceName"])
                result.append(_update("adGroupCriterionOperation", ref, {target: value}, [target]))
        return result
    if action in {"remove_ad", "pause_ad", "enable_ad"}:
        _change_keys(changes, {"ad_group_ad_resource"}, {"ad_group_ad_resource"})
        ad = _ad_snapshot(before, customer_id, campaign, kind, changes["ad_group_ad_resource"])
        if action != "remove_ad":
            return [_update("adGroupAdOperation", ad["resourceName"], {"status": "PAUSED" if action == "pause_ad" else "ENABLED"}, ["status"])]
        return [{"adGroupAdOperation": {"remove": ad["resourceName"]}}]
    if action in {"add_group", "add_ad", "update_ad", "update_group_assets"}:
        allowed = {"group"} if action == "add_group" else {"group", "ad_group_ad_resource"} if action == "update_ad" else {"group", "group_id"}
        _change_keys(changes, allowed, allowed)
        group = GoogleCampaignGroupInput.model_validate(changes["group"])
        validate_group_references(group, customer_id)
        if action in {"add_ad", "update_ad"}:
            for field in ("keywords", "audience_ids", "search_themes", "user_list_ids", "product_offer_ids", "hotel_ids", "channels", "cpc_bid_native", "percent_cpc_bid", "disable_ai_max_search_term_matching"):
                if getattr(group, field):
                    raise GoogleCampaignBuildError("google_ad_only_inputs_invalid", "Ad-only actions cannot change group targeting, listing filters or bids; submit a separate semantic action.")
            if kind == "SEARCH" and (group.image_assets or group.square_image_assets):
                raise GoogleCampaignBuildError("google_ad_only_inputs_invalid", "Search image assets are group associations; an ad-only action cannot modify them.")
        source_group = None
        old_ad = None
        if action == "update_ad":
            old_ad = _ad_snapshot(before, customer_id, campaign, kind, changes["ad_group_ad_resource"])
            source_group = _group_snapshot(before, customer_id, campaign, kind, old_ad["adGroup"].rsplit("/", 1)[-1])
        elif action in {"add_ad", "update_group_assets"}:
            source_group = _group_snapshot(before, customer_id, campaign, kind, changes["group_id"])
        if action in {"add_ad", "update_ad"} and kind == "PERFORMANCE_MAX" or action == "update_group_assets" and kind != "PERFORMANCE_MAX":
            raise GoogleCampaignBuildError("google_entity_action_unsupported", "The selected action does not match this campaign's resource hierarchy.")
        draft = _context_draft(before, customer_id, campaign, kind, group, source_group=source_group)
        _group_ready(draft, ad_only=action in {"add_ad", "update_ad"})
        if kind == "PERFORMANCE_MAX":
            _check_existing_brand(before, group, campaign, customer_id)
        if action == "update_group_assets":
            return _replace_pmax_assets(before, customer_id, campaign, source_group, draft, group)
        operations = _Operations(customer_id)
        if action == "add_group":
            if kind == "PERFORMANCE_MAX":
                _pmax_group(operations, draft, group, resource)
            else:
                _ordinary_group(operations, draft, group, resource)
        else:
            fmt = _format(kind, group)
            existing_type = source_group.get("type")
            if kind == "SEARCH" and existing_type != ("SEARCH_DYNAMIC_ADS" if fmt == "DSA" else "SEARCH_STANDARD"):
                raise GoogleCampaignBuildError("google_ad_group_format_mismatch", "The ad format does not match this immutable Search group type.")
            _ad(operations, draft, group, source_group["resourceName"], status=old_ad.get("status", "PAUSED") if old_ad else "PAUSED")
            if old_ad:
                operations.items.append({"adGroupAdOperation": {"remove": old_ad["resourceName"]}})
        return operations.items
    if action == "duplicate":
        raise GoogleCampaignBuildError("google_duplicate_draft_required", "Duplicate a verified saved Mezan draft through full campaign creation; arbitrary provider campaign reconstruction is not implemented.")
    raise GoogleCampaignBuildError("google_action_unsupported", "This action is not implemented by the deterministic Google Ads builder.")
