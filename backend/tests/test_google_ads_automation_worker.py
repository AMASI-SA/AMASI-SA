"""Default-off Google cycles never acquire human write authority or resend RPCs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import asyncio

import pytest
from pymongo.errors import DuplicateKeyError

from integrations_control_center import google_ads_automation_worker as worker
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_management import ANALYSES, PROPOSALS

NOW = datetime(2026,10,26,1,tzinfo=timezone.utc)


def matches(row, query):
    for key, value in query.items():
        if key == "$and":
            if not all(matches(row, clause) for clause in value): return False
        elif key == "$or":
            if not any(matches(row, clause) for clause in value): return False
        elif isinstance(value, dict):
            for op, bound in value.items():
                current = row.get(key)
                if op == "$in" and current not in bound: return False
                if op == "$ne" and current == bound: return False
                if op == "$lte" and (current is None or current > bound): return False
                if op == "$gt" and (current is None or current <= bound): return False
                if op == "$exists" and ((key in row) != bound): return False
        else:
            current = row
            for part in key.split("."):
                current = current.get(part) if isinstance(current,dict) else None
            if current != value: return False
    return True


class Cursor:
    def __init__(self,rows): self.rows=rows
    def sort(self,*args): return self
    async def to_list(self,length): return deepcopy(self.rows[:length])


class Collection:
    def __init__(self,rows=()): self.rows=deepcopy(list(rows))
    async def insert_one(self,row):
        if "_id" in row and any(prior.get("_id")==row["_id"] for prior in self.rows): raise DuplicateKeyError("duplicate")
        self.rows.append(deepcopy(row))
    async def find_one(self,scope,projection=None): return next((deepcopy(row) for row in self.rows if matches(row,scope)),None)
    def find(self,scope,projection=None): return Cursor([row for row in self.rows if matches(row,scope)])
    async def find_one_and_update(self,scope,update,**kwargs):
        for row in self.rows:
            if matches(row,scope):
                row.update(deepcopy(update.get("$set",{}))); return deepcopy(row)
        return None
    async def update_one(self,scope,update,**kwargs):
        return await self.find_one_and_update(scope,update)


class DB:
    def __init__(self):
        self.users=Collection([{"id":"owner","role":"owner"}]); self.collections={}
    def __getitem__(self,name): return self.collections.setdefault(name,Collection())


def fixtures():
    period=dict(account_id="123",currency_code="SAR",time_zone="Asia/Riyadh",date_from="2026-10-24",date_to="2026-10-26")
    evidence=dict(evidence_id="google:campaign:123:456:spend_native",campaign_id="456",source="google_ads_api",metric="spend_native",value="20",observed_at=NOW.isoformat(),**{k:v for k,v in period.items() if k!="account_id"})
    recommendation=dict(provider="google_ads",account_id="123",campaign_id="456",recommendation_id="rec1",action="PAUSE_CAMPAIGN",confidence="high",evidence=[evidence],proposed_budget_native=None)
    policy=dict(user_id="owner",account_id="123",schema_version=1,revision=1,enabled=True,currency_code="SAR",time_zone="Asia/Riyadh",campaign_ids=["456"],allowed_actions=["pause"],max_campaign_daily_budget_native="40",max_account_daily_budget_native="55",max_budget_change_percent="10",cooldown_seconds=3600,max_actions_per_day=3,expires_at=(NOW+timedelta(days=1)).isoformat())
    analysis=dict(provider="google_ads",account_id="123",period=period,recommendations=[recommendation])
    campaign=dict(account_id="123",campaign_id="456",source="google_ads_api",observed_at=NOW.isoformat(),currency_code="SAR",time_zone="Asia/Riyadh",status="ENABLED",budget_native="20",budget_mode="DAILY")
    inventory=dict(account_id="123",currency_code="SAR",time_zone="Asia/Riyadh",observed_at=NOW.isoformat(),complete=True,campaigns=[campaign])
    return policy,analysis,inventory


def setup(monkeypatch):
    db=DB(); policy,analysis,inventory=fixtures(); db[AUTOMATION_POLICIES].rows=[policy]
    calls=[]
    monkeypatch.setattr(worker,"worker_enabled",lambda:True)
    monkeypatch.setattr(worker,"execution_enabled",lambda:True)
    async def builder(database,config,**kwargs): calls.append("analyze");return deepcopy(analysis)
    async def reader(database,actor,**kwargs):
        calls.append("inventory"); assert actor["owner"] is False and actor["can_draft"] is False
        assert actor["actor_id"]=="mezan_google_automation_v1"
        return deepcopy(inventory)
    async def preview(database,**kwargs):
        calls.append("preview");assert kwargs["user_id"]=="owner" and kwargs["policy_revision"]==1
        return {"proposal_id":"proposal1","status":"previewed"}
    async def execute(database,**kwargs): calls.append("execute");return {"proposal_id":"proposal1","status":"completed","verified":True,"submission_started":True}
    async def reconcile(database,**kwargs): calls.append("reconcile");return {"proposal_id":kwargs["proposal_id"],"status":"uncertain","submission_started":True}
    dependencies=dict(analysis_builder=builder,inventory_reader=reader,previewer=preview,executor=execute,reconciler=reconcile,clock=lambda:NOW)
    return db,calls,dependencies


@pytest.mark.asyncio
async def test_default_off_touches_no_database_and_does_not_import_gateway(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED",raising=False)
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED",raising=False)
    class NoDB:
        def __getitem__(self,name): raise AssertionError("disabled worker touched database")
    result=await worker.run_google_automation_cycle(NoDB())
    assert result["status"]=="disabled" and result["accounts"]==0


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_flag,execution_flag",[("false","true"),("false","false"),("missing","true")])
async def test_worker_gate_is_required_before_any_database_read(monkeypatch,worker_flag,execution_flag):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED",worker_flag)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED",execution_flag)
    class NoDB:
        def __getitem__(self,name): raise AssertionError("disabled worker touched database")
    assert (await worker.run_google_automation_cycle(NoDB()))["status"]=="disabled"


@pytest.mark.asyncio
async def test_execution_kill_switch_allows_only_existing_pending_readback(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    monkeypatch.setattr(worker,"execution_enabled",lambda:False)
    # An enabled policy must not cause analysis or a new execution claim while
    # dispatch is disabled, but a previous submission may still be read back.
    db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","proposal_id":"pending1","status":"uncertain","submission_started":True,"automation_source":{"kind":"standing_policy"}}]
    before=deepcopy(db[PROPOSALS].rows)
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["status"]=="recovery_only" and calls==["reconcile"]
    assert db[PROPOSALS].rows==before and db[ANALYSES].rows==[]
    assert result["results"][0]["status"]=="uncertain"


@pytest.mark.asyncio
async def test_execution_disabled_with_no_pending_outcome_does_not_create_cycle_claim(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    monkeypatch.setattr(worker,"execution_enabled",lambda:False)
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["status"]=="recovery_only" and result["accounts"]==0 and not calls
    assert worker.CYCLES not in db.collections and db[ANALYSES].rows==[]


@pytest.mark.asyncio
async def test_one_account_cycle_saves_bound_analysis_and_executes_one_candidate(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert calls==["analyze","inventory","preview","execute"]
    assert result["results"][0]["status"]=="completed"
    saved=db[ANALYSES].rows[0]
    assert saved["policy_revision"]==1 and saved["source"]=="google_standing_policy_cycle"
    assert saved["actor_id"]=="mezan_google_automation_v1" and saved["user_id"]=="owner"
    assert saved["date_from"]=="2026-10-24" and saved["date_to"]=="2026-10-26"
    second=await worker.run_google_automation_cycle(db,**dependencies)
    assert second["results"][0]["status"]=="not_due" and calls.count("execute")==1


@pytest.mark.asyncio
async def test_expired_or_disabled_policy_does_no_provider_or_model_work(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    db[AUTOMATION_POLICIES].rows[0]["enabled"]=False
    assert (await worker.run_google_automation_cycle(db,**dependencies))["accounts"]==0
    assert calls==[]


@pytest.mark.asyncio
async def test_persisted_owner_revocation_prevents_native_work(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);db.users.rows[0]["role"]="viewer"
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="owner_unproved" and not calls


@pytest.mark.asyncio
async def test_pending_autonomous_outcome_reconciles_even_when_policy_revoked_and_never_executes(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);db[AUTOMATION_POLICIES].rows[0]["enabled"]=False
    db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","proposal_id":"pending1","status":"uncertain","submission_started":True,"automation_source":{"kind":"standing_policy"}}]
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert calls==["reconcile"] and result["results"][0]["status"]=="uncertain"
    assert db[ANALYSES].rows==[]


@pytest.mark.asyncio
async def test_pending_recovery_bypasses_analysis_cadence_but_never_resends(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    await worker.run_google_automation_cycle(db,**dependencies)
    db[AUTOMATION_POLICIES].rows[0]["enabled"]=False
    db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","proposal_id":"pending1","status":"uncertain","submission_started":True,"automation_source":{"kind":"standing_policy"}}]
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="uncertain" and calls.count("execute")==1
    assert calls[-1]=="reconcile"


@pytest.mark.asyncio
async def test_authorized_but_unsubmitted_floor_is_preserved_without_auto_dispatch(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","proposal_id":"pending1","status":"approved","submission_started":False,"automation_source":{"kind":"standing_policy"}}]
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="pending_dispatch" and not calls


@pytest.mark.asyncio
async def test_history_overflow_prevents_model_and_provider_work(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","status":"failed"} for _ in range(1001)]
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="history_incomplete" and not calls


@pytest.mark.asyncio
async def test_policy_revision_change_during_analysis_stops_preview(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);original=dependencies["analysis_builder"]
    async def changed(*args,**kwargs):
        result=await original(*args,**kwargs);db[AUTOMATION_POLICIES].rows[0]["revision"]=2;return result
    dependencies["analysis_builder"]=changed
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="policy_changed" and "preview" not in calls and "execute" not in calls


@pytest.mark.asyncio
async def test_unknown_execution_exception_is_not_retried_within_the_cycle(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    async def fail(*args,**kwargs): calls.append("execute");raise RuntimeError("private provider response")
    dependencies["executor"]=fail
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert calls.count("execute")==1 and result["results"][0]["status"]=="failed"
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_processing_is_bounded_to_four_accounts(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    template=deepcopy(db[AUTOMATION_POLICIES].rows[0])
    db[AUTOMATION_POLICIES].rows=[{**template,"account_id":str(index)} for index in range(100,105)]
    db.users.rows=[]
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["accounts"]==4 and len(result["results"])==4 and not calls


@pytest.mark.asyncio
async def test_concurrent_replicas_cannot_analyze_or_execute_the_same_account(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    started,release=asyncio.Event(),asyncio.Event();original=dependencies["analysis_builder"]
    async def wait(*args,**kwargs):
        started.set();await release.wait();return await original(*args,**kwargs)
    dependencies["analysis_builder"]=wait
    first=asyncio.create_task(worker.run_google_automation_cycle(db,**dependencies))
    await asyncio.wait_for(started.wait(),timeout=1)
    second=await worker.run_google_automation_cycle(db,**dependencies)
    assert second["results"][0]["status"]=="not_due" and calls==[]
    release.set();await first
    assert calls.count("analyze")==calls.count("execute")==1


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_claim",[False,True])
async def test_kill_switch_or_lost_cycle_claim_after_model_stops_native_preview(monkeypatch,lose_claim):
    db,calls,dependencies=setup(monkeypatch);original=dependencies["analysis_builder"]
    async def stop(*args,**kwargs):
        result=await original(*args,**kwargs)
        if lose_claim: db[worker.CYCLES].rows[0]["claim_token"]="other"
        else: monkeypatch.setattr(worker,"worker_enabled",lambda:False)
        return result
    dependencies["analysis_builder"]=stop
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="claim_lost" and calls==["analyze"]


@pytest.mark.asyncio
async def test_policy_revocation_during_preview_prevents_dispatch(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);original=dependencies["previewer"]
    async def revoke(*args,**kwargs):
        result=await original(*args,**kwargs);db[AUTOMATION_POLICIES].rows[0]["enabled"]=False;return result
    dependencies["previewer"]=revoke
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="policy_changed" and "execute" not in calls


@pytest.mark.asyncio
async def test_execution_kill_after_inventory_prevents_even_a_new_preview(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);original=dependencies["inventory_reader"]
    async def stop(*args,**kwargs):
        result=await original(*args,**kwargs);monkeypatch.setattr(worker,"execution_enabled",lambda:False);return result
    dependencies["inventory_reader"]=stop
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="execution_disabled" and calls==["analyze","inventory"]


@pytest.mark.asyncio
async def test_a_second_eligible_campaign_is_not_executed_in_the_same_cycle(monkeypatch):
    db,calls,dependencies=setup(monkeypatch);original_builder=dependencies["analysis_builder"];original_reader=dependencies["inventory_reader"]
    db[AUTOMATION_POLICIES].rows[0]["campaign_ids"].append("789")
    async def two(*args,**kwargs):
        result=await original_builder(*args,**kwargs);rec=deepcopy(result["recommendations"][0]);rec.update(campaign_id="789",recommendation_id="rec2")
        rec["evidence"][0].update(campaign_id="789",evidence_id="google:campaign:123:789:spend_native")
        result["recommendations"].append(rec);return result
    async def inventory(*args,**kwargs):
        result=await original_reader(*args,**kwargs);other=deepcopy(result["campaigns"][0]);other["campaign_id"]="789";result["campaigns"].append(other);return result
    dependencies.update(analysis_builder=two,inventory_reader=inventory)
    await worker.run_google_automation_cycle(db,**dependencies)
    assert calls.count("preview")==calls.count("execute")==1


@pytest.mark.asyncio
async def test_cancellation_after_durable_submission_only_reconciles_next_time(monkeypatch):
    db,calls,dependencies=setup(monkeypatch)
    async def interrupted(*args,**kwargs):
        calls.append("execute")
        db[PROPOSALS].rows=[{"user_id":"owner","account_id":"123","proposal_id":"proposal1","status":"submitted","submission_started":True,"automation_source":{"kind":"standing_policy"}}]
        raise asyncio.CancelledError()
    dependencies["executor"]=interrupted
    with pytest.raises(asyncio.CancelledError): await worker.run_google_automation_cycle(db,**dependencies)
    result=await worker.run_google_automation_cycle(db,**dependencies)
    assert result["results"][0]["status"]=="uncertain" and calls.count("execute")==1 and calls[-1]=="reconcile"


@pytest.mark.asyncio
async def test_native_analysis_has_service_read_actor_and_policy_limited_model_actions(monkeypatch):
    from integrations_control_center import google_ads_workspace,google_ads_ai
    policy,result,inventory=fixtures();captured=[]
    async def reader(db,actor,**kwargs):
        assert actor=={"user_id":"owner","actor_id":"mezan_google_automation_v1","owner":False,"can_draft":False}
        assert kwargs["from_date"]=="2026-10-24" and kwargs["to_date"]=="2026-10-26"
        return {**inventory,"campaigns":[inventory["campaigns"][0],{**inventory["campaigns"][0],"campaign_id":"999"}]}
    async def analyzer(db,user_id,body,**kwargs): captured.append(kwargs["facts"]);return result
    monkeypatch.setattr(google_ads_workspace,"read_google_workspace",reader)
    monkeypatch.setattr(google_ads_ai,"analyze_google_campaigns",analyzer)
    assert await worker.build_google_automation_analysis(DB(),policy,current=NOW)==result
    assert captured[0]["permitted_actions"]==["PAUSE_CAMPAIGN"]
    assert captured[0]["limits"]["max_daily_budget_native"]=="40"
    assert [row["campaign_id"] for row in captured[0]["campaigns"]]==["456"]
