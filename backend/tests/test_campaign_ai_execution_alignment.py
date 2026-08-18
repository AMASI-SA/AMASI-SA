import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import campaign_ai_monitor as monitor
from campaign_ai_public_guard import _public_document


def candidate(*, level="ad", entity_id="ad-1", budget=None, purchases=0, spend=120.0):
    return {
        "provider": "snapchat",
        "entity_level": level,
        "account_id": "snap-account-1",
        "account_name": "Self Service",
        "entity_id": entity_id,
        "entity_name": "عنصر سناب",
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
        "revenue_sar": 0.0,
        "purchases": purchases,
        "impressions": 1000,
        "clicks": 50,
        "roas": 0.0,
        "cpa_sar": None,
        "observed_days": 3,
        "spend_per_day_sar": spend / 3,
        "data_complete": True,
        "current_daily_budget_native": budget,
        "provider_result_source": "snapchat_ads_manager_conversion_reporting",
        "action_report_time": "conversion",
    }


def recommendation_payload(*, level, entity_id, action, change_percent=15):
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
        "evidence": ["صرف بدون عائد كافٍ"],
        "why_now": "بعد اكتمال نافذة القياس",
        "recommended_wait_hours": 5,
        "observation_plan": "أعد القياس بعد 5 ساعات",
        "success_criteria": ["تحسن تكلفة الشراء"],
        "risk_if_ignored": "استمرار الهدر",
        "guardrail": "بعد موافقة المالك فقط",
        "next_check_at": "ignored",
    }


def install_fake_openai(monkeypatch, recommendation, captured):
    class Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status="completed",
                output_text=json.dumps({
                    "summary": "تحليل OpenAI",
                    "recommendations": [recommendation],
                    "limitations": [],
                }, ensure_ascii=False),
            )

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.responses = Responses()

        async def close(self):
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(monitor, "AsyncOpenAI", Client)


def run_openai(rows):
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


def test_ad_execution_capabilities_forbid_budget_scale_and_reduce():
    caps = monitor.execution_capabilities(candidate(level="ad"))
    assert caps["budget_actions_allowed"] is False
    assert "reduce" not in caps["allowed_actions"]
    assert "scale" not in caps["allowed_actions"]
    assert "pause" in caps["allowed_actions"]
    assert caps["budget_owner_level"] == "ad_group"
    assert caps["budget_owner_id"] == "group-1"
    assert caps["automatic_parent_retargeting_allowed"] is False


def test_ad_group_with_direct_budget_allows_budget_change():
    caps = monitor.execution_capabilities(
        candidate(level="ad_group", entity_id="group-1", budget=50.0)
    )
    assert caps["budget_actions_allowed"] is True
    assert {"pause", "reduce", "scale"}.issubset(caps["allowed_actions"])
    assert caps["budget_owner_level"] == "ad_group"
    assert caps["budget_owner_id"] == "group-1"


def test_openai_payload_exposes_execution_contract_and_ad_limits(monkeypatch):
    captured = {}
    ad = candidate(level="ad")
    install_fake_openai(
        monkeypatch,
        recommendation_payload(level="ad", entity_id="ad-1", action="monitor", change_percent=None),
        captured,
    )

    output = run_openai([ad])
    payload = json.loads(captured["input"])
    contract = payload["execution_capability_contract"]
    row = payload["active_entities_last_3_days"][0]

    assert output.recommendations[0].action == "monitor"
    assert contract["ad_budget_actions_allowed"] is False
    assert contract["automatic_parent_retargeting_allowed"] is False
    assert contract["budget_change_levels"] == ["campaign", "ad_group"]
    assert row["execution_capabilities"]["budget_actions_allowed"] is False
    assert "reduce" not in row["execution_capabilities"]["allowed_actions"]
    assert "scale" not in row["execution_capabilities"]["allowed_actions"]
    assert "لا تُرجع reduce أو scale على entity_level=ad مطلقًا" in captured["instructions"]


def test_invalid_ad_budget_action_is_removed_not_retargeted(monkeypatch):
    captured = {}
    ad = candidate(level="ad", spend=180.0)
    parent = candidate(level="ad_group", entity_id="group-1", budget=70.0, spend=240.0)
    install_fake_openai(
        monkeypatch,
        recommendation_payload(level="ad", entity_id="ad-1", action="reduce"),
        captured,
    )

    output = run_openai([ad, parent])

    assert output.recommendations == []
    assert any(value.startswith("unsupported_action_removed:snapchat:ad:") for value in output.limitations)
    assert not any("ad_group:group-1" in value for value in output.limitations)


def test_valid_snapchat_ad_group_budget_reduction_survives(monkeypatch):
    captured = {}
    group = candidate(level="ad_group", entity_id="group-1", budget=70.0, spend=180.0)
    install_fake_openai(
        monkeypatch,
        recommendation_payload(level="ad_group", entity_id="group-1", action="reduce"),
        captured,
    )

    output = run_openai([group])

    assert len(output.recommendations) == 1
    item = output.recommendations[0]
    assert item.entity_level == "ad_group"
    assert item.entity_id == "group-1"
    assert item.action == "reduce"
    assert item.change_percent == 15


def test_budget_change_without_direct_budget_is_removed(monkeypatch):
    captured = {}
    group = candidate(level="ad_group", entity_id="group-1", budget=None, spend=180.0)
    install_fake_openai(
        monkeypatch,
        recommendation_payload(level="ad_group", entity_id="group-1", action="reduce"),
        captured,
    )

    output = run_openai([group])

    assert output.recommendations == []
    assert any(value.endswith(":reduce") for value in output.limitations)


def test_public_guard_removes_old_ad_budget_card_but_keeps_executable_parent_card():
    result = _public_document({
        "snapshot_id": "openai-snapshot",
        "recommendation_source": "openai",
        "summary": "تحليل OpenAI",
        "recommendations": [
            {
                "recommendation_id": "snapchat:ad:snap-account-1:ad-1",
                "provider": "snapchat",
                "entity_level": "ad",
                "entity_id": "ad-1",
                "action": "scale",
                "approval_available": False,
            },
            {
                "recommendation_id": "snapchat:ad_group:snap-account-1:group-1",
                "provider": "snapchat",
                "entity_level": "ad_group",
                "entity_id": "group-1",
                "action": "reduce",
                "approval_available": True,
            },
        ],
    })

    assert result["available"] is True
    assert [item["entity_id"] for item in result["recommendations"]] == ["group-1"]
    assert result["execution_alignment_suppressed"] == 1
    assert "non_executable_ad_budget_recommendation_suppressed" in result["limitations"]
