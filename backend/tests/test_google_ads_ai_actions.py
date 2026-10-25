"""Saved native advice crosses the existing approval gateway, never a model write."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center import google_ads_routes as routes

OWNER = {"user_id": "owner", "actor_id": "owner", "owner": True, "can_draft": True}
CURRENT = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def saved_analysis(action="PAUSE_CAMPAIGN", **changes):
    period = {"account_id": "123", "currency_code": "SAR", "time_zone": "Asia/Riyadh",
              "date_from": "2026-10-23", "date_to": "2026-10-25"}
    evidence = {"evidence_id": "google:campaign:123:456:spend_native", "campaign_id": "456",
                "metric": "spend_native", "value": "45", "source": "google_ads_api",
                "currency_code": "SAR", "time_zone": "Asia/Riyadh",
                "date_from": period["date_from"], "date_to": period["date_to"],
                "observed_at": CURRENT.isoformat()}
    rec = {"recommendation_id": "google_ads:campaign:123:456:" + action,
           "provider": "google_ads", "account_id": "123", "campaign_id": "456",
           "campaign_name": "هدية شتوية", "action": action, "rationale": "راجع أداء الحملة",
           "confidence": "high", "evidence": [evidence], "observation_plan": "راجع النتيجة لاحقًا",
           "proposed_budget_native": "30.123456" if action == "CHANGE_BUDGET" else None}
    return {"analysis_id": "saved-analysis", "user_id": "owner", "actor_id": "owner",
            "account_id": "123", "created_at": CURRENT.isoformat(),
            "date_from": period["date_from"], "date_to": period["date_to"],
            "result": {"provider": "google_ads", "account_id": "123", "period": period,
                       "read_only": True, "recommendations": [rec]}, **changes}


class Analyses:
    def __init__(self, row):
        self.row = deepcopy(row)

    async def find_one(self, query, projection=None):
        if all(self.row.get(key) == value for key, value in query.items()):
            return deepcopy(self.row)
        return None


@pytest.mark.asyncio
async def test_route_previews_only_the_saved_recommendation_and_keeps_approval_separate(monkeypatch):
    from integrations_control_center.google_ads_management import ANALYSES
    row, calls = saved_analysis(), []

    async def principal(*args, **kwargs):
        assert kwargs.get("write") is True
        return OWNER

    async def preview(db, actor, body, **kwargs):
        calls.append((actor, body.model_dump(), kwargs))
        return {"proposal_id": "native-proposal", "status": "previewed", "verified": False}

    monkeypatch.setattr(routes, "principal", principal)
    monkeypatch.setattr(routes, "preview_google_action", preview)
    monkeypatch.setattr(routes, "now", lambda: CURRENT)
    app, router = FastAPI(), APIRouter()

    async def current_user():
        return {"id": "owner"}

    routes.attach_google_ads_management_routes(router, {ANALYSES: Analyses(row)}, current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        payload = {"analysis_id": row["analysis_id"], "recommendation_id": row["result"]["recommendations"][0]["recommendation_id"]}
        response = await http.post("/google_ads/ai/recommendations/preview", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "previewed"
        assert len(calls) == 1
        actor, body, kwargs = calls[0]
        assert actor == OWNER
        assert body["action"] == "pause" and body["account_id"] == "123" and body["campaign_id"] == "456"
        assert body["changes"] == {}
        assert kwargs["ai_source"]["analysis_id"] == row["analysis_id"]
        # Account, action and amount are recovered by the server, not accepted from the client.
        forged = await http.post("/google_ads/ai/recommendations/preview", json={**payload, "action": "enable", "account_id": "999"})
        assert forged.status_code == 422 and len(calls) == 1


@pytest.mark.parametrize("action,native,changes", [
    ("PAUSE_CAMPAIGN", "pause", {}), ("RESUME_CAMPAIGN", "enable", {}),
    ("CHANGE_BUDGET", "set_budget", {"budget_native": "30.123456"}),
])
def test_saved_native_action_preserves_identity_amount_and_durable_idempotency(action, native, changes):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_analysis(action)
    rec_id = row["result"]["recommendations"][0]["recommendation_id"]
    body, source = recommendation_action(row, OWNER, rec_id, current=CURRENT)
    repeated, _ = recommendation_action(row, OWNER, rec_id, current=CURRENT + timedelta(minutes=1))
    assert body.action == native and body.changes == changes
    assert body.account_id == "123" and body.campaign_id == "456"
    assert body.idempotency_key == repeated.idempotency_key
    assert source["currency"] == "SAR" and source["time_zone"] == "Asia/Riyadh"
    assert source["expires_at"] == (CURRENT + timedelta(hours=5)).isoformat()


@pytest.mark.parametrize("change,code", [
    (lambda r: r.update(user_id="other"), "google_ai_analysis_not_owned"),
    (lambda r: r.update(created_at=(CURRENT-timedelta(hours=6)).isoformat()), "google_ai_analysis_expired"),
    (lambda r: r.update(created_at=(CURRENT+timedelta(minutes=1)).isoformat()), "google_ai_analysis_expired"),
    (lambda r: r.update(created_at="invalid"), "google_ai_analysis_expired"),
    (lambda r: r["result"].update(account_id="999"), "google_ai_recommendation_scope_invalid"),
    (lambda r: r["result"]["period"].update(currency_code=""), "google_ai_recommendation_evidence_invalid"),
    (lambda r: r["result"]["period"].update(date_to="2026-10-24"), "google_ai_recommendation_evidence_invalid"),
    (lambda r: r["result"]["recommendations"][0].update(account_id="999"), "google_ai_recommendation_scope_invalid"),
    (lambda r: r["result"]["recommendations"][0].update(evidence=[]), "google_ai_recommendation_evidence_invalid"),
    (lambda r: r["result"]["recommendations"][0]["evidence"][0].update(campaign_id="789"), "google_ai_recommendation_evidence_invalid"),
    (lambda r: r["result"]["recommendations"][0]["evidence"][0].update(currency_code="USD"), "google_ai_recommendation_evidence_invalid"),
    (lambda r: r["result"]["recommendations"].append(deepcopy(r["result"]["recommendations"][0])), "google_ai_recommendation_not_found"),
])
def test_stale_foreign_incomplete_and_ambiguous_saved_evidence_cannot_prepare_action(change, code):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_analysis()
    rec_id = row["result"]["recommendations"][0]["recommendation_id"]
    change(row)
    with pytest.raises(HTTPException) as error:
        recommendation_action(row, OWNER, rec_id, current=CURRENT)
    assert error.value.detail["code"] == code


@pytest.mark.parametrize("action", ["MONITOR", "REVIEW_TRACKING", "CHANGE_BIDDING", "CREATE_TEST_DRAFT", "pause"])
def test_diagnostic_or_incomplete_actions_never_become_provider_mutations(action):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_analysis(action)
    with pytest.raises(HTTPException) as error:
        recommendation_action(row, OWNER, row["result"]["recommendations"][0]["recommendation_id"], current=CURRENT)
    assert error.value.detail["code"] == "google_ai_recommendation_review_only"


@pytest.mark.parametrize("amount", [None, "", "NaN", "-1", "1e3", "0", 100, "1.1234567"])
def test_budget_action_rejects_incomplete_or_noncanonical_money(amount):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_analysis("CHANGE_BUDGET")
    row["result"]["recommendations"][0]["proposed_budget_native"] = amount
    with pytest.raises(HTTPException) as error:
        recommendation_action(row, OWNER, row["result"]["recommendations"][0]["recommendation_id"], current=CURRENT)
    assert error.value.detail["code"] == "google_ai_recommendation_budget_invalid"
