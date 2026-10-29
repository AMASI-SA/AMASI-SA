import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import ValidationError

from integrations_control_center import google_ads_plan_suggestions as module
from integrations_control_center.google_ads_plan_suggestions import PlanSuggestionInput, _validate_fields


TEXT = "ركز على هدايا الشتاء في السعودية من 2026-11-01 إلى 2026-12-31، استهدف الرجال الباحثين عن هدية، والرسالة دفء الشتاء. رابط القسم https://amasi-sa.com/shawls. الميزانية 50 ريال"
KEYS = ("season", "starts_on", "ends_on", "market", "audience", "message", "category_url")


def quotes(**changes):
    return {"season": "هدايا الشتاء", "starts_on": "2026-11-01", "ends_on": "2026-12-31",
            "market": "السعودية", "audience": "الرجال الباحثين عن هدية", "message": "دفء الشتاء",
            "category_url": "https://amasi-sa.com/shawls", **changes}


@pytest.mark.asyncio
async def test_structured_reasoning_uses_only_instruction_no_tools_and_never_proposes_money_or_authority():
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="completed", output_text=json.dumps(quotes(), ensure_ascii=False))
    result = await module.suggest_commercial_plan(PlanSuggestionInput(instruction=TEXT),
        client_factory=lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    assert len(calls) == 1 and calls[0]["tools"] == [] and calls[0]["store"] is False
    assert json.loads(calls[0]["input"]) == {"owner_instruction": TEXT}
    assert calls[0]["text"]["format"]["strict"] is True
    assert result["fields"] == {**quotes(), "market": "SA"}
    assert result["evidence"] == quotes()
    assert set(result["fields"]) == set(KEYS)
    assert "limits" in result["manual_fields"] and "pinned_product_ids" in result["manual_fields"]
    assert result["missing_fields"] == []
    assert result["status"] == "unreviewed_suggestion"
    assert result["execution_authorized"] is result["persisted"] is False
    assert result["provider_writes"] == 0


@pytest.mark.parametrize("raw", [quotes(season="موسم افتراضي"), quotes(audience="الجميع"),
    quotes(market="SA"), quotes(category_url="https://evil.invalid"), quotes(execute=True),
    quotes(limits={"daily_budget_native": "1000"}), quotes(pinned_product_ids=["123"]),
    quotes(season=" "), quotes(season=123), {"season": "هدايا الشتاء"}])
def test_invented_nonquoted_extra_or_incomplete_output_is_rejected(raw):
    with pytest.raises(HTTPException) as exc:
        _validate_fields(TEXT, raw)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "google_plan_suggestion_unverified"
    assert "evil" not in str(exc.value.detail)


def test_missing_facts_remain_null_without_dates_budget_targets_or_product_defaults():
    fields, evidence = _validate_fields("ركز على الشتاء بالسعودية", {**dict.fromkeys(KEYS), "season": "الشتاء", "market": "السعودية"})
    assert fields == {**dict.fromkeys(KEYS), "season": "الشتاء", "market": "SA"}
    assert evidence == {"season": "الشتاء", "market": "السعودية"}


@pytest.mark.parametrize("value", ["2026-02-30", "غدًا", "2026/11/01", "٢٠٢٦/١١/٠١"])
def test_quoted_ambiguous_or_invalid_dates_cannot_become_calendar_facts(value):
    with pytest.raises(HTTPException):
        _validate_fields(value, {**dict.fromkeys(KEYS), "starts_on": value})


def test_arabic_digits_are_normalized_only_in_explicit_iso_dates():
    fields, evidence = _validate_fields("من ٢٠٢٦-١١-٠١", {**dict.fromkeys(KEYS), "starts_on": "٢٠٢٦-١١-٠١"})
    assert fields["starts_on"] == "2026-11-01" and evidence["starts_on"] == "٢٠٢٦-١١-٠١"


@pytest.mark.parametrize("start,end", [("2026-12-31", "2026-11-01"), ("2026-01-01", "2029-01-01")])
def test_invalid_complete_interval_is_rejected(start, end):
    with pytest.raises(HTTPException):
        _validate_fields(f"{start} {end}", {**dict.fromkeys(KEYS), "starts_on": start, "ends_on": end})


@pytest.mark.parametrize("url", ["http://example.com/c", "https://user:password@example.com/c", "https://example.com/c#secret", "https://example.com/a\nb"])
def test_unsafe_literal_url_is_rejected_without_fetching(url):
    with pytest.raises(HTTPException):
        _validate_fields(url, {**dict.fromkeys(KEYS), "category_url": url})


def test_unsupported_country_normalization_is_left_unresolved():
    fields, evidence = _validate_fields("استهدف كندا", {**dict.fromkeys(KEYS), "market": "كندا"})
    assert fields["market"] is None and "market" not in evidence


@pytest.mark.parametrize("instruction", [" ", "x" * 6001, 123])
def test_input_is_bounded_and_strict(instruction):
    with pytest.raises(ValidationError):
        PlanSuggestionInput(instruction=instruction)


@pytest.mark.asyncio
async def test_owner_gate_precedes_reasoning_and_route_never_reads_or_writes_database(monkeypatch):
    from integrations_control_center import google_commercial_plan as routes
    class NoDatabase:
        def __getitem__(self, name):
            raise AssertionError("suggestions must not read or write persisted data")
    identity = {"user_id": "owner", "actor_id": "staff", "owner": False}
    calls = []
    async def principal(*args, **kwargs): return dict(identity)
    async def current_user(): return {"id": identity["actor_id"]}
    async def reason(**kwargs):
        calls.append(kwargs)
        return dict.fromkeys(KEYS)
    monkeypatch.setattr(routes, "principal", principal)
    monkeypatch.setattr(module, "_reason", reason)
    app, router = FastAPI(), APIRouter()
    routes.attach_google_commercial_plan_routes(router, NoDatabase(), current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/google_ads/commercial-plan-suggestions", json={"instruction": TEXT})).status_code == 403
        assert calls == []
        identity.update(owner=True, actor_id="owner")
        result = await client.post("/google_ads/commercial-plan-suggestions", json={"instruction": TEXT})
        assert result.status_code == 200 and result.json()["persisted"] is False
        assert result.json()["missing_fields"] == list(KEYS)
        assert len(calls) == 1
        assert (await client.post("/google_ads/commercial-plan-suggestions", json={"instruction": TEXT, "execute": True})).status_code == 422
        assert len(calls) == 1
