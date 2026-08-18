import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import campaign_ai_monitor as monitor


def candidate(*, level, entity_id, budget=None, spend=180.0, purchases=1, revenue=90.0):
    return {
        "provider": "snapchat",
        "entity_level": level,
        "account_id": "snap-account-1",
        "account_name": "Self Service",
        "entity_id": entity_id,
        "entity_name": "الإعلان" if level == "ad" else "المجموعة",
        "parent_id": "group-1" if level == "ad" else "campaign-1",
        "parent_name": "المجموعة" if level == "ad" else "الحملة",
        "campaign_id": "campaign-1",
        "campaign_name": "الحملة",
        "campaign_status": "ACTIVE",
        "ad_group_id": "group-1" if level in {"ad", "ad_group"} else None,
        "ad_group_name": "المجموعة" if level in {"ad", "ad_group"} else None,
        "ad_group_status": "ACTIVE" if level in {"ad", "ad_group"} else None,
        "status": "ACTIVE",
        "active": True,
        "spend_sar": spend,
        "revenue_sar": revenue,
        "purchases": purchases,
        "impressions": 1000,
        "clicks": 50,
        "roas": revenue / spend if spend else None,
        "cpa_sar": spend / purchases if purchases else None,
        "observed_days": 3,
        "spend_per_day_sar": spend / 3,
        "data_complete": True,
        "current_daily_budget_native": budget,
        "provider_result_source": "snapchat_ads_manager_conversion_reporting",
        "action_report_time": "conversion",
    }


def recommendation(*, level, entity_id, action, change_percent=15):
    return {
        "recommendation_id": "temporary",
        "provider": "snapchat",
        "entity_level": level,
        "entity_id": entity_id,
        "entity_name": "عنصر سناب",
        "account_id": "snap-account-1",
        "account_name": "Self Service",
        "parent_name": "المجموعة",
        "action": action,
        "change_percent": change_percent if action in {"reduce", "scale"} else None,
        "priority": "high",
        "confidence": "medium",
        "title": "قرار اختبار",
        "rationale": "الأداء يحتاج إجراء",
        "evidence": ["مراجعة أثر الصرف والنتائج"],
        "why_now": "بعد اكتمال نافذة القياس",
        "recommended_wait_hours": 5,
        "observation_plan": "أعد القياس بعد 5 ساعات",
        "success_criteria": ["تحسن تكلفة الشراء"],
        "risk_if_ignored": "استمرار الأثر المالي السلبي",
        "guardrail": "بعد موافقة المالك فقط",
        "next_check_at": "ignored",
    }


def response(summary, recommendations):
    return {
        "summary": summary,
        "recommendations": recommendations,
        "limitations": [],
    }


def install_sequence(monkeypatch, outputs, captured):
    queue = list(outputs)

    class Responses:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text=json.dumps(queue.pop(0), ensure_ascii=False),
            )

    class Client:
        def __init__(self, **_kwargs):
            self.responses = Responses()

        async def close(self):
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(monitor, "AsyncOpenAI", Client)


def run(rows):
    return asyncio.run(monitor._ask_openai(
        rows,
        now=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        campaign_history={},
        prior_decisions={
            "source": "owner_approved_executed_changes_only",
            "experiments": [],
        },
        business_profit={"available": False},
    ))


def test_rejected_ad_budget_action_gets_openai_repair_to_executable_group(monkeypatch):
    captured = []
    ad = candidate(level="ad", entity_id="ad-1", budget=None, spend=180.0)
    group = candidate(level="ad_group", entity_id="group-1", budget=70.0, spend=260.0)
    install_sequence(monkeypatch, [
        response("خفض ميزانية الإعلان", [recommendation(level="ad", entity_id="ad-1", action="reduce")]),
        response("خفض ميزانية المجموعة بعد مراجعة بياناتها", [recommendation(level="ad_group", entity_id="group-1", action="reduce")]),
    ], captured)

    result = run([ad, group])

    assert len(captured) == 2
    assert len(result.recommendations) == 1
    assert result.recommendations[0].entity_level == "ad_group"
    assert result.recommendations[0].action == "reduce"
    assert "execution_alignment_repair_pass" in result.limitations
    assert "budget_owner_loss_coverage_review" in result.limitations
    review_payload = json.loads(captured[1]["input"])
    assert review_payload["review_reason"] == "execution_target_repair_and_budget_owner_loss_coverage"
    assert review_payload["rejected_first_pass_actions"]


def test_budget_owner_loss_review_adds_omitted_high_impact_group(monkeypatch):
    captured = []
    good_group = candidate(
        level="ad_group", entity_id="group-good", budget=70.0,
        spend=160.0, purchases=4, revenue=650.0,
    )
    bad_group = candidate(
        level="ad_group", entity_id="group-bad", budget=120.0,
        spend=710.0, purchases=2, revenue=305.0,
    )
    install_sequence(monkeypatch, [
        response("استمرار المجموعة الجيدة", [recommendation(level="ad_group", entity_id="group-good", action="maintain", change_percent=None)]),
        response(
            "بعد تدقيق جميع أصحاب الميزانية، أضف خفض المجموعة ذات الأثر الأكبر.",
            [recommendation(level="ad_group", entity_id="group-bad", action="reduce")],
        ),
    ], captured)

    result = run([good_group, bad_group])

    assert len(captured) == 2
    keys = {(item.entity_id, item.action) for item in result.recommendations}
    assert ("group-good", "maintain") in keys
    assert ("group-bad", "reduce") in keys
    assert "budget_owner_loss_coverage_review" in result.limitations
    review_payload = json.loads(captured[1]["input"])
    ids = {row["entity_id"] for row in review_payload["direct_budget_owners_to_review"]}
    assert ids == {"group-good", "group-bad"}
    assert review_payload["review_reason"] == "budget_owner_loss_coverage"


def test_second_invalid_model_answer_returns_consistent_zero_card_summary(monkeypatch):
    captured = []
    ad = candidate(level="ad", entity_id="ad-1", budget=None, spend=180.0)
    group = candidate(level="ad_group", entity_id="group-1", budget=70.0, spend=260.0)
    invalid = response(
        "وسّع الإعلان",
        [recommendation(level="ad", entity_id="ad-1", action="scale")],
    )
    install_sequence(monkeypatch, [invalid, invalid], captured)

    result = run([ad, group])

    assert len(captured) == 2
    assert result.recommendations == []
    assert "لا توجد حاليًا توصية تغيير قابلة للتنفيذ" in result.summary
    assert "execution_alignment_no_executable_recommendation" in result.limitations
    assert "execution_alignment_repair_pass" in result.limitations


def test_valid_first_pass_still_gets_budget_owner_coverage_review(monkeypatch):
    captured = []
    group = candidate(level="ad_group", entity_id="group-1", budget=70.0, spend=260.0)
    install_sequence(monkeypatch, [
        response("خفض المجموعة", [recommendation(level="ad_group", entity_id="group-1", action="reduce")]),
        response("بعد المراجعة الشاملة يبقى الخفض هو القرار", [recommendation(level="ad_group", entity_id="group-1", action="reduce")]),
    ], captured)

    result = run([group])

    assert len(captured) == 2
    assert len(result.recommendations) == 1
    assert result.recommendations[0].entity_level == "ad_group"
    assert result.recommendations[0].action == "reduce"
    assert "budget_owner_loss_coverage_review" in result.limitations
    assert "execution_alignment_repair_pass" not in result.limitations
