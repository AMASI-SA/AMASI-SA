"""Lossless, bounded projection of fresh Google reads into editable inputs.

An external ad is editable only when every creative setting is represented.
Unknown controls, pinned copy, specialized listing trees and unread asset bodies
produce explicit gaps rather than an apparently complete, lossy replacement.
This module is pure: it performs no provider calls or mutations.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from .google_ads_campaign_contract import (
    GoogleCampaignAdInput, GoogleCampaignDraft, GoogleCampaignGroupInput,
    owned_resource,
)
from .google_ads_campaign_builder import (
    GoogleCampaignBuildError, _context_draft, _group_ready, _group_snapshot,
    _native_from_micros, _snapshot_campaign, validate_draft_readiness,
)


GROUP_FIELDS = {
    "status", "ads", "keywords", "audience_ids", "user_list_ids", "search_themes",
    "product_offer_ids", "hotel_ids", "channels", "geo_target_ids", "language_ids",
    "cpc_bid_native", "percent_cpc_bid", "disable_ai_max_search_term_matching",
}
AD_KINDS = {
    "responsiveSearchAd": "RSA", "expandedDynamicSearchAd": "DSA",
    "responsiveDisplayAd": "RESPONSIVE_DISPLAY", "imageAd": "UPLOADED_IMAGE",
    "displayUploadAd": "UPLOADED_HTML5", "demandGenMultiAssetAd": "MULTI_ASSET",
    "demandGenVideoResponsiveAd": "VIDEO", "demandGenCarouselAd": "CAROUSEL",
    "demandGenProductAd": "PRODUCT", "appAd": "APP", "appEngagementAd": "APP",
    "appPreRegistrationAd": "APP", "shoppingProductAd": "PRODUCT", "hotelAd": "HOTEL",
}
AD_TYPES = {
    "responsiveSearchAd": "RESPONSIVE_SEARCH_AD", "expandedDynamicSearchAd": "EXPANDED_DYNAMIC_SEARCH_AD",
    "responsiveDisplayAd": "RESPONSIVE_DISPLAY_AD", "imageAd": "IMAGE_AD", "displayUploadAd": "HTML5_UPLOAD_AD",
    "demandGenMultiAssetAd": "DEMAND_GEN_MULTI_ASSET_AD", "demandGenVideoResponsiveAd": "DEMAND_GEN_VIDEO_RESPONSIVE_AD",
    "demandGenCarouselAd": "DEMAND_GEN_CAROUSEL_AD", "demandGenProductAd": "DEMAND_GEN_PRODUCT_AD",
    "appAd": "APP_AD", "appEngagementAd": "APP_ENGAGEMENT_AD", "appPreRegistrationAd": "APP_PRE_REGISTRATION_AD",
    "shoppingProductAd": "SHOPPING_PRODUCT_AD", "hotelAd": "HOTEL_AD",
}


def _row(value: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in keys:
        if isinstance(value.get(key), dict):
            return value[key]
    return value


def _present(value: Any) -> bool:
    # An explicit false can differ from a provider's true default. Unknown
    # booleans therefore cannot be treated as empty source settings.
    return value is not None and value != "" and value != [] and value != {}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


class _Projection:
    def __init__(self, customer_id: str):
        self.customer_id = customer_id
        self.missing: list[str] = []
        self.unsupported: list[str] = []

    def gap(self, path: str, *, missing: bool = False) -> None:
        target = self.missing if missing else self.unsupported
        if path not in target:
            target.append(path)

    def only(self, values: dict[str, Any], allowed: set[str], path: str) -> None:
        for field, value in values.items():
            if field not in allowed and _present(value):
                self.gap(path + "." + field)

    def ref(self, value: Any, collection: str, path: str) -> str | None:
        if not isinstance(value, str):
            self.gap(path, missing=True)
            return None
        try:
            return owned_resource(value, self.customer_id, collection)
        except ValueError:
            self.gap(path)
            return None

    def assets(self, values: Any, path: str) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            self.gap(path, missing=True)
            return []
        result = []
        for index, value in enumerate(values):
            reference = value if isinstance(value, str) else _row(value).get("asset")
            if isinstance(value, dict):
                self.only(value, {"asset"}, f"{path}.{index}")
            valid = self.ref(reference, "assets", f"{path}.{index}")
            if valid:
                result.append(valid)
        return result

    def text(self, value: Any, path: str) -> str | None:
        if isinstance(value, str):
            return value
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            self.gap(path, missing=True)
            return None
        if value.get("pinnedField") not in (None, "", "UNSPECIFIED"):
            self.gap(path + ".pinnedField")
        # Google returns review evidence alongside the editable text. It is
        # retained in the source revision, but never sent as replacement copy.
        self.only(value, {"text", "pinnedField", "assetPerformanceLabel", "policySummary", "policySummaryInfo"}, path)
        return value["text"]

    def texts(self, values: Any, path: str) -> list[str]:
        if not isinstance(values, list):
            self.gap(path, missing=True)
            return []
        return [text for index, value in enumerate(values) if (text := self.text(value, f"{path}.{index}")) is not None]


def _asset_bodies(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for source in snapshot.get("creative_assets", []):
        asset = _row(source, "asset")
        if isinstance(asset.get("resourceName"), str):
            result[asset["resourceName"]] = asset
    return result


def _carousel_cards(projection: _Projection, values: Any, snapshot: dict[str, Any], path: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        projection.gap(path, missing=True)
        return []
    inventory = _asset_bodies(snapshot)
    result = []
    for index, value in enumerate(values):
        card_path = f"{path}.{index}"
        reference = projection.ref(_row(value).get("asset"), "assets", card_path + ".asset")
        asset = inventory.get(reference or "")
        if not asset:
            projection.gap(card_path + ".asset_body", missing=True)
            continue
        card = asset.get("demandGenCarouselCardAsset")
        if not isinstance(card, dict):
            projection.gap(card_path + ".demandGenCarouselCardAsset", missing=True)
            continue
        projection.only(card, {"headline", "marketingImageAsset", "squareMarketingImageAsset", "portraitMarketingImageAsset"}, card_path)
        urls = asset.get("finalUrls")
        if not isinstance(urls, list) or len(urls) != 1:
            projection.gap(card_path + ".finalUrls")
            continue
        output = {"headline": card.get("headline"), "final_url": urls[0]}
        for source, target in (("marketingImageAsset", "image_asset"), ("squareMarketingImageAsset", "square_image_asset"), ("portraitMarketingImageAsset", "portrait_image_asset")):
            if card.get(source):
                output[target] = projection.ref(card[source], "assets", card_path + "." + source)
        for field in ("trackingUrlTemplate", "finalUrlSuffix", "urlCustomParameters", "finalMobileUrls"):
            if _present(asset.get(field)):
                projection.gap(card_path + "." + field)
        result.append(output)
    return result


def _project_ad(projection: _Projection, source: dict[str, Any], kind: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    row = _row(source, "adGroupAd")
    ad = _row(row.get("ad"))
    path = "ad"
    if row.get("status") not in {"ENABLED", "PAUSED"}:
        projection.gap(path + ".status", missing=True)
    output: dict[str, Any] = {"ad_name": ad.get("name"), "ad_status": row.get("status", "ENABLED"), "final_urls": ad.get("finalUrls", [])}
    payloads = [field for field in AD_KINDS if field in ad]
    if not payloads:
        implicit = {"SHOPPING_PRODUCT_AD": "shoppingProductAd", "HOTEL_AD": "hotelAd"}.get(ad.get("type"))
        if implicit:
            payloads = [implicit]
    if len(payloads) != 1:
        projection.gap(path + ".supported_format", missing=True)
        return output
    field = payloads[0]
    creative = _row(ad.get(field))
    output["ad_format"] = AD_KINDS[field]
    if ad.get("type") not in (None, AD_TYPES[field]):
        projection.gap(path + ".type")
    if field != "appEngagementAd" and ad.get("finalAppUrls"):
        projection.gap(path + ".finalAppUrls")
    projection.only(ad, {"resourceName", "id", "name", "type", "finalUrls", "finalAppUrls", field,
                         "addedByGoogleAds", "systemManagedResourceSource"}, path)
    if ad.get("systemManagedResourceSource") not in (None, "", "UNSPECIFIED"):
        projection.gap(path + ".systemManagedResourceSource")
    common = {"headlines", "descriptions"}
    if field in {"responsiveSearchAd", "responsiveDisplayAd", "demandGenMultiAssetAd", "demandGenVideoResponsiveAd", "appAd", "appEngagementAd", "appPreRegistrationAd"}:
        output["headlines"] = projection.texts(creative.get("headlines"), path + ".headlines")
        output["descriptions"] = projection.texts(creative.get("descriptions"), path + ".descriptions")
    if field == "responsiveSearchAd":
        projection.only(creative, common, path)
    elif field == "expandedDynamicSearchAd":
        output["descriptions"] = [creative[key] for key in ("description", "description2") if creative.get(key)]
        # Google computes DSA ad final URLs; webpage criteria are the editable inputs.
        output["final_urls"] = []
        projection.only(creative, {"description", "description2"}, path)
    elif field in {"responsiveDisplayAd", "demandGenMultiAssetAd", "demandGenVideoResponsiveAd"}:
        mapping = {
            "marketingImages": "image_assets", "squareMarketingImages": "square_image_assets",
            "portraitMarketingImages": "portrait_image_assets", "tallPortraitMarketingImages": "tall_portrait_image_assets",
            "logoImages": "logo_assets", "youtubeVideos": "video_assets", "videos": "video_assets",
            "companionBanners": "image_assets",
        }
        allowed = common | set(mapping) | {"businessName"}
        if field == "responsiveDisplayAd":
            mapping["logoImages"] = "landscape_logo_assets"
            mapping["squareLogoImages"] = "logo_assets"
            allowed |= {"squareLogoImages", "longHeadline", "allowFlexibleColor", "formatSetting"}
            if creative.get("allowFlexibleColor") not in (None, True):
                projection.gap(path + ".allowFlexibleColor")
            if creative.get("formatSetting") not in (None, "", "UNSPECIFIED", "ALL_FORMATS"):
                projection.gap(path + ".formatSetting")
            long_headline = projection.text(creative.get("longHeadline"), path + ".longHeadline")
            output["long_headlines"] = [long_headline] if long_headline is not None else []
        elif field == "demandGenVideoResponsiveAd":
            allowed |= {"longHeadlines"}
            output["long_headlines"] = projection.texts(creative.get("longHeadlines"), path + ".longHeadlines")
        for source_field, target in mapping.items():
            if creative.get(source_field):
                output[target] = projection.assets(creative[source_field], path + "." + source_field)
        output["business_name"] = projection.text(creative.get("businessName"), path + ".businessName")
        projection.only(creative, allowed, path)
    elif field == "imageAd":
        refs = projection.assets([creative.get("imageAsset")], path + ".imageAsset")
        output["image_assets"] = refs
        projection.only(creative, {"imageAsset"}, path)
    elif field == "displayUploadAd":
        if creative.get("displayUploadProductType") != "HTML5_UPLOAD_AD":
            projection.gap(path + ".displayUploadProductType")
        refs = projection.assets([creative.get("mediaBundle")], path + ".mediaBundle")
        output["media_bundle_asset"] = refs[0] if refs else None
        projection.only(creative, {"displayUploadProductType", "mediaBundle"}, path)
    elif field in {"demandGenCarouselAd", "demandGenProductAd"}:
        output["headlines"] = [text] if (text := projection.text(creative.get("headline"), path + ".headline")) is not None else []
        output["descriptions"] = [text] if (text := projection.text(creative.get("description"), path + ".description")) is not None else []
        output["business_name"] = projection.text(creative.get("businessName"), path + ".businessName")
        output["logo_assets"] = projection.assets([creative.get("logoImage")], path + ".logoImage")
        allowed = {"headline", "description", "businessName", "logoImage"}
        if field == "demandGenCarouselAd":
            output["carousel_cards"] = _carousel_cards(projection, creative.get("carouselCards"), snapshot, path + ".carouselCards")
            allowed.add("carouselCards")
        projection.only(creative, allowed, path)
    elif field in {"appAd", "appEngagementAd", "appPreRegistrationAd"}:
        for source_field, target in (("images", "image_assets"), ("videos", "video_assets"), ("youtubeVideos", "video_assets")):
            if creative.get(source_field):
                output[target] = projection.assets(creative[source_field], path + "." + source_field)
        projection.only(creative, common | {"images", "videos", "youtubeVideos"}, path)
        if field == "appEngagementAd":
            urls = ad.get("finalAppUrls")
            store = snapshot.get("campaign", {}).get("appCampaignSetting", {}).get("appStore")
            expected_os = "IOS" if store == "APPLE_APP_STORE" else "ANDROID"
            if not isinstance(urls, list):
                projection.gap(path + ".finalAppUrls", missing=True)
            else:
                output["final_urls"] = []
                for index, value in enumerate(urls):
                    if not isinstance(value, dict) or value.get("osType") != expected_os:
                        projection.gap(f"{path}.finalAppUrls.{index}")
                    else:
                        projection.only(value, {"osType", "url"}, f"{path}.finalAppUrls.{index}")
                        output["final_urls"].append(value.get("url"))
        elif ad.get("finalAppUrls"):
            projection.gap(path + ".finalAppUrls")
    else:
        projection.only(creative, set(), path)
    return output


def _listing_ids(projection: _Projection, rows: list[dict[str, Any]], path: str, *, pmax: bool = False, hotel: bool = False, group: dict[str, Any] | None = None) -> list[str]:
    """Only a complete all-products unit or one-level ID partition is editable."""
    if not rows:
        projection.gap(path, missing=True)
        return []
    nodes = []
    for index, source in enumerate(rows):
        row = _row(source, "assetGroupListingGroupFilter", "adGroupCriterion")
        node = row if pmax else _row(row.get("listingGroup"))
        if not node:
            projection.gap(f"{path}.{index}", missing=True)
            continue
        if pmax and node.get("listingSource") != "SHOPPING":
            projection.gap(f"{path}.{index}.listingSource")
        projection.only(node, {"resourceName", "assetGroup", "id", "type", "listingSource", "parentListingGroupFilter", "parentAdGroupCriterion", "caseValue"}, f"{path}.{index}")
        if pmax and group and node.get("assetGroup") not in (None, group["resourceName"]):
            projection.gap(f"{path}.{index}.assetGroup")
        if row.get("status") not in (None, "", "ENABLED"):
            projection.gap(f"{path}.{index}.status")
        parent = node.get("parentListingGroupFilter" if pmax else "parentAdGroupCriterion")
        nodes.append((row, node, parent))
    roots = [(row, node) for row, node, parent in nodes if not parent]
    if len(roots) != 1:
        projection.gap(path + ".root")
        return []
    root_row, root = roots[0]
    if root.get("type") in {"UNIT", "UNIT_INCLUDED"} and len(nodes) == 1 and not root.get("caseValue"):
        if root.get("type") == "UNIT" and root_row.get("negative"):
            projection.gap(path + ".excluded_root")
        return []
    if root.get("type") != "SUBDIVISION" or root.get("caseValue"):
        projection.gap(path + ".partition")
        return []
    root_ref = root_row.get("resourceName")
    dimension = "hotelId" if hotel else "productItemId"
    result = []
    other_count = 0
    for index, (row, node, parent) in enumerate(nodes):
        if row is root_row:
            continue
        if parent != root_ref:
            projection.gap(f"{path}.{index}.nested_partition")
        case = node.get("caseValue") or {}
        if set(case) != {dimension} or not isinstance(case.get(dimension), dict):
            projection.gap(f"{path}.{index}.caseValue")
            continue
        value = case[dimension].get("value")
        excluded = node.get("type") == "UNIT_EXCLUDED" if pmax else bool(row.get("negative"))
        if not value:
            other_count += 1
            if not excluded:
                projection.gap(f"{path}.{index}.included_other")
        elif excluded or node.get("type") not in {"UNIT", "UNIT_INCLUDED"}:
            projection.gap(f"{path}.{index}.excluded_or_subdivided_id")
        else:
            result.append(str(value))
        if not pmax:
            for field in ("cpcBidMicros", "percentCpcBidMicros"):
                if row.get(field) not in (None, "", "0", 0) and str(row[field]) != str((group or {}).get(field)):
                    projection.gap(f"{path}.{index}.{field}")
    if other_count != 1 or not result:
        projection.gap(path + ".other_unit")
    return result


def _project_criteria(projection: _Projection, source: dict[str, Any], kind: str, output: dict[str, Any]) -> None:
    criteria = source.get("criteria")
    if not isinstance(criteria, list):
        projection.gap("group.criteria", missing=True)
        return
    keywords, audiences, user_lists, geo, languages, webpages, listing = [], [], [], [], [], [], []
    for index, raw in enumerate(criteria):
        row = _row(raw, "adGroupCriterion")
        path = f"group.criteria.{index}"
        if row.get("adGroup") and row["adGroup"] != source["resourceName"]:
            projection.gap(path + ".adGroup")
        if row.get("resourceName") and not str(row["resourceName"]).startswith(f"customers/{projection.customer_id}/adGroupCriteria/{source['resourceName'].rsplit('/', 1)[-1]}~"):
            projection.gap(path + ".resourceName")
        negative = bool(row.get("negative", False))
        if row.get("keyword"):
            keyword = row["keyword"]
            projection.only(keyword, {"text", "matchType"}, path + ".keyword")
            entry = {"text": keyword.get("text"), "match_type": keyword.get("matchType"), "negative": negative}
            if not negative:
                entry["status"] = row.get("status", "ENABLED")
            keywords.append(entry)
            if row.get("cpcBidMicros") not in (None, "", "0", 0):
                projection.gap(path + ".cpcBidMicros")
        elif row.get("audience") or row.get("userList"):
            field, collection, target = ("audience", "audiences", audiences) if row.get("audience") else ("userList", "userLists", user_lists)
            value = projection.ref(row[field].get(field), collection, path + "." + field)
            if value:
                target.append(value)
            if negative or row.get("status") not in (None, "", "ENABLED"):
                projection.gap(path + ".negative_or_status")
        elif row.get("location") or row.get("language"):
            field, constant, target = ("location", "geoTargetConstant", geo) if row.get("location") else ("language", "languageConstant", languages)
            prefix = "geoTargetConstants/" if field == "location" else "languageConstants/"
            reference = row[field].get(constant)
            if kind != "DEMAND_GEN" or negative or not isinstance(reference, str) or not reference.startswith(prefix) or not reference[len(prefix):].isdigit():
                projection.gap(path + "." + field)
            else:
                target.append(reference[len(prefix):])
        elif "webpage" in row:
            page = _row(row.get("webpage"))
            projection.only(page, {"criterionName", "conditions"}, path + ".webpage")
            conditions = page.get("conditions", [])
            if negative or row.get("status") not in (None, "", "ENABLED") or not isinstance(conditions, list):
                projection.gap(path + ".webpage")
            elif not conditions:
                webpages.append(None)
            elif len(conditions) == 1 and conditions[0].get("operand") == "URL" and conditions[0].get("operator") == "EQUALS" and conditions[0].get("argument"):
                projection.only(conditions[0], {"operand", "operator", "argument"}, path + ".webpage.conditions")
                webpages.append(conditions[0]["argument"])
            else:
                projection.gap(path + ".webpage.conditions")
        elif "listingGroup" in row:
            listing.append(row)
        else:
            projection.gap(path + "." + str(row.get("type") or "unknown_criterion"))
        if row.get("type") != "KEYWORD" and row.get("status") not in (None, "", "ENABLED"):
            projection.gap(path + ".status")
        projection.only(row, {"resourceName", "criterionId", "adGroup", "type", "status", "negative", "keyword", "audience", "userList", "location", "language", "webpage", "listingGroup", "cpcBidMicros", "percentCpcBidMicros", "qualityInfo", "policySummary", "approvalStatus", "effectiveCpcBidMicros", "effectiveCpcBidSource"}, path)
    output.update({"keywords": keywords, "audience_ids": audiences, "user_list_ids": user_lists})
    if kind == "DEMAND_GEN":
        output.update({"geo_target_ids": geo, "language_ids": languages})
    if source.get("type") == "SEARCH_DYNAMIC_ADS":
        if not webpages:
            projection.gap("group.webpage_criteria", missing=True)
        elif None in webpages and len(webpages) != 1:
            projection.gap("group.webpage_criteria.mixed_all_pages")
        output["final_urls"] = [value for value in webpages if value is not None]
    elif webpages:
        projection.gap("group.webpage_criteria")
    needs_listing = kind in {"SHOPPING", "HOTEL"} or bool(listing)
    if needs_listing:
        output["hotel_ids" if kind == "HOTEL" else "product_offer_ids"] = _listing_ids(projection, listing, "group.listing_group", hotel=kind == "HOTEL", group=source)


def _project_ordinary_group(projection: _Projection, source: dict[str, Any], kind: str, ad: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    output = {"name": source.get("name"), "status": source.get("status", "ENABLED"), **_project_ad(projection, ad, kind, snapshot)}
    if source.get("status") not in {"ENABLED", "PAUSED"}:
        projection.gap("group.status", missing=True)
    _project_criteria(projection, source, kind, output)
    bidding = snapshot.get("campaign", {}).get("biddingStrategyType")
    manual = "manualCpc" in snapshot.get("campaign", {}) or bidding == "MANUAL_CPC"
    if manual and source.get("cpcBidMicros") not in (None, "", "0", 0):
        output["cpc_bid_native"] = _native_from_micros(source["cpcBidMicros"])
    if kind == "HOTEL" and source.get("percentCpcBidMicros") not in (None, "", "0", 0):
        output["percent_cpc_bid"] = _native_from_micros(source["percentCpcBidMicros"])
    for field in ("targetCpaMicros", "targetRoas"):
        if source.get(field) not in (None, "", "0", 0, 0.0):
            projection.gap("group." + field)
    audience_setting = source.get("audienceSetting") or {}
    if output.get("audience_ids") and audience_setting.get("useAudienceGrouped") is not True:
        projection.gap("group.audienceSetting.useAudienceGrouped")
    elif not output.get("audience_ids") and audience_setting.get("useAudienceGrouped") is True:
        projection.gap("group.audienceSetting.useAudienceGrouped")
    projection.only(audience_setting, {"useAudienceGrouped"}, "group.audienceSetting")
    ai_max = source.get("aiMaxAdGroupSetting") or {}
    output["disable_ai_max_search_term_matching"] = bool(ai_max.get("disableSearchTermMatching", False))
    projection.only(ai_max, {"disableSearchTermMatching"}, "group.aiMaxAdGroupSetting")
    settings = source.get("demandGenAdGroupSettings") or {}
    if settings:
        projection.only(settings, {"channelControls"}, "group.demandGenAdGroupSettings")
        controls = settings.get("channelControls") or {}
        projection.only(controls, {"selectedChannels"}, "group.channelControls")
        selected = controls.get("selectedChannels") or {}
        mapping = {"youtubeInStream": "YOUTUBE_IN_STREAM", "youtubeInFeed": "YOUTUBE_IN_FEED", "youtubeShorts": "YOUTUBE_SHORTS", "discover": "DISCOVER", "gmail": "GMAIL", "display": "DISPLAY"}
        projection.only(selected, set(mapping), "group.channelControls.selectedChannels")
        output["channels"] = [target for field, target in mapping.items() if selected.get(field) is True]
        if not output["channels"]:
            projection.gap("group.channelControls.selectedChannels")
    projection.only(source, {"resourceName", "id", "name", "status", "campaign", "type", "criteria", "cpcBidMicros", "percentCpcBidMicros", "targetCpaMicros", "targetRoas", "effectiveTargetCpaMicros", "effectiveTargetRoas", "effectiveTargetCpaSource", "effectiveTargetRoasSource", "audienceSetting", "demandGenAdGroupSettings", "aiMaxAdGroupSetting", "primaryStatus", "primaryStatusReasons", "adRotationMode", "baseAdGroup"}, "group")
    if source.get("adRotationMode") not in (None, "", "UNSPECIFIED", "OPTIMIZE"):
        projection.gap("group.adRotationMode")
    if source.get("baseAdGroup") and source["baseAdGroup"] != source["resourceName"]:
        projection.gap("group.baseAdGroup")
    return output


def _project_pmax_group(projection: _Projection, source: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"name": source.get("name"), "status": source.get("status", "ENABLED"), "ad_format": "MULTI_ASSET", "final_urls": source.get("finalUrls", [])}
    if source.get("status") not in {"ENABLED", "PAUSED"}:
        projection.gap("group.status", missing=True)
    if not isinstance(source.get("assets"), list) or not isinstance(source.get("signals"), list):
        projection.gap("group.assets_and_signals", missing=True)
        return output
    text_fields = {"HEADLINE": "headlines", "LONG_HEADLINE": "long_headlines", "DESCRIPTION": "descriptions"}
    asset_fields = {"MARKETING_IMAGE": "image_assets", "SQUARE_MARKETING_IMAGE": "square_image_assets", "PORTRAIT_MARKETING_IMAGE": "portrait_image_assets", "LOGO": "logo_assets", "LANDSCAPE_LOGO": "landscape_logo_assets", "YOUTUBE_VIDEO": "video_assets"}
    names = []
    for index, raw in enumerate(source["assets"]):
        link = _row(raw, "assetGroupAsset")
        path = f"group.assets.{index}"
        if link.get("assetGroup") not in (None, source["resourceName"]):
            projection.gap(path + ".assetGroup")
        if link.get("status") not in (None, "", "ENABLED"):
            projection.gap(path + ".status")
        projection.only(link, {"resourceName", "assetGroup", "asset", "fieldType", "status", "textAsset", "source", "performanceLabel", "primaryStatus", "primaryStatusReasons", "policySummary"}, path)
        ref = projection.ref(link.get("asset"), "assets", path + ".asset")
        field_type = link.get("fieldType")
        if field_type in text_fields or field_type == "BUSINESS_NAME":
            text = projection.text(link.get("textAsset") or _row(raw.get("asset")).get("textAsset"), path + ".textAsset")
            if text is not None:
                if field_type == "BUSINESS_NAME":
                    names.append(text)
                else:
                    output.setdefault(text_fields[field_type], []).append(text)
        elif field_type in asset_fields and ref:
            output.setdefault(asset_fields[field_type], []).append(ref)
        else:
            projection.gap(path + ".fieldType." + str(field_type))
    if snapshot["campaign"].get("brandGuidelinesEnabled"):
        if names or output.get("logo_assets") or output.get("landscape_logo_assets"):
            projection.gap("group.brand_assets_with_campaign_brand_guidelines")
        if not isinstance(snapshot.get("campaign_assets"), list):
            projection.gap("campaign_assets", missing=True)
        else:
            for index, raw in enumerate(snapshot["campaign_assets"]):
                link = _row(raw, "campaignAsset")
                field_type = link.get("fieldType")
                if field_type not in {"BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO"}:
                    continue
                path = f"campaign_assets.{index}"
                if link.get("campaign") not in (None, snapshot["campaign"]["resourceName"]):
                    projection.gap(path + ".campaign")
                if link.get("status") not in (None, "", "ENABLED"):
                    projection.gap(path + ".status")
                asset = _row(raw.get("asset"))
                ref = projection.ref(link.get("asset") or asset.get("resourceName"), "assets", path + ".asset")
                if field_type == "BUSINESS_NAME":
                    name = projection.text(asset.get("textAsset") or raw.get("textAsset"), path + ".textAsset")
                    if name is not None:
                        names.append(name)
                elif ref:
                    output.setdefault("logo_assets" if field_type == "LOGO" else "landscape_logo_assets", []).append(ref)
    if len(names) != 1:
        projection.gap("group.business_name", missing=not names)
    else:
        output["business_name"] = names[0]
    audiences, themes = [], []
    for index, raw in enumerate(source["signals"]):
        signal = _row(raw, "assetGroupSignal")
        path = f"group.signals.{index}"
        if signal.get("assetGroup") not in (None, source["resourceName"]):
            projection.gap(path + ".assetGroup")
        projection.only(signal, {"resourceName", "assetGroup", "audience", "searchTheme", "approvalStatus", "disapprovalReasons"}, path)
        if signal.get("audience"):
            projection.only(signal["audience"], {"audience"}, path + ".audience")
            reference = projection.ref(signal["audience"].get("audience"), "audiences", path + ".audience")
            if reference:
                audiences.append(reference)
        elif signal.get("searchTheme"):
            projection.only(signal["searchTheme"], {"text"}, path + ".searchTheme")
            themes.append(signal["searchTheme"].get("text"))
        else:
            projection.gap(path + ".supported_signal")
    output.update({"audience_ids": audiences, "search_themes": themes})
    if snapshot["campaign"].get("shoppingSetting"):
        filters = source.get("listing_group_filters")
        if not isinstance(filters, list):
            projection.gap("group.listing_group_filters", missing=True)
        else:
            output["product_offer_ids"] = _listing_ids(projection, filters, "group.listing_group_filters", pmax=True, group=source)
    elif source.get("listing_group_filters"):
        projection.gap("group.listing_group_filters_without_shopping")
    projection.only(source, {"resourceName", "id", "name", "status", "campaign", "finalUrls", "assets", "signals", "listing_group_filters", "adStrength", "primaryStatus", "primaryStatusReasons"}, "group")
    return output


def project_google_group(snapshot: dict[str, Any], account_id: str, group_id: str, ad_resource: str | None = None) -> dict[str, Any]:
    """Return editable group/creative values and explicit provider-read gaps.

    Ordinary ad groups with multiple ads require an explicit ad resource. Every
    returned identity is proved against the selected campaign and customer.
    """
    campaign, kind = _snapshot_campaign(snapshot, account_id)
    source = _group_snapshot(snapshot, account_id, campaign, kind, str(group_id))
    projection = _Projection(account_id)
    evidence: dict[str, Any] = {"campaign": campaign, "campaign_budget": snapshot.get("campaign_budget"), "group": source}
    if kind == "PERFORMANCE_MAX":
        if ad_resource is not None:
            projection.gap("ad_resource_for_asset_group")
        values = _project_pmax_group(projection, source, snapshot)
        evidence["campaign_assets"] = snapshot.get("campaign_assets")
    else:
        rows = [_row(value, "adGroupAd") for value in snapshot.get("ads", [])]
        rows = [row for row in rows if row.get("adGroup") == source["resourceName"] and row.get("status") != "REMOVED"]
        if ad_resource is not None:
            reference = owned_resource(ad_resource, account_id, "adGroupAds", composite=True)
            rows = [row for row in rows if row.get("resourceName") == reference]
            if reference.rsplit("/", 1)[-1].split("~", 1)[0] != str(group_id):
                raise GoogleCampaignBuildError("google_ad_group_mismatch", "The selected ad reference belongs to a different group.")
        if len(rows) != 1:
            projection.gap("selected_ad" if ad_resource is not None else "single_ad_or_selected_ad", missing=True)
            values = {"name": source.get("name"), "status": source.get("status", "ENABLED")}
        else:
            owned_resource(str(rows[0].get("resourceName") or ""), account_id, "adGroupAds", composite=True)
            values = _project_ordinary_group(projection, source, kind, rows[0], snapshot)
            evidence["ad"] = rows[0]
    evidence["creative_assets"] = snapshot.get("creative_assets")
    group = None
    try:
        model = GoogleCampaignGroupInput.model_validate(values)
        group = model.model_dump(mode="json")
        draft = _context_draft(snapshot, account_id, campaign, kind, model, source_group=source)
        _group_ready(draft, ad_only=kind != "PERFORMANCE_MAX")
    except ValidationError as exc:
        for error in exc.errors():
            projection.gap(".".join(str(part) for part in error["loc"]), missing=True)
    except GoogleCampaignBuildError as exc:
        if exc.details:
            for error in exc.details:
                projection.gap(error.get("field") or exc.code, missing=True)
        else:
            projection.gap(exc.code, missing=True)
    editable = group is not None and not projection.missing and not projection.unsupported
    creative = {field: value for field, value in (group or {}).items() if field not in GROUP_FIELDS}
    return {
        "editable": editable, "group": group, "creative": creative if editable else None,
        "unsupported_fields": projection.unsupported, "missing_fields": projection.missing,
        "source_revision": _digest(evidence),
        "limitations": [] if editable else ["Provider settings cannot be replaced until every reported field is represented and read completely."],
    }


def _duplicate_settings(projection: _Projection, snapshot: dict[str, Any], campaign: dict[str, Any], kind: str, data: dict[str, Any]) -> None:
    """Prove source settings fit the human contract before cloning anything."""
    budget = snapshot.get("campaign_budget") or {}
    if budget.get("explicitlyShared") is not False:
        projection.gap("campaign_budget.explicitlyShared", missing="explicitlyShared" not in budget)
    if int(budget.get("referenceCount") or 0) > 1:
        projection.gap("campaign_budget.referenceCount")
    if budget.get("period") not in {"DAILY", "CUSTOM_PERIOD"}:
        projection.gap("campaign_budget.period")
    if budget.get("deliveryMethod") not in (None, "", "STANDARD"):
        projection.gap("campaign_budget.deliveryMethod")
    if budget.get("type") not in (None, "", "UNSPECIFIED", "STANDARD"):
        projection.gap("campaign_budget.type")
    projection.only(budget, {"resourceName", "id", "name", "status", "period", "amountMicros", "totalAmountMicros", "explicitlyShared", "referenceCount", "deliveryMethod", "hasRecommendedBudget", "recommendedBudgetAmountMicros", "recommendedBudgetEstimatedChangeWeeklyClicks", "recommendedBudgetEstimatedChangeWeeklyCost", "recommendedBudgetEstimatedChangeWeeklyInteractions", "recommendedBudgetEstimatedChangeWeeklyViews", "type"}, "campaign_budget")
    if campaign.get("biddingStrategy"):
        projection.gap("campaign.biddingStrategy")
    if campaign.get("status") not in {"ENABLED", "PAUSED"}:
        projection.gap("campaign.status", missing=True)
    if campaign.get("containsEuPoliticalAdvertising") not in {"CONTAINS_EU_POLITICAL_ADVERTISING", "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"}:
        projection.gap("campaign.containsEuPoliticalAdvertising", missing=True)
    bidding_fields = {
        "manualCpc": {"enhancedCpcEnabled"},
        "targetSpend": {"cpcBidCeilingMicros"},
        "percentCpc": {"cpcBidCeilingMicros"},
        "targetCpa": {"targetCpaMicros"},
        "targetRoas": {"targetRoas"},
        "maximizeConversions": {"targetCpaMicros"},
        "maximizeConversionValue": {"targetRoas"},
    }
    for field, allowed in bidding_fields.items():
        if field in campaign:
            projection.only(_row(campaign[field]), allowed, "campaign." + field)
    if campaign.get("manualCpc", {}).get("enhancedCpcEnabled"):
        projection.gap("campaign.manualCpc.enhancedCpcEnabled")
    network = campaign.get("networkSettings") or {}
    if kind in {"SEARCH", "SHOPPING", "HOTEL"} and network.get("targetGoogleSearch") is not True:
        projection.gap("campaign.networkSettings.targetGoogleSearch")
    if kind == "SEARCH":
        data.setdefault("search", {})["target_partner_search_network"] = bool(network.get("targetPartnerSearchNetwork", False))
    elif kind == "SHOPPING":
        data.setdefault("shopping", {})["target_search_network"] = bool(network.get("targetSearchNetwork", False))
        if network.get("targetContentNetwork") or network.get("targetPartnerSearchNetwork"):
            projection.gap("campaign.networkSettings")
    elif kind == "DISPLAY":
        if network.get("targetGoogleSearch") or network.get("targetSearchNetwork") or network.get("targetPartnerSearchNetwork") or network.get("targetContentNetwork") is not True:
            projection.gap("campaign.networkSettings")
    elif kind == "HOTEL":
        if network.get("targetSearchNetwork") or network.get("targetContentNetwork") or network.get("targetPartnerSearchNetwork"):
            projection.gap("campaign.networkSettings")
    elif network:
        projection.gap("campaign.networkSettings")
    projection.only(network, {"targetGoogleSearch", "targetSearchNetwork", "targetContentNetwork", "targetPartnerSearchNetwork"}, "campaign.networkSettings")
    projection.only(campaign.get("shoppingSetting") or {}, {"merchantId", "feedLabel", "campaignPriority", "enableLocal"}, "campaign.shoppingSetting")
    projection.only(campaign.get("dynamicSearchAdsSetting") or {}, {"domainName", "languageCode", "useSuppliedUrlsOnly"}, "campaign.dynamicSearchAdsSetting")
    projection.only(campaign.get("aiMaxSetting") or {}, {"enableAiMax", "bundlingRequired"}, "campaign.aiMaxSetting")
    if campaign.get("aiMaxSetting", {}).get("bundlingRequired"):
        projection.gap("campaign.aiMaxSetting.bundlingRequired")
    for index, setting in enumerate(campaign.get("assetAutomationSettings") or []):
        if setting.get("assetAutomationType") not in {"TEXT_ASSET_AUTOMATION", "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION"} or kind != "SEARCH":
            projection.gap(f"campaign.assetAutomationSettings.{index}")
        elif setting.get("assetAutomationStatus") not in {"OPTED_IN", "OPTED_OUT"}:
            projection.gap(f"campaign.assetAutomationSettings.{index}.assetAutomationStatus")
    projection.only(campaign.get("appCampaignSetting") or {}, {"appId", "appStore", "biddingStrategyGoalType"}, "campaign.appCampaignSetting")
    projection.only(campaign.get("selectiveOptimization") or {}, {"conversionActions"}, "campaign.selectiveOptimization")
    projection.only(campaign.get("pmaxCampaignSettings") or {}, {"localServicesEnabled"}, "campaign.pmaxCampaignSettings")
    for source, target, expected_time in (("startDateTime", "start_date", "00:00:00"), ("endDateTime", "end_date", "23:59:59")):
        raw = campaign.get(source)
        if raw:
            parts = str(raw).split(" ")
            if len(parts) != 2 or parts[1] != expected_time:
                projection.gap("campaign." + source)
            if target == "end_date" and data.get(target) and date.fromisoformat(data[target]) < date.today():
                projection.gap("campaign.expired_end_date")
    if kind != "APP":
        config_rows = snapshot.get("conversion_goal_config")
        if not isinstance(config_rows, list) or len(config_rows) != 1:
            projection.gap("conversion_goal_config", missing=True)
        else:
            config = _row(config_rows[0], "conversionGoalCampaignConfig")
            if config.get("campaign") not in (None, campaign["resourceName"]):
                projection.gap("conversion_goal_config.campaign")
            if config.get("customConversionGoal"):
                projection.gap("conversion_goal_config.customConversionGoal")
            if config.get("goalConfigLevel") == "CAMPAIGN":
                rows = snapshot.get("conversion_goals")
                if not isinstance(rows, list) or not rows:
                    projection.gap("conversion_goals", missing=True)
                else:
                    data["conversion_goals"] = []
                    for index, raw in enumerate(rows):
                        goal = _row(raw, "campaignConversionGoal")
                        if goal.get("campaign") not in (None, campaign["resourceName"]):
                            projection.gap(f"conversion_goals.{index}.campaign")
                        if not isinstance(goal.get("biddable"), bool):
                            projection.gap(f"conversion_goals.{index}.biddable", missing=True)
                        data["conversion_goals"].append({"category": goal.get("category"), "origin": goal.get("origin"), "biddable": goal.get("biddable")})
            elif config.get("goalConfigLevel") != "CUSTOMER":
                projection.gap("conversion_goal_config.goalConfigLevel")
    elif campaign.get("optimizationGoalSetting"):
        setting = campaign["optimizationGoalSetting"]
        if setting.get("optimizationGoals") != ["APP_PRE_REGISTRATION"] or data.get("app", {}).get("subtype") != "PRE_REGISTRATION":
            projection.gap("campaign.optimizationGoalSetting")
    supported = {
        "resourceName", "id", "name", "status", "advertisingChannelType", "advertisingChannelSubType",
        "campaignBudget", "biddingStrategyType", "biddingStrategy", *bidding_fields,
        "networkSettings", "shoppingSetting", "dynamicSearchAdsSetting", "aiMaxSetting", "assetAutomationSettings",
        "appCampaignSetting", "selectiveOptimization", "hotelSetting", "startDateTime", "endDateTime",
        "brandGuidelinesEnabled", "containsEuPoliticalAdvertising", "optimizationGoalSetting",
        "pmaxCampaignSettings",
        "primaryStatus", "primaryStatusReasons", "servingStatus", "optimizationScore",
        "resourceCount", "adServingOptimizationStatus", "baseCampaign",
    }
    projection.only(campaign, supported, "campaign")
    if campaign.get("baseCampaign") and campaign["baseCampaign"] != campaign["resourceName"]:
        projection.gap("campaign.baseCampaign")
    if campaign.get("adServingOptimizationStatus") not in (None, "", "OPTIMIZE", "UNSPECIFIED"):
        projection.gap("campaign.adServingOptimizationStatus")
    if kind != "APP" and campaign.get("optimizationGoalSetting"):
        projection.gap("campaign.optimizationGoalSetting")
    for index, raw in enumerate(snapshot.get("criteria") or []):
        row = _row(raw, "campaignCriterion")
        if row.get("negative") or not (row.get("location") or row.get("language")) or kind == "DEMAND_GEN":
            projection.gap(f"campaign.criteria.{index}")
        elif row.get("status") not in (None, "", "ENABLED"):
            projection.gap(f"campaign.criteria.{index}.status")
        projection.only(row, {"resourceName", "campaign", "criterionId", "type", "status", "negative", "location", "language", "displayName", "primaryStatus", "primaryStatusReasons"}, f"campaign.criteria.{index}")
        for field, constant, prefix in (("location", "geoTargetConstant", "geoTargetConstants/"), ("language", "languageConstant", "languageConstants/")):
            if field in row:
                value = row[field]
                reference = value.get(constant) if isinstance(value, dict) else None
                if not isinstance(reference, str) or not reference.startswith(prefix) or not reference[len(prefix):].isdigit():
                    projection.gap(f"campaign.criteria.{index}.{field}")
                elif isinstance(value, dict):
                    projection.only(value, {constant}, f"campaign.criteria.{index}.{field}")
    # Non-brand campaign assets (extensions, placements, etc.) are not part of
    # this draft contract and therefore cannot silently disappear in a clone.
    for index, raw in enumerate(snapshot.get("campaign_assets") or []):
        link = _row(raw, "campaignAsset")
        if kind != "PERFORMANCE_MAX" or link.get("fieldType") not in {"BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO"}:
            projection.gap(f"campaign_assets.{index}.fieldType")


def duplicate_campaign_draft(snapshot: dict[str, Any], customer_id: str, name: str) -> GoogleCampaignDraft:
    """Reconstruct a complete supported campaign or reject its precise gaps.

    All source groups and all non-removed ads are copied. The mutation builder
    always creates the new campaign paused. No source setting is guessed,
    truncated, silently reset or imported as arbitrary operation JSON.
    """
    campaign, kind = _snapshot_campaign(snapshot, customer_id)
    if not isinstance(name, str) or not name.strip() or len(name) > 255 or any(ord(character) < 32 for character in name):
        raise GoogleCampaignBuildError("google_duplicate_name_required", "Choose a valid name for the duplicated campaign.", details=[{"field": "changes.name", "code": "required_input", "message": "A nonblank campaign name of at most 255 characters is required."}])
    if snapshot.get("duplicate_controls_complete") is not True:
        raise GoogleCampaignBuildError("google_duplicate_snapshot_incomplete", "A complete source-control read is required; unread provider defaults cannot be guessed.", details=[{"field": "duplicate_controls_complete", "code": "source_unreadable", "message": "Read every supported and unmanaged source setting before reconstructing a duplicate."}])
    if not isinstance(snapshot.get("groups"), list) or not snapshot["groups"]:
        raise GoogleCampaignBuildError("google_duplicate_snapshot_incomplete", "Read every source group and ad before duplicating a campaign.")
    if kind != "PERFORMANCE_MAX" and not isinstance(snapshot.get("ads"), list):
        raise GoogleCampaignBuildError("google_duplicate_snapshot_incomplete", "Read every source ad before duplicating a campaign.")
    projection = _Projection(customer_id)
    groups: list[GoogleCampaignGroupInput] = []
    first_source = None
    for index, raw in enumerate(snapshot["groups"]):
        source = _row(raw, "adGroup", "assetGroup")
        group_id = str(source.get("id") or str(source.get("resourceName") or "").rsplit("/", 1)[-1])
        group_source = _group_snapshot(snapshot, customer_id, campaign, kind, group_id)
        first_source = first_source or group_source
        if kind == "PERFORMANCE_MAX":
            selected = [None]
        else:
            selected = [_row(row, "adGroupAd").get("resourceName") for row in snapshot["ads"] if _row(row, "adGroupAd").get("adGroup") == source.get("resourceName") and _row(row, "adGroupAd").get("status") != "REMOVED"]
        if not selected:
            projection.gap(f"groups.{index}.ads", missing=True)
            continue
        values = None
        additional = []
        for ad_index, resource in enumerate(selected):
            result = project_google_group(snapshot, customer_id, group_id, resource)
            for field in result["missing_fields"]:
                projection.gap(f"groups.{index}.ads.{ad_index}." + field, missing=True)
            for field in result["unsupported_fields"]:
                projection.gap(f"groups.{index}.ads.{ad_index}." + field)
            if not result["group"]:
                continue
            if values is None:
                values = result["group"]
            else:
                creative = {field: value for field, value in result["group"].items() if field not in GROUP_FIELDS | {"name", "ad_name", "ad_status"}}
                creative["name"] = result["group"].get("ad_name")
                creative["status"] = result["group"].get("ad_status", "ENABLED")
                additional.append(GoogleCampaignAdInput.model_validate(creative).model_dump(mode="json"))
        if values is not None:
            values["ads"] = additional
            groups.append(GoogleCampaignGroupInput.model_validate(values))
    if not groups:
        raise GoogleCampaignBuildError("google_duplicate_draft_incomplete", "The source campaign has no complete editable creative group.", details=[{"field": field, "code": "source_unreadable", "message": "Read this source field completely."} for field in projection.missing + projection.unsupported])
    if projection.missing or projection.unsupported:
        _raise_duplicate_gaps(projection)
    try:
        data = _context_draft(snapshot, customer_id, campaign, kind, groups[0], source_group=first_source).model_dump(mode="json")
    except ValidationError as exc:
        raise GoogleCampaignBuildError("google_duplicate_source_unsupported", "Source settings do not fit the editable contract without altering their values.", details=[{"field": ".".join(str(part) for part in error["loc"]), "code": "source_setting_unrepresented", "message": "This exact source value cannot be represented."} for error in exc.errors()]) from None
    data.update({"name": name, "groups": [group.model_dump(mode="json") for group in groups]})
    _duplicate_settings(projection, snapshot, campaign, kind, data)
    if projection.missing or projection.unsupported:
        _raise_duplicate_gaps(projection)
    try:
        draft = GoogleCampaignDraft.model_validate(data)
    except ValidationError as exc:
        raise GoogleCampaignBuildError("google_duplicate_source_unsupported", "Source settings do not fit the editable contract without altering their values.", details=[{"field": ".".join(str(part) for part in error["loc"]), "code": "source_setting_unrepresented", "message": "This exact source value cannot be represented."} for error in exc.errors()]) from None
    gaps = validate_draft_readiness(draft, customer_id)
    if gaps:
        raise GoogleCampaignBuildError("google_duplicate_draft_incomplete", "The reconstructed source campaign does not meet current Google requirements.", details=gaps)
    return draft


def _raise_duplicate_gaps(projection: _Projection) -> None:
    details = [{"field": field, "code": "source_unreadable", "message": "Read this source field completely."} for field in projection.missing]
    details += [{"field": field, "code": "source_setting_unrepresented", "message": "This source setting has no lossless editable contract."} for field in projection.unsupported]
    raise GoogleCampaignBuildError("google_duplicate_source_unsupported", "This campaign cannot be duplicated without losing provider settings.", details=details)
