"""Independent verification/recovery fixtures; no provider mutation is available."""
from __future__ import annotations

import copy
import re

import pytest
from fastapi import HTTPException

from integrations_control_center.google_ads_verification import (
    recover_create_operation_names,
    verify_google_operations,
)

ACCOUNT = "1234567890"
LABEL = "MEZAN-a4f8b60d-876d-45ea-9ced-a4823d7afe45"
PNG_ONE_PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="


def resource(collection, value):
    return f"customers/{ACCOUNT}/{collection}/{value}"


def field(kind="STRING", repeated=False):
    return {"selectable": True, "dataType": kind, "isRepeated": repeated}


METADATA = {
    "campaign.resource_name": field("RESOURCE_NAME"), "campaign.name": field(), "campaign.status": field("ENUM"),
    "campaign.campaign_budget": field("RESOURCE_NAME"), "campaign.bidding_strategy_type": field("ENUM"),
    "campaign.network_settings.target_search_network": field("BOOLEAN"),
    "campaign_budget.resource_name": field("RESOURCE_NAME"), "campaign_budget.amount_micros": field("INT64"),
    "campaign_budget.explicitly_shared": field("BOOLEAN"), "campaign_budget.delivery_method": field("ENUM"), "campaign_budget.period": field("ENUM"),
    "campaign_criterion.resource_name": field("RESOURCE_NAME"), "campaign_criterion.campaign": field("RESOURCE_NAME"),
    "campaign_criterion.negative": field("BOOLEAN"), "campaign_criterion.location.geo_target_constant": field("RESOURCE_NAME"),
    "ad_group.resource_name": field("RESOURCE_NAME"), "ad_group.campaign": field("RESOURCE_NAME"), "ad_group.name": field(),
    "ad_group.status": field("ENUM"), "ad_group.type": field("ENUM"), "ad_group.cpc_bid_micros": field("INT64"),
    "ad_group_criterion.resource_name": field("RESOURCE_NAME"), "ad_group_criterion.ad_group": field("RESOURCE_NAME"),
    "ad_group_criterion.negative": field("BOOLEAN"), "ad_group_criterion.status": field("ENUM"),
    "ad_group_criterion.keyword.text": field(), "ad_group_criterion.keyword.match_type": field("ENUM"),
    "ad_group_criterion.listing_group.type": field("ENUM"),
    "ad_group_criterion.listing_group.case_value.product_item_id.value": field(),
    "ad_group_ad.resource_name": field("RESOURCE_NAME"), "ad_group_ad.ad_group": field("RESOURCE_NAME"),
    "ad_group_ad.status": field("ENUM"), "ad_group_ad.ad.type": field("ENUM"),
    "ad_group_ad.ad.final_urls": field(repeated=True), "ad_group_ad.ad.responsive_search_ad.headlines": field("MESSAGE", repeated=True),
    "ad_group_ad.ad.responsive_search_ad.descriptions": field("MESSAGE", repeated=True),
    "ad_group_ad.ad.demand_gen_carousel_ad.carousel_cards": field("MESSAGE", repeated=True),
    "asset.resource_name": field("RESOURCE_NAME"), "asset.name": field(), "asset.type": field("ENUM"),
    "asset.text_asset.text": field(), "asset.youtube_video_asset.youtube_video_id": field(),
    "asset.image_asset.full_size.width_pixels": field("INT64"), "asset.image_asset.full_size.height_pixels": field("INT64"),
    "label.resource_name": field("RESOURCE_NAME"), "label.name": field(),
    "campaign_label.resource_name": field("RESOURCE_NAME"), "campaign_label.campaign": field("RESOURCE_NAME"), "campaign_label.label": field("RESOURCE_NAME"),
    "campaign_conversion_goal.resource_name": field("RESOURCE_NAME"), "campaign_conversion_goal.biddable": field("BOOLEAN"),
}


