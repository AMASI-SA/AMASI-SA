import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import campaign_ai_monitor

from campaign_ai_monitor import (
    DEFAULT_INITIAL_DELAY_SECONDS,
    RecommendationItem,
    RecommendationOutput,
    _bounded_account_sample,
    _ask_openai,
    _active,
    _deterministic_recommendations,
    _govern_output,
    _monitored_user_ids,
    _meta_child_entities,
    _normalize_openai_output,
    _openai_error_code,
    _recommendation_explanation,
    deterministic_candidates,
)


def entity(**overrides):
    value = {
        "provider": "snapchat",
        "entity_level": "ad",
        "entity_id": "ad-1",
        "entity_name": "إعلان المنتج",
        "spend_sar": 120.0,
        "revenue_sar": 0.0,
        "purchases": 0,
        "roas": 0.0,
        "cpa_sar": None,
        "data_complete": True,
    }
    value.update(overrides)
    return value


def test_all_active_spending_entities_reach_ai_ordered_by_spend():
    rows = deterministic_candidates([
        entity(entity_id="waste", spend_sar=140, purchases=0),
        entity(entity_id="watch", spend_sar=80, purchases=2, cpa_sar=40, roas=1.2),
    ])

    assert rows[0]["entity_id"] == "waste"
    assert rows[0]["spend_per_day_sar"] == 140
    assert {row["entity_id"] for row in rows} == {"waste", "watch"}


def test_preparation_does_not_assign_a_mezan_decision_signal():
    rows = deterministic_candidates([
        entity(entity_id="slow-waste", spend_sar=140, purchases=0, observed_days=3),
    ])

    assert "screening_signal" not in rows[0]


def test_ai_receives_profitable_entities_even_before_a_mezan_volume_threshold():
    rows = deterministic_candidates([
        entity(entity_id="scale", spend_sar=150, revenue_sar=600, purchases=4, roas=4, cpa_sar=37.5),
        entity(entity_id="too-early", spend_sar=40, revenue_sar=160, purchases=2, roas=4, cpa_sar=20),
    ])

    assert [row["entity_id"] for row in rows] == ["scale", "too-early"]


def test_zero_spend_entities_never_reach_openai_candidates():
    assert deterministic_candidates([entity(spend_sar=0, purchases=0)]) == []


def test_paused_entity_is_not_recommended_for_another_change():
    assert deterministic_candidates([entity(status="PAUSED", active=False)]) == []


def test_real_snapchat_arabic_delivery_status_reaches_ai_candidates():
    assert _active("يتم التسليم — مرحلة التعلم") is True
    row = entity(
        entity_id="snap-live",
        status="يتم التسليم — مرحلة التعلم",
        active=_active("يتم التسليم — مرحلة التعلم"),
    )
    assert [item["entity_id"] for item in deterministic_candidates([row])] == ["snap-live"]


def test_snapchat_negative_delivery_status_wins_before_active_words():
    assert _active("NOT_DELIVERING") is False
    assert _active("غير نشط — لا يتم التسليم") is False
    assert _active({
        "configured_status": "ACTIVE",
        "delivery_state": "NOT_DELIVERING",
        "delivery_status": "لا يتم التسليم",
    }) is False


def test_governance_discards_a_stale_pause_for_an_entity_now_stopped():
    stopped = entity(status="PAUSED", active=False)
    output = RecommendationOutput(
        summary="قديم",
        recommendations=[RecommendationItem(
            recommendation_id="old",
            provider="snapchat",
            entity_level="ad",
            entity_id="ad-1",
            entity_name="إعلان المنتج",
            action="pause",
            priority="critical",
            confidence="high",
            title="إيقاف",
            rationale="قرار أصبح قديمًا",
            evidence=[],
            why_now="قديم",
            recommended_wait_hours=5,
            observation_plan="راقب",
            success_criteria=[],
            risk_if_ignored="هدر",
            guardrail="موافقة",
            next_check_at="old",
        )],
    )

    governed = _govern_output(output, [stopped], next_check_at="2026-08-16T20:00:00+00:00")

    assert governed.recommendations == []


