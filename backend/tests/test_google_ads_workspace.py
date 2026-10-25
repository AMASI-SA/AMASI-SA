from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re

import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_workspace as module
from integrations_control_center import google_ads_snapshot


CUSTOMER = "1234567890"
ACTOR = {"user_id": "owner-1", "actor_id": "marketer-1", "owner": False, "can_draft": True}


class Collection:
    def __init__(self):
        self.writes = []
        self.indexes = []

    async def create_index(self, fields, **kwargs):
        self.indexes.append((fields, kwargs))

    async def update_one(self, scope, update, **kwargs):
        self.writes.append((deepcopy(scope), deepcopy(update), kwargs))


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


class ReadClient:
    def __init__(self, rows=None, *, incomplete_roots=(), unavailable_fields=()):
        self.customer_id = CUSTOMER
        self.rows = rows or {}
        self.incomplete_roots = set(incomplete_roots)
        self.unavailable_fields = set(unavailable_fields)
        self.queries = []
        self.metadata_requests = []
        self.closed = False

    async def account_metadata(self):
        return {"account_id": CUSTOMER, "customer_id": CUSTOMER, "name": "AMASI US Google", "currency_code": "USD", "time_zone": "America/New_York", "identity_verified": True}

    async def field_metadata(self, fields):
        self.metadata_requests.append(list(fields))
        return {field: {"name": field, "selectable": field not in self.unavailable_fields} for field in fields}

    async def search(self, query, **kwargs):
        self.queries.append(query)
        root = re.search(r" FROM ([a-z_]+)", query).group(1)
        return deepcopy(self.rows.get(root, [])), root not in self.incomplete_roots

    async def campaign_snapshot(self, campaign_id, *, children=False, duplicate_controls=False):
        return await google_ads_snapshot.snapshot(self, campaign_id, children=children, duplicate_controls=duplicate_controls)

    async def assets(self):
        return [], True

    async def close(self):
        self.closed = True


def campaign_row(metrics=None):
    row = {"campaign": {"resourceName": f"customers/{CUSTOMER}/campaigns/900", "id": "900", "name": "AMASI hoodies", "status": "PAUSED", "advertisingChannelType": "SEARCH", "campaignBudget": f"customers/{CUSTOMER}/campaignBudgets/902", "maximizeConversions": {}}, "campaignBudget": {"resourceName": f"customers/{CUSTOMER}/campaignBudgets/902", "amountMicros": "100000000", "period": "DAILY", "explicitlyShared": False, "referenceCount": "1"}}
    if metrics is not None:
        row["metrics"] = metrics
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy,field,target", [
    ("MAXIMIZE_CONVERSIONS", "maximizeConversions", "target_cpa_micros"),
    ("MAXIMIZE_CONVERSION_VALUE", "maximizeConversionValue", "target_roas"),
])
async def test_snapshot_preserves_targetless_smart_bidding_when_selected_optional_child_is_omitted(strategy, field, target):
    row = campaign_row()
    row["campaign"].pop("maximizeConversions")
    row["campaign"]["biddingStrategyType"] = strategy
    parent = "campaign.maximize_conversions" if field == "maximizeConversions" else "campaign.maximize_conversion_value"
    instance = ReadClient({"campaign": [row]}, unavailable_fields={parent})
    result = await google_ads_snapshot.snapshot(instance, "900")
    assert result["campaign"][field] == {}
    # An unavailable optional child is not evidence of an absent target.
    unavailable = ReadClient({"campaign": [row]}, unavailable_fields={parent, parent + "." + target})
    unknown = await google_ads_snapshot.snapshot(unavailable, "900")
    assert field not in unknown["campaign"]
    # Portfolio targets must be read from their own resource, never fabricated.
    row["campaign"]["biddingStrategy"] = f"customers/{CUSTOMER}/biddingStrategies/909"
    portfolio = await google_ads_snapshot.snapshot(ReadClient({"campaign": [row]}), "900")
    assert field not in portfolio["campaign"]
    row["campaign"].pop("biddingStrategy")
    target_key = "targetCpaMicros" if field == "maximizeConversions" else "targetRoas"
    row["campaign"][field] = {target_key: "2500000" if field == "maximizeConversions" else 2.5}
    targeted = await google_ads_snapshot.snapshot(ReadClient({"campaign": [row]}), "900")
    assert targeted["campaign"][field] == row["campaign"][field]


