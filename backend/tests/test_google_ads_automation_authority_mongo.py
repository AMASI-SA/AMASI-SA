"""Real Mongo evidence for policy-claim/revocation and quota linearization.

Every database is disposable and loopback-only. Internal native-read proofs are
fixtures; the authority never calls Google or a model. Race gates control only
when the real Mongo CAS executes, not its result or matching behavior.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
from tests.test_google_ads_automation_policy_mongo import guard_mongo_contract, set_guard_time

from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from motor.motor_asyncio import AsyncIOMotorClientSession
from integrations_control_center import google_ads_automation_policy as policy
from integrations_control_center import google_ads_management as management
from integrations_control_center.google_ads_autonomy_planner import plan_google_autonomy
from tests.test_google_ads_automation_authority import NOW, case_documents
from tests.test_google_ads_automation_policy_mongo import automation_mongo_uri


@pytest.fixture(autouse=True)
def execution_on(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "true")


@pytest_asyncio.fixture
async def authority_db(automation_mongo_uri):
    client = AsyncIOMotorClient(automation_mongo_uri, serverSelectionTimeoutMS=1000)
    name = "google_auto_auth_" + uuid.uuid4().hex
    assert len(name.encode("ascii")) <= 63
    db = client[name]
    try:
        await client.admin.command("ping")
        await management.ensure_google_management_indexes(db)
        await db[policy.AUTOMATION_POLICIES].create_index([("user_id",1),("account_id",1)],unique=True,name="google_automation_tenant_account")
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def rebased_case(current, *, proposal_id="proposal1", claim_id="claim1", stored_policy=None):
    docs,native=case_documents(proposal_id=proposal_id,claim_id=claim_id)
    configured=deepcopy(stored_policy) if stored_policy is not None else docs[policy.AUTOMATION_POLICIES][0]
    if stored_policy is None:
        configured.update(updated_at=current.isoformat(),expires_at=(current+timedelta(days=1)).isoformat(),
            activated_at=current.isoformat())
    configured.pop("_id",None)
    docs[policy.AUTOMATION_POLICIES]=[configured]
    analysis=docs[management.ANALYSES][0]
    analysis.update(analysis_id="analysis_"+proposal_id,created_at=current.isoformat())
    for fact in analysis["result"]["recommendations"][0]["evidence"]:
        fact["observed_at"]=current.isoformat()
    inventory=native["inventory"]
    native["observed_at"]=inventory["observed_at"]=current.isoformat()
    for campaign in inventory["campaigns"]:campaign["observed_at"]=current.isoformat()
    candidate=plan_google_autonomy(user_id="owner",account_id="123",policy=configured,analysis=analysis,
        workspace=inventory,history={"user_id":"owner","account_id":"123","complete":True,"items":[]},current=current)["candidates"][0]
    row=docs[management.PROPOSALS][0]
    row.update(created_at=current.isoformat(),expires_at=(current+timedelta(minutes=30)).isoformat(),
        automation_source=authority.make_automation_source(configured,analysis_id=analysis["analysis_id"],recommendation_id="rec1"),
        idempotency_key=candidate["idempotency_key"])
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    return docs,native


async def seed(db,documents):
    for name,rows in documents.items():
        if rows:await db[name].insert_many(deepcopy(rows))


async def authorize(db,native,current=NOW,proposal_id="proposal1",claim_id="claim1"):
    set_guard_time(current)
    return await authority.authorize_google_automation_claim(db,user_id="owner",account_id="123",proposal_id=proposal_id,
        execution_claim_id=claim_id,native_evidence=native,current=current)


def policy_app(monkeypatch,db,current):
    set_guard_time(current)
    async def current_user():return {"id":"owner"}
    async def account_client(database,user_id,account_id,**kwargs):
        assert database is db and user_id == "owner" and account_id == "123"
        async def metadata():return {"account_id":"123","identity_verified":True,"currency_code":"SAR","time_zone":"Asia/Riyadh"}
        async def close():pass
        return SimpleNamespace(account_metadata=metadata,close=close),"test-epoch"
    monkeypatch.setattr(policy,"now",lambda:current)
    monkeypatch.setattr(policy,"account_client",account_client)
    app,router=FastAPI(),APIRouter()
    policy.attach_google_ads_automation_policy_routes(router,db,current_user)
    app.include_router(router)
    return app


def renewal_body(configured,revision):
    keys=("campaign_ids","allowed_actions","max_campaign_daily_budget_native","max_account_daily_budget_native",
          "max_budget_change_percent","cooldown_seconds","max_actions_per_day","expires_at")
    return {**{key:configured[key] for key in keys},"expected_revision":revision,"enabled":True}


@pytest.mark.asyncio
async def test_real_revoke_before_authorization_cas_yields_no_claim(monkeypatch,authority_db):
    docs,native=rebased_case(NOW);await seed(authority_db,docs)
    ready,proceed=asyncio.Event(),asyncio.Event()
    original=guard.run_google_budget_guard_transaction
    async def gated(database, **kwargs):
        if kwargs["operation_id"].startswith("standing_"):
            ready.set();await proceed.wait()
        return await original(database, **kwargs)
    monkeypatch.setattr(guard,"run_google_budget_guard_transaction",gated)
    task=asyncio.create_task(authorize(authority_db,native))
    try:
        await asyncio.wait_for(ready.wait(),5)
        app=policy_app(monkeypatch,authority_db,NOW)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
            response=await client.post("/google_ads/automation/policy/123/revoke",json={"expected_revision":3})
        assert response.status_code == 200
        proceed.set()
        with pytest.raises(HTTPException) as error:await task
        assert error.value.detail["code"] == "google_budget_guard_policy_changed"
        assert error.value.detail["preserve_execution_fence"] is True
        stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
        assert stored["enabled"] is False and stored.get("_dispatch_claim") is None
        assert stored.get("_dispatch_generation",0) == 0
    finally:
        proceed.set()
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)


@pytest.mark.asyncio
async def test_real_claim_before_revoke_is_preserved_and_new_claims_are_denied(monkeypatch,authority_db):
    docs,native=rebased_case(NOW);await seed(authority_db,docs)
    assert (await authorize(authority_db,native))["authorized"] is True
    app=policy_app(monkeypatch,authority_db,NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
        response=await client.post("/google_ads/automation/policy/123/revoke",json={"expected_revision":3})
    assert response.status_code == 200
    stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert stored["revision"] == 4 and stored["enabled"] is False
    assert stored["_dispatch_claim"]["claim_id"] == "claim1"
    assert stored["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]
    budget = await authority_db[guard.BUDGET_GUARDS].find_one({"user_id":"owner","account_id":"123"})
    assert budget["active_claim"]["allocation_id"] == "standing_claim1"
    assert budget["allocations"]["standing_claim1"]["state"] == "reserved"
    with pytest.raises(HTTPException):await authorize(authority_db,native)
    assert (await authority_db[management.PROPOSALS].find_one({"proposal_id":"proposal1"}))["submission_started"] is False


@pytest.mark.asyncio
async def test_real_postcommit_response_loss_is_unconfirmed_and_keeps_claim_and_fence(monkeypatch,authority_db):
    docs,native=rebased_case(NOW);await seed(authority_db,docs)
    original=guard._commit_once
    async def lost_response(database,session):
        await original(database,session)
        raise ConnectionError("private transport response lost after commit")
    monkeypatch.setattr(guard,"_commit_once",lost_response)
    with pytest.raises(HTTPException) as error:await authorize(authority_db,native)
    detail=error.value.detail
    assert detail["code"] == "google_budget_guard_commit_unconfirmed"
    assert detail["commit_attempted"] is True
    assert detail["authorization_cas_attempted"] is detail["preserve_execution_fence"] is True
    assert detail["automatic_retry_allowed"] is False and "private" not in str(detail)
    stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert stored["_dispatch_claim"]["claim_id"] == "claim1"
    budget = await authority_db[guard.BUDGET_GUARDS].find_one({"user_id":"owner","account_id":"123"})
    assert budget["active_claim"]["allocation_id"] == "standing_claim1"
    assert budget["allocations"]["standing_claim1"]["state"] == "reserved"
    fence=await authority_db[management.FENCES].find_one({"user_id":"owner","account_id":"123"})
    row=await authority_db[management.PROPOSALS].find_one({"proposal_id":"proposal1"})
    assert fence["status"] == "claimed" and row["status"] == "approved" and row["submission_started"] is False
    monkeypatch.setattr(guard,"_commit_once",original)
    with pytest.raises(HTTPException) as repeated:await authorize(authority_db,native)
    assert repeated.value.detail["code"] == "google_automation_claim_pending"
    # No automatic retry/resume after the lost authorization acknowledgement.
    assert (await authority_db[management.PROPOSALS].find_one({"proposal_id":"proposal1"}))["submission_started"] is False


async def cancel_and_settle(db,current):
    await db[management.PROPOSALS].update_one({"proposal_id":"proposal1","execution_claim_id":"claim1","submission_started":False},
        {"$set":{"status":"failed","safe_error":"execution_cancelled_before_dispatch"}})
    await authority.settle_google_automation_claim(db,user_id="owner",account_id="123",claim_id="claim1",current=current)
    old=await db[management.PROPOSALS].find_one({"proposal_id":"proposal1"})
    await management._release_fence(db,old)


async def insert_attempt(db,current,*,proposal_id="proposal2",claim_id="claim2"):
    configured=await db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    docs,native=rebased_case(current,proposal_id=proposal_id,claim_id=claim_id,stored_policy=configured)
    await db[management.ANALYSES].insert_one(docs[management.ANALYSES][0])
    await db[management.PROPOSALS].insert_one(docs[management.PROPOSALS][0])
    await management._claim_fence(db,docs[management.PROPOSALS][0])
    return native


@pytest.mark.asyncio
async def test_real_unused_reservation_survives_revoke_and_renewal_revision(monkeypatch,authority_db):
    docs,native=rebased_case(NOW);docs[policy.AUTOMATION_POLICIES][0]["max_actions_per_day"]=1
    configured=docs[policy.AUTOMATION_POLICIES][0]
    docs[management.PROPOSALS][0]["automation_source"]=authority.make_automation_source(configured,analysis_id=docs[management.ANALYSES][0]["analysis_id"],recommendation_id="rec1")
    docs[management.PROPOSALS][0]["confirmation_digest"]=authority.automation_confirmation_digest(docs[management.PROPOSALS][0])
    await seed(authority_db,docs)
    await authorize(authority_db,native)
    await cancel_and_settle(authority_db,NOW)
    later=NOW+timedelta(hours=2)
    app=policy_app(monkeypatch,authority_db,later)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://test") as client:
        assert (await client.post("/google_ads/automation/policy/123/revoke",json={"expected_revision":3})).status_code == 200
        assert (await client.put("/google_ads/automation/policy/123",json=renewal_body(configured,4))).status_code == 200
    renewed=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert renewed["revision"] == 5 and renewed["activation_revision"] == 3
    assert authority.activation_proof_valid(renewed,later) is False
    # Model the future explicit owner-activation CAS, not the generic PUT above.
    # The activation endpoint itself is deliberately outside this module/slice.
    activated=await authority_db[policy.AUTOMATION_POLICIES].update_one({"user_id":"owner","account_id":"123","revision":5},
        {"$set":{"revision":6,"activation_version":1,"activation_revision":6,"activated_by":"owner",        "activated_at":later.isoformat(),"updated_at":later.isoformat()}})
    assert activated.matched_count == 1
    fresh=await insert_attempt(authority_db,later)
    with pytest.raises(HTTPException) as error:
        await authorize(authority_db,fresh,later,"proposal2","claim2")
    assert error.value.detail["code"] == "google_automation_daily_action_limit"
    stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert stored["revision"] == 6 and stored["enabled"] is True and stored["_dispatch_claim"] is None
    assert stored["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]


@pytest.mark.asyncio
async def test_real_day_quota_rolls_at_account_local_midnight_not_utc(authority_db):
    before=datetime(2026,10,25,20,59,tzinfo=timezone.utc)
    after=before+timedelta(minutes=2)
    docs,native=rebased_case(before);configured=docs[policy.AUTOMATION_POLICIES][0]
    configured["max_actions_per_day"]=1
    row=docs[management.PROPOSALS][0]
    row["automation_source"]=authority.make_automation_source(configured,analysis_id=docs[management.ANALYSES][0]["analysis_id"],recommendation_id="rec1")
    row["confirmation_digest"]=authority.automation_confirmation_digest(row)
    await seed(authority_db,docs)
    await authorize(authority_db,native,before)
    await cancel_and_settle(authority_db,before)
    fresh=await insert_attempt(authority_db,after)
    result=await authorize(authority_db,fresh,after,"proposal2","claim2")
    assert result["authorized"] is True
    stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert before.date() == after.date()  # UTC date did not change.
    assert stored["_dispatch_days"] == [
        {"date":"2026-10-25","time_zone":"Asia/Riyadh","proposal_ids":["proposal1"]},
        {"date":"2026-10-26","time_zone":"Asia/Riyadh","proposal_ids":["proposal2"]}]


@pytest.mark.asyncio
async def test_real_concurrent_authorization_acknowledges_one_claim_only(monkeypatch,authority_db):
    docs,native=rebased_case(NOW);await seed(authority_db,docs)
    barrier=asyncio.Barrier(2);original=guard.run_google_budget_guard_transaction
    async def simultaneous(database, **kwargs):
        await barrier.wait()
        return await original(database, **kwargs)
    monkeypatch.setattr(guard,"run_google_budget_guard_transaction",simultaneous)
    results=await asyncio.wait_for(asyncio.gather(authorize(authority_db,native),authorize(authority_db,native),return_exceptions=True),10)
    granted=[result for result in results if isinstance(result,dict) and result.get("authorized")]
    denied=[result for result in results if isinstance(result,HTTPException)]
    assert len(granted) == len(denied) == 1
    assert denied[0].detail["code"] == "google_budget_guard_policy_changed"
    assert denied[0].detail["preserve_execution_fence"] is True
    stored=await authority_db[policy.AUTOMATION_POLICIES].find_one({"user_id":"owner","account_id":"123"})
    assert stored["_dispatch_generation"] == 1
    assert stored["_dispatch_days"][0]["proposal_ids"] == ["proposal1"]
    assert stored["_dispatch_claim"]["claim_id"] == "claim1"
