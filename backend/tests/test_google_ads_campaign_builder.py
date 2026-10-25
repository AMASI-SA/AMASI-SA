"""Contract evidence at the deterministic provider boundary; no live mutations."""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from integrations_control_center.google_ads_campaign_contract import GoogleCampaignActionInput, GoogleCampaignDraft, campaign_capabilities
from integrations_control_center.google_ads_campaign_builder import (
    GoogleCampaignBuildError, build_campaign_operations, build_update_operations,
    native_to_micros, validate_draft_readiness,
)
from integrations_control_center.google_ads_creative_projection import duplicate_campaign_draft, project_google_group


CUSTOMER = "1234567890"
URL = "https://mezansalla.com/products/hoodie"
IMAGE = f"customers/{CUSTOMER}/assets/101"
SQUARE = f"customers/{CUSTOMER}/assets/102"
LOGO = f"customers/{CUSTOMER}/assets/103"
VIDEO = f"customers/{CUSTOMER}/assets/104"


def test_search_is_ready_without_deprecated_manual_language_and_never_emits_it():
    value = draft_for()
    value["language_ids"] = []
    assert validate_draft_readiness(value) == []
    for language_ids in ([], ["1001"]):
        value["language_ids"] = language_ids
        operations = build_campaign_operations(value, CUSTOMER)
        criteria = creates(operations, "campaignCriterionOperation")
        assert any("location" in item for item in criteria)
        assert not any("language" in item for item in criteria)


def test_search_manual_language_update_is_explicitly_unavailable():
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("set_targeting", CUSTOMER, snapshot(), {"language_ids": ["1000"]})
    assert error.value.code == "google_search_language_targeting_unavailable"


@pytest.mark.parametrize("action,changes", [("rename", {"name": "Updated Smart"}), ("pause", {}), ("enable", {}), ("set_budget", {"budget_native": "25"}), ("remove", {})])
def test_existing_smart_has_bounded_common_updates_and_no_creation(action, changes):
    before = snapshot()
    before["campaign"]["advertisingChannelType"] = "SMART"
    before["campaign"]["advertisingChannelSubType"] = "SMART_CAMPAIGN"
    operations = build_update_operations(action, CUSTOMER, before, changes)
    assert len(operations) == 1
    capability = next(item for item in campaign_capabilities() if item["type"] == "SMART")
    assert capability["create"] is False
    assert capability["manage"] is True
    assert set(capability["actions"]) == {"rename", "pause", "enable", "set_budget", "remove"}


@pytest.mark.parametrize("action,changes", [("set_bidding", {"bidding": {"strategy": "MAXIMIZE_CLICKS"}}), ("duplicate", {}), ("add_ad", {}), ("remove", {"ad_group_ad_resource": f"customers/{CUSTOMER}/adGroupAds/901~1"})])
def test_smart_specialized_or_new_entity_actions_stay_unavailable(action, changes):
    before = snapshot()
    before["campaign"]["advertisingChannelType"] = "SMART"
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations(action, CUSTOMER, before, changes)
    assert error.value.code == "google_smart_management_action_unsupported"


def draft_for(kind="SEARCH"):
    draft = {
        "account_id": CUSTOMER, "campaign_type": kind, "name": "AMASI Google launch",
        "budget_native": "139.123456", "budget_mode": "DAILY",
        "bidding": {"strategy": "MAXIMIZE_CONVERSIONS"},
        "geo_target_ids": ["2682"], "language_ids": ["1001"],
        "groups": [],
    }
    group = {"name": "AMASI products", "headlines": ["Personalized hoodie", "AMASI gifts", "Made for your family"], "descriptions": ["Choose your size and personal details.", "Shop the AMASI collection."], "final_urls": [URL]}
    if kind == "SEARCH":
        group["keywords"] = [{"text": "personalized hoodie", "match_type": "PHRASE"}, {"text": "free hoodie", "match_type": "BROAD", "negative": True}]
    elif kind == "DISPLAY":
        group.update({"headlines": ["AMASI family gifts"], "descriptions": ["Personalized gifts for your family."], "long_headlines": ["A personalized AMASI gift for the people you love"], "business_name": "AMASI", "image_assets": [IMAGE], "square_image_assets": [SQUARE], "logo_assets": [LOGO]})
    elif kind == "SHOPPING":
        draft["bidding"] = {"strategy": "MANUAL_CPC", "max_cpc_native": "1.25"}
        draft["shopping"] = {"merchant_id": "222", "feed_label": "SA", "campaign_priority": 1}
        group = {"name": "Selected hoodie offers", "product_offer_ids": ["hoodie-child", "hoodie-adult"]}
    elif kind == "PERFORMANCE_MAX":
        group.update({"long_headlines": ["Personalized AMASI hoodies for your family"], "business_name": "AMASI", "image_assets": [IMAGE], "square_image_assets": [SQUARE], "logo_assets": [LOGO], "video_assets": [VIDEO], "audience_ids": ["31"]})
    elif kind == "DEMAND_GEN":
        group.update({"headlines": ["A gift from the heart"], "descriptions": ["Create a personal AMASI gift."], "business_name": "AMASI", "image_assets": [IMAGE], "square_image_assets": [SQUARE], "logo_assets": [LOGO], "audience_ids": ["31"], "channels": ["YOUTUBE_IN_STREAM", "YOUTUBE_SHORTS"]})
    elif kind == "APP":
        draft["bidding"] = {"strategy": "TARGET_CPA", "target_cpa_native": "20.00"}
        draft["app"] = {"subtype": "INSTALLS", "app_id": "com.amasi.store", "app_store": "GOOGLE_APP_STORE"}
        group = {"name": "AMASI app", "headlines": ["Get the AMASI app"], "descriptions": ["Shop personalized gifts with AMASI."], "image_assets": [IMAGE], "video_assets": [VIDEO]}
    elif kind == "HOTEL":
        draft["bidding"] = {"strategy": "PERCENT_CPC", "max_cpc_native": "15"}
        draft["hotel"] = {"hotel_center_id": "333"}
        group = {"name": "Selected hotel properties", "hotel_ids": ["hotel-1", "hotel-2"], "percent_cpc_bid": "0.03"}
    draft["groups"] = [group]
    return draft


def creates(operations, key):
    return [operation[key]["create"] for operation in operations if key in operation and "create" in operation[key]]