def test_meta_child_uses_latest_live_status_and_keeps_full_hierarchy():
    documents = [{
        "user_id": "owner", "ad_account_id": "act-1", "account_name": "أماسي",
        "entity_level": "ad", "entity_id": "ad-1", "entity_name": "إعلان عام",
        "campaign_id": "campaign-1", "campaign_name": "دمية متحركة",
        "campaign_status": "ACTIVE", "ad_group_id": "group-1",
        "ad_group_name": "مجموعة المبيعات", "ad_group_status": "ACTIVE",
        "configured_status": "PAUSED", "effective_status": "PAUSED", "status": "PAUSED",
        "status_updated_at": "2026-08-16T18:00:00+0000", "observed_at": "2026-08-16T18:01:00+00:00",
        "campaign_ad_group_count": 1, "campaign_ad_count": 1, "date": "2026-08-16",
        "spend_sar": 82.84, "revenue_sar": 0, "purchases": 0, "impressions": 100, "clicks": 2,
    }]

    class Cursor:
        def limit(self, _): return self
        async def to_list(self, length): return documents[:length]

    class Collection:
        def find(self, *_args, **_kwargs): return Cursor()

    class DB:
        def __getitem__(self, _name): return Collection()

    rows = asyncio.run(_meta_child_entities(
        DB(), "owner",
        datetime(2026, 8, 16).date(), datetime(2026, 8, 16).date(),
    ))

    assert rows[0]["status"] == "PAUSED"
    assert rows[0]["active"] is False
    assert rows[0]["campaign_name"] == "دمية متحركة"
    assert rows[0]["ad_group_name"] == "مجموعة المبيعات"
    assert rows[0]["campaign_ad_group_count"] == 1
    assert rows[0]["campaign_ad_count"] == 1


def test_meta_ad_keeps_its_own_spend_separate_from_group_and_campaign_totals():
    base = {
        "user_id": "owner", "ad_account_id": "act-1", "account_name": "أماسي",
        "campaign_id": "campaign-1", "campaign_name": "منتجات جاهزة أماسي",
        "campaign_status": "ACTIVE", "configured_status": "ACTIVE",
        "effective_status": "ACTIVE", "status": "ACTIVE",
        "observed_at": "2026-08-17T00:07:00+00:00", "date": "2026-08-17",
        "revenue_sar": 0, "impressions": 100, "clicks": 2,
    }
    documents = [
        {**base, "entity_level": "ad_group", "entity_id": "group-1", "entity_name": "المجموعة الأولى", "ad_group_id": "group-1", "ad_group_name": "المجموعة الأولى", "spend_sar": 54.25, "purchases": 1},
        {**base, "entity_level": "ad_group", "entity_id": "group-2", "entity_name": "المجموعة الثانية", "ad_group_id": "group-2", "ad_group_name": "المجموعة الثانية", "spend_sar": 249.97, "purchases": 3},
        {**base, "entity_level": "ad", "entity_id": "ad-1", "entity_name": "الإعلان المستهدف", "ad_group_id": "group-2", "ad_group_name": "المجموعة الثانية", "spend_sar": 37.18, "purchases": 0},
        {**base, "entity_level": "ad", "entity_id": "ad-2", "entity_name": "إعلان رابح", "ad_group_id": "group-2", "ad_group_name": "المجموعة الثانية", "spend_sar": 212.79, "purchases": 3},
    ]

    class Cursor:
        def limit(self, _): return self
        async def to_list(self, length): return documents[:length]

    class Collection:
        def find(self, *_args, **_kwargs): return Cursor()

    class DB:
        def __getitem__(self, _name): return Collection()

    rows = asyncio.run(_meta_child_entities(
        DB(), "owner",
        datetime(2026, 8, 17).date(), datetime(2026, 8, 17).date(),
    ))
    target = next(row for row in rows if row["entity_id"] == "ad-1")

    assert target["entity_period_spend_sar"] == 37.18
    assert target["ad_group_period_spend_sar"] == 249.97
    assert target["campaign_period_spend_sar"] == 304.22
    assert target["campaign_period_purchases"] == 4
    assert target["campaign_ad_group_count"] == 2
    assert target["campaign_ad_count"] == 2


