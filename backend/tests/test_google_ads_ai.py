"""Offline tests at the model boundary: provider and money facts stay authoritative."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from integrations_control_center.google_ads_ai import GoogleAIProductOfferSelection


@pytest.mark.asyncio
async def test_model_optional_suggestions_are_review_notes_not_readiness_blockers():
    output = draft_output()
    output["missing_requirements"] = ["Optional conversion tracking", "Merchant Center for a future Shopping campaign"]
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(),
        account_context=account_context(), client_factory=lambda: ModelClient(output))
    assert result["ready_for_preview"] is True
    assert result["missing_requirements"] == []
    assert result["review_notes"] == output["missing_requirements"]
    assert result["ready_for_execution"] is False


@pytest.mark.asyncio
async def test_authoritative_missing_group_still_blocks_preview_with_model_review_notes():
    output = draft_output()
    output["draft"]["groups"] = []
    output["missing_requirements"] = ["Review creative copy"]
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(),
        account_context=account_context(), client_factory=lambda: ModelClient(output))
    assert result["ready_for_preview"] is False
    assert any(item["field"] == "groups" for item in result["missing_requirements"])
    assert result["review_notes"] == ["Review creative copy"]


@pytest.mark.asyncio
async def test_search_generation_no_longer_requests_manual_language_targeting():
    client = ModelClient(draft_output())
    await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(),
        account_context=account_context(), client_factory=lambda: client)
    schema = client.calls[0]["text"]["format"]["schema"]
    assert schema["$defs"]["GoogleCampaignDraft"]["properties"]["language_ids"]["maxItems"] == 0


def test_explicit_product_offer_selection_contract_preserves_ids_and_forbids_extra_data():
    selected = {"product_id": "mpv2_1", "merchant_id": "00123", "offer_id": "SKU:HOODIE/BLUE"}
    parsed = GoogleAIProductOfferSelection.model_validate(selected)
    assert parsed.model_dump() == selected
    body = draft_request(product_ids=["mpv2_1"], product_offer_selections=[selected])
    assert body.product_offer_selections[0].model_dump() == selected
    assert draft_request().product_offer_selections == []
    with pytest.raises(ValidationError):
        GoogleAIProductOfferSelection.model_validate({**selected, "order_count": 7})


@pytest.mark.parametrize("changes", [
    {"product_id": 1}, {"product_id": ""}, {"product_id": "x" * 161},
    {"merchant_id": 123}, {"merchant_id": "customers/123"},
    {"offer_id": 123}, {"offer_id": ""}, {"offer_id": "x" * 151},
])
def test_explicit_product_offer_selection_is_strict_and_bounded(changes):
    with pytest.raises(ValidationError):
        GoogleAIProductOfferSelection.model_validate({"product_id": "1", "merchant_id": "123", "offer_id": "hoodie-1", **changes})


def test_explicit_product_offer_selection_request_limit_is_50():
    choices = [{"product_id": "1", "merchant_id": "123", "offer_id": f"hoodie-{index}"} for index in range(51)]
    assert len(draft_request(product_offer_selections=choices[:50]).product_offer_selections) == 50
    with pytest.raises(ValidationError):
        draft_request(product_offer_selections=choices)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_ids", [[], ["1"]])
async def test_native_ai_draft_preserves_exact_canonical_selected_product_ids(model_ids):
    output = draft_output()
    output["draft"]["product_ids"] = model_ids
    db, client = DraftDB([product()]), ModelClient(output)
    result = await generate_google_campaign_draft(db, OWNER, draft_request(),
        account_context=account_context(), client_factory=lambda: client)
    assert result["draft"]["product_ids"] == ["1"]
    assert result["product_ids"] == ["1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_ids", [["2"], ["1", "2"]])
async def test_native_ai_draft_cannot_replace_or_expand_selected_product_scope(model_ids):
    output = draft_output()
    output["draft"]["product_ids"] = model_ids
    db, client = DraftDB([product()]), ModelClient(output)
    with pytest.raises(HTTPException) as error:
        await generate_google_campaign_draft(db, OWNER, draft_request(),
            account_context=account_context(), client_factory=lambda: client)
    assert error.value.detail["code"] == "google_ai_selected_product_scope_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_pairs", [[], [{"product_id": "1", "merchant_id": "123", "offer_id": "hoodie-1"}]])
async def test_native_ai_draft_preserves_explicit_trusted_offer_selections(model_pairs):
    selection = {"product_id": "1", "merchant_id": "123", "offer_id": "hoodie-1"}
    output = draft_output()
    output["draft"]["product_ids"] = ["1"]
    output["draft"]["product_offer_selections"] = model_pairs
    context = account_context()
    context.update({"merchant_ids": ["123"], "product_offer_ids": ["hoodie-1"],
                    "product_offer_associations": [{**selection, "verification_status": "verified"}]})
    db, client = DraftDB([product()]), ModelClient(output)
    result = await generate_google_campaign_draft(db, OWNER, draft_request(product_offer_selections=[selection]),
        account_context=context, client_factory=lambda: client)
    assert result["draft"]["product_ids"] == ["1"]
    assert result["draft"]["product_offer_selections"] == [selection]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("merchant_id", "999"), ("offer_id", "invented-offer")])
async def test_native_ai_draft_cannot_invent_or_replace_explicit_offer_mapping(field, value):
    selection = {"product_id": "1", "merchant_id": "123", "offer_id": "hoodie-1"}
    output = draft_output()
    output["draft"]["product_ids"] = ["1"]
    output["draft"]["product_offer_selections"] = [{**selection, field: value}]
    context = account_context()
    context.update({"merchant_ids": ["123"], "product_offer_ids": ["hoodie-1"],
                    "product_offer_associations": [{**selection, "verification_status": "verified"}]})
    db, client = DraftDB([product()]), ModelClient(output)
    with pytest.raises(HTTPException) as error:
        await generate_google_campaign_draft(db, OWNER, draft_request(product_offer_selections=[selection]),
            account_context=context, client_factory=lambda: client)
    assert error.value.detail["code"] == "google_ai_product_offer_selection_changed"


@pytest.mark.asyncio
async def test_native_mezan_only_product_can_ground_ai_draft_without_salla_product_id():
    native_product = {**product(), "salla_product_id": None}
    output = draft_output()
    output["product_ids"] = ["mpv2_1"]
    output["draft"]["product_ids"] = ["mpv2_1"]
    output["evidence_ids"] = ["product:mpv2_1", f"google:account:{ACCOUNT}"]
    db, client = DraftDB([native_product]), ModelClient(output)
    result = await generate_google_campaign_draft(db, OWNER, draft_request(product_ids=["mpv2_1"]),
        account_context=account_context(), client_factory=lambda: client)
    assert result["draft"]["product_ids"] == ["mpv2_1"]
    assert result["product_ids"] == ["mpv2_1"]

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from integrations_control_center.google_ads_ai import (
    GoogleAIAnalysisInput,
    GoogleAIDraftInput,
    analyze_google_campaigns,
    generate_google_campaign_draft,
)


ACCOUNT = "1234567890"
OWNER = "owner-1"
URL = "https://mezansalla.com/product-1"


class ProductStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.reads = []

    async def find_one(self, query, projection):
        self.reads.append((copy.deepcopy(query), copy.deepcopy(projection)))
        for row in self.rows:
            if row.get("user_id") != query["user_id"]:
                continue
            if any(all(row.get(key) == value for key, value in clause.items()) for clause in query["$or"]):
                return copy.deepcopy(row)
        return None


class DraftDB:
    def __init__(self, rows=()):
        self.products = ProductStore(rows)

    def __getitem__(self, collection):
        assert collection == "mezan_products_v2", "Only the tenant Product V2 collection may be read"
        return self.products


class NoDB:
    def __getitem__(self, collection):
        raise AssertionError("Analysis must consume the authorized aggregate facts without DB access")


class ModelClient:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.responses = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return SimpleNamespace(status="completed", output_text=json.dumps(self.result, ensure_ascii=False))


@pytest.mark.asyncio
async def test_search_model_contract_excludes_foreign_fields_and_bounds_rsa_copy():
    client = ModelClient(draft_output())
    await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(),
        account_context=account_context(), client_factory=lambda: client)
    schema = client.calls[0]["text"]["format"]["schema"]
    definitions = schema["$defs"]
    campaign = definitions["GoogleCampaignDraft"]["properties"]
    assert campaign["campaign_type"]["enum"] == ["SEARCH"]
    assert not {"shopping", "app", "hotel"} & campaign.keys()
    choices = campaign["groups"]["items"]["anyOf"]
    formats = {}
    for choice in choices:
        group = definitions[choice["$ref"].rsplit("/", 1)[-1]]["properties"]
        formats[group["ad_format"]["enum"][0]] = group
        assert not {"ad_name", "geo_target_ids", "language_ids", "hotel_ids", "product_offer_ids"} & group.keys()
    assert set(formats) == {"RSA", "DSA"}
    rsa = formats["RSA"]
    assert rsa["headlines"]["minItems"] == 3
    assert rsa["headlines"]["maxItems"] == 15
    assert rsa["headlines"]["items"]["maxLength"] == 30
    assert rsa["descriptions"]["minItems"] == 2
    assert rsa["descriptions"]["maxItems"] == 4
    assert formats["DSA"]["headlines"]["maxItems"] == 0
    assert formats["DSA"]["descriptions"]["maxItems"] == 2


def product():
    return {
        "user_id": OWNER, "salla_product_id": "1", "mezan_product_id": "mpv2_1",
        "name": "هودي بالاسم", "description": "وصف المنتج customer@example.com +966 50 123 4567",
        "price": 139, "currency": "SAR", "status": "active", "quantity": 20,
        "raw_salla": {"urls": {"product": URL + "?email=private@example.com"}, "customer_name": "PRIVATE", "token": "raw-secret"},
        "options": [{"id": "size", "name": "المقاس", "values": [
            {"id": "kids", "name": "أطفال", "additional_price": 0, "price": 0},
            {"id": "adults", "name": "كبار", "price": 10, "customer_email": "variant-private@example.com"},
        ]}],
        "variants": [{"id": "adult-1", "name": "مقاس كبار", "price": 149, "quantity": 12,
                      "customer_phone": "private-phone", "selections": [{"name": "المقاس", "value": "كبار", "address": "private-address"}]}],
        "last_synced_at": "2026-10-13T15:00:00+00:00", "details_loaded": True,
        "owner_email": "catalog-private@example.com", "secret": "secret-never-to-model",
    }


def account_context():
    return {
        "account_id": ACCOUNT, "customer_id": ACCOUNT, "currency_code": "USD",
        "time_zone": "America/New_York", "supported_campaign_types": ["SEARCH", "DISPLAY"],
        "geo_targets": [{"id": "2124", "resource_name": "geoTargetConstants/2124", "name": "موقع مثبت"}],
        "language_targets": [{"id": "1019", "resource_name": "languageConstants/1019", "name": "العربية"}],
        "asset_inventory": [{"resource_name": f"customers/{ACCOUNT}/assets/10", "type": "IMAGE", "name": "صورة"}],
        "conversion_actions": [], "access_token": "account-credential-never-to-model",
    }


def draft_output():
    return {
        "draft": {
            "account_id": ACCOUNT, "campaign_type": "SEARCH", "name": "حملة الهودي", "budget_native": "90",
            "bidding": {"strategy": "MAXIMIZE_CLICKS"}, "geo_target_ids": ["2124"], "language_ids": ["1019"],
            "groups": [{"name": "هودي", "ad_format": "RSA", "final_urls": [URL],
                        "headlines": ["هودي بالاسم", "اختر مقاسك", "اطلبه من أماسي"],
                        "descriptions": ["اختر مقاس الهودي المناسب لك", "اكتشف هودي أماسي بالاسم"],
                        "keywords": [{"text": "هودي بالاسم", "match_type": "PHRASE"}]}],
        },
        "product_ids": ["1"], "explanation": "مسودة بحث للمنتج المحدد من بيانات الكتالوج.",
        "evidence_ids": ["product:1", f"google:account:{ACCOUNT}"],
        "limitations": [], "missing_requirements": [],
    }


def draft_request(**changes):
    return GoogleAIDraftInput.model_validate({
        "account_id": ACCOUNT, "prompt": "جهز حملة للمنتج بميزانية ضمن الحد المحدد", "budget_ceiling_native": "100",
        "product_ids": ["1"], "campaign_type": "SEARCH", **changes,
    })


@pytest.mark.asyncio
async def test_faithful_tenant_draft_preserves_variants_and_excludes_pii_secrets():
    db, client = DraftDB([product()]), ModelClient(draft_output())
    result = await generate_google_campaign_draft(db, OWNER, draft_request(
        prompt="جهز الحملة customer@example.com +966 50 123 4567 api_key=sk-private-secret-value"),
        account_context=account_context(), client_factory=lambda: client)
    assert result["ready_for_preview"] is True
    assert result["ready_for_execution"] is False
    assert result["provider_writes"] == 0
    assert result["draft"]["budget_native"] == "90"
    assert result["evidence"][0]["variants"][0]["price_native"] == "149"
    assert result["evidence"][0]["options"][0]["values"][1]["additional_price_native"] is None
    assert "option_price_addition_semantics_unverified" in result["limitations"]
    query, projection = db.products.reads[0]
    assert query["user_id"] == OWNER
    assert "raw_salla" not in projection
    assert projection["raw_salla.urls.product"] == 1
    request = client.calls[0]
    assert request["tools"] == []
    assert request["store"] is False
    assert request["text"]["format"]["strict"] is True
    model_input = request["input"]
    for private in ("customer@example.com", "966 50 123 4567", "sk-private-secret-value", "raw-secret", "PRIVATE",
                    "variant-private@example.com", "private-address", "account-credential-never-to-model", "owner_email"):
        assert private not in model_input
    payload = json.loads(model_input)
    assert payload["account"]["currency_code"] == "USD"
    assert payload["account"]["time_zone"] == "America/New_York"
    assert payload["evidence"]["product:1"]["canonical_final_url"] == URL
    assert payload["evidence"]["product:1"]["last_synced_at"] == "2026-10-13T15:00:00+00:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    (lambda output: output["draft"].update(budget_native="100.000001"), "google_ai_budget_ceiling_exceeded"),
    (lambda output: output["draft"].update(account_id="999999"), "google_ai_draft_account_mismatch"),
    (lambda output: output["draft"].update(campaign_type="VIDEO"), "google_ai_draft_type_unsupported"),
    (lambda output: output["draft"].update(geo_target_ids=["99999"]), "google_ai_geo_target_ids_unknown"),
    (lambda output: output["draft"].update(conversion_goals=[{"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}]), "google_ai_conversion_goal_unknown"),
    (lambda output: output["draft"]["groups"][0].update(image_assets=[f"customers/{ACCOUNT}/assets/999"]), "google_ai_asset_reference_unknown"),
    (lambda output: output["draft"]["groups"][0].update(tall_portrait_image_assets=[f"customers/{ACCOUNT}/assets/999"]), "google_ai_asset_reference_unknown"),
    (lambda output: output["draft"]["groups"][0].update(media_bundle_asset=f"customers/{ACCOUNT}/assets/999"), "google_ai_asset_reference_unknown"),
    (lambda output: output["draft"]["groups"][0].update(ads=[{"ad_format": "RSA", "image_assets": [f"customers/{ACCOUNT}/assets/999"]}]), "google_ai_asset_reference_unknown"),
    (lambda output: output["draft"]["groups"][0].update(image_assets=["customers/999999/assets/10"]), "google_ai_draft_contract_invalid"),
    (lambda output: output["draft"]["groups"][0].update(final_urls=["https://foreign.example/product"]), "google_ai_destination_unknown"),
    (lambda output: output.update(product_ids=["foreign-product"]), "google_ai_product_reference_unknown"),
    (lambda output: output.update(product_ids=[]), "google_ai_selected_product_scope_changed"),
    (lambda output: output.update(evidence_ids=["product:foreign"]), "google_ai_evidence_reference_unknown"),
])
async def test_model_cannot_override_account_money_or_trusted_references(mutation, code):
    output = draft_output()
    mutation(output)
    client = ModelClient(output)
    with pytest.raises(HTTPException) as error:
        await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(),
                                             account_context=account_context(), client_factory=lambda: client)
    assert error.value.detail["code"] == code
    assert error.value.detail["provider_writes"] == 0


@pytest.mark.asyncio
async def test_missing_product_and_assets_return_editable_incomplete_draft():
    output = draft_output()
    output["draft"]["campaign_type"] = "DISPLAY"
    output["draft"]["groups"] = []
    output["product_ids"] = []
    output["evidence_ids"] = [f"google:account:{ACCOUNT}"]
    client = ModelClient(output)
    result = await generate_google_campaign_draft(DraftDB(), OWNER, draft_request(campaign_type="DISPLAY"),
                                                  account_context=account_context(), client_factory=lambda: client)
    assert result["draft"]["campaign_type"] == "DISPLAY"
    assert result["ready_for_preview"] is False
    assert any(item["code"] == "product_v2_fact_missing" for item in result["missing_requirements"])
    assert len(result["missing_requirements"]) >= 2
    assert result["ready_for_execution"] is False


@pytest.mark.asyncio
async def test_selected_product_retail_draft_distinguishes_owner_mapping_from_native_eligibility():
    output = draft_output()
    output["draft"].update(campaign_type="SHOPPING", shopping={"merchant_id": "123", "feed_label": "SA"},
                           bidding={"strategy": "MANUAL_CPC", "max_cpc_native": "1"},
                           groups=[{"name": "هودي", "ad_format": "PRODUCT", "product_offer_ids": []}])
    context = account_context()
    context.update(merchant_id="123", supported_campaign_types=["SHOPPING"], product_offer_ids=["offer-1"])
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(campaign_type="SHOPPING"),
                                                  account_context=context, client_factory=lambda: ModelClient(output))
    assert result["ready_for_preview"] is False
    assert any(item["code"] == "merchant_product_offer_mapping_required" for item in result["missing_requirements"])
    output["draft"]["groups"][0]["product_offer_ids"] = ["offer-1"]
    context["product_offer_associations"] = [{"product_id": "1", "offer_id": "offer-1", "merchant_id": "123", "verification_status": "verified"}]
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(campaign_type="SHOPPING"),
                                                  account_context=context, client_factory=lambda: ModelClient(output))
    assert any(item["code"] == "merchant_product_offer_mapping_required" for item in result["missing_requirements"])
    selection = {"product_id": "1", "merchant_id": "123", "offer_id": "offer-1"}
    context["product_offer_associations"] = [{**selection, "feed_label": "SA", "language_code": "ar", "channel": "ONLINE",
        "verification_status": "provider_eligibility_verified", "mapping_status": "owner_selected_unverified"}]
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER,
        draft_request(campaign_type="SHOPPING", product_offer_selections=[selection]),
        account_context=context, client_factory=lambda: ModelClient(output))
    assert not any(item["code"] == "merchant_product_offer_mapping_required" for item in result["missing_requirements"])
    assert result["product_offer_associations"][0]["mapping_status"] == "owner_selected_unverified"
    assert "owner_selected_product_offer_mapping_not_independently_verified" in result["limitations"]
    output["draft"]["shopping"]["feed_label"] = "QA"
    result = await generate_google_campaign_draft(DraftDB([product()]), OWNER,
        draft_request(campaign_type="SHOPPING", product_offer_selections=[selection]),
        account_context=context, client_factory=lambda: ModelClient(output))
    assert any(item["code"] == "merchant_product_offer_mapping_required" for item in result["missing_requirements"])


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,context,code", [
    ({"campaign_type": "VIDEO"}, account_context(), "google_ai_campaign_type_unsupported"),
    ({"campaign_type": "SMART"}, account_context(), "google_ai_campaign_type_unsupported"),
    ({}, {**account_context(), "account_id": "foreign"}, "google_ai_account_scope_mismatch"),
])
async def test_invalid_request_does_not_call_model(changes, context, code):
    client = ModelClient(draft_output())
    with pytest.raises(HTTPException) as error:
        await generate_google_campaign_draft(DraftDB([product()]), OWNER, draft_request(**changes),
                                             account_context=context, client_factory=lambda: client)
    assert error.value.detail["code"] == code
    assert client.calls == []


@pytest.mark.asyncio
async def test_foreign_catalog_product_is_unavailable_not_revealed():
    foreign = {**product(), "user_id": "other-owner"}
    output = draft_output()
    output.update(product_ids=[], evidence_ids=[f"google:account:{ACCOUNT}"])
    output["draft"]["groups"] = []
    client = ModelClient(output)
    result = await generate_google_campaign_draft(DraftDB([foreign]), OWNER, draft_request(),
                                                  account_context=account_context(), client_factory=lambda: client)
    assert result["ready_for_preview"] is False
    assert "other-owner" not in client.calls[0]["input"]
    assert not any(item.get("product_id") == "1" for item in result["evidence"])


def analysis_body():
    return GoogleAIAnalysisInput(account_id=ACCOUNT, date_from="2026-10-12", date_to="2026-10-13")


def campaign_facts():
    return {
        "account_id": ACCOUNT, "currency_code": "USD", "time_zone": "America/New_York",
        "date_from": "2026-10-12", "date_to": "2026-10-13", "permitted_actions": ["CHANGE_BUDGET", "PAUSE_CAMPAIGN"],
        "limits": {"max_daily_budget_native": "300", "max_budget_change_percent": "20"},
        "campaigns": [{"campaign_id": "11", "name": "حملة الهودي private@example.com", "campaign_type": "SEARCH",
                       "status": "ENABLED", "budget_native": "90", "budget_mode": "DAILY", "source": "google_ads_api",
                       "observed_at": "2026-10-13T15:00:00+00:00",
                       "metrics": {"spend_native": "120", "impressions": 1000, "clicks": 80, "conversions": "3.5",
                                   "conversion_value_native": "490", "roas": "4.0833", "customer_email": "metrics-private@example.com"},
                       "raw": {"token": "raw-provider-token"}}],
        "customer_email": "account-private@example.com", "api_key": "other-secret",
    }


def analysis_output(action="MONITOR", budget=None):
    return {
        "summary": "راجع بيانات الأداء والإسناد قبل تغيير الإنفاق.",
        "recommendations": [{"campaign_id": "11", "action": action, "title": "متابعة الحملة",
                             "rationale": "راجع النتائج المثبتة وتتبع الأداء خلال النافذة نفسها.",
                             "priority": "medium", "confidence": "medium",
                             "evidence_ids": [f"google:campaign:{ACCOUNT}:11:spend_native", f"google:campaign:{ACCOUNT}:11:conversions"],
                             "proposed_budget_native": budget, "observation_plan": "راجع دورة تحويل مكتملة مع بيانات سلة.",
                             "limitations": []}],
        "limitations": [],
    }


@pytest.mark.asyncio
async def test_analysis_generation_schema_binds_each_campaign_to_its_own_evidence_and_actions():
    facts = campaign_facts()
    second = {**facts["campaigns"][0], "campaign_id": "22", "status": "PAUSED",
              "metrics": {"spend_native": "0"}}
    facts["campaigns"].append(second)
    client = ModelClient(analysis_output())
    await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    schema = client.calls[0]["text"]["format"]["schema"]
    item = schema["properties"]["recommendations"]["items"]
    branches = item["anyOf"]
    scopes = {b["properties"]["campaign_id"]["enum"][0]: b["properties"] for b in branches}
    assert set(scopes) == {"11", "22"}
    import re
    for campaign_id, props in scopes.items():
        pattern = props["evidence_ids"]["items"]["pattern"]
        assert re.fullmatch(pattern, f"google:campaign:{ACCOUNT}:{campaign_id}:spend_native")
        other_id = "22" if campaign_id == "11" else "11"
        assert not re.fullmatch(pattern, f"google:campaign:{ACCOUNT}:{other_id}:spend_native")
        assert not re.fullmatch(pattern, f"google:campaign:{ACCOUNT}:{campaign_id}:invented_metric")
    assert re.fullmatch(scopes["11"]["evidence_ids"]["items"]["pattern"], f"google:campaign:{ACCOUNT}:11:conversions")
    assert not re.fullmatch(scopes["22"]["evidence_ids"]["items"]["pattern"], f"google:campaign:{ACCOUNT}:22:conversions")
    actions = lambda props: schema["$defs"][props["action"]["$ref"].rsplit("/", 1)[-1]]["enum"]
    assert "PAUSE_CAMPAIGN" in actions(scopes["11"])
    assert "PAUSE_CAMPAIGN" not in actions(scopes["22"])


@pytest.mark.asyncio
async def test_analysis_generation_schema_excludes_campaigns_without_any_verified_evidence():
    facts = campaign_facts()
    facts["campaigns"].append({**facts["campaigns"][0], "campaign_id": "22", "metrics": {}})
    client = ModelClient(analysis_output())
    await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    schema = client.calls[0]["text"]["format"]["schema"]
    branches = schema["properties"]["recommendations"]["items"]["anyOf"]
    assert [b["properties"]["campaign_id"]["enum"] for b in branches] == [["11"]]


@pytest.mark.asyncio
async def test_analysis_accepts_100_native_campaigns_within_existing_context_budget():
    import copy
    facts = campaign_facts()
    original = facts["campaigns"][0]
    facts["campaigns"] = [{**copy.deepcopy(original), "campaign_id": str(11 + index)}
                          for index in range(100)]
    client = ModelClient(analysis_output())
    result = await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts,
                                           client_factory=lambda: client)
    assert len(client.calls) == 1
    assert result["recommendations"][0]["campaign_id"] == "11"
    assert result["provider_writes"] == 0
    payload = json.loads(client.calls[0]["input"])
    assert payload["period"]["currency_code"] == "USD"
    assert payload["period"]["time_zone"] == "America/New_York"
    assert payload["period"]["date_from"] == "2026-10-12"
    assert payload["period"]["date_to"] == "2026-10-13"
    assert "date_from" not in next(iter(payload["evidence"].values()))
    evidence = result["recommendations"][0]["evidence"][0]
    assert {key: evidence[key] for key in ("currency_code", "time_zone", "date_from", "date_to")} == {
        "currency_code": "USD", "time_zone": "America/New_York",
        "date_from": "2026-10-12", "date_to": "2026-10-13"}


def test_analysis_generation_schema_supports_full_100_campaign_scope_within_service_limits():
    from integrations_control_center.google_ads_ai import _analysis_reasoning_schema, _strict_schema
    actions = ["MONITOR", "REVIEW_TRACKING", "PAUSE_CAMPAIGN", "CHANGE_BUDGET"]
    campaigns = {str(index): {"allowed_actions": actions} for index in range(100)}
    registry = {f"{source}:campaign:{ACCOUNT}:{index}:{metric}": {"campaign_id": str(index)}
                for index in range(100) for source in ("google", "salla", "profitability")
                for metric in ("spend_native", "conversions", "roas", "value_native")}
    schema = _strict_schema(_analysis_reasoning_schema(campaigns, registry))
    def enums(value):
        if isinstance(value, dict):
            return len(value.get("enum", [])) + sum(enums(child) for key, child in value.items() if key != "enum")
        return sum(map(enums, value)) if isinstance(value, list) else 0
    assert len(schema["properties"]["recommendations"]["items"]["anyOf"]) == 100
    assert enums(schema) <= 1000
    assert len(json.dumps(schema).encode()) < 200_000


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DAILY", "TOTAL"])
@pytest.mark.parametrize("start", ["2026-10-12", "2026-10-13"])
async def test_analysis_supplies_budget_semantics_without_inventing_verified_billing_limits(mode, start):
    facts = campaign_facts()
    facts["date_from"] = start
    facts["campaigns"][0].update(budget_mode=mode, budget_native="40")
    facts["campaigns"][0]["metrics"]["spend_native"] = "42.32"
    client = ModelClient(analysis_output())
    body = GoogleAIAnalysisInput(account_id=ACCOUNT, date_from=start, date_to="2026-10-13")
    result = await analyze_google_campaigns(NoDB(), OWNER, body, facts=facts, client_factory=lambda: client)
    payload = json.loads(client.calls[0]["input"])
    semantics = payload["budget_semantics"]
    assert semantics["DAILY"]["kind"] == "average_daily_budget"
    assert semantics["DAILY"]["hard_daily_spend_cap"] is False
    assert semantics["TOTAL"]["kind"] == "campaign_total_budget"
    assert semantics["billing_limits_verified"] is False
    assert semantics["budget_history_verified"] is False
    assert "daily_spending_limit_native" not in semantics
    assert "monthly_spending_limit_native" not in semantics
    assert payload["period"]["date_from"] == start
    assert payload["period"]["date_to"] == "2026-10-13"
    assert payload["campaigns"][0]["budget_mode"] == mode
    assert payload["campaigns"][0]["budget_native"] == "40"
    assert payload["campaigns"][0]["metrics"]["spend_native"] == "42.32"
    assert result["provider_writes"] == 0


@pytest.mark.asyncio
async def test_analysis_binds_exact_native_evidence_and_never_queries_orders_or_executes():
    client = ModelClient(analysis_output("CHANGE_BUDGET", "100"))
    result = await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: client)
    recommendation = result["recommendations"][0]
    assert recommendation["evidence"][1]["value"] == "3.5"
    assert recommendation["evidence"][1]["source"] == "google_ads_api"
    assert recommendation["evidence"][1]["currency_code"] == "USD"
    assert result["period"]["time_zone"] == "America/New_York"
    assert recommendation["proposed_budget_native"] == "100"
    assert recommendation["approval_required"] is True
    assert recommendation["executable"] is False
    assert result["read_only"] is True
    assert result["provider_writes"] == 0
    assert "profitability_unverified" in result["limitations"]
    assert "salla_attribution_unverified" in result["limitations"]
    model_input = client.calls[0]["input"]
    for private in ("private@example.com", "metrics-private@example.com", "account-private@example.com", "raw-provider-token", "other-secret", "customer_email"):
        assert private not in model_input


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,code", [
    (lambda output: output["recommendations"][0].update(campaign_id="999"), "google_ai_recommendation_campaign_unknown"),
    (lambda output: output["recommendations"][0].update(evidence_ids=[f"google:campaign:{ACCOUNT}:999:spend_native"]), "google_ai_recommendation_evidence_unknown"),
    (lambda output: output["recommendations"][0].update(action="RESUME_CAMPAIGN"), "google_ai_recommendation_action_not_permitted"),
    (lambda output: output["recommendations"][0].update(action="CHANGE_BUDGET", proposed_budget_native="500"), "google_ai_recommendation_budget_invalid"),
    (lambda output: output["recommendations"][0].update(action="CHANGE_BUDGET", proposed_budget_native="120"), "google_ai_recommendation_budget_change_exceeded"),
    (lambda output: output["recommendations"][0].update(proposed_budget_native="50"), "google_ai_recommendation_budget_action_mismatch"),
    (lambda output: output.update(summary="صافي الربح 100 دولار"), "google_ai_profit_claim_unverified"),
])
async def test_analysis_rejects_model_fabrication_and_financial_limits(mutation, code):
    output = analysis_output()
    mutation(output)
    client = ModelClient(output)
    with pytest.raises(HTTPException) as error:
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: client)
    assert error.value.detail["code"] == code
    assert error.value.detail["provider_writes"] == 0


@pytest.mark.asyncio
async def test_platform_conversions_and_verified_salla_orders_remain_separate():
    facts = campaign_facts()
    facts["campaigns"][0]["salla_results"] = {
        "source": "salla_order_attribution_v3", "complete": True, "attribution": "campaign_exact", "currency_code": "USD",
        "time_zone": "America/New_York", "date_from": "2026-10-12", "date_to": "2026-10-13", "orders": 2, "revenue_native": "280",
    }
    client = ModelClient(analysis_output())
    result = await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    conversions = next(item for item in result["evidence"] if item["metric"] == "conversions")
    orders = next(item for item in result["evidence"] if item["metric"] == "orders")
    assert conversions["value"] == "3.5"
    assert orders["value"] == "2"
    assert orders["source"] == "salla_order_attribution_v3"
    assert "salla_attribution_unverified" not in result["limitations"]
    assert "profitability_unverified" in result["limitations"]


@pytest.mark.asyncio
async def test_incomplete_attribution_is_excluded_and_missing_metrics_are_not_zero():
    facts = campaign_facts()
    facts["campaigns"][0]["metrics"]["conversions"] = None
    facts["campaigns"][0]["salla_results"] = {"source": "salla_orders", "complete": False, "orders": 99}
    output = analysis_output()
    output["recommendations"][0]["evidence_ids"] = [f"google:campaign:{ACCOUNT}:11:spend_native"]
    client = ModelClient(output)
    result = await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    payload = json.loads(client.calls[0]["input"])
    assert payload["campaigns"][0]["metrics"]["conversions"] is None
    assert not any(item["metric"] == "orders" for item in result["evidence"])
    assert "salla_evidence_unverified:11" in result["limitations"]


@pytest.mark.asyncio
async def test_no_performance_facts_returns_no_recommendations_without_model_call():
    client = ModelClient(analysis_output())
    facts = {**campaign_facts(), "campaigns": []}
    result = await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    assert result["recommendations"] == []
    assert "campaign_performance_evidence_unavailable" in result["limitations"]
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value,code", [
    ("account_id", "foreign", "google_ai_account_scope_mismatch"),
    ("date_from", "2026-10-11", "google_ai_evidence_period_mismatch"),
])
async def test_foreign_or_wrong_period_analysis_is_rejected_before_model(key, value, code):
    facts, client = campaign_facts(), ModelClient(analysis_output())
    facts[key] = value
    with pytest.raises(HTTPException) as error:
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=facts, client_factory=lambda: client)
    assert error.value.detail["code"] == code
    assert client.calls == []


@pytest.mark.asyncio
async def test_interactive_analysis_has_a_bounded_budget_for_structured_reasoning(monkeypatch):
    monkeypatch.delenv("MEZAN_OPENAI_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MEZAN_CAMPAIGN_AI_MODEL", raising=False)
    monkeypatch.delenv("MEZAN_OPENAI_MODEL", raising=False)
    client = ModelClient(analysis_output())
    await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: client)
    assert client.calls[0]["timeout"] == 40.0
    assert client.calls[0]["reasoning"] == {"effort": "minimal"}
    assert client.calls[0]["tools"] == []
    assert client.calls[0]["max_output_tokens"] == 8000


@pytest.mark.asyncio
async def test_analysis_rejects_an_unbounded_recommendation_list():
    output = analysis_output()
    output["recommendations"] *= 7
    with pytest.raises(HTTPException) as error:
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: ModelClient(output))
    assert error.value.detail["code"] == "google_ai_analysis_contract_invalid"


@pytest.mark.asyncio
async def test_analysis_respects_configured_models_without_minimal_effort(monkeypatch):
    monkeypatch.setenv("MEZAN_CAMPAIGN_AI_MODEL", "gpt-5.1")
    client = ModelClient(analysis_output())
    await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: client)
    assert client.calls[0]["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_incomplete_response_logs_only_safe_failure_metadata(caplog):
    class Incomplete(ModelClient):
        async def create(self, **kwargs):
            return SimpleNamespace(status="incomplete", output_text="private-model-text", incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                usage=SimpleNamespace(output_tokens=6000, output_tokens_details=SimpleNamespace(reasoning_tokens=5900)))
    with pytest.raises(HTTPException) as error:
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: Incomplete({}))
    assert error.value.detail["code"] == "google_ai_response_incomplete"
    assert "max_output_tokens" in caplog.text and "5900" in caplog.text
    assert "google_ai_response_incomplete" in caplog.text
    assert "private-model-text" not in caplog.text


@pytest.mark.asyncio
async def test_diagnostic_metadata_cannot_log_untrusted_provider_strings(caplog):
    class Malformed(ModelClient):
        async def create(self, **kwargs):
            return SimpleNamespace(status="private-provider-status", output_text="private-model-text",
                incomplete_details=SimpleNamespace(reason="private-provider-reason"),
                usage=SimpleNamespace(output_tokens="private-usage", output_tokens_details=SimpleNamespace(reasoning_tokens="private-reasoning")))
    with pytest.raises(HTTPException):
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: Malformed({}))
    assert "reason=unknown" in caplog.text
    assert "private-" not in caplog.text


@pytest.mark.asyncio
async def test_model_timeout_is_bounded_and_errors_do_not_echo_secrets(monkeypatch):
    monkeypatch.setenv("MEZAN_OPENAI_TIMEOUT_SECONDS", "0.05")
    client = ModelClient(analysis_output())

    async def slow_response(**kwargs):
        await asyncio.sleep(0.2)
        raise RuntimeError("secret-provider-body")

    client.create = slow_response
    with pytest.raises(HTTPException) as error:
        await analyze_google_campaigns(NoDB(), OWNER, analysis_body(), facts=campaign_facts(), client_factory=lambda: client)
    assert error.value.status_code == 504
    assert error.value.detail == {"code": "google_ai_timeout", "provider_writes": 0}


@pytest.mark.parametrize("changes", [
    {"budget_ceiling_native": "NaN"}, {"budget_ceiling_native": "1e3"}, {"budget_ceiling_native": "-1"},
    {"budget_ceiling_native": "0"}, {"budget_ceiling_native": 100}, {"unexpected_provider_operation": {}},
])
def test_draft_request_disallows_ambiguous_money_and_raw_operations(changes):
    with pytest.raises(ValidationError):
        draft_request(**changes)


def test_analysis_dates_require_real_dates_and_bounded_ordered_period():
    for date_from, date_to in (("2026-02-30", "2026-03-01"), ("2026-10-13", "2026-10-12"), ("2026-09-01", "2026-10-13")):
        with pytest.raises(ValidationError):
            GoogleAIAnalysisInput(account_id=ACCOUNT, date_from=date_from, date_to=date_to)

