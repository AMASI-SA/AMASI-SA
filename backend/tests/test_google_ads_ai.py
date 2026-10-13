"""Offline tests at the model boundary: provider and money facts stay authoritative."""
from __future__ import annotations

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