def test_model_cannot_scale_an_entity_without_scale_evidence():
    candidate = entity(entity_id="waste", spend_sar=140, purchases=0)
    output = RecommendationOutput(
        summary="اختبار",
        recommendations=[RecommendationItem(
            recommendation_id="invented",
            provider="snapchat",
            entity_level="ad",
            entity_id="waste",
            entity_name="اسم غير موثوق",
            action="scale",
            change_percent=30,
            priority="high",
            confidence="high",
            title="اختبار",
            rationale="اختبار الحماية",
            evidence=["اختبار"],
            why_now="الوقت مناسب للاختبار",
            recommended_wait_hours=5,
            observation_plan="أعد القياس",
            success_criteria=["تحسن النتيجة"],
            risk_if_ignored="استمرار الهدر",
            guardrail="مراجعة",
            next_check_at="wrong",
        )],
    )

    governed = _govern_output(output, [candidate], next_check_at="2026-08-16T03:00:00+00:00")

    assert governed.recommendations[0].action == "monitor"
    assert governed.recommendations[0].change_percent is None
    assert governed.recommendations[0].entity_name == "إعلان المنتج"


def test_snapchat_recommendations_keep_each_account_identity_separate():
    candidates = [
        entity(
            entity_id="shared-ad",
            account_id="snap-account-1",
            account_name="أماسي الرئيسي",
            entity_name="إعلان الحساب الأول",
        ),
        entity(
            entity_id="shared-ad",
            account_id="snap-account-2",
            account_name="أماسي الوطني",
            entity_name="إعلان الحساب الثاني",
        ),
    ]
    recommendations = []
    for account_id in ("snap-account-1", "snap-account-2"):
        recommendations.append(RecommendationItem(
            recommendation_id="temporary",
            provider="snapchat",
            entity_level="ad",
            entity_id="shared-ad",
            entity_name="اسم يعاد توثيقه",
            account_id=account_id,
            account_name="اسم يعاد توثيقه",
            action="monitor",
            priority="medium",
            confidence="medium",
            title="مراقبة",
            rationale="اختبار فصل الحسابات",
            evidence=["اختبار"],
            why_now="اختبار",
            recommended_wait_hours=5,
            observation_plan="أعد القياس",
            success_criteria=["بقاء الحساب منفصلًا"],
            risk_if_ignored="اختلاط الحسابات",
            guardrail="مراجعة",
            next_check_at="wrong",
        ))

    governed = _govern_output(
        RecommendationOutput(
            summary="اختبار",
            recommendations=recommendations,
            limitations=[],
        ),
        candidates,
        next_check_at="2026-08-16T03:00:00+00:00",
    )

    assert [item.account_name for item in governed.recommendations] == [
        "أماسي الرئيسي",
        "أماسي الوطني",
    ]
    assert len({item.recommendation_id for item in governed.recommendations}) == 2


def test_bounded_sample_keeps_each_snapchat_account_in_the_ai_evidence():
    rows = [
        entity(entity_id="a1-high", account_id="account-1", spend_sar=500),
        entity(entity_id="a1-next", account_id="account-1", spend_sar=400),
        entity(entity_id="a2", account_id="account-2", spend_sar=20),
    ]

    sampled = _bounded_account_sample(rows, 2)

    assert {row["account_id"] for row in sampled} == {"account-1", "account-2"}


def test_scheduler_discovers_connected_v2_ad_account_owner():
    class Accounts:
        async def distinct(self, field, query):
            assert field == "user_id"
            assert query == {
                "provider": {"$in": ["snapchat_ads", "meta_ads"]},
                "connection_status": {"$in": ["connected", "needs_reauth"]},
            }
            return ["owner-1"]

    class DB:
        mezan_integration_accounts_v2 = Accounts()

    assert asyncio.run(_monitored_user_ids(DB())) == ["owner-1"]