def camel(value):
    first, *rest = value.split("_")
    return first + "".join(item.title() for item in rest)


def quoted(value):
    return value.replace("\\'", "'").replace("\\\\", "\\")


class ReadClient:
    customer_id = ACCOUNT

    def __init__(self, records=None, metadata=None):
        self.records = copy.deepcopy(records or {})
        self.metadata = copy.deepcopy(METADATA if metadata is None else metadata)
        self.queries = []
        self.metadata_calls = []
        self.complete = True
        self.failure = None

    async def field_metadata(self, names):
        self.metadata_calls.append(list(names))
        return {name: self.metadata[name] for name in names if name in self.metadata}

    async def search(self, query, max_rows=2000):
        self.queries.append(query)
        if self.failure:
            raise self.failure
        match = re.search(r"SELECT (.*?) FROM ([a-z_]+) WHERE (.*)$", query)
        assert match, query
        columns, table, where = match.groups()
        for column in columns.split(", "):
            assert self.metadata.get(column, {}).get("selectable") is True, column
        values = copy.deepcopy(self.records.get(camel(table), []))
        in_clause = re.search(r"\.resource_name IN \((.*)\)", where)
        if in_clause:
            names = {quoted(value) for value in re.findall(r"'((?:\\.|[^'])*)'", in_clause.group(1))}
            values = [value for value in values if value["resourceName"] in names]
        else:
            constraint = re.fullmatch(r"([a-z_.]+) = '((?:\\.|[^'])*)'", where)
            assert constraint, where
            column, text = constraint.groups()
            text = quoted(text)
            if column == "label.name" and table == "campaign_label":
                labels = {value["resourceName"]: value["name"] for value in self.records.get("label", [])}
                values = [value for value in values if labels.get(value["label"]) == text]
            else:
                path = [camel(part) for part in column.split(".")[1:]]
                def get(value):
                    for part in path:
                        value = value.get(part, {}) if isinstance(value, dict) else {}
                    return value
                values = [value for value in values if get(value) == text]
        return [{camel(table): value} for value in values[:max_rows]], self.complete and len(values) <= max_rows


def graph():
    budget, campaign, group, label = (resource(kind, number) for kind, number in (
        ("campaignBudgets", "-1"), ("campaigns", "-2"), ("adGroups", "-3"), ("labels", "-9000000000000")))
    creative = {"finalUrls": ["https://mezansalla.com/product"], "responsiveSearchAd": {
        "headlines": [{"text": "اختر الهودي"}, {"text": "هودي بالاسم"}, {"text": "اطلب من أماسي"}],
        "descriptions": [{"text": "اختر مقاسك"}, {"text": "اكتشف منتجات أماسي"}],
    }}
    operations = [
        {"campaignBudgetOperation": {"create": {"resourceName": budget, "amountMicros": "90000000", "explicitlyShared": False, "deliveryMethod": "STANDARD", "period": "DAILY"}}},
        {"campaignOperation": {"create": {"resourceName": campaign, "name": "Campaign name preserved", "status": "PAUSED", "campaignBudget": budget, "targetSpend": {}}}},
        {"campaignCriterionOperation": {"create": {"campaign": campaign, "negative": False, "location": {"geoTargetConstant": "geoTargetConstants/2124"}}}},
        {"adGroupOperation": {"create": {"resourceName": group, "campaign": campaign, "name": "Group name preserved", "status": "ENABLED", "type": "SEARCH_STANDARD"}}},
        {"adGroupCriterionOperation": {"create": {"adGroup": group, "negative": False, "status": "ENABLED", "keyword": {"text": "هودي بالاسم", "matchType": "PHRASE"}}}},
        {"adGroupAdOperation": {"create": {"adGroup": group, "status": "ENABLED", "ad": creative}}},
        {"labelOperation": {"create": {"resourceName": label, "name": LABEL}}},
        {"campaignLabelOperation": {"create": {"campaign": campaign, "label": label}}},
    ]
    names = [resource("campaignBudgets", "11"), resource("campaigns", "22"), resource("campaignCriteria", "22~33"),
             resource("adGroups", "44"), resource("adGroupCriteria", "44~55"), resource("adGroupAds", "44~66"),
             resource("labels", "77"), resource("campaignLabels", "22~77")]
    records = {
        "campaignBudget": [{"resourceName": names[0], "amountMicros": "90000000", "deliveryMethod": "STANDARD", "period": "DAILY"}],
        "campaign": [{"resourceName": names[1], "name": "Campaign name preserved", "status": "PAUSED", "campaignBudget": names[0], "biddingStrategyType": "TARGET_SPEND"}],
        "campaignCriterion": [{"resourceName": names[2], "campaign": names[1], "location": {"geoTargetConstant": "geoTargetConstants/2124"}}],
        "adGroup": [{"resourceName": names[3], "campaign": names[1], "name": "Group name preserved", "status": "ENABLED", "type": "SEARCH_STANDARD"}],
        "adGroupCriterion": [{"resourceName": names[4], "adGroup": names[3], "status": "ENABLED", "keyword": {"text": "هودي بالاسم", "matchType": "PHRASE"}}],
        "adGroupAd": [{"resourceName": names[5], "adGroup": names[3], "status": "ENABLED", "ad": {**copy.deepcopy(creative), "type": "RESPONSIVE_SEARCH_AD"}}],
        "label": [{"resourceName": names[6], "name": LABEL}],
        "campaignLabel": [{"resourceName": names[7], "campaign": names[1], "label": names[6]}],
    }
    return operations, names, records


