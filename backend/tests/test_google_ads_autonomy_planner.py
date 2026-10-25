"""Read-only autonomy readiness must never authorize or execute a mutation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center.google_ads_autonomy_planner import plan_google_autonomy

NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)


def inputs(action="PAUSE_CAMPAIGN"):
    period = dict(account_id="123", currency_code="SAR", time_zone="Asia/Riyadh",
                  date_from="2026-10-25", date_to="2026-10-25")
    evidence = dict(evidence_id="google:campaign:123:456:spend_native", campaign_id="456",
                    metric="spend_native", value="20", source="google_ads_api",
                    observed_at=NOW.isoformat(), **{k:v for k,v in period.items() if k != "account_id"})
    rec = dict(recommendation_id="rec1", provider="google_ads", account_id="123", campaign_id="456",
               action=action, confidence="high", evidence=[evidence], proposed_budget_native=None)
    if action == "CHANGE_BUDGET":
        rec["proposed_budget_native"] = "22"
    campaign = dict(campaign_id="456", account_id="123", source="google_ads_api", status="PAUSED" if action == "RESUME_CAMPAIGN" else "ENABLED",
                    currency_code="SAR", time_zone="Asia/Riyadh", budget_mode="DAILY", budget_native="20", observed_at=NOW.isoformat())
    return dict(user_id="owner", account_id="123", current=NOW,
        policy=dict(schema_version=1, user_id="owner", account_id="123", revision=3, enabled=True,
                    currency_code="SAR", time_zone="Asia/Riyadh", campaign_ids=["456"],
                    allowed_actions=["pause", "enable", "set_budget"], max_campaign_daily_budget_native="30",
                    max_account_daily_budget_native="55", max_budget_change_percent="20",
                    cooldown_seconds=3600, max_actions_per_day=3, expires_at=(NOW+timedelta(days=1)).isoformat()),
        analysis=dict(analysis_id="analysis1", user_id="owner", account_id="123", created_at=NOW.isoformat(),
                      date_from=period["date_from"], date_to=period["date_to"],
                      result=dict(provider="google_ads", account_id="123", period=period, recommendations=[rec])),
        workspace=dict(complete=True, account_id="123", currency_code="SAR", time_zone="Asia/Riyadh",
                       observed_at=NOW.isoformat(), campaigns=[campaign]),
        history=dict(complete=True, user_id="owner", account_id="123", items=[]))


@pytest.mark.parametrize("action,native", [("PAUSE_CAMPAIGN","pause"),("RESUME_CAMPAIGN","enable"),("CHANGE_BUDGET","set_budget")])
def test_valid_candidate_is_only_a_stable_preview_plan_and_inputs_are_unchanged(action, native):
    data=inputs(action); before=deepcopy(data)
    result=plan_google_autonomy(**data)
    assert result["provider_writes"] == 0 and result["executable"] is False and result["executor_available"] is False
    candidate=result["candidates"][0]
    assert candidate["decision"] == "eligible_for_preview" and candidate["native_action"] == native
    assert candidate["approval_required"] is True and candidate["executed"] is False
    assert candidate["idempotency_key"] == plan_google_autonomy(**data)["candidates"][0]["idempotency_key"]
    assert candidate["changes"] == ({"budget_native":"22"} if native=="set_budget" else {})
    assert data == before


@pytest.mark.parametrize("change,code", [
    (lambda d:d["policy"].update(enabled=False), "policy_disabled"),
    (lambda d:d["policy"].update(user_id="other"), "policy_scope_unproved"),
    (lambda d:d["policy"].update(expires_at=NOW.isoformat()), "policy_expired"),
    (lambda d:d["analysis"].update(user_id="other"), "analysis_scope_unproved"),
    (lambda d:d["analysis"].update(created_at=(NOW-timedelta(hours=5)).isoformat()), "analysis_stale"),
    (lambda d:d["analysis"].update(created_at=(NOW+timedelta(seconds=1)).isoformat()), "analysis_stale"),
    (lambda d:d["workspace"].update(complete=False), "workspace_incomplete"),
    (lambda d:d["workspace"].update(account_id="999"), "workspace_scope_unproved"),
    (lambda d:d["workspace"].update(currency_code="USD"), "workspace_scope_unproved"),
    (lambda d:d["workspace"].update(observed_at=(NOW-timedelta(minutes=10)).isoformat()), "workspace_stale"),
    (lambda d:d["history"].update(complete=False), "history_incomplete"),
    (lambda d:d["policy"].update(revision=0), "policy_invalid"),
    (lambda d:d.update(current=NOW.replace(tzinfo=None)), "clock_unproved"),
])
def test_global_unproved_inputs_fail_closed_without_candidates(change, code):
    data=inputs();change(data);result=plan_google_autonomy(**data)
    assert result["blockers"] == [code] and result["candidates"] == [] and result["provider_writes"] == 0


@pytest.mark.parametrize("change,code", [
    (lambda d:d["policy"].update(campaign_ids=["789"]), "campaign_not_allowed"),
    (lambda d:d["policy"].update(allowed_actions=["enable"]), "action_not_allowed"),
    (lambda d:d["analysis"]["result"]["recommendations"][0].update(confidence="medium"), "confidence_below_high"),
    (lambda d:d["analysis"]["result"]["recommendations"][0].update(action="CREATE_TEST_DRAFT"), "action_unsupported"),
    (lambda d:d["analysis"]["result"]["recommendations"][0].update(evidence=[]), "evidence_unproved"),
    (lambda d:d["analysis"]["result"]["recommendations"][0]["evidence"][0].update(campaign_id="789"), "evidence_unproved"),
    (lambda d:d["workspace"]["campaigns"][0].update(status="PAUSED"), "campaign_status_changed"),
    (lambda d:d["workspace"]["campaigns"][0].update(budget_mode="TOTAL"), "campaign_budget_unproved"),
])
def test_unsafe_recommendation_is_reported_as_blocked(change, code):
    data=inputs();change(data);result=plan_google_autonomy(**data)
    assert result["candidates"][0]["blockers"] == [code]
    assert result["candidates"][0]["decision"] == "blocked"
    assert "idempotency_key" not in result["candidates"][0]


def history_row(**changes):
    return {"user_id":"owner","account_id":"123","campaign_id":"456","status":"completed",
                "executed_at":(NOW-timedelta(minutes=30)).isoformat(), **changes}


def test_uncertain_provider_outcome_blocks_whole_account_and_completed_campaign_observes_cooldown():
    data=inputs();data["history"]["items"]=[history_row(status="uncertain")]
    assert plan_google_autonomy(**data)["blockers"] == ["account_execution_pending"]
    data["history"]["items"]=[history_row()]
    assert plan_google_autonomy(**data)["candidates"][0]["blockers"] == ["campaign_cooldown"]


def test_account_local_daily_action_limit_and_in_run_reservation_prevent_overcommit():
    data=inputs();data["policy"]["max_actions_per_day"]=1
    data["history"]["items"]=[history_row(campaign_id="789", executed_at=(NOW-timedelta(hours=2)).isoformat())]
    assert plan_google_autonomy(**data)["blockers"] == ["daily_action_limit"]
    data["history"]["items"]=[]
    rec=deepcopy(data["analysis"]["result"]["recommendations"][0]);rec["recommendation_id"]="rec2"
    data["analysis"]["result"]["recommendations"].append(rec)
    result=plan_google_autonomy(**data)
    assert result["candidates"][0]["decision"] == "eligible_for_preview"
    assert result["candidates"][1]["blockers"] == ["campaign_already_planned"]


@pytest.mark.parametrize("amount,code", [("31","campaign_budget_limit"),("24.000001","budget_change_limit"),("NaN","budget_amount_invalid"),("0","budget_amount_invalid")])
def test_budget_decimal_limits_are_exact(amount, code):
    data=inputs("CHANGE_BUDGET");data["analysis"]["result"]["recommendations"][0]["proposed_budget_native"]=amount
    assert plan_google_autonomy(**data)["candidates"][0]["blockers"] == [code]


def test_account_aggregate_budget_uses_all_enabled_campaigns_even_outside_policy_allowlist():
    data=inputs("RESUME_CAMPAIGN")
    other=deepcopy(data["workspace"]["campaigns"][0]);other.update(campaign_id="789",status="ENABLED",budget_native="40")
    data["workspace"]["campaigns"].append(other)
    assert plan_google_autonomy(**data)["candidates"][0]["blockers"] == ["account_budget_limit"]


def test_duplicate_recommendation_ids_are_not_actionable_and_malformed_history_fails_closed():
    data=inputs();data["analysis"]["result"]["recommendations"].append(deepcopy(data["analysis"]["result"]["recommendations"][0]))
    assert plan_google_autonomy(**data)["blockers"] == ["recommendations_ambiguous"]
    data=inputs();data["history"]["items"]=[history_row(executed_at="bad")]
    assert plan_google_autonomy(**data)["blockers"] == ["history_unproved"]


def add_candidate(data, cid, action, *, budget="20", target=None):
    campaign=deepcopy(data["workspace"]["campaigns"][0])
    campaign.update(campaign_id=cid,status="PAUSED" if action=="RESUME_CAMPAIGN" else "ENABLED",budget_native=budget)
    data["workspace"]["campaigns"].append(campaign)
    rec=deepcopy(data["analysis"]["result"]["recommendations"][0])
    rec.update(recommendation_id="rec"+cid,campaign_id=cid,action=action,proposed_budget_native=target)
    for evidence in rec["evidence"]:
        evidence.update(campaign_id=cid,evidence_id=f"google:campaign:123:{cid}:spend_native")
    data["analysis"]["result"]["recommendations"].append(rec)
    data["policy"]["campaign_ids"].append(cid)


def test_unexecuted_pause_does_not_free_budget_for_a_new_enable():
    data=inputs();data["policy"]["max_account_daily_budget_native"]="30"
    add_candidate(data,"789","RESUME_CAMPAIGN")
    planned=plan_google_autonomy(**data)["candidates"]
    assert planned[0]["decision"] == "eligible_for_preview"
    assert planned[1]["blockers"] == ["account_budget_limit"]


def test_two_enable_candidates_reserve_total_budget_within_batch():
    data=inputs("RESUME_CAMPAIGN");data["policy"]["max_account_daily_budget_native"]="30"
    add_candidate(data,"789","RESUME_CAMPAIGN")
    planned=plan_google_autonomy(**data)["candidates"]
    assert planned[0]["decision"] == "eligible_for_preview"
    assert planned[1]["blockers"] == ["account_budget_limit"]


def test_policy_revision_and_target_are_part_of_idempotency_and_sensitive_bodies_are_not_returned():
    data=inputs("CHANGE_BUDGET");data["analysis"]["private_body"]="must never be returned"
    original=plan_google_autonomy(**data)
    assert "must never" not in str(original)
    data["policy"]["revision"] += 1
    revised=plan_google_autonomy(**data)
    assert original["candidates"][0]["idempotency_key"] != revised["candidates"][0]["idempotency_key"]
    data["analysis"]["result"]["recommendations"][0]["proposed_budget_native"]="23"
    assert revised["candidates"][0]["idempotency_key"] != plan_google_autonomy(**data)["candidates"][0]["idempotency_key"]


def test_any_stale_native_evidence_blocks_even_when_another_native_fact_is_fresh():
    data=inputs();evidence=deepcopy(data["analysis"]["result"]["recommendations"][0]["evidence"][0])
    evidence["observed_at"]=(NOW-timedelta(hours=5)).isoformat()
    data["analysis"]["result"]["recommendations"][0]["evidence"].append(evidence)
    assert plan_google_autonomy(**data)["candidates"][0]["blockers"] == ["evidence_unproved"]


def test_unknown_or_stale_enabled_campaign_budget_prevents_aggregate_proof():
    data=inputs("RESUME_CAMPAIGN")
    other=deepcopy(data["workspace"]["campaigns"][0]);other.update(campaign_id="789",status="ENABLED",observed_at=(NOW-timedelta(minutes=10)).isoformat())
    data["workspace"]["campaigns"].append(other)
    assert plan_google_autonomy(**data)["candidates"][0]["blockers"] == ["account_budget_unproved"]