def test_scheduler_discovers_legacy_credential_owners_when_v2_is_empty():
    class DistinctCollection:
        def __init__(self, values, expected_query):
            self.values = values
            self.expected_query = expected_query

        async def distinct(self, field, query):
            assert field == "user_id"
            assert query == self.expected_query
            return self.values

    class DB:
        mezan_integration_accounts_v2 = DistinctCollection([], {
            "provider": {"$in": ["snapchat_ads", "meta_ads"]},
            "connection_status": {"$in": ["connected", "needs_reauth"]},
        })
        mezan_integrations_v2 = DistinctCollection([], {
            "provider": {"$in": ["snapchat_ads", "meta_ads"]},
            "connection_status": "connected",
        })
        snapchat_connections = DistinctCollection(["snap-owner", "shared"], {
            "refresh_token": {"$exists": True, "$nin": ["", None]},
        })
        meta_connections = DistinctCollection(["meta-owner", "shared", ""], {
            "access_token": {"$exists": True, "$nin": ["", None]},
            "connection_status": {
                "$nin": ["error", "failed", "last_check_failed"],
            },
        })

    assert asyncio.run(_monitored_user_ids(DB())) == [
        "meta-owner", "shared", "snap-owner",
    ]


def test_scheduler_keeps_legacy_fallback_out_of_the_v2_path():
    class Accounts:
        async def distinct(self, field, query):
            assert field == "user_id"
            assert query["connection_status"]["$in"] == ["connected", "needs_reauth"]
            return ["owner-1"]

    class DB:
        mezan_integration_accounts_v2 = Accounts()

        def __getattr__(self, name):
            raise AssertionError(f"unexpected fallback collection: {name}")

    assert asyncio.run(_monitored_user_ids(DB())) == ["owner-1"]


def test_first_monitor_pass_starts_promptly_after_boot():
    assert DEFAULT_INITIAL_DELAY_SECONDS <= 10


def test_unavailable_model_returns_conservative_mezan_fallback():
    candidate = deterministic_candidates([
        entity(entity_id="fast-waste", spend_sar=180, purchases=0),
    ])[0]

    result = _deterministic_recommendations(
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="openai_recommendation:ValidationError",
    )

    assert result.recommendations[0].action == "pause"
    assert "ميزان" in result.recommendations[0].guardrail
    assert result.limitations == ["openai_recommendation:ValidationError"]


def test_openai_output_normalizer_repairs_safe_format_variance_without_changing_decision():
    candidate = entity(
        provider="snapchat",
        entity_level="ad_group",
        entity_id="group-1",
        entity_name="مجموعة الوطني",
        account_id="snap-account-1",
        account_name="أماسي الرئيسي",
    )
    raw = json.dumps({
        "summary": "قرار مستقل من OpenAI",
        "recommendations": [{
            "recommendation_id": "temporary",
            "provider": "snapchat_ads",
            "entity_level": "ad_squad",
            "entity_id": "group-1",
            "action": "خفض",
            "change_percent": "17",
            "priority": "عالي",
            "confidence": "متوسطة",
            "why_now": "الصرف أسرع من النتائج",
            "evidence": "صرف مرتفع دون نتائج كافية",
            "recommended_wait_hours": "5",
            "success_criteria": "انخفاض تكلفة الشراء",
        }],
        "limitations": [],
    }, ensure_ascii=False)

    output = _normalize_openai_output(
        raw,
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
    )

    item = output.recommendations[0]
    assert item.action == "reduce"
    assert item.change_percent == 17
    assert item.account_id == "snap-account-1"
    assert item.account_name == "أماسي الرئيسي"
    assert item.entity_name == "مجموعة الوطني"
    assert item.recommended_wait_hours == 5


def test_openai_quota_and_validation_errors_are_not_reported_as_connection_errors():
    quota = RuntimeError("insufficient_quota: billing quota exceeded")

    assert _openai_error_code(quota) == "openai_insufficient_quota"
    result = _deterministic_recommendations(
        [],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="openai_recommendation:openai_response_validation_error",
    )
    assert "وصل رد OpenAI" in result.summary