@pytest.mark.asyncio
async def test_exact_graph_verification_replaces_refs_and_uses_real_repeated_fields():
    operations, names, records = graph()
    client = ReadClient(records)
    verified, report = await verify_google_operations(client, operations, names)
    assert verified is True
    assert report["coverage_gaps"] == []
    assert set(report["verified_resources"]) == set(names)
    query = next(query for query in client.queries if " FROM ad_group_ad " in query)
    assert "ad_group_ad.ad.responsive_search_ad.headlines" in query
    assert ".headlines.text" not in query
    assert "ad_group_ad.ad.type" in query


@pytest.mark.asyncio
async def test_no_false_defaults_without_exact_selected_scalar_metadata():
    name = resource("campaigns", "22")
    operation = [{"campaignOperation": {"update": {"resourceName": name, "networkSettings": {"targetSearchNetwork": False}}, "updateMask": "network_settings.target_search_network"}}]
    metadata = {"campaign.resource_name": field("RESOURCE_NAME"), "campaign.network_settings": field("MESSAGE")}
    client = ReadClient({"campaign": [{"resourceName": name}]}, metadata)
    verified, report = await verify_google_operations(client, operation, [name])
    assert verified is False
    assert report["mismatches"][0]["field"] == "networkSettings.targetSearchNetwork"
    client.metadata["campaign.network_settings.target_search_network"] = field("BOOLEAN")
    verified, report = await verify_google_operations(client, operation, [name])
    assert verified is True


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [None, True, "false", 0])
async def test_false_boolean_is_not_equal_to_null_strings_or_numeric_zero(changed):
    name = resource("campaignBudgets", "11")
    operation = [{"campaignBudgetOperation": {"create": {"explicitlyShared": False}}}]
    client = ReadClient({"campaignBudget": [{"resourceName": name, "explicitlyShared": changed}]})
    verified, report = await verify_google_operations(client, operation, [name])
    assert verified is False
    assert report["mismatches"][0]["field"] == "explicitlyShared"