def snapshot():
    campaign = f"customers/{CUSTOMER}/campaigns/900"
    group = f"customers/{CUSTOMER}/adGroups/901"
    return {
        "campaign": {"resourceName": campaign, "name": "AMASI existing", "status": "PAUSED", "advertisingChannelType": "SEARCH", "campaignBudget": f"customers/{CUSTOMER}/campaignBudgets/902", "maximizeConversions": {}, "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False, "targetContentNetwork": False}},
        "campaign_budget": {"resourceName": f"customers/{CUSTOMER}/campaignBudgets/902", "period": "DAILY", "amountMicros": "100000000", "explicitlyShared": False, "referenceCount": "1"},
        "criteria": [
            {"resourceName": f"customers/{CUSTOMER}/campaignCriteria/900~910", "campaign": campaign, "negative": False, "location": {"geoTargetConstant": "geoTargetConstants/2682"}},
            {"resourceName": f"customers/{CUSTOMER}/campaignCriteria/900~911", "campaign": campaign, "negative": True, "location": {"geoTargetConstant": "geoTargetConstants/1023"}},
            {"resourceName": f"customers/{CUSTOMER}/campaignCriteria/900~912", "campaign": campaign, "negative": False, "language": {"languageConstant": "languageConstants/1001"}},
        ],
        "groups": [{"resourceName": group, "campaign": campaign, "name": "Existing group", "type": "SEARCH_STANDARD", "status": "ENABLED", "criteria": []}],
        "ads": [{"resourceName": f"customers/{CUSTOMER}/adGroupAds/901~920", "adGroup": group, "status": "ENABLED", "ad": {"responsiveSearchAd": {}}}],
    }


@pytest.mark.parametrize("kind", ["SEARCH", "DISPLAY", "SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN", "APP", "HOTEL"])
def test_complete_hierarchy_is_deterministic_and_initial_campaign_paused(kind):
    draft = draft_for(kind)
    original = deepcopy(draft)
    operations = build_campaign_operations(draft, CUSTOMER)
    assert operations == build_campaign_operations(draft, CUSTOMER)
    assert draft == original
    campaigns = creates(operations, "campaignOperation")
    budgets = creates(operations, "campaignBudgetOperation")
    assert len(campaigns) == len(budgets) == 1
    assert campaigns[0]["status"] == "PAUSED"
    assert campaigns[0]["campaignBudget"] == budgets[0]["resourceName"]
    assert campaigns[0]["advertisingChannelType"] == ("MULTI_CHANNEL" if kind == "APP" else kind)
    assert budgets[0]["amountMicros"] == "139123456"
    assert budgets[0]["explicitlyShared"] is False
    if kind == "PERFORMANCE_MAX":
        assert creates(operations, "assetGroupOperation")
        assert creates(operations, "assetGroupAssetOperation")
        assert not creates(operations, "adGroupOperation")
    else:
        assert len(creates(operations, "adGroupOperation")) == 1
        assert len(creates(operations, "adGroupAdOperation")) == 1
        assert creates(operations, "adGroupAdOperation")[0]["adGroup"] == creates(operations, "adGroupOperation")[0]["resourceName"]


def test_search_criteria_negative_keyword_and_real_rsa_assets():
    operations = build_campaign_operations(draft_for(), CUSTOMER)
    ad = creates(operations, "adGroupAdOperation")[0]["ad"]
    assert set(ad) == {"finalUrls", "responsiveSearchAd"}
    assert len(ad["responsiveSearchAd"]["headlines"]) == 3
    assert len(ad["responsiveSearchAd"]["descriptions"]) == 2
    keywords = creates(operations, "adGroupCriterionOperation")
    assert keywords[1]["negative"] is True
    assert "status" not in keywords[1]
    assert creates(operations, "campaignCriterionOperation")[0]["location"]["geoTargetConstant"] == "geoTargetConstants/2682"


def test_ai_max_dependencies_and_no_deprecated_keyword_match_type():
    draft = draft_for()
    draft["ai_max"] = {"enabled": True, "text_customization": True, "final_url_expansion": True}
    draft["groups"][0]["disable_ai_max_search_term_matching"] = True
    operations = build_campaign_operations(draft, CUSTOMER)
    campaign = creates(operations, "campaignOperation")[0]
    assert campaign["aiMaxSetting"] == {"enableAiMax": True}
    assert campaign["assetAutomationSettings"] == [
        {"assetAutomationType": "TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN"},
        {"assetAutomationType": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION", "assetAutomationStatus": "OPTED_IN"},
    ]
    assert "keywordMatchType" not in campaign
    assert creates(operations, "adGroupOperation")[0]["aiMaxAdGroupSetting"]["disableSearchTermMatching"] is True
    draft["ai_max"]["text_customization"] = False
    with pytest.raises(ValidationError):
        GoogleCampaignDraft.model_validate(draft)


def test_dsa_targets_urls_without_setting_computed_ad_final_urls():
    draft = draft_for()
    draft["groups"] = [{"name": "Dynamic hoodie pages", "ad_format": "DSA", "descriptions": ["Create a personalized gift."], "final_urls": [URL]}]
    draft["search"] = {"dynamic_search_domain": "mezansalla.com", "dynamic_search_language_code": "ar"}
    operations = build_campaign_operations(draft, CUSTOMER)
    assert creates(operations, "adGroupOperation")[0]["type"] == "SEARCH_DYNAMIC_ADS"
    assert "finalUrls" not in creates(operations, "adGroupAdOperation")[0]["ad"]
    assert creates(operations, "campaignOperation")[0]["dynamicSearchAdsSetting"] == {"domainName": "mezansalla.com", "languageCode": "ar"}
    webpage = creates(operations, "adGroupCriterionOperation")[0]["webpage"]
    assert webpage["conditions"] == [{"operand": "URL", "operator": "EQUALS", "argument": URL}]


@pytest.mark.parametrize("format_name,asset_field,provider_field", [("UPLOADED_IMAGE", "image_assets", "imageAd"), ("UPLOADED_HTML5", "media_bundle_asset", "displayUploadAd")])
def test_uploaded_display_formats_use_existing_assets(format_name, asset_field, provider_field):
    draft = draft_for("DISPLAY")
    group = {"name": "Uploaded creative", "ad_format": format_name, "final_urls": [URL], asset_field: [IMAGE] if asset_field == "image_assets" else IMAGE}
    draft["groups"] = [group]
    ad = creates(build_campaign_operations(draft, CUSTOMER), "adGroupAdOperation")[0]["ad"]
    assert provider_field in ad
    assert "responsiveDisplayAd" not in ad
    if format_name == "UPLOADED_HTML5":
        assert ad[provider_field] == {"displayUploadProductType": "HTML5_UPLOAD_AD", "mediaBundle": {"asset": IMAGE}}


def test_shopping_partition_includes_selected_offers_and_excludes_other():
    operations = build_campaign_operations(draft_for("SHOPPING"), CUSTOMER)
    nodes = creates(operations, "adGroupCriterionOperation")
    assert len(nodes) == 4
    root, first, second, other = nodes
    assert root["listingGroup"] == {"type": "SUBDIVISION"}
    assert first["listingGroup"]["parentAdGroupCriterion"] == root["resourceName"]
    assert first["listingGroup"]["caseValue"] == {"productItemId": {"value": "hoodie-child"}}
    assert second["listingGroup"]["caseValue"] == {"productItemId": {"value": "hoodie-adult"}}
    assert other["listingGroup"]["caseValue"] == {"productItemId": {}}
    assert other["negative"] is True
    assert first["cpcBidMicros"] == "1250000"
    assert "cpcBidMicros" not in root and "cpcBidMicros" not in other
    assert creates(operations, "adGroupAdOperation")[0]["ad"] == {"shoppingProductAd": {}}


@pytest.mark.parametrize("brand_guidelines", [True, False])
def test_pmax_rich_assets_are_atomic_brand_links_and_retail_tree(brand_guidelines):
    draft = draft_for("PERFORMANCE_MAX")
    draft["brand_guidelines_enabled"] = brand_guidelines
    draft["shopping"] = {"merchant_id": "222", "feed_label": "SA"}
    draft["groups"][0]["product_offer_ids"] = ["hoodie-child"]
    operations = build_campaign_operations(draft, CUSTOMER)
    brand_operation = "campaignAssetOperation" if brand_guidelines else "assetGroupAssetOperation"
    brand_links = [link for link in creates(operations, brand_operation) if link["fieldType"] in {"BUSINESS_NAME", "LOGO"}]
    assert {link["fieldType"] for link in brand_links} == {"BUSINESS_NAME", "LOGO"}
    group_links = creates(operations, "assetGroupAssetOperation")
    assert len([link for link in group_links if link["fieldType"] == "HEADLINE"]) == 3
    assert len([link for link in group_links if link["fieldType"] == "DESCRIPTION"]) == 2
    signal = creates(operations, "assetGroupSignalOperation")[0]
    assert signal["audience"] == {"audience": f"customers/{CUSTOMER}/audiences/31"}
    nodes = creates(operations, "assetGroupListingGroupFilterOperation")
    assert [node["type"] for node in nodes] == ["SUBDIVISION", "UNIT_INCLUDED", "UNIT_EXCLUDED"]
    assert all(node["listingSource"] == "SHOPPING" for node in nodes)
    assert nodes[2]["caseValue"] == {"productItemId": {}}
    if brand_guidelines:
        assert all(link["fieldType"] not in {"BUSINESS_NAME", "LOGO"} for link in group_links)


@pytest.mark.parametrize("format_name", ["MULTI_ASSET", "VIDEO", "CAROUSEL", "PRODUCT"])
def test_demand_gen_format_resource_graph_and_ad_group_targeting(format_name):
    draft = draft_for("DEMAND_GEN")
    group = draft["groups"][0]
    group["ad_format"] = format_name
    if format_name != "MULTI_ASSET":
        group.pop("image_assets"); group.pop("square_image_assets")
    if format_name == "VIDEO":
        group["video_assets"] = [VIDEO]
        group["long_headlines"] = ["A personal AMASI gift for your family"]
    elif format_name == "CAROUSEL":
        group["carousel_cards"] = [{"headline": "Children's sizes", "final_url": URL + "?size=child", "image_asset": IMAGE}, {"headline": "Adult sizes", "final_url": URL + "?size=adult", "square_image_asset": SQUARE}]
    elif format_name == "PRODUCT":
        draft["shopping"] = {"merchant_id": "222"}
        group["product_offer_ids"] = ["hoodie-child"]
    operations = build_campaign_operations(draft, CUSTOMER)
    assert not creates(operations, "campaignCriterionOperation")
    target_group = creates(operations, "adGroupOperation")[0]
    assert "type" not in target_group
    assert target_group["audienceSetting"] == {"useAudienceGrouped": True}
    assert target_group["demandGenAdGroupSettings"]["channelControls"]["selectedChannels"]["youtubeShorts"] is True
    criteria = creates(operations, "adGroupCriterionOperation")
    assert any(row.get("location", {}).get("geoTargetConstant") == "geoTargetConstants/2682" for row in criteria)
    assert any(row.get("audience", {}).get("audience") == f"customers/{CUSTOMER}/audiences/31" for row in criteria)
    ad = creates(operations, "adGroupAdOperation")[0]["ad"]
    provider_field = {"MULTI_ASSET": "demandGenMultiAssetAd", "VIDEO": "demandGenVideoResponsiveAd", "CAROUSEL": "demandGenCarouselAd", "PRODUCT": "demandGenProductAd"}[format_name]
    assert provider_field in ad
    if format_name == "VIDEO":
        assert ad[provider_field]["businessName"] == {"text": "AMASI"}
        assert ad[provider_field]["videos"] == [{"asset": VIDEO}]
    if format_name == "CAROUSEL":
        cards = creates(operations, "assetOperation")
        assert len(cards) == 2
        assert cards[0]["demandGenCarouselCardAsset"]["marketingImageAsset"] == IMAGE
        assert cards[0]["finalUrls"] == [URL + "?size=child"]
        assert ad[provider_field]["carouselCards"][0]["asset"] == cards[0]["resourceName"]


@pytest.mark.parametrize("subtype,provider_ad", [("INSTALLS", "appAd"), ("ENGAGEMENT", "appEngagementAd"), ("PRE_REGISTRATION", "appPreRegistrationAd")])
def test_current_app_subtypes_have_correct_goal_assets_and_prerequisites(subtype, provider_ad):
    draft = draft_for("APP")
    draft["app"]["subtype"] = subtype
    if subtype == "ENGAGEMENT":
        draft["app"]["conversion_action_ids"] = ["88"]
        draft["groups"][0]["user_list_ids"] = ["77"]
        draft["groups"][0]["final_urls"] = ["https://mezansalla.com/app/hoodie"]
    operations = build_campaign_operations(draft, CUSTOMER)
    campaign = creates(operations, "campaignOperation")[0]
    assert campaign["advertisingChannelSubType"] == {"INSTALLS": "APP_CAMPAIGN", "ENGAGEMENT": "APP_CAMPAIGN_FOR_ENGAGEMENT", "PRE_REGISTRATION": "APP_CAMPAIGN_FOR_PRE_REGISTRATION"}[subtype]
    assert "type" not in creates(operations, "adGroupOperation")[0]
    ad = creates(operations, "adGroupAdOperation")[0]["ad"]
    assert provider_ad in ad
    assert "finalUrls" not in ad
    if subtype == "ENGAGEMENT":
        assert campaign["selectiveOptimization"] == {"conversionActions": [f"customers/{CUSTOMER}/conversionActions/88"]}
        assert ad["finalAppUrls"] == [{"osType": "ANDROID", "url": "https://mezansalla.com/app/hoodie"}]
        assert ad[provider_ad]["videos"] == [{"asset": VIDEO}]
    elif subtype == "PRE_REGISTRATION":
        assert campaign["optimizationGoalSetting"] == {"optimizationGoalTypes": ["APP_PRE_REGISTRATION"]}
        assert "selectiveOptimization" not in campaign
        assert ad[provider_ad]["youtubeVideos"] == [{"asset": VIDEO}]


def test_hotel_percent_cpc_is_fraction_and_complete_property_partition():
    operations = build_campaign_operations(draft_for("HOTEL"), CUSTOMER)
    campaign = creates(operations, "campaignOperation")[0]
    assert campaign["hotelSetting"] == {"hotelCenterId": "333"}
    assert campaign["networkSettings"] == {"targetGoogleSearch": True}
    group = creates(operations, "adGroupOperation")[0]
    assert group["type"] == "HOTEL_ADS" and group["percentCpcBidMicros"] == "30000"
    nodes = creates(operations, "adGroupCriterionOperation")
    assert nodes[1]["listingGroup"]["caseValue"] == {"hotelId": {"value": "hotel-1"}}
    assert nodes[1]["percentCpcBidMicros"] == "30000"
    assert nodes[-1]["negative"] is True


@pytest.mark.parametrize("kind", ["SEARCH", "SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN"])
def test_total_budget_v25_uses_custom_period_and_customer_local_date_time(kind):
    draft = draft_for(kind)
    draft.update({"budget_mode": "TOTAL", "start_date": "2026-10-20", "end_date": "2026-11-20"})
    operations = build_campaign_operations(draft, CUSTOMER)
    budget = creates(operations, "campaignBudgetOperation")[0]
    assert budget["period"] == "CUSTOM_PERIOD"
    assert budget["totalAmountMicros"] == "139123456" and "amountMicros" not in budget
    campaign = creates(operations, "campaignOperation")[0]
    assert campaign["startDateTime"] == "2026-10-20 00:00:00"
    assert campaign["endDateTime"] == "2026-11-20 23:59:59"
    assert "startDate" not in campaign and "endDate" not in campaign


def test_conversion_goal_false_is_explicit_in_atomic_new_campaign():
    draft = draft_for()
    draft["conversion_goals"] = [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}, {"category": "ADD_TO_CART", "origin": "WEBSITE", "biddable": False}]
    operations = build_campaign_operations(draft, CUSTOMER)
    updates = [operation["campaignConversionGoalOperation"] for operation in operations if "campaignConversionGoalOperation" in operation]
    assert updates == [
        {"update": {"resourceName": f"customers/{CUSTOMER}/campaignConversionGoals/-2~PURCHASE~WEBSITE", "biddable": True}, "updateMask": "biddable"},
        {"update": {"resourceName": f"customers/{CUSTOMER}/campaignConversionGoals/-2~ADD_TO_CART~WEBSITE", "biddable": False}, "updateMask": "biddable"},
    ]


@pytest.mark.parametrize("value,expected", [("0.000001", "1"), ("1.000001", "1000001"), ("9223372036854.775807", "9223372036854775807"), ("139.10", "139100000")])
def test_micros_are_exact_without_binary_float_or_rounding(value, expected):
    assert native_to_micros(value) == expected


@pytest.mark.parametrize("value", [1.1, "0", "-1", "NaN", "1e3", "0.0000001", "9223372036854.775808", "+1", "01.20"])
def test_money_rejects_non_strings_imprecision_and_int64_overflow(value):
    with pytest.raises((ValidationError, ValueError)):
        native_to_micros(value)


def test_incomplete_editable_draft_saves_but_cannot_compile_empty_campaign():
    draft = draft_for()
    draft["groups"] = []
    typed = GoogleCampaignDraft.model_validate(draft)
    assert typed.groups == []
    assert any(gap["field"] == "groups" for gap in validate_draft_readiness(typed, CUSTOMER))
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_campaign_operations(typed, CUSTOMER)
    assert error.value.code == "google_campaign_draft_incomplete"


@pytest.mark.parametrize("kind", ["VIDEO", "SMART", "LOCAL_SERVICES"])
def test_unsupported_new_campaigns_are_explicit_capability_states(kind):
    draft = draft_for()
    draft["campaign_type"] = kind
    row = next(row for row in campaign_capabilities() if row["type"] == kind)
    assert row["create"] is False and row["provider_limitation"]
    assert row["actions"] == list(("rename", "pause", "enable", "set_budget", "remove")) if kind == "SMART" else row["actions"] == []
    assert validate_draft_readiness(draft)[0]["code"] == "provider_creation_unsupported"
    with pytest.raises(GoogleCampaignBuildError):
        build_campaign_operations(draft, CUSTOMER)


@pytest.mark.parametrize("field,value", [("currency", "SAR"), ("operations", []), ("update_mask", "*"), ("status", "ENABLED")])
def test_draft_does_not_accept_provider_claims_or_raw_mutation_controls(field, value):
    draft = draft_for()
    draft[field] = value
    with pytest.raises(ValidationError):
        GoogleCampaignDraft.model_validate(draft)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp/image.png", "https://user:password@example.com", "https://example.com/with space", "https://example.com:bad/path"])
def test_invalid_or_credential_bearing_destination_urls_rejected(url):
    draft = draft_for()
    draft["groups"][0]["final_urls"] = [url]
    with pytest.raises(ValidationError):
        GoogleCampaignDraft.model_validate(draft)


@pytest.mark.parametrize("field,value", [("image_assets", ["customers/9999999999/assets/101"]), ("audience_ids", ["customers/9999999999/audiences/31"]), ("user_list_ids", ["customers/9999999999/userLists/77"])])
def test_reference_customer_prefix_cannot_be_discarded(field, value):
    draft = draft_for("PERFORMANCE_MAX")
    draft["groups"][0][field] = value
    with pytest.raises(ValidationError):
        GoogleCampaignDraft.model_validate(draft)


def test_mutated_typed_draft_is_revalidated_before_compilation():
    draft = GoogleCampaignDraft.model_validate(draft_for("DISPLAY"))
    draft.groups[0].image_assets.append("customers/9999999999/assets/999")
    with pytest.raises(ValidationError):
        build_campaign_operations(draft, CUSTOMER)
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_campaign_operations(draft_for(), "9999999999")
    assert error.value.code == "google_campaign_account_mismatch"


@pytest.mark.parametrize("kind,mutate,field", [
    ("SEARCH", lambda draft: draft["groups"][0].update({"headlines": ["Only one"]}), "groups.0.headlines"),
    ("DISPLAY", lambda draft: draft["groups"][0].update({"square_image_assets": []}), "groups.0.square_image_assets"),
    ("SHOPPING", lambda draft: draft.pop("shopping"), "shopping"),
    ("PERFORMANCE_MAX", lambda draft: draft["groups"][0].update({"logo_assets": []}), "groups.0.logo_assets"),
    ("DEMAND_GEN", lambda draft: draft["groups"][0].update({"ad_format": "VIDEO", "image_assets": [], "square_image_assets": []}), "groups.0.video_assets"),
    ("APP", lambda draft: draft["app"].update({"subtype": "ENGAGEMENT"}), "app.conversion_action_ids"),
    ("HOTEL", lambda draft: draft["groups"][0].pop("percent_cpc_bid"), "groups.0.percent_cpc_bid"),
])
def test_each_format_refuses_missing_write_prerequisite(kind, mutate, field):
    draft = draft_for(kind)
    mutate(draft)
    assert field in {gap["field"] for gap in validate_draft_readiness(draft)}
    with pytest.raises(GoogleCampaignBuildError):
        build_campaign_operations(draft, CUSTOMER)


def test_wide_cjk_characters_do_not_bypass_google_display_width_limit():
    draft = draft_for()
    draft["groups"][0]["headlines"][0] = "界" * 16
    assert any(gap["code"] == "creative_text_too_long" for gap in validate_draft_readiness(draft))


@pytest.mark.parametrize("action,status", [("pause", "PAUSED"), ("enable", "ENABLED")])
def test_campaign_status_updates_have_only_server_owned_mask(action, status):
    before = snapshot()
    operations = build_update_operations(action, CUSTOMER, before, {})
    assert operations == [{"campaignOperation": {"update": {"resourceName": before["campaign"]["resourceName"], "status": status}, "updateMask": "status"}}]
    with pytest.raises(GoogleCampaignBuildError):
        build_update_operations(action, CUSTOMER, before, {"updateMask": "*"})


def test_budget_updates_existing_bound_unshared_budget_and_refuses_shared_scope():
    before = snapshot()
    operations = build_update_operations("set_budget", CUSTOMER, before, {"budget_native": "149.01"})
    assert operations == [{"campaignBudgetOperation": {"update": {"resourceName": f"customers/{CUSTOMER}/campaignBudgets/902", "amountMicros": "149010000"}, "updateMask": "amountMicros"}}]
    before["campaign_budget"]["explicitlyShared"] = True
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("set_budget", CUSTOMER, before, {"budget_native": "149"})
    assert error.value.code == "google_shared_budget_requires_scope"


def test_standard_search_target_cpa_and_empty_maximize_message_use_subfield_masks():
    before = snapshot()
    operations = build_update_operations("set_bidding", CUSTOMER, before, {"bidding": {"strategy": "TARGET_CPA", "target_cpa_native": "39.50"}})
    operation = operations[0]["campaignOperation"]
    assert operation["update"]["maximizeConversions"] == {"targetCpaMicros": "39500000"}
    assert operation["updateMask"] == "maximizeConversions.targetCpaMicros"
    operations = build_update_operations("set_bidding", CUSTOMER, before, {"bidding": {"strategy": "MAXIMIZE_CONVERSIONS"}})
    assert operations[0]["campaignOperation"]["update"]["maximizeConversions"] == {}
    assert operations[0]["campaignOperation"]["updateMask"] == "maximizeConversions.targetCpaMicros"


def test_target_replacement_preserves_exclusions_and_untouched_language():
    before = snapshot()
    operations = build_update_operations("set_targeting", CUSTOMER, before, {"geo_target_ids": ["1000"]})
    removals = [row["campaignCriterionOperation"]["remove"] for row in operations if "remove" in row["campaignCriterionOperation"]]
    assert removals == [f"customers/{CUSTOMER}/campaignCriteria/900~910"]
    assert creates(operations, "campaignCriterionOperation") == [{"campaign": before["campaign"]["resourceName"], "negative": False, "location": {"geoTargetConstant": "geoTargetConstants/1000"}}]
    assert f"customers/{CUSTOMER}/campaignCriteria/900~911" not in removals
    assert f"customers/{CUSTOMER}/campaignCriteria/900~912" not in removals


@pytest.mark.parametrize("kind", ["VIDEO", "LOCAL_SERVICES"])
def test_management_does_not_apply_ordinary_campaign_mutations_to_restricted_types(kind):
    before = snapshot()
    before["campaign"]["advertisingChannelType"] = kind
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("pause", CUSTOMER, before, {})
    assert error.value.code == "google_campaign_management_unsupported"


def test_hotel_travel_and_local_services_pmax_do_not_inherit_ordinary_pmax_write_contract():
    before = snapshot()
    before["campaign"]["advertisingChannelType"] = "PERFORMANCE_MAX"
    for field, value in [("localServicesCampaignSettings", {"categoryId": "123"}), ("hotelPropertyAssetSet", f"customers/{CUSTOMER}/assetSets/99")]:
        trial = deepcopy(before)
        trial["campaign"][field] = value
        with pytest.raises(GoogleCampaignBuildError):
            build_update_operations("pause", CUSTOMER, trial, {})
    travel = next(row for row in campaign_capabilities() if row["type"] == "PERFORMANCE_MAX_TRAVEL")
    assert travel["implemented"] == "not_implemented" and travel["create"] is False


@pytest.mark.parametrize("action,status", [("pause_group", "PAUSED"), ("enable_group", "ENABLED")])
def test_group_status_updates_require_selected_campaign_parent(action, status):
    before = snapshot()
    operations = build_update_operations(action, CUSTOMER, before, {"group_id": "901"})
    assert operations[0]["adGroupOperation"]["update"] == {"resourceName": f"customers/{CUSTOMER}/adGroups/901", "status": status}
    before["groups"][0]["campaign"] = f"customers/{CUSTOMER}/campaigns/999"
    with pytest.raises(GoogleCampaignBuildError):
        build_update_operations(action, CUSTOMER, before, {"group_id": "901"})


def test_add_group_binds_complete_new_hierarchy_to_existing_campaign_without_new_budget():
    before = snapshot()
    group = draft_for()["groups"][0]
    operations = build_update_operations("add_group", CUSTOMER, before, {"group": group})
    assert not creates(operations, "campaignBudgetOperation") and not creates(operations, "campaignOperation")
    new_group = creates(operations, "adGroupOperation")[0]
    assert new_group["campaign"] == before["campaign"]["resourceName"]
    assert creates(operations, "adGroupAdOperation")[0]["adGroup"] == new_group["resourceName"]
    assert len(creates(operations, "adGroupCriterionOperation")) == 2


def test_ad_content_replacement_is_atomic_and_keeps_original_serving_status():
    before = snapshot()
    group = draft_for()["groups"][0]
    group.pop("keywords")
    operations = build_update_operations("update_ad", CUSTOMER, before, {"ad_group_ad_resource": before["ads"][0]["resourceName"], "group": group})
    assert operations[0]["adGroupAdOperation"]["create"]["status"] == "ENABLED"
    assert operations[0]["adGroupAdOperation"]["create"]["adGroup"] == before["groups"][0]["resourceName"]
    assert operations[-1] == {"adGroupAdOperation": {"remove": before["ads"][0]["resourceName"]}}
    group["keywords"] = [{"text": "new keyword"}]
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("update_ad", CUSTOMER, before, {"ad_group_ad_resource": before["ads"][0]["resourceName"], "group": group})
    assert error.value.code == "google_ad_only_inputs_invalid"


@pytest.mark.parametrize("action,status", [("pause_ad", "PAUSED"), ("enable_ad", "ENABLED")])
def test_ad_status_updates_are_bound_to_existing_campaign_and_group(action, status):
    before = snapshot()
    operations = build_update_operations(action, CUSTOMER, before, {"ad_group_ad_resource": before["ads"][0]["resourceName"]})
    assert operations == [{"adGroupAdOperation": {"update": {"resourceName": before["ads"][0]["resourceName"], "status": status}, "updateMask": "status"}}]


def test_pmax_asset_replacement_is_complete_and_keeps_existing_asset_group_identity():
    before = snapshot()
    before["campaign"].update({"advertisingChannelType": "PERFORMANCE_MAX", "brandGuidelinesEnabled": False})
    asset_group = f"customers/{CUSTOMER}/assetGroups/901"
    before["groups"] = [{"resourceName": asset_group, "name": "Existing PMax group", "campaign": before["campaign"]["resourceName"], "status": "ENABLED", "assets": [{"resourceName": f"customers/{CUSTOMER}/assetGroupAssets/901~103~LOGO", "assetGroup": asset_group, "asset": LOGO, "fieldType": "LOGO"}], "signals": []}]
    group = draft_for("PERFORMANCE_MAX")["groups"][0]
    operations = build_update_operations("update_group_assets", CUSTOMER, before, {"group_id": "901", "group": group})
    assert operations[0]["assetGroupOperation"]["update"]["resourceName"] == asset_group
    assert {"assetGroupAssetOperation": {"remove": f"customers/{CUSTOMER}/assetGroupAssets/901~103~LOGO"}} in operations
    assert not creates(operations, "assetGroupOperation")
    assert all(link["assetGroup"] == asset_group for link in creates(operations, "assetGroupAssetOperation"))
    assert len([link for link in creates(operations, "assetGroupAssetOperation") if link["fieldType"] == "HEADLINE"]) == 3


def test_action_input_has_create_alias_saved_revision_and_no_arbitrary_changes():
    action = GoogleCampaignActionInput.model_validate({"action": "create", "account_id": CUSTOMER, "draft_id": "draft-1", "draft_revision": 1, "idempotency_key": "create-1"})
    assert action.action == "create_campaign"
    with pytest.raises(ValidationError):
        GoogleCampaignActionInput.model_validate({"action": "create", "account_id": CUSTOMER, "draft_id": "draft-1"})
    with pytest.raises(ValidationError):
        GoogleCampaignActionInput.model_validate({"action": "set_budget", "account_id": CUSTOMER, "campaign_id": "900", "changes": {"budget_native": "100", "operations": []}})


def test_capabilities_do_not_claim_account_eligibility_or_live_verification():
    rows = campaign_capabilities()
    for row in rows:
        assert row["provider_api_version"] == "v25"
        assert row["live_verified"] is False
        if row["create"]:
            assert row["implemented"] == "builder_ready"
            assert row["formats"] and row["bidding_strategies"] and row["prerequisites"]
    assert "not a hard daily spending cap" in rows[0]["budget_explanation"]
    assert "30.4" in rows[0]["budget_explanation"]


def test_pmax_search_themes_are_explicit_signals_and_not_keyword_targets():
    draft = draft_for("PERFORMANCE_MAX")
    draft["groups"][0]["search_themes"] = ["personalized family hoodies", "custom gifts Saudi Arabia"]
    operations = build_campaign_operations(draft, CUSTOMER)
    themes = [signal["searchTheme"]["text"] for signal in creates(operations, "assetGroupSignalOperation") if "searchTheme" in signal]
    assert themes == ["personalized family hoodies", "custom gifts Saudi Arabia"]
    assert not creates(operations, "adGroupCriterionOperation")


def test_keyword_set_replaces_only_keyword_criteria_and_preserves_audience():
    before = snapshot()
    group = before["groups"][0]
    group["criteria"] = [
        {"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/901~941", "adGroup": group["resourceName"], "keyword": {"text": "old hoodie", "matchType": "PHRASE"}, "negative": False},
        {"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/901~942", "adGroup": group["resourceName"], "userList": {"userList": f"customers/{CUSTOMER}/userLists/77"}, "negative": False},
    ]
    operations = build_update_operations("set_keywords", CUSTOMER, before, {"group_id": "901", "keywords": [{"text": "new hoodie", "match_type": "EXACT"}, {"text": "free hoodie", "match_type": "BROAD", "negative": True}]})
    assert operations[0] == {"adGroupCriterionOperation": {"remove": f"customers/{CUSTOMER}/adGroupCriteria/901~941"}}
    assert len(operations) == 3
    assert creates(operations, "adGroupCriterionOperation")[0]["keyword"] == {"text": "new hoodie", "matchType": "EXACT"}
    assert "status" not in creates(operations, "adGroupCriterionOperation")[1]


def test_demand_gen_channel_updates_include_false_for_deselected_channels():
    before = snapshot()
    before["campaign"]["advertisingChannelType"] = "DEMAND_GEN"
    before["groups"][0].pop("type")
    operations = build_update_operations("set_channels", CUSTOMER, before, {"group_id": "901", "channels": ["YOUTUBE_SHORTS"]})
    body = operations[0]["adGroupOperation"]
    selected = body["update"]["demandGenAdGroupSettings"]["channelControls"]["selectedChannels"]
    assert selected == {"youtubeInStream": False, "youtubeInFeed": False, "youtubeShorts": True, "discover": False, "gmail": False, "display": False}
    assert len(body["updateMask"].split(",")) == 6


def test_unmanaged_pmax_creative_role_cannot_be_dropped_by_asset_replacement():
    before = snapshot()
    before["campaign"].update({"advertisingChannelType": "PERFORMANCE_MAX", "brandGuidelinesEnabled": False})
    resource = f"customers/{CUSTOMER}/assetGroups/901"
    before["groups"] = [{"resourceName": resource, "name": "Existing group", "campaign": before["campaign"]["resourceName"], "status": "ENABLED", "assets": [{"resourceName": f"customers/{CUSTOMER}/assetGroupAssets/901~103~CALL_TO_ACTION_SELECTION", "assetGroup": resource, "asset": LOGO, "fieldType": "CALL_TO_ACTION_SELECTION"}], "signals": []}]
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("update_group_assets", CUSTOMER, before, {"group_id": "901", "group": draft_for("PERFORMANCE_MAX")["groups"][0]})
    assert error.value.code == "google_asset_group_reconstruction_unsupported"


def test_non_search_keyword_edit_and_non_demand_gen_channel_edit_are_rejected():
    before = snapshot()
    with pytest.raises(GoogleCampaignBuildError):
        build_update_operations("set_channels", CUSTOMER, before, {"group_id": "901", "channels": ["YOUTUBE_SHORTS"]})
    before["campaign"]["advertisingChannelType"] = "DISPLAY"
    with pytest.raises(GoogleCampaignBuildError):
        build_update_operations("set_keywords", CUSTOMER, before, {"group_id": "901", "keywords": [{"text": "hoodie"}]})


def complete_source_snapshot():
    before = snapshot()
    before["duplicate_controls_complete"] = True
    before["campaign"]["containsEuPoliticalAdvertising"] = "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    before["criteria"] = [row for row in before["criteria"] if not row.get("negative")]
    before["conversion_goal_config"] = [{"campaign": before["campaign"]["resourceName"], "goalConfigLevel": "CUSTOMER"}]
    group = before["groups"][0]
    group["criteria"] = [{"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/901~940", "adGroup": group["resourceName"], "type": "KEYWORD", "status": "PAUSED", "negative": False, "keyword": {"text": "personalized hoodie", "matchType": "PHRASE"}}]
    before["ads"][0]["ad"] = {
        "id": "920", "type": "RESPONSIVE_SEARCH_AD",
        "finalUrls": [URL],
        "responsiveSearchAd": {
            "headlines": [{"text": value} for value in draft_for()["groups"][0]["headlines"]],
            "descriptions": [{"text": value} for value in draft_for()["groups"][0]["descriptions"]],
        },
    }
    return before


def test_additional_ads_form_one_group_and_preserve_child_serving_status():
    draft = draft_for()
    group = draft["groups"][0]
    group["status"] = "PAUSED"
    group["ad_status"] = "PAUSED"
    group["keywords"][0]["status"] = "PAUSED"
    group["ads"] = [{
        "status": "ENABLED", "ad_format": "RSA",
        "headlines": ["Second hoodie offer", "Personal AMASI gift", "Create your family gift"],
        "descriptions": ["Choose a personal AMASI gift.", "Shop personalized hoodies."],
        "final_urls": [URL],
    }]
    operations = build_campaign_operations(draft, CUSTOMER)
    assert len(creates(operations, "adGroupOperation")) == 1
    assert creates(operations, "adGroupOperation")[0]["status"] == "PAUSED"
    ads = creates(operations, "adGroupAdOperation")
    assert len(ads) == 2 and [ad["status"] for ad in ads] == ["PAUSED", "ENABLED"]
    assert len({ad["adGroup"] for ad in ads}) == 1
    assert creates(operations, "adGroupCriterionOperation")[0]["status"] == "PAUSED"
    assert creates(operations, "campaignOperation")[0]["status"] == "PAUSED"


def test_additional_ad_cannot_use_foreign_assets_or_immutable_other_search_group_type():
    draft = draft_for()
    draft["groups"][0]["ads"] = [{"ad_format": "UPLOADED_IMAGE", "image_assets": ["customers/999/assets/103"]}]
    with pytest.raises(ValidationError):
        GoogleCampaignDraft.model_validate(draft)
    draft["groups"][0]["ads"] = [{"ad_format": "DSA", "descriptions": ["A dynamic description."]}]
    draft["search"] = {"dynamic_search_domain": "mezansalla.com", "dynamic_search_language_code": "en"}
    assert any(issue["code"] == "incompatible_input" for issue in validate_draft_readiness(draft))


def test_demand_gen_group_target_overrides_do_not_change_other_groups():
    draft = draft_for("DEMAND_GEN")
    second = deepcopy(draft["groups"][0])
    second.update({"name": "Second market", "geo_target_ids": ["2840"], "language_ids": ["1000"]})
    draft["groups"].append(second)
    operations = build_campaign_operations(draft, CUSTOMER)
    group_refs = [group["resourceName"] for group in creates(operations, "adGroupOperation")]
    rows = creates(operations, "adGroupCriterionOperation")
    assert {row["location"]["geoTargetConstant"] for row in rows if row.get("adGroup") == group_refs[0] and row.get("location")} == {"geoTargetConstants/2682"}
    assert {row["location"]["geoTargetConstant"] for row in rows if row.get("adGroup") == group_refs[1] and row.get("location")} == {"geoTargetConstants/2840"}


def test_existing_creative_projection_is_lossless_and_source_revision_tracks_copy():
    before = complete_source_snapshot()
    result = project_google_group(before, CUSTOMER, "901")
    assert result["editable"] is True
    assert result["group"]["headlines"] == draft_for()["groups"][0]["headlines"]
    assert result["group"]["keywords"][0]["status"] == "PAUSED"
    assert "keywords" not in result["creative"]
    assert result["source_revision"] == project_google_group(deepcopy(before), CUSTOMER, "901")["source_revision"]
    before["ads"][0]["ad"]["responsiveSearchAd"]["headlines"][0]["text"] = "Updated hoodie"
    assert result["source_revision"] != project_google_group(before, CUSTOMER, "901")["source_revision"]


def test_google_text_policy_summary_info_is_read_only_evidence_not_creative_input():
    before = complete_source_snapshot()
    ad = before["ads"][0]["ad"]["responsiveSearchAd"]
    for text in ad["headlines"] + ad["descriptions"]:
        text["policySummaryInfo"] = {"approvalStatus": "APPROVED", "reviewStatus": "REVIEWED"}
    result = project_google_group(before, CUSTOMER, "901")
    assert result["editable"] is True
    assert result["group"]["headlines"] == draft_for()["groups"][0]["headlines"]
    assert "policySummaryInfo" not in str(result["creative"])
    ad["headlines"][0]["pinnedField"] = "HEADLINE_1"
    assert project_google_group(before, CUSTOMER, "901")["editable"] is False


@pytest.mark.parametrize("change,field", [
    ("pinned", "pinnedField"),
    ("tracking", "trackingUrlTemplate"),
    ("path", "path1"),
])
def test_external_ad_projection_never_discards_unrepresented_settings(change, field):
    before = complete_source_snapshot()
    ad = before["ads"][0]["ad"]
    if change == "pinned":
        ad["responsiveSearchAd"]["headlines"][0]["pinnedField"] = "HEADLINE_1"
    elif change == "tracking":
        ad["trackingUrlTemplate"] = "https://track.example.test/?url={lpurl}"
    else:
        ad["responsiveSearchAd"]["path1"] = "gifts"
    result = project_google_group(before, CUSTOMER, "901")
    assert result["editable"] is False and result["creative"] is None
    assert any(field in gap for gap in result["unsupported_fields"])
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Copy")
    assert error.value.code == "google_duplicate_source_unsupported"


def test_duplicate_reconstructs_every_group_ad_status_keyword_goal_and_network():
    before = complete_source_snapshot()
    before["groups"][0]["status"] = "PAUSED"
    before["campaign"]["networkSettings"]["targetPartnerSearchNetwork"] = True
    before["campaign"].update({"startDateTime": "2026-10-13 00:00:00", "endDateTime": "2030-12-31 23:59:59"})
    before["conversion_goal_config"][0]["goalConfigLevel"] = "CAMPAIGN"
    before["conversion_goals"] = [{"campaign": before["campaign"]["resourceName"], "category": "PURCHASE", "origin": "WEBSITE", "biddable": True}, {"campaign": before["campaign"]["resourceName"], "category": "PAGE_VIEW", "origin": "WEBSITE", "biddable": False}]
    second_ad = deepcopy(before["ads"][0])
    second_ad.update({"resourceName": f"customers/{CUSTOMER}/adGroupAds/901~921", "status": "PAUSED"})
    second_ad["ad"].update({"id": "921"})
    second_ad["ad"]["responsiveSearchAd"]["headlines"][0]["text"] = "Alternate hoodie gift"
    before["ads"].append(second_ad)
    copied = duplicate_campaign_draft(before, CUSTOMER, "All-source copy")
    assert copied.name == "All-source copy" and copied.budget_native == "100"
    assert copied.search.target_partner_search_network is True
    assert copied.start_date == "2026-10-13" and copied.end_date == "2030-12-31"
    assert copied.groups[0].status == "PAUSED"
    assert copied.groups[0].keywords[0].status == "PAUSED"
    assert len(copied.groups[0].ads) == 1 and copied.groups[0].ads[0].status == "PAUSED"
    assert [goal.biddable for goal in copied.conversion_goals] == [True, False]
    operations = build_campaign_operations(copied, CUSTOMER)
    assert len(creates(operations, "adGroupAdOperation")) == 2
    assert creates(operations, "campaignOperation")[0]["status"] == "PAUSED"
    assert creates(operations, "campaignOperation")[0]["networkSettings"]["targetPartnerSearchNetwork"] is True


def test_projection_requires_selected_ad_when_existing_group_has_multiple_ads():
    before = complete_source_snapshot()
    second = deepcopy(before["ads"][0])
    second["resourceName"] = f"customers/{CUSTOMER}/adGroupAds/901~921"
    before["ads"].append(second)
    assert project_google_group(before, CUSTOMER, "901")["editable"] is False
    assert project_google_group(before, CUSTOMER, "901", before["ads"][0]["resourceName"])["editable"] is True
    with pytest.raises((ValueError, GoogleCampaignBuildError)):
        project_google_group(before, CUSTOMER, "901", f"customers/{CUSTOMER}/adGroupAds/999~920")


@pytest.mark.parametrize("mutation,field", [
    (lambda before: before["campaign"].update({"biddingStrategy": f"customers/{CUSTOMER}/biddingStrategies/33"}), "biddingStrategy"),
    (lambda before: before["campaign_budget"].update({"explicitlyShared": True}), "explicitlyShared"),
    (lambda before: before["conversion_goal_config"][0].update({"customConversionGoal": f"customers/{CUSTOMER}/customConversionGoals/1"}), "customConversionGoal"),
    (lambda before: before["campaign"].update({"endDateTime": "2020-01-01 23:59:59"}), "expired_end_date"),
    (lambda before: before["campaign"].update({"startDateTime": "2026-10-13 12:30:00"}), "startDateTime"),
])
def test_duplicate_rejects_unrepresented_source_semantics(mutation, field):
    before = complete_source_snapshot()
    mutation(before)
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Unsafe copy")
    assert error.value.code == "google_duplicate_source_unsupported"
    assert any(field in detail["field"] for detail in error.value.details)


def test_duplicate_missing_conversion_configuration_cannot_guess_default_goals():
    before = complete_source_snapshot()
    before.pop("conversion_goal_config")
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Unproven goals")
    assert any(detail["field"] == "conversion_goal_config" for detail in error.value.details)


def test_duplicate_does_not_drop_campaign_negative_location_criteria():
    before = complete_source_snapshot()
    before["criteria"].append(snapshot()["criteria"][1])
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Lossy copy")
    assert any("campaign.criteria" in detail["field"] for detail in error.value.details)


def test_duplicate_requires_server_read_proof_even_when_missing_controls_look_default():
    before = complete_source_snapshot()
    before.pop("duplicate_controls_complete")
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Incomplete source proof")
    assert error.value.code == "google_duplicate_snapshot_incomplete"


def pmax_source_snapshot(*, retail=False):
    before = complete_source_snapshot()
    before["campaign"].update({"advertisingChannelType": "PERFORMANCE_MAX", "brandGuidelinesEnabled": False})
    before["campaign"].pop("networkSettings")
    resource = f"customers/{CUSTOMER}/assetGroups/901"
    source = draft_for("PERFORMANCE_MAX")["groups"][0]
    assets = []
    asset_id = 110
    for field, role in (("headlines", "HEADLINE"), ("long_headlines", "LONG_HEADLINE"), ("descriptions", "DESCRIPTION")):
        for text in source[field]:
            assets.append({"resourceName": f"customers/{CUSTOMER}/assetGroupAssets/901~{asset_id}~{role}", "assetGroup": resource, "asset": f"customers/{CUSTOMER}/assets/{asset_id}", "fieldType": role, "status": "ENABLED", "textAsset": {"text": text}})
            asset_id += 1
    assets.append({"resourceName": f"customers/{CUSTOMER}/assetGroupAssets/901~119~BUSINESS_NAME", "assetGroup": resource, "asset": f"customers/{CUSTOMER}/assets/119", "fieldType": "BUSINESS_NAME", "textAsset": {"text": "AMASI"}})
    for asset, role in ((IMAGE, "MARKETING_IMAGE"), (SQUARE, "SQUARE_MARKETING_IMAGE"), (LOGO, "LOGO"), (VIDEO, "YOUTUBE_VIDEO")):
        assets.append({"resourceName": f"customers/{CUSTOMER}/assetGroupAssets/901~{asset.rsplit('/', 1)[-1]}~{role}", "assetGroup": resource, "asset": asset, "fieldType": role, "status": "ENABLED"})
    group = {"resourceName": resource, "id": "901", "name": "Existing PMax", "campaign": before["campaign"]["resourceName"], "status": "PAUSED", "finalUrls": [URL], "assets": assets, "signals": [{"resourceName": f"customers/{CUSTOMER}/assetGroupSignals/901~945", "assetGroup": resource, "searchTheme": {"text": "personalized family gifts"}}], "listing_group_filters": []}
    if retail:
        before["campaign"]["shoppingSetting"] = {"merchantId": "222", "feedLabel": "SA", "campaignPriority": 0, "enableLocal": False}
        root = f"customers/{CUSTOMER}/assetGroupListingGroupFilters/901~960"
        group["listing_group_filters"] = [
            {"resourceName": root, "assetGroup": resource, "type": "SUBDIVISION", "listingSource": "SHOPPING"},
            {"resourceName": f"customers/{CUSTOMER}/assetGroupListingGroupFilters/901~961", "assetGroup": resource, "type": "UNIT_INCLUDED", "listingSource": "SHOPPING", "parentListingGroupFilter": root, "caseValue": {"productItemId": {"value": "hoodie-child"}}},
            {"resourceName": f"customers/{CUSTOMER}/assetGroupListingGroupFilters/901~962", "assetGroup": resource, "type": "UNIT_EXCLUDED", "listingSource": "SHOPPING", "parentListingGroupFilter": root, "caseValue": {"productItemId": {}}},
        ]
    before.update({"groups": [group], "ads": [], "campaign_assets": []})
    return before


@pytest.mark.parametrize("retail", [False, True])
def test_pmax_projection_and_duplicate_keep_full_creative_signals_and_listing_partition(retail):
    before = pmax_source_snapshot(retail=retail)
    projection = project_google_group(before, CUSTOMER, "901")
    assert projection["editable"] is True
    assert projection["group"]["business_name"] == "AMASI"
    assert projection["group"]["search_themes"] == ["personalized family gifts"]
    copied = duplicate_campaign_draft(before, CUSTOMER, "PMax copy")
    assert copied.groups[0].status == "PAUSED"
    assert copied.groups[0].video_assets == [VIDEO]
    assert copied.groups[0].product_offer_ids == (["hoodie-child"] if retail else [])
    operations = build_campaign_operations(copied, CUSTOMER)
    assert len(creates(operations, "assetGroupAssetOperation")) == len(before["groups"][0]["assets"])
    filters = creates(operations, "assetGroupListingGroupFilterOperation")
    assert len(filters) == (3 if retail else 0)


def test_pmax_complex_listing_partition_or_unmanaged_signal_cannot_be_duplicated():
    before = pmax_source_snapshot(retail=True)
    before["groups"][0]["listing_group_filters"][1]["caseValue"] = {"productBrand": {"value": "AMASI"}}
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Lossy PMax copy")
    assert any("caseValue" in gap["field"] for gap in error.value.details)
    before = pmax_source_snapshot()
    before["groups"][0]["signals"].append({"resourceName": f"customers/{CUSTOMER}/assetGroupSignals/901~946", "assetGroup": before["groups"][0]["resourceName"], "localServicesId": {"serviceId": "plumber"}})
    with pytest.raises(GoogleCampaignBuildError):
        duplicate_campaign_draft(before, CUSTOMER, "Wrong specialized signals")


def test_shopping_source_duplicate_preserves_selected_products_excluded_other_and_native_bids():
    before = complete_source_snapshot()
    before["campaign"].update({"advertisingChannelType": "SHOPPING", "manualCpc": {}, "shoppingSetting": {"merchantId": "222", "feedLabel": "SA", "campaignPriority": 1, "enableLocal": False}})
    before["campaign"].pop("maximizeConversions")
    group = before["groups"][0]
    group.update({"type": "SHOPPING_PRODUCT_ADS", "cpcBidMicros": "2250000"})
    root = f"customers/{CUSTOMER}/adGroupCriteria/901~960"
    group["criteria"] = [
        {"resourceName": root, "adGroup": group["resourceName"], "type": "LISTING_GROUP", "negative": False, "listingGroup": {"type": "SUBDIVISION"}},
        {"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/901~961", "adGroup": group["resourceName"], "type": "LISTING_GROUP", "negative": False, "cpcBidMicros": "2250000", "listingGroup": {"type": "UNIT", "parentAdGroupCriterion": root, "caseValue": {"productItemId": {"value": "hoodie-child"}}}},
        {"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/901~962", "adGroup": group["resourceName"], "type": "LISTING_GROUP", "negative": True, "listingGroup": {"type": "UNIT", "parentAdGroupCriterion": root, "caseValue": {"productItemId": {}}}},
    ]
    before["ads"][0]["ad"] = {"id": "920", "type": "SHOPPING_PRODUCT_AD", "shoppingProductAd": {}}
    copied = duplicate_campaign_draft(before, CUSTOMER, "Shopping copy")
    assert copied.shopping.campaign_priority == 1 and copied.groups[0].cpc_bid_native == "2.25"
    assert copied.groups[0].product_offer_ids == ["hoodie-child"]
    operations = build_campaign_operations(copied, CUSTOMER)
    criteria = creates(operations, "adGroupCriterionOperation")
    assert len(criteria) == 3 and criteria[-1]["negative"] is True
    assert criteria[1]["cpcBidMicros"] == "2250000"


def test_local_services_pmax_cannot_use_ordinary_campaign_or_group_write_contract():
    before = pmax_source_snapshot()
    before["campaign"]["pmaxCampaignSettings"] = {"localServicesEnabled": True}
    with pytest.raises(GoogleCampaignBuildError) as error:
        build_update_operations("pause", CUSTOMER, before, {})
    assert error.value.code == "google_campaign_management_unsupported"
    with pytest.raises(GoogleCampaignBuildError):
        project_google_group(before, CUSTOMER, "901")


@pytest.mark.parametrize("name", [None, "", "   ", "A" * 256])
def test_duplicate_invalid_name_is_a_reviewable_input_gap(name):
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(complete_source_snapshot(), CUSTOMER, name)
    assert error.value.code == "google_duplicate_name_required"
    assert error.value.details[0]["field"] == "changes.name"


def test_explicit_unknown_false_control_is_not_silently_replaced_with_a_provider_default():
    before = complete_source_snapshot()
    before["ads"][0]["ad"]["responsiveSearchAd"]["unmanagedAutomationEnabled"] = False
    result = project_google_group(before, CUSTOMER, "901")
    assert result["editable"] is False
    assert "ad.unmanagedAutomationEnabled" in result["unsupported_fields"]


def test_duplicate_does_not_invent_eu_political_declaration_from_unspecified_source():
    before = complete_source_snapshot()
    before["campaign"]["containsEuPoliticalAdvertising"] = "UNSPECIFIED"
    with pytest.raises(GoogleCampaignBuildError) as error:
        duplicate_campaign_draft(before, CUSTOMER, "Unknown declaration")
    assert any(gap["field"] == "campaign.containsEuPoliticalAdvertising" for gap in error.value.details)