def linked(monkeypatch, instance, *, accounts=None):
    calls = []
    async def available_accounts(db, user_id):
        assert user_id == "owner-1"
        return deepcopy(accounts if accounts is not None else [{"account_id": CUSTOMER, "name": "AMASI stored", "currency": "SAR", "timezone": "Asia/Riyadh"}])

    async def account_client(db, user_id, account_id, *, client_factory=None):
        calls.append({"user_id": user_id, "account_id": account_id})
        if user_id != "owner-1" or account_id != CUSTOMER:
            raise HTTPException(status_code=404, detail={"code": "google_ads_account_not_owned"})
        return instance, "credential-epoch"

    monkeypatch.setattr(module, "available_accounts", available_accounts)
    monkeypatch.setattr(module, "account_client", account_client)
    return calls


@pytest.mark.parametrize("raw,expected", [({}, None), ({"costMicros": "0"}, "0"), ({"costMicros": "1000001"}, "1.000001"), ({"costMicros": "NaN"}, None), ({"costMicros": None}, None)])
def test_missing_and_actual_zero_native_spend_remain_distinct(raw, expected):
    facts = module.native_metrics({"metrics": raw})
    assert facts["spend_native"] == expected
    assert facts["clicks"] is None


def test_native_roas_is_based_on_provider_currency_and_never_divides_by_zero():
    facts = module.native_metrics({"metrics": {"costMicros": "2500000", "conversionsValue": "10.0", "conversions": "1", "clicks": "0"}})
    assert Decimal(facts["roas"]) == Decimal(4)
    assert facts["clicks"] == "0"
    zero = module.native_metrics({"metrics": {"costMicros": "0", "conversionsValue": "0"}})
    assert zero["spend_native"] == "0" and zero["conversion_value_native"] == "0" and zero["roas"] is None


@pytest.mark.asyncio
async def test_workspace_carries_account_currency_timezone_source_scope_and_exact_period(monkeypatch):
    metrics = {"costMicros": "139123456", "impressions": "100", "clicks": "10", "conversions": "2.5", "conversionsValue": "500.00"}
    instance = ReadClient({"campaign": [campaign_row(metrics)], "customer": [{"segments": {"date": "2026-10-13"}, "metrics": metrics}]})
    calls = linked(monkeypatch, instance)
    db = DB()
    result = await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13", account_id=CUSTOMER)
    assert calls == [{"user_id": "owner-1", "account_id": CUSTOMER}]
    assert result["currency_code"] == "USD" and result["time_zone"] == "America/New_York"
    assert result["accounts"][0]["currency"] == "USD" and result["accounts"][0]["timezone"] == "America/New_York"
    assert result["totals"]["spend_native"] == "139.123456"
    assert result["range"] == {"from_date": "2026-10-13", "to_date": "2026-10-13"}
    assert all("segments.date BETWEEN '2026-10-13' AND '2026-10-13'" in query for query in instance.queries)
    assert result["campaigns"][0]["source"] == "google_ads_api"
    assert result["campaigns"][0]["raw_revision"]
    assert result["daily"][0]["currency_code"] == "USD"
    assert result["complete"] is True and instance.closed is True
    scope, update, kwargs = db[module.FACTSETS].writes[0]
    assert scope == {"user_id": "owner-1", "account_id": CUSTOMER, "date_from": "2026-10-13", "date_to": "2026-10-13"}
    assert [row["campaign_id"] for row in update["$set"]["campaigns"]] == ["900"]
    assert update["$set"]["complete"] is True and kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_incomplete_campaign_report_has_unknown_totals_and_no_authoritative_fact_write(monkeypatch):
    metrics = {"costMicros": "100000000", "impressions": "100", "clicks": "10", "conversions": "1", "conversionsValue": "200"}
    instance = ReadClient({"campaign": [campaign_row(metrics)], "customer": []}, incomplete_roots={"campaign"})
    linked(monkeypatch, instance)
    db = DB()
    result = await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert result["complete"] is False
    assert result["totals"]["spend_native"] is None
    assert result["pagination"]["total"] is None and result["pagination"]["truncated"] is True
    assert db[module.FACTSETS].writes == []