@pytest.mark.asyncio
async def test_unobservable_fields_and_unknown_empty_messages_cannot_pass():
    name = resource("campaigns", "22")
    operations = [{"campaignOperation": {"create": {"name": "Campaign", "unknownSetting": {}}}}]
    client = ReadClient({"campaign": [{"resourceName": name, "name": "Campaign"}]})
    verified, report = await verify_google_operations(client, operations, [name])
    assert verified is False
    assert "campaign.unknown_setting:empty_message_unobservable" in report["coverage_gaps"]
    operations[0]["campaignOperation"]["create"]["unknownSetting"] = {"positive": True}
    verified, report = await verify_google_operations(client, operations, [name])
    assert verified is False
    assert "campaign.unknown_setting.positive" in report["coverage_gaps"]


@pytest.mark.asyncio
async def test_empty_bidding_union_proves_strategy_not_any_existing_campaign():
    operations, names, records = graph()
    records["campaign"][0]["biddingStrategyType"] = "MANUAL_CPC"
    verified, report = await verify_google_operations(ReadClient(records), operations, names)
    assert verified is False
    assert {"resource_name": names[1], "field": "biddingStrategyType"} in report["mismatches"]


@pytest.mark.asyncio
async def test_missing_masked_clear_is_a_coverage_gap_and_wrong_update_identity_fails():
    name = resource("campaigns", "22")
    op = [{"campaignOperation": {"update": {"resourceName": name, "name": "Updated"}, "updateMask": "name,network_settings"}}]
    client = ReadClient({"campaign": [{"resourceName": name, "name": "Updated"}]})
    verified, report = await verify_google_operations(client, op, [name])
    assert verified is False
    assert "campaign.network_settings:masked_clear_unobservable" in report["coverage_gaps"]
    op[0]["campaignOperation"]["updateMask"] = "name"
    op[0]["campaignOperation"]["update"]["resourceName"] = resource("campaigns", "999")
    verified, report = await verify_google_operations(client, op, [name])
    assert verified is False
    assert {"resource_name": name, "field": "operation_identity"} in report["mismatches"]


@pytest.mark.asyncio
async def test_repeated_creative_content_is_a_multiset_but_carousel_order_is_preserved():
    operations, names, records = graph()
    records["adGroupAd"][0]["ad"]["responsiveSearchAd"]["headlines"].reverse()
    assert (await verify_google_operations(ReadClient(records), operations, names))[0] is True
    ad_name = names[5]
    op = [{"adGroupAdOperation": {"create": {"ad": {"demandGenCarouselAd": {"carouselCards": [{"asset": resource("assets", "1")}, {"asset": resource("assets", "2")}]}}}}}]
    client = ReadClient({"adGroupAd": [{"resourceName": ad_name, "ad": {"type": "DEMAND_GEN_CAROUSEL_AD", "demandGenCarouselAd": {"carouselCards": [{"asset": resource("assets", "2")}, {"asset": resource("assets", "1")}]}}}]})
    verified, report = await verify_google_operations(client, op, [ad_name])
    assert verified is False
    assert report["mismatches"][0]["field"] == "ad.demandGenCarouselAd.carouselCards"


@pytest.mark.asyncio
async def test_other_listing_unit_must_be_empty_concrete_dimension_not_any_item():
    name = resource("adGroupCriteria", "44~55")
    op = [{"adGroupCriterionOperation": {"create": {"listingGroup": {"type": "UNIT", "caseValue": {"productItemId": {}}}}}}]
    client = ReadClient({"adGroupCriterion": [{"resourceName": name, "listingGroup": {"type": "UNIT", "caseValue": {"productItemId": {"value": "unwanted-product"}}}}]})
    assert (await verify_google_operations(client, op, [name]))[0] is False
    client.records["adGroupCriterion"][0]["listingGroup"]["caseValue"]["productItemId"] = {}
    assert (await verify_google_operations(client, op, [name]))[0] is True


