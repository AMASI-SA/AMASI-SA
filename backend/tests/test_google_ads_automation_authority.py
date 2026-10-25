"""Standing authorization is a durable policy claim, never a provider call."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_management import ANALYSES, FENCES, PROPOSALS, digest
from integrations_control_center.google_ads_campaign_builder import build_update_operations
from tests.test_google_ads_autonomy_planner import inputs

NOW = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)


def case_documents(action="PAUSE_CAMPAIGN", *, current=NOW, proposal_id="proposal1", claim_id="claim1", analysis_id="analysis1"):
    data = inputs(action)
    data["current"] = current
    policy = data["policy"]
    policy.update(updated_by="owner", updated_at=current.isoformat(), expires_at=(current+timedelta(days=1)).isoformat(), activation_version=1,
        activation_revision=policy["revision"], activated_by="owner", activated_at=current.isoformat())
    analysis = data["analysis"]
    analysis.update(analysis_id=analysis_id, created_at=current.isoformat())
    for fact in analysis["result"]["recommendations"][0]["evidence"]:
        fact["observed_at"] = current.isoformat()
    inventory = data["workspace"]
    inventory["observed_at"] = current.isoformat()
    for campaign in inventory["campaigns"]:
        campaign["observed_at"] = current.isoformat()
    snapshot = {"campaign": {"id":"456", "resourceName":"customers/123/campaigns/456", "status":"PAUSED" if action == "RESUME_CAMPAIGN" else "ENABLED",
        "advertisingChannelType":"SEARCH", "campaignBudget":"customers/123/campaignBudgets/999"},
        "campaign_budget":{"resourceName":"customers/123/campaignBudgets/999", "period":"DAILY", "amountMicros":"20000000", "explicitlyShared":False, "referenceCount":1}, "groups":[],"ads":[]}
    native_action = {"PAUSE_CAMPAIGN":"pause", "RESUME_CAMPAIGN":"enable", "CHANGE_BUDGET":"set_budget"}[action]
    changes = {"budget_native":"22"} if native_action == "set_budget" else {}
    source = authority.make_automation_source(policy, analysis_id=analysis["analysis_id"], recommendation_id="rec1")
    from integrations_control_center.google_ads_autonomy_planner import plan_google_autonomy
    candidate = plan_google_autonomy(**data)["candidates"][0]
    row = {"user_id":"owner", "account_id":"123", "proposal_id":proposal_id, "execution_claim_id":claim_id,
        "actor_id":authority.SERVICE_ACTOR_ID, "provider":"google_ads", "action":native_action, "campaign_id":"456",
        "status":"approved", "submission_started":False, "auth_epoch":"test-epoch", "currency":"SAR", "time_zone":"Asia/Riyadh",
        "planned":{"action":native_action, **changes}, "before_digest":digest(snapshot), "before_snapshot":snapshot,
        "operations":build_update_operations(native_action,"123",snapshot,changes),
        "created_at":current.isoformat(), "expires_at":(current+timedelta(minutes=30)).isoformat(),
        "automation_source":source, "idempotency_key":candidate["idempotency_key"]}
    row["confirmation_digest"] = authority.automation_confirmation_digest(row)
    fence = {"user_id":"owner","account_id":"123","status":"claimed","proposal_id":proposal_id,"execution_claim_id":claim_id}
    native = {"metadata":{"account_id":"123","identity_verified":True,"currency_code":"SAR","time_zone":"Asia/Riyadh"},
        "auth_epoch":"test-epoch","observed_at":current.isoformat(),"inventory":inventory,"campaign_snapshot":snapshot}
    docs = {AUTOMATION_POLICIES:[policy], ANALYSES:[analysis], PROPOSALS:[row], FENCES:[fence],
        "users":[{"id":"owner","role":"owner"}],
        "mezan_integrations_v2":[{"user_id":"owner","provider":"google_ads","connection_status":"connected","connection_provenance":"api_connection"}],
        "mezan_integration_accounts_v2":[{"user_id":"owner","provider":"google_ads","ad_account_id":"123","connection_status":"connected","connection_provenance":"api_connection","mezan_selected":True}]}
    return docs, native


def matches(row, query):
    for key, value in query.items():
        found = key in row
        actual = row.get(key)
        if isinstance(value, dict) and any(str(k).startswith("$") for k in value):
            for operator, expected in value.items():
                if operator == "$exists" and found != expected: return False
                if operator == "$eq" and actual != expected: return False
                if operator == "$gt" and (actual is None or actual <= expected): return False
                if operator == "$in" and actual not in expected: return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, rows): self.rows=deepcopy(rows)
    def sort(self,*args): return self
    async def to_list(self,length=None): return self.rows[:length]


class Collection:
    def __init__(self, rows): self.rows=deepcopy(rows); self.updates=[]; self.before_update=None
    async def find_one(self, scope, projection=None):
        row=next((r for r in self.rows if matches(r,scope)),None)
        if row is None:return None
        if projection and any(v==1 for v in projection.values()):return {k:deepcopy(v) for k,v in row.items() if projection.get(k)==1}
        return deepcopy(row)
    def find(self,scope,projection=None):
        rows=[r for r in self.rows if matches(r,scope)]
        if projection:rows=[{k:deepcopy(v) for k,v in r.items() if projection.get(k)==1} for r in rows]
        return Cursor(rows)
    async def create_index(self,*args,**kwargs): return "index"
    async def update_one(self,scope,update):
        if self.before_update: self.before_update(self)
        self.updates.append((deepcopy(scope),deepcopy(update)))
        row=next((r for r in self.rows if matches(r,scope)),None)
        if row is None:return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get("$set",{})))
        for k,v in update.get("$inc",{}).items():row[k]=row.get(k,0)+v
        return SimpleNamespace(matched_count=1)


class DB:
    def __init__(self,documents): self.collections={k:Collection(v) for k,v in documents.items()};self.users=self["users"]
    def __getitem__(self,key):return self.collections[key]
    def __getattr__(self,key):return self[key]


async def authorize(db,native,**kwargs):
    return await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id="proposal1",execution_claim_id="claim1",native_evidence=native,current=NOW,**kwargs)


@pytest.fixture(autouse=True)
def feature_on(monkeypatch):monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED","true")


@pytest.mark.asyncio
async def test_default_off_creates_no_claim_or_database_writes(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED",raising=False)
    docs,native=case_documents();db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_execution_disabled"
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
@pytest.mark.parametrize("action,delta",[("PAUSE_CAMPAIGN","0"),("RESUME_CAMPAIGN","20"),("CHANGE_BUDGET","2")])
async def test_authorization_only_claims_policy_and_requires_separate_submission_floor(action,delta):
    docs,native=case_documents(action);db=DB(docs)
    result=await authorize(db,native)
    assert result["authorized"] is True and result["submission_floor_required"] is True and result["provider_writes"] == 0
    stored=db[AUTOMATION_POLICIES].rows[0]
    claim=stored["_dispatch_claim"]
    assert claim["claim_id"] == "claim1" and claim["proposal_id"] == "proposal1"
    assert claim["budget_increase_reserved_native"] == delta
    assert stored["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]
    assert db[PROPOSALS].rows[0]["submission_started"] is False
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_claim_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code",[
    (lambda d,n:d["users"][0].update(disabled=True),"google_automation_owner_unavailable"),
    (lambda d,n:d["users"][0].update(role="admin"),"google_automation_owner_unavailable"),
    (lambda d,n:d[AUTOMATION_POLICIES][0].update(enabled=False),"google_automation_policy_changed"),
    (lambda d,n:d[AUTOMATION_POLICIES][0].update(revision=4),"google_automation_policy_changed"),
    (lambda d,n:d[FENCES][0].update(execution_claim_id="other"),"google_automation_execution_claim_unproved"),
    (lambda d,n:d[PROPOSALS][0].update(submission_started=True),"google_automation_execution_claim_unproved"),
    (lambda d,n:d[PROPOSALS][0].update(actor_id="owner"),"google_automation_proposal_binding_invalid"),
    (lambda d,n:n.update(auth_epoch="changed"),"google_automation_native_evidence_invalid"),
    (lambda d,n:n["metadata"].update(identity_verified=False),"google_automation_native_evidence_invalid"),
    (lambda d,n:n["campaign_snapshot"]["campaign"].update(status="REMOVED"),"google_automation_native_evidence_invalid"),
    (lambda d,n:n.update(observed_at=(NOW-timedelta(minutes=2)).isoformat()),"google_automation_native_evidence_invalid"),
    (lambda d,n:d["mezan_integrations_v2"][0].update(connection_status="disconnected"),"google_automation_account_unavailable"),
])
async def test_changed_or_unproved_authority_never_reserves(change,code):
    docs,native=case_documents();change(docs,native);db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == code
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
async def test_manual_confirmation_cannot_be_upgraded_by_adding_automation_source():
    docs,native=case_documents();row=docs[PROPOSALS][0]
    from integrations_control_center.google_ads_management import immutable
    row["confirmation_digest"]=digest({k:v for k,v in immutable(row).items() if k!="automation_source"})
    db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_proposal_binding_invalid"


@pytest.mark.asyncio
async def test_revoke_winning_policy_cas_prevents_authorization():
    docs,native=case_documents();db=DB(docs)
    def revoke(collection):collection.rows[0].update(enabled=False,revision=4)
    db[AUTOMATION_POLICIES].before_update=revoke
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_policy_claim_changed"
    assert "_dispatch_claim" not in db[AUTOMATION_POLICIES].rows[0]


@pytest.mark.asyncio
async def test_ambiguous_policy_cas_acknowledgement_is_not_authorization():
    docs,native=case_documents();db=DB(docs)
    original=db[AUTOMATION_POLICIES].update_one
    async def lost_ack(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError("private transport failure")
    db[AUTOMATION_POLICIES].update_one=lost_ack
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_authorization_unconfirmed"
    assert "private" not in str(error.value.detail)
    assert error.value.detail["authorization_cas_attempted"] is True
    assert error.value.detail["preserve_execution_fence"] is True
    assert error.value.detail["automatic_retry_allowed"] is False
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_claim"]["claim_id"] == "claim1"


@pytest.mark.asyncio
async def test_uncertain_claim_survives_disabled_policy_and_cannot_be_released_by_time():
    docs,native=case_documents();db=DB(docs);await authorize(db,native)
    db[AUTOMATION_POLICIES].rows[0].update(enabled=False,revision=4)
    db[PROPOSALS].rows[0].update(status="uncertain",submission_started=True)
    with pytest.raises(HTTPException) as error:
        await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=NOW+timedelta(days=2))
    assert error.value.detail["code"] == "google_automation_settlement_unproved"
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_claim"]


@pytest.mark.asyncio
@pytest.mark.parametrize("final",["completed","cancelled_before_dispatch","provider_rejected"])
async def test_read_proved_terminal_settlement_clears_claim_without_refunding_quota(final,monkeypatch):
    docs,native=case_documents();db=DB(docs);await authorize(db,native)
    db[AUTOMATION_POLICIES].rows[0].update(enabled=False,revision=4)
    db[AUTOMATION_POLICIES].rows[0].pop("activation_version",None)
    row=db[PROPOSALS].rows[0]
    if final=="completed":row.update(status="completed",verified=True,submission_started=True)
    elif final=="cancelled_before_dispatch":row.update(status="failed",submission_started=False,safe_error="execution_cancelled_before_dispatch")
    else:row.update(status="failed",submission_started=True,provider_write_reached=False,rejection={"provider_status":400})
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED","false")
    result=await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=NOW)
    assert result["released"] is True and result["provider_writes"] == 0
    stored=db[AUTOMATION_POLICIES].rows[0]
    assert stored["_dispatch_claim"] is None and stored["enabled"] is False
    assert stored["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]


@pytest.mark.asyncio
async def test_owner_disabled_after_history_reads_cannot_win_policy_claim():
    docs,native=case_documents();db=DB(docs)
    original=db.users.find_one
    calls=[]
    async def revoked_owner(*args,**kwargs):
        calls.append(True)
        if len(calls)>1:db.users.rows[0]["disabled"]=True
        return await original(*args,**kwargs)
    db.users.find_one=revoked_owner
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_owner_unavailable"
    assert error.value.detail["authorization_cas_attempted"] is False
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
async def test_cancellation_after_policy_commit_retains_claim_and_classifies_fence_preservation(monkeypatch):
    import asyncio
    docs,native=case_documents();db=DB(docs)
    committed=asyncio.Event();never=asyncio.Event()
    original=authority._linearize_claim
    async def commit_then_pause(*args,**kwargs):
        await original(*args,**kwargs)
        committed.set();await never.wait()
    monkeypatch.setattr(authority,"_linearize_claim",commit_then_pause)
    task=asyncio.create_task(authorize(db,native))
    await asyncio.wait_for(committed.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as error:await task
    assert getattr(error.value,"preserve_execution_fence",False) is True
    assert getattr(error.value,"authorization_cas_attempted",False) is True
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_claim"]["claim_id"] == "claim1"
    assert db[FENCES].rows[0]["status"] == "claimed"
    assert db[PROPOSALS].rows[0]["submission_started"] is False


@pytest.mark.asyncio
async def test_global_write_switch_disabled_during_cas_prevents_a_permission_return(monkeypatch):
    docs,native=case_documents();db=DB(docs)
    original=authority._linearize_claim
    async def commit_then_disable(*args,**kwargs):
        result=await original(*args,**kwargs)
        monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED","false")
        return result
    monkeypatch.setattr(authority,"_linearize_claim",commit_then_disable)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_execution_disabled"
    assert error.value.detail["preserve_execution_fence"] is True
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_claim"]


@pytest.mark.asyncio
async def test_native_proof_ages_during_authorization_reads_and_is_rechecked_before_cas(monkeypatch):
    docs,native=case_documents();db=DB(docs)
    times=iter([NOW,NOW+timedelta(minutes=2)])
    monkeypatch.setattr(authority,"_now",lambda current=None:next(times))
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_native_evidence_invalid"
    assert not db[AUTOMATION_POLICIES].updates


def next_attempt(db,current,*,action="PAUSE_CAMPAIGN"):
    from integrations_control_center.google_ads_autonomy_planner import plan_google_autonomy
    docs,native=case_documents(action,current=current,proposal_id="proposal2",claim_id="claim2",analysis_id="analysis2")
    configured=db[AUTOMATION_POLICIES].rows[0]
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis2",recommendation_id="rec1")
    candidate=plan_google_autonomy(user_id="owner",account_id="123",policy=configured,analysis=docs[ANALYSES][0],workspace=native["inventory"],
        history={"complete":True,"user_id":"owner","account_id":"123","items":[]},current=current)["candidates"][0]
    row["idempotency_key"]=candidate["idempotency_key"]
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db[PROPOSALS].rows.append(row);db[ANALYSES].rows.extend(docs[ANALYSES]);db[FENCES].rows=docs[FENCES]
    return native


@pytest.mark.asyncio
async def test_reserved_daily_slot_is_not_reset_by_cancel_or_policy_revisions():
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0];configured["max_actions_per_day"]=1
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs);await authorize(db,native)
    db[PROPOSALS].rows[0].update(status="failed",submission_started=False,safe_error="execution_cancelled_before_dispatch")
    await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=NOW)
    db[AUTOMATION_POLICIES].rows[0].update(revision=5,enabled=True,updated_at=(NOW+timedelta(hours=1)).isoformat(),
        activation_revision=5,activated_by="owner",activated_at=(NOW+timedelta(hours=1)).isoformat())
    later=NOW+timedelta(hours=2);fresh=next_attempt(db,later)
    with pytest.raises(HTTPException) as error:
        await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id="proposal2",execution_claim_id="claim2",native_evidence=fresh,current=later)
    assert error.value.detail["code"] == "google_automation_daily_action_limit"
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]


@pytest.mark.asyncio
async def test_history_and_existing_reservation_are_unioned_without_double_counting():
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0];configured["max_actions_per_day"]=2
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs);await authorize(db,native)
    db[PROPOSALS].rows[0].update(status="completed",verified=True,submission_started=True,submitted_at=NOW.isoformat(),executed_at=NOW.isoformat())
    await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=NOW)
    later=NOW+timedelta(hours=2);fresh=next_attempt(db,later,action="RESUME_CAMPAIGN")
    result=await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id="proposal2",execution_claim_id="claim2",native_evidence=fresh,current=later)
    assert result["authorized"] is True
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_days"][0]["proposal_ids"] == ["proposal1","proposal2"]


@pytest.mark.asyncio
async def test_quota_rollover_uses_account_local_midnight_with_unchanged_utc_date():
    before=datetime(2026,10,25,20,59,tzinfo=timezone.utc);after=before+timedelta(minutes=2)
    docs,native=case_documents(current=before);configured=docs[AUTOMATION_POLICIES][0];configured["max_actions_per_day"]=1
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs)
    await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id="proposal1",execution_claim_id="claim1",native_evidence=native,current=before)
    db[PROPOSALS].rows[0].update(status="failed",submission_started=False,safe_error="execution_cancelled_before_dispatch")
    await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=before)
    fresh=next_attempt(db,after)
    result=await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id="proposal2",execution_claim_id="claim2",native_evidence=fresh,current=after)
    assert result["authorized"] is True and before.date() == after.date()
    assert [b["date"] for b in db[AUTOMATION_POLICIES].rows[0]["_dispatch_days"]] == ["2026-10-25","2026-10-26"]


@pytest.mark.asyncio
async def test_policy_must_record_the_actual_owner_as_authorizer():
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    configured["updated_by"]="staff"
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_policy_changed"
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
async def test_extra_mutation_cannot_be_authorized_even_with_a_recomputed_frozen_digest():
    docs,native=case_documents();row=docs[PROPOSALS][0]
    row["operations"].append({"campaignOperation":{"remove":"customers/123/campaigns/789"}})
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_proposal_binding_invalid"
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
async def test_authorization_never_constructs_provider_client_or_impersonates_a_principal(monkeypatch):
    from integrations_control_center import google_ads_client,google_ads_management
    def forbidden(*args,**kwargs):raise AssertionError("provider or principal boundary must not be called")
    monkeypatch.setattr(google_ads_client.GoogleAdsClient,"__init__",forbidden)
    monkeypatch.setattr(google_ads_management,"principal",forbidden)
    docs,native=case_documents();result=await authorize(DB(docs),native)
    assert result["authorized"] is True and result["provider_writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stop",["revoked","global_disabled"])
async def test_pre_cas_failure_with_existing_claim_never_permits_fence_cleanup(stop,monkeypatch):
    docs,native=case_documents();db=DB(docs);await authorize(db,native)
    if stop=="revoked":db[AUTOMATION_POLICIES].rows[0].update(enabled=False,revision=4)
    else:monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED","false")
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["authorization_cas_attempted"] is False
    assert error.value.detail["preserve_execution_fence"] is True
    assert error.value.detail["automatic_retry_allowed"] is False
    assert db[AUTOMATION_POLICIES].rows[0]["_dispatch_claim"]["claim_id"] == "claim1"
    assert db[FENCES].rows[0]["status"] == "claimed"
    assert db[PROPOSALS].rows[0]["status"] == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("activation",[None,0,True,"1",2])
async def test_legacy_or_unrecognized_activation_cannot_start_when_execution_flag_is_enabled(activation):
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    if activation is None:configured.pop("activation_version",None)
    else:configured["activation_version"]=activation
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_activation_required"
    assert error.value.detail["preserve_execution_fence"] is True
    assert not db[AUTOMATION_POLICIES].updates


def test_explicit_activation_version_is_part_of_the_frozen_policy_digest():
    docs,_=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    activated=authority.policy_authorization_digest({**configured,"activation_version":1})
    legacy=authority.policy_authorization_digest({k:v for k,v in configured.items() if k!="activation_version"})
    assert activated != legacy


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value",[
    ("activation_revision",None),("activation_revision",True),("activation_revision","3"),("activation_revision",4),
    ("activated_by","staff"),("activated_by",None),("activated_at",None),
    ("activated_at","2026-10-26T01:00:00"),("activated_at",(NOW+timedelta(seconds=1)).isoformat()),
])
async def test_activation_ack_must_match_current_revision_owner_and_aware_nonfuture_time(field,value):
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    configured.update(activation_revision=3,activated_by="owner",activated_at=NOW.isoformat())
    configured[field]=value
    row=docs[PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    db=DB(docs)
    with pytest.raises(HTTPException) as error:await authorize(db,native)
    assert error.value.detail["code"] == "google_automation_activation_required"
    assert not db[AUTOMATION_POLICIES].updates


@pytest.mark.asyncio
async def test_generic_policy_revision_bump_cannot_reactivate_a_previous_owner_acknowledgement():
    docs,native=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    configured.update(activation_version=1,activation_revision=3,activated_by="owner",activated_at=NOW.isoformat())
    # The existing generic PUT/revoke increments revision and preserves internal
    # activation fields. That preservation must not reactivate the new settings.
    configured.update(revision=4,enabled=True)
    row=docs[PROPOSALS][0]
    from integrations_control_center.google_ads_autonomy_planner import plan_google_autonomy
    row["idempotency_key"]=plan_google_autonomy(user_id="owner",account_id="123",policy=configured,analysis=docs[ANALYSES][0],
        workspace=native["inventory"],history={"user_id":"owner","account_id":"123","items":[],"complete":True},current=NOW)["candidates"][0]["idempotency_key"]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id="analysis1",recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    with pytest.raises(HTTPException) as error:await authorize(DB(docs),native)
    assert error.value.detail["code"] == "google_automation_activation_required"


@pytest.mark.parametrize("field,value",[("activation_revision",99),("activated_by","other"),("activated_at","2026-10-26T00:00:00Z")])
def test_activation_acknowledgement_fields_are_frozen_in_policy_digest(field,value):
    docs,_=case_documents();configured=docs[AUTOMATION_POLICIES][0]
    assert authority.policy_authorization_digest(configured) != authority.policy_authorization_digest({**configured,field:value})