@pytest.mark.asyncio
async def test_complete_empty_report_publishes_empty_set_instead_of_stale_campaigns(monkeypatch):
    instance = ReadClient({"campaign": [], "customer": []})
    linked(monkeypatch, instance)
    db = DB()
    await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert len(db[module.FACTSETS].writes) == 1
    assert db[module.FACTSETS].writes[0][1]["$set"]["campaigns"] == []


@pytest.mark.asyncio
async def test_multiple_linked_accounts_require_a_scope_instead_of_querying_first_account(monkeypatch):
    instance = ReadClient()
    calls = linked(monkeypatch, instance, accounts=[
        {"account_id": "9999999999", "name": "Access unavailable"},
        {"account_id": CUSTOMER, "name": "Owned readable account"},
    ])
    db = DB()
    result = await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert result["account_selection_required"] is True
    assert result["complete"] is False and result["totals"] == {}
    assert result["items"] == result["campaigns"] == []
    assert calls == [] and instance.queries == []
    assert not db.collections


@pytest.mark.asyncio
async def test_an_explicit_inaccessible_account_is_never_replaced_by_another_linked_account(monkeypatch):
    instance = ReadClient()
    calls = linked(monkeypatch, instance, accounts=[
        {"account_id": "9999999999", "name": "Access unavailable"},
        {"account_id": CUSTOMER, "name": "Owned readable account"},
    ])
    with pytest.raises(HTTPException) as raised:
        await module.read_google_workspace(DB(), ACTOR, from_date="2026-10-13", to_date="2026-10-13", account_id="9999999999")
    assert raised.value.status_code == 404
    assert calls == [{"user_id": "owner-1", "account_id": "9999999999"}]
    assert instance.queries == []


@pytest.mark.asyncio
async def test_hotel_lookup_uses_real_invitation_resource_and_states_eligibility_limit():
    instance = ReadClient({"product_link_invitation": [{"productLinkInvitation": {"resourceName": f"customers/{CUSTOMER}/productLinkInvitations/10", "hotelCenter": {"hotelCenterId": "123"}, "type": "HOTEL_CENTER", "status": "ACCEPTED"}}]})
    result = await module.google_lookup(instance, "hotels")
    assert result["items"][0]["hotel_center_id"] == "123"
    assert result["items"][0]["eligibility"] == "provider_validate_only_required"
    assert "FROM product_link_invitation" in instance.queries[0]
    assert "status = 'ACCEPTED'" in instance.queries[0]