def test_openai_request_has_enough_output_budget_and_normalizes_the_response(monkeypatch):
    captured = {}
    candidate = entity(
        provider="meta",
        entity_level="campaign",
        entity_id="campaign-1",
        entity_name="حملة اختبار",
        account_id="meta-account-1",
        account_name="أماسي",
    )
    response_payload = {
        "summary": "تحليل OpenAI",
        "recommendations": [{
            "recommendation_id": "temporary",
            "provider": "meta",
            "entity_level": "campaign",
            "entity_id": "campaign-1",
            "entity_name": "حملة اختبار",
            "account_id": "meta-account-1",
            "account_name": "أماسي",
            "parent_name": None,
            "action": "monitor",
            "change_percent": None,
            "priority": "medium",
            "confidence": "medium",
            "title": "مراقبة",
            "rationale": "لا توجد نافذة كافية للتغيير",
            "evidence": ["الصرف قيد التقييم"],
            "why_now": "موعد المراجعة",
            "recommended_wait_hours": 5,
            "observation_plan": "أعد القياس",
            "success_criteria": ["اكتمال البيانات"],
            "risk_if_ignored": "قرار مبكر",
            "guardrail": "بعد موافقة المالك",
            "next_check_at": "ignored",
        }],
        "limitations": [],
    }

    class FakeResponses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text=json.dumps(response_payload, ensure_ascii=False),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.responses = FakeResponses()

        async def close(self):
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(campaign_ai_monitor, "AsyncOpenAI", FakeClient)

    output = asyncio.run(_ask_openai(
        [candidate],
        now=datetime(2026, 8, 16, tzinfo=timezone.utc),
        campaign_history={},
        prior_decisions={},
        business_profit={},
    ))

    assert captured["max_output_tokens"] >= 12000
    assert captured["reasoning"] == {"effort": "low"}
    assert captured["client_kwargs"]["timeout"] == campaign_ai_monitor.OPENAI_TIMEOUT_SECONDS
    assert captured["client_kwargs"]["timeout"] > 45
    assert captured["client_kwargs"]["max_retries"] == 0
    assert output.recommendations[0].action == "monitor"
    assert output.recommendations[0].account_name == "أماسي"


def test_fallback_does_not_scale_a_profitable_entity():
    candidate = entity(
        entity_id="scale-incomplete",
        spend_sar=150,
        revenue_sar=600,
        purchases=4,
        roas=4,
        cpa_sar=37.5,
        data_complete=False,
    )
    result = _deterministic_recommendations(
        [candidate],
        next_check_at="2026-08-16T15:00:00+00:00",
        limitation="fallback",
    )

    assert result.recommendations == []


def test_recommendation_explanation_states_reason_wait_and_success_criteria():
    candidate = deterministic_candidates([
        entity(entity_id="fast-waste", spend_sar=180, purchases=0),
    ])[0]
    item = RecommendationItem(
        recommendation_id="snapchat:ad:fast-waste",
        provider="snapchat",
        entity_level="ad",
        entity_id="fast-waste",
        entity_name="إعلان المنتج",
        action="pause",
        priority="critical",
        confidence="high",
        title="إيقاف مقترح",
        rationale="فشل تاريخي مستمر",
        evidence=["صرف 180 ر.س دون شراء"],
        why_now="التاريخ يؤكد استمرار الفشل",
        recommended_wait_hours=5,
        observation_plan="راجع الصرف والطلبات بعد 5 ساعات",
        success_criteria=["توقف الهدر"],
        risk_if_ignored="خسارة إضافية",
        guardrail="بعد الموافقة فقط",
        next_check_at="2026-08-16T15:00:00+00:00",
    )

    explanation = _recommendation_explanation(item, candidate)

    assert explanation["decision_signal"] == "openai_independent_judgment"
    assert explanation["recommended_wait_hours"] == 5
    assert "اصبر 5 ساعات" in explanation["observation_plan"]
    assert any("صرف 180.00 ر.س" in fact for fact in explanation["decision_facts"])
    assert explanation["success_criteria"] == ["توقف الهدر"]
    assert explanation["financial_impact"]["period_estimated_contribution_sar"] == -180
    assert explanation["financial_impact"]["forecast_delta_sar"] > 0
    assert explanation["financial_impact"]["is_estimate"] is True