@pytest.mark.asyncio
async def test_remove_requires_complete_independent_absence_or_removed_status():
    name = resource("adGroupCriteria", "44~55")
    op = [{"adGroupCriterionOperation": {"remove": name}}]
    client = ReadClient()
    assert (await verify_google_operations(client, op, [name]))[0] is True
    client.complete = False
    verified, report = await verify_google_operations(client, op, [name])
    assert verified is False
    assert report["verified_resources"] == []
    client.complete = True
    client.records["adGroupCriterion"] = [{"resourceName": name, "status": "ENABLED"}]
    assert (await verify_google_operations(client, op, [name]))[0] is False
    client.records["adGroupCriterion"][0]["status"] = "REMOVED"
    assert (await verify_google_operations(client, op, [name]))[0] is True


@pytest.mark.asyncio
async def test_duplicate_provider_rows_and_read_errors_remain_pending_without_raw_payload():
    operations, names, records = graph()
    # Request two distinct campaign identities so the bounded client exposes
    # both duplicate rows instead of truncating the second one at max_rows=1.
    operations.append({"campaignOperation": {"create": {"name": "Different campaign"}}})
    names.append(resource("campaigns", "222"))
    records["campaign"] *= 2
    verified, report = await verify_google_operations(ReadClient(records), operations, names)
    assert verified is False
    assert "campaign:ambiguous_or_invalid_rows" in report["coverage_gaps"]
    client = ReadClient()
    client.failure = HTTPException(status_code=409, detail={"code": "provider_denied", "token": "sensitive-provider-body"})
    verified, report = await verify_google_operations(client, operations, names)
    assert verified is False
    assert any("provider_denied" in gap for gap in report["coverage_gaps"])
    assert "sensitive-provider-body" not in str(report)


@pytest.mark.asyncio
async def test_image_verification_has_honest_receipt_identity_type_dimensions_scope():
    name = resource("assets", "88")
    asset_name = "Image " + LABEL + "-frozenhash"
    op = [{"assetOperation": {"create": {"name": asset_name, "imageAsset": {"data": PNG_ONE_PIXEL}}}}]
    client = ReadClient({"asset": [{"resourceName": name, "name": asset_name, "type": "IMAGE", "imageAsset": {"fullSize": {"widthPixels": "1", "heightPixels": "1"}}}]})
    verified, report = await verify_google_operations(client, op, [name])
    assert verified is True
    scope = report["verification_scope"]["image_uploads"][0]
    assert scope["byte_equality_verified"] is False
    assert scope["requires_verified_upload_receipt"] is True
    assert scope["operator_preview_required"] is True
    assert not any("image_asset.data" in query for query in client.queries)
    client.records["asset"][0]["imageAsset"]["fullSize"]["widthPixels"] = "2"
    assert (await verify_google_operations(client, op, [name]))[0] is False


@pytest.mark.asyncio
async def test_recovery_anchors_unique_label_and_recovers_full_graph_without_mutation():
    operations, expected_names, records = graph()
    client = ReadClient(records)
    names = await recover_create_operation_names(client, operations, LABEL)
    assert names == expected_names
    assert (await verify_google_operations(client, operations, names))[0] is True
    assert "label.name = '" + LABEL + "'" in client.queries[0]
    assert not hasattr(client, "mutate")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("missing_label", "google_ads_recovery_correlation_missing"),
    ("duplicate_label", "google_ads_recovery_correlation_ambiguous"),
    ("duplicate_group", "google_ads_recovery_resource_ambiguous"),
    ("incomplete", "google_ads_recovery_read_incomplete"),
    ("wrong_ad", "google_ads_recovery_resource_missing"),
])
async def test_recovery_never_guesses_ambiguous_or_missing_graph(mode, code):
    operations, names, records = graph()
    if mode == "missing_label":
        records["campaignLabel"] = []
    if mode == "duplicate_label":
        records["campaignLabel"].append({"resourceName": resource("campaignLabels", "222~77"), "campaign": resource("campaigns", "222"), "label": names[6]})
    if mode == "duplicate_group":
        records["adGroup"].append({**records["adGroup"][0], "resourceName": resource("adGroups", "444")})
    if mode == "wrong_ad":
        records["adGroupAd"][0]["ad"]["responsiveSearchAd"]["headlines"][0]["text"] = "different content"
    client = ReadClient(records)
    if mode == "incomplete":
        client.complete = False
    with pytest.raises(HTTPException) as error:
        await recover_create_operation_names(client, operations, LABEL)
    assert error.value.detail["code"] == code


