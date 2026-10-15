from fastapi import APIRouter, HTTPException
import pytest

from integrations_control_center import snapchat_decision_routes as routes
from integrations_control_center import snapchat_adaptive_decision_ai as adaptive_ai
from integrations_control_center import snapchat_decision_metrics as decision_metrics
from integrations_control_center import snapchat_intraday_waste_monitor as intraday


def _router():
    router = APIRouter()

    async def current_user():
        return {"id": "owner-1"}

    routes.attach_snapchat_decision_routes(
        router, object(), current_user, lambda user: user
    )
    return router


def _endpoint(router, suffix, method="GET"):
    return next(
        route.endpoint
        for route in router.routes
        if route.path.endswith(suffix) and method in route.methods
    )


@pytest.mark.asyncio
async def test_account_summary_and_history_routes_are_owner_scoped(monkeypatch):
    calls = []

    async def summaries(db, user_id, limit_per_account):
        calls.append(("summaries", user_id, limit_per_account))
        return {"accounts": []}

    async def history(db, user_id, account_id, page, limit):
        calls.append(("history", user_id, account_id, page, limit))
        return {"account_id": account_id, "items": []}

    async def reconcile(db, user_id, limit):
        calls.append(("auto_reconcile", user_id, limit))
        return {"inserted": 0}

    monkeypatch.setattr(routes, "list_account_decision_summaries", summaries)
    monkeypatch.setattr(routes, "list_ad_decisions", history)
    monkeypatch.setattr(routes, "reconcile_snapchat_management_decisions", reconcile)
    router = _router()

    result_accounts = await _endpoint(router, "/decision-ledger/accounts")(
        limit_per_account=5, user={"id": "owner-1"}
    )
    result_history = await _endpoint(router, "/decision-ledger")(
        account_id="account-1", page=2, limit=5, user={"id": "owner-1"}
    )

    assert result_accounts == {"accounts": []}
    assert result_history["account_id"] == "account-1"
    assert calls == [
        ("auto_reconcile", "owner-1", 1000),
        ("summaries", "owner-1", 5),
        ("auto_reconcile", "owner-1", 1000),
        ("history", "owner-1", "account-1", 2, 5),
    ]


@pytest.mark.asyncio
async def test_reconcile_backfill_and_annotation_keep_actor(monkeypatch):
    calls = []

    async def reconcile(db, user_id, limit):
        calls.append(("reconcile", user_id, limit))
        return {"inserted": 12}

    async def annotate(db, user_id, decision_id, annotation, actor_id, actor_kind):
        calls.append(
            (
                "annotation",
                user_id,
                decision_id,
                annotation,
                actor_id,
                actor_kind,
            )
        )
        return {"decision_id": decision_id, "annotations": [annotation]}

    monkeypatch.setattr(routes, "reconcile_snapchat_management_decisions", reconcile)
    monkeypatch.setattr(routes, "add_decision_annotation", annotate)
    router = _router()

    reconciled = await _endpoint(router, "/decision-ledger/reconcile", "POST")(
        payload=routes.AdDecisionReconcileInput(limit=100),
        user={"id": "owner-1"},
    )
    annotated = await _endpoint(
        router, "/decision-ledger/{decision_id}/annotations", "POST"
    )(
        decision_id="decision-1",
        payload=routes.AdDecisionAnnotationInput(
            text="مضاف يدويًا في سلة؛ واتساب احتمال غير متحقق",
            evidence=[{"verification_status": "user_suggestion"}],
        ),
        user={"id": "owner-1"},
    )

    assert reconciled == {"inserted": 12}
    assert annotated["decision_id"] == "decision-1"
    assert calls[1][-2:] == ("owner-1", "mezan_user")
    assert "واتساب احتمال" in calls[1][3]["text"]


@pytest.mark.asyncio
async def test_missing_decision_returns_404(monkeypatch):
    async def missing(*args, **kwargs):
        return None

    monkeypatch.setattr(routes, "get_ad_decision", missing)
    router = _router()

    with pytest.raises(HTTPException) as raised:
        await _endpoint(router, "/decision-ledger/{decision_id}")(
            decision_id="missing", user={"id": "owner-1"}
        )

    assert raised.value.status_code == 404
    assert raised.value.detail["code"] == "ad_decision_not_found"


@pytest.mark.asyncio
async def test_adaptive_review_still_judges_when_conversion_delay_is_not_learned(
    monkeypatch,
):
    calls = []

    async def acquire(*args, **kwargs):
        calls.append("slot")

    async def monitor(*args, **kwargs):
        return {
            "items": [
                {
                    "account_id": "account-1",
                    "campaign_id": "campaign-1",
                    "entity_type": "campaign",
                    "entity_id": "campaign-1",
                    "metrics": {"spend_sar": 120, "orders": 0},
                    "recommendation": {"code": "learn_conversion_delay"},
                    "provider_write_reached": False,
                }
            ],
            "coverage": {"complete": True},
        }

    async def baseline(*args, **kwargs):
        return {
            "windows": [],
            "inventory_verification_status": "not_linked",
        }

    async def judge(evidence):
        calls.append(evidence)
        return [{"judgment": {"recommended_action": "investigate"}}]

    monkeypatch.setattr(adaptive_ai, "acquire_adaptive_review_slot", acquire)
    monkeypatch.setattr(intraday, "monitor_snapchat_intraday_waste", monitor)
    monkeypatch.setattr(decision_metrics, "capture_decision_baseline", baseline)
    monkeypatch.setattr(adaptive_ai, "judge_adaptive_snapchat_decisions", judge)
    router = _router()

    result = await _endpoint(router, "/decision-ledger/adaptive-review", "POST")(
        payload=routes.AdaptiveReviewInput(max_entities=5),
        user={"id": "owner-1"},
    )

    assert result["judgments"] == [
        {"judgment": {"recommended_action": "investigate"}}
    ]
    assert result["proposals_created"] == 0
    assert result["provider_write_reached"] is False
    evidence = calls[1][0]
    assert evidence["entity_evidence"]["recommendation"]["code"] == (
        "learn_conversion_delay"
    )
    assert evidence["policy"]["fixed_rules"] is False