def test_app_engagement_context_keeps_subtype_and_exact_conversion_resource():
    campaign = {"advertisingChannelSubType": "APP_CAMPAIGN_FOR_ENGAGEMENT", "appCampaignSetting": {"appId": "com.amasi.store", "appStore": "GOOGLE_APP_STORE", "biddingStrategyGoalType": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST"}, "selectiveOptimization": {"conversionActions": [f"customers/{CUSTOMER}/conversionActions/20"]}}
    assert module.campaign_context(campaign)["app"] == {"subtype": "ENGAGEMENT", "app_id": "com.amasi.store", "app_store": "GOOGLE_APP_STORE", "goal_type": "OPTIMIZE_IN_APP_CONVERSIONS_TARGET_CONVERSION_COST", "conversion_action_ids": [f"customers/{CUSTOMER}/conversionActions/20"]}


@pytest.mark.asyncio
async def test_arabic_geo_query_and_dsa_language_code_use_provider_identities():
    instance = ReadClient({"geo_target_constant": [{"geoTargetConstant": {"id": "2682", "name": "Saudi Arabia", "resourceName": "geoTargetConstants/2682"}}], "language_constant": [{"languageConstant": {"id": "1001", "resourceName": "languageConstants/1001", "name": "Arabic", "code": "ar"}}]})
    geo = await module.google_lookup(instance, "geo", query="السعودية")
    language = await module.google_lookup(instance, "languages")
    assert geo["items"][0]["id"] == "2682" and "Saudi Arabia" in instance.queries[0]
    assert language["items"][0]["code"] == language["items"][0]["language_code"] == "ar"


@pytest.mark.asyncio
async def test_missing_metric_value_is_not_a_zero_total_even_in_complete_report(monkeypatch):
    metrics = {"impressions": "0", "clicks": "0", "conversions": "0", "conversionsValue": "0"}
    instance = ReadClient({"campaign": [campaign_row(metrics)], "customer": []})
    linked(monkeypatch, instance)
    result = await module.read_google_workspace(DB(), ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert result["campaigns"][0]["metrics"]["spend_native"] is None
    assert result["totals"]["spend_native"] is None
    assert result["totals"]["clicks"] == "0"


@pytest.mark.asyncio
async def test_required_metric_metadata_gap_fails_without_persisting_fabricated_facts(monkeypatch):
    instance = ReadClient({"campaign": [campaign_row()]}, unavailable_fields={"metrics.cost_micros"})
    linked(monkeypatch, instance)
    db = DB()
    with pytest.raises(HTTPException) as error:
        await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert error.value.detail["code"] == "google_ads_read_contract_unavailable"
    assert db.collections == {} and instance.closed is True


@pytest.mark.asyncio
async def test_unlinked_account_never_reaches_provider_or_other_tenant_facts(monkeypatch):
    instance = ReadClient()
    calls = linked(monkeypatch, instance)
    db = DB()
    with pytest.raises(HTTPException) as error:
        await module.read_google_workspace(db, ACTOR, from_date="2026-10-13", to_date="2026-10-13", account_id="9999999999")
    assert error.value.status_code == 404
    assert instance.queries == [] and db.collections == {}
    assert calls == [{"user_id": "owner-1", "account_id": "9999999999"}]


@pytest.mark.asyncio
async def test_no_linked_accounts_returns_empty_without_a_provider_call(monkeypatch):
    instance = ReadClient()
    linked(monkeypatch, instance, accounts=[])
    result = await module.read_google_workspace(DB(), ACTOR, from_date="2026-10-13", to_date="2026-10-13")
    assert result["accounts"] == [] and result["campaigns"] == [] and result["totals"] == {}
    assert instance.queries == []


def test_raw_source_revision_is_stable_for_same_facts_and_changes_with_provider_setting():
    metadata = {"account_id": CUSTOMER, "currency_code": "USD", "time_zone": "America/New_York"}
    raw = campaign_row({"costMicros": "0"})
    first = module.campaign_item(raw, metadata, "2026-10-13T00:00:00Z")
    second = module.campaign_item(deepcopy(raw), metadata, "2026-10-13T01:00:00Z")
    assert first["raw_revision"] == second["raw_revision"]
    raw["campaign"]["status"] = "ENABLED"
    changed = module.campaign_item(raw, metadata, "2026-10-13T01:00:00Z")
    assert changed["raw_revision"] != first["raw_revision"]


@pytest.mark.parametrize("start,end", [("2026-10-14", "2026-10-13"), ("2026-01-01", "2026-10-13"), ("bad", "2026-10-13"), ("2026-13-01", "2026-10-13")])
def test_invalid_reversed_or_unbounded_period_rejected(start, end):
    with pytest.raises(HTTPException):
        module.period(start, end)


@pytest.mark.asyncio
async def test_snapshot_refuses_truncated_children_and_binds_campaign_identity():
    instance = ReadClient({"campaign": [campaign_row()], "ad_group": [{"adGroup": {"resourceName": f"customers/{CUSTOMER}/adGroups/901", "id": "901", "campaign": f"customers/{CUSTOMER}/campaigns/900", "status": "ENABLED", "type": "SEARCH_STANDARD"}}]}, incomplete_roots={"ad_group_criterion"})
    with pytest.raises(HTTPException) as error:
        await google_ads_snapshot.snapshot(instance, "900", children=True)
    assert error.value.detail["code"] == "google_ads_snapshot_incomplete"


@pytest.mark.asyncio
async def test_demand_gen_snapshot_requests_real_group_location_and_language_payloads():
    row = campaign_row()
    row["campaign"]["advertisingChannelType"] = "DEMAND_GEN"
    instance = ReadClient({"campaign": [row], "ad_group": [{"adGroup": {"resourceName": f"customers/{CUSTOMER}/adGroups/901", "id": "901", "campaign": f"customers/{CUSTOMER}/campaigns/900", "status": "ENABLED", "type": "UNSPECIFIED"}}]})
    await google_ads_snapshot.snapshot(instance, "900", children=True)
    query = next(query for query in instance.queries if " FROM ad_group_criterion " in query)
    assert "ad_group_criterion.location.geo_target_constant" in query
    assert "ad_group_criterion.language.language_constant" in query


@pytest.mark.asyncio
async def test_fresh_snapshot_never_accepts_a_foreign_customer_campaign_resource():
    row = campaign_row()
    row["campaign"]["resourceName"] = "customers/9999999999/campaigns/900"
    instance = ReadClient({"campaign": [row]})
    with pytest.raises(HTTPException):
        await google_ads_snapshot.snapshot(instance, "900", children=False)


class ControlClient(ReadClient):
    api_version = "v25"

    def __init__(self, rows=None, *, catalogs=None, unavailable_catalogs=(), **kwargs):
        super().__init__(rows, **kwargs)
        self.catalogs = catalogs or {}
        self.unavailable_catalogs = set(unavailable_catalogs)
        self.catalog_requests = []

    async def fields_for_resource(self, root):
        self.catalog_requests.append(root)
        if root in self.unavailable_catalogs:
            raise HTTPException(status_code=409, detail={"code": "google_ads_field_catalog_incomplete"})
        names = [root + "." + field for field in google_ads_snapshot.CONTROL_RESOURCES[root][2]]
        names += self.catalogs.get(root, [])
        return {name: {"name": name, "selectable": True, "category": "ATTRIBUTE", "isRepeated": False} for name in names}


def owned_control_rows():
    campaign = campaign_row()
    campaign["campaign"]["networkSettings"] = {"targetGoogleSearch": True, "targetSearchNetwork": False, "targetContentNetwork": False}
    group = {"resourceName": f"customers/{CUSTOMER}/adGroups/901", "id": "901", "name": "Source group", "campaign": campaign["campaign"]["resourceName"], "status": "ENABLED", "type": "SEARCH_STANDARD"}
    return {
        "campaign": [campaign],
        "campaign_budget": [{"campaignBudget": deepcopy(campaign["campaignBudget"])}],
        "ad_group": [{"adGroup": group}],
        "ad_group_ad": [{"adGroupAd": {"resourceName": f"customers/{CUSTOMER}/adGroupAds/901~920", "adGroup": group["resourceName"], "status": "ENABLED", "ad": {"id": "920", "type": "RESPONSIVE_SEARCH_AD", "finalUrls": ["https://amasi.example.test/gift"], "responsiveSearchAd": {"headlines": [{"text": "AMASI hoodie"}, {"text": "Personal family gift"}, {"text": "Create a special gift"}], "descriptions": [{"text": "Shop personalized gifts."}, {"text": "Choose an AMASI hoodie."}]}}}}],
    }


@pytest.mark.asyncio
async def test_ordinary_snapshot_does_not_enumerate_controls_or_claim_duplicate_proof():
    instance = ControlClient(owned_control_rows())
    result = await google_ads_snapshot.snapshot(instance, "900", children=True)
    assert instance.catalog_requests == []
    assert result.get("duplicate_controls_complete") is not True


@pytest.mark.asyncio
async def test_duplicate_control_proof_reads_every_catalogued_source_attribute_and_assets():
    rows = owned_control_rows()
    rows["campaign"][0]["campaign"]["trackingUrlTemplate"] = "https://track.example.test/?url={lpurl}"
    instance = ControlClient(rows, catalogs={"campaign": ["campaign.tracking_url_template"]})
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is True and result["duplicate_control_gaps"] == []
    assert result["campaign"]["trackingUrlTemplate"] == "https://track.example.test/?url={lpurl}"
    assert {"campaign", "campaign_budget", "campaign_criterion", "campaign_asset", "ad_group", "ad_group_ad", "ad_group_criterion", "ad_group_asset"} <= set(instance.catalog_requests)
    assert any("campaign.tracking_url_template" in query for query in instance.queries)
    assert result["groups"][0]["group_assets"] == []


@pytest.mark.asyncio
async def test_missing_catalog_cannot_turn_unread_controls_into_default_values():
    instance = ControlClient(owned_control_rows(), unavailable_catalogs={"ad_group_asset"})
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert {"resource": "ad_group_asset", "code": "google_ads_field_catalog_incomplete"} in result["duplicate_control_gaps"]


@pytest.mark.asyncio
async def test_duplicate_proof_rejects_missing_catalog_identity_and_truncated_deep_reads():
    class NoIdentity(ControlClient):
        async def fields_for_resource(self, root):
            values = await super().fields_for_resource(root)
            if root == "campaign_asset":
                values.pop("campaign_asset.campaign")
            return values
    result = await google_ads_snapshot.snapshot(NoIdentity(owned_control_rows()), "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert any(gap["code"] == "google_ads_control_identity_unavailable" for gap in result["duplicate_control_gaps"])
    instance = ControlClient(owned_control_rows(), incomplete_roots={"ad_group_asset"})
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert any(gap["code"] == "google_ads_control_read_incomplete" for gap in result["duplicate_control_gaps"])


@pytest.mark.asyncio
async def test_duplicate_controls_require_same_account_collection_and_campaign_parent():
    rows = owned_control_rows()
    rows["ad_group_asset"] = [{"adGroupAsset": {"resourceName": f"customers/{CUSTOMER}/adGroupAssets/901~123~AD_IMAGE", "adGroup": f"customers/{CUSTOMER}/adGroups/999", "asset": f"customers/{CUSTOMER}/assets/123", "fieldType": "AD_IMAGE", "status": "ENABLED"}}]
    with pytest.raises(HTTPException) as error:
        await google_ads_snapshot.snapshot(ControlClient(rows), "900", duplicate_controls=True)
    assert error.value.detail["code"] == "google_ads_snapshot_parent_mismatch"


@pytest.mark.asyncio
async def test_changed_resource_set_between_control_chunks_cannot_prove_complete_source(monkeypatch):
    monkeypatch.setattr(google_ads_snapshot, "CONTROL_CHUNK_FIELDS", 1)
    class Drift(ControlClient):
        async def search(self, query, **kwargs):
            if " FROM campaign WHERE " in query and "campaign.control_two" in query:
                self.queries.append(query)
                return [], True
            return await super().search(query, **kwargs)
    instance = Drift(owned_control_rows(), catalogs={"campaign": ["campaign.control_one", "campaign.control_two"]})
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert any(gap["code"] == "google_ads_control_resources_changed" for gap in result["duplicate_control_gaps"])


@pytest.mark.asyncio
async def test_conflicting_settings_between_control_chunks_cannot_be_merged(monkeypatch):
    monkeypatch.setattr(google_ads_snapshot, "CONTROL_CHUNK_FIELDS", 1)
    class Drift(ControlClient):
        async def search(self, query, **kwargs):
            rows, complete = await super().search(query, **kwargs)
            if " FROM campaign WHERE " in query and "campaign.control_two" in query:
                rows[0]["campaign"]["status"] = "ENABLED"
            return rows, complete
    instance = Drift(owned_control_rows(), catalogs={"campaign": ["campaign.control_one", "campaign.control_two"]})
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert any(gap["code"] == "google_ads_control_settings_changed" for gap in result["duplicate_control_gaps"])


@pytest.mark.asyncio
async def test_non_v25_catalog_and_resource_limits_leave_explicit_proof_gap(monkeypatch):
    instance = ControlClient(owned_control_rows())
    instance.api_version = "v24"
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False and instance.catalog_requests == []
    instance = ControlClient(owned_control_rows())
    monkeypatch.setattr(google_ads_snapshot, "MAX_CONTROL_QUERIES", 2)
    result = await google_ads_snapshot.snapshot(instance, "900", duplicate_controls=True)
    assert result["duplicate_controls_complete"] is False
    assert any(gap["code"] == "google_ads_control_query_limit" for gap in result["duplicate_control_gaps"])


@pytest.mark.asyncio
async def test_carousel_asset_bodies_are_scoped_to_actual_frozen_card_references():
    rows = owned_control_rows()
    card_ref = f"customers/{CUSTOMER}/assets/104"
    rows["ad_group_ad"][0]["adGroupAd"]["ad"] = {"id": "920", "type": "DEMAND_GEN_CAROUSEL_AD", "demandGenCarouselAd": {"carouselCards": [{"asset": card_ref}]}}
    rows["asset"] = [{"asset": {"resourceName": card_ref, "type": "DEMAND_GEN_CAROUSEL_CARD", "finalUrls": ["https://amasi.example.test/gift"], "demandGenCarouselCardAsset": {"headline": "AMASI gift", "marketingImageAsset": f"customers/{CUSTOMER}/assets/105"}}}]
    instance = ControlClient(rows)
    result = await google_ads_snapshot.snapshot(instance, "900", children=True)
    assert result["creative_assets"][0]["resourceName"] == card_ref
    query = next(query for query in instance.queries if " FROM asset " in query)
    assert "asset.resource_name IN ('" + card_ref + "')" in query
    rows["asset"][0]["asset"]["resourceName"] = f"customers/{CUSTOMER}/assets/999"
    with pytest.raises(HTTPException) as error:
        await google_ads_snapshot.snapshot(ControlClient(rows), "900", children=True)
    assert error.value.detail["code"] == "google_ads_carousel_asset_mismatch"


@pytest.mark.asyncio
async def test_snapshot_campaign_child_collection_and_composite_parent_must_match():
    rows = owned_control_rows()
    rows["campaign_criterion"] = [{"campaignCriterion": {"resourceName": f"customers/{CUSTOMER}/adGroupCriteria/900~910", "campaign": f"customers/{CUSTOMER}/campaigns/900", "type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/2682"}}}]
    with pytest.raises(HTTPException) as error:
        await google_ads_snapshot.snapshot(ControlClient(rows), "900", children=True)
    assert error.value.detail["code"] == "google_ads_foreign_resource"
    rows["campaign_criterion"][0]["campaignCriterion"]["resourceName"] = f"customers/{CUSTOMER}/campaignCriteria/999~910"
    with pytest.raises(HTTPException) as error:
        await google_ads_snapshot.snapshot(ControlClient(rows), "900", children=True)
    assert error.value.detail["code"] == "google_ads_snapshot_parent_mismatch"