@pytest.mark.asyncio
async def test_asset_only_recovery_uses_unique_name_not_type_or_size_alone():
    name = resource("assets", "88")
    asset_name = "Image " + LABEL + "-frozenhash"
    op = [{"assetOperation": {"create": {"resourceName": resource("assets", "-1"), "name": asset_name, "imageAsset": {"data": PNG_ONE_PIXEL}}}}]
    records = {"asset": [{"resourceName": name, "name": asset_name, "type": "IMAGE", "imageAsset": {"fullSize": {"widthPixels": 1, "heightPixels": 1}}}]}
    client = ReadClient(records)
    assert await recover_create_operation_names(client, op, LABEL) == [name]
    op[0]["assetOperation"]["create"]["name"] = "ordinary asset name"
    with pytest.raises(HTTPException) as error:
        await recover_create_operation_names(client, op, LABEL)
    assert error.value.detail["code"] == "google_ads_recovery_asset_correlation_missing"


@pytest.mark.asyncio
async def test_existing_campaign_create_recovery_requires_complete_before_ids_and_provider_marker():
    operations, names, records = graph()
    create_ad = copy.deepcopy(operations[5])
    create_ad["adGroupAdOperation"]["create"]["adGroup"] = names[3]
    create_label, create_link = copy.deepcopy(operations[6]), copy.deepcopy(operations[7])
    create_link["campaignLabelOperation"]["create"]["campaign"] = names[1]
    plan = [create_ad, create_label, create_link]
    old_name = resource("adGroupAds", "44~166")
    records["adGroupAd"].append({**copy.deepcopy(records["adGroupAd"][0]), "resourceName": old_name})
    client = ReadClient(records)
    with pytest.raises(HTTPException) as error:
        await recover_create_operation_names(client, plan, LABEL)
    assert error.value.detail["code"] == "google_ads_recovery_existing_scope_checkpoint_missing"
    recovered = await recover_create_operation_names(client, plan, LABEL, known_before_resource_names=[names[1], names[3], old_name])
    assert recovered == [names[5], names[6], names[7]]
    client.records["campaignLabel"] = []
    with pytest.raises(HTTPException) as error:
        await recover_create_operation_names(client, plan, LABEL, known_before_resource_names=[names[1], names[3], old_name])
    assert error.value.detail["code"] == "google_ads_recovery_correlation_missing"


@pytest.mark.asyncio
async def test_recovery_unknown_global_text_asset_without_name_marker_is_explicit_gap():
    operations, names, records = graph()
    op = {"assetOperation": {"create": {"resourceName": resource("assets", "-4"), "textAsset": {"text": "عنوان"}}}}
    operations.insert(2, op)
    records["asset"] = [{"resourceName": resource("assets", "88"), "textAsset": {"text": "عنوان"}, "type": "TEXT"}]
    with pytest.raises(HTTPException) as error:
        await recover_create_operation_names(ReadClient(records), operations, LABEL)
    assert error.value.detail["code"] == "google_ads_recovery_asset_correlation_missing"


@pytest.mark.asyncio
async def test_foreign_names_and_wrong_resource_collections_are_rejected_before_reads():
    for name in ("customers/999999/campaigns/22", resource("assets", "22")):
        client = ReadClient()
        with pytest.raises(HTTPException):
            await verify_google_operations(client, [{"campaignOperation": {"create": {"name": "Campaign"}}}], [name])
        assert client.queries == []
