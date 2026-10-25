"""Durable Google gateway invariants against a disposable real MongoDB."""
import asyncio
import copy
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import uuid

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
from pymongo import MongoClient

from integrations_control_center import google_ads_management as gateway
from integrations_control_center.google_ads_campaign_contract import GoogleCampaignActionInput, GoogleCampaignDraft
from integrations_control_center.google_ads_routes import DraftCreate, attach_google_ads_management_routes, save_draft

ACCOUNT = "1234567890"
OWNER = {"user_id": "owner-a", "actor_id": "owner-a", "owner": True, "can_read": True, "can_draft": True}


def saved_native_advice():
    instant = gateway.now().isoformat()
    period = {"account_id": ACCOUNT, "currency_code": "USD", "time_zone": "America/New_York",
              "date_from": "2026-10-23", "date_to": "2026-10-25"}
    item = {"recommendation_id": f"google_ads:campaign:{ACCOUNT}:100:RESUME_CAMPAIGN",
            "provider": "google_ads", "account_id": ACCOUNT, "campaign_id": "100", "action": "RESUME_CAMPAIGN",
            "rationale": "اختبار اعتماد توصية محفوظة", "evidence": [{
                "evidence_id": f"google:campaign:{ACCOUNT}:100:spend_native", "campaign_id": "100",
                "metric": "spend_native", "value": "20", "source": "google_ads_api", "observed_at": instant,
                **{key: value for key, value in period.items() if key != "account_id"}}]}
    return {"analysis_id": uuid.uuid4().hex, "user_id": OWNER["user_id"], "account_id": ACCOUNT,
            "created_at": instant, "date_from": period["date_from"], "date_to": period["date_to"],
            "result": {"provider": "google_ads", "account_id": ACCOUNT, "period": period, "recommendations": [item]}}


@pytest.fixture(scope="session")
def mongo_uri():
    uri = os.getenv("GOOGLE_ADS_TEST_MONGO_URI")
    if uri:
        if not uri.startswith(("mongodb://127.0.0.1:", "mongodb://localhost:")):
            raise ValueError("Google gateway tests require a disposable loopback MongoDB")
        yield uri
        return
    binary = os.getenv("GOOGLE_ADS_TEST_MONGOD")
    if not binary or not Path(binary).is_file():
        pytest.skip("Set GOOGLE_ADS_TEST_MONGOD to the official disposable MongoDB binary")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="mezan-google-gateway-") as storage:
        process = subprocess.Popen([binary, "--dbpath", storage, "--port", str(port), "--bind_ip", "127.0.0.1", "--nounixsocket", "--logpath", str(Path(storage) / "mongo.log")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        uri = f"mongodb://127.0.0.1:{port}"
        client = MongoClient(uri, serverSelectionTimeoutMS=250)
        try:
            deadline = time.monotonic() + 12
            while True:
                try:
                    client.admin.command("ping")
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Disposable MongoDB did not start") from None
                    time.sleep(.05)
            yield uri
        finally:
            client.close()
            process.terminate()
            process.wait(timeout=10)


@pytest_asyncio.fixture
async def db(mongo_uri):
    client = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=1000)
    name = "google_gateway_test_" + uuid.uuid4().hex
    db = client[name]
    await db.users.insert_many([{"id": "owner-a", "role": "owner"}, {"id": "staff-a", "role": "admin", "created_by": "owner-a"}, {"id": "owner-b", "role": "owner"}])
    await db.mezan_integrations_v2.insert_one({"user_id": "owner-a", "provider": "google_ads", "connection_status": "connected", "connection_provenance": "api_connection"})
    await db.mezan_integration_accounts_v2.insert_one({"user_id": "owner-a", "provider": "google_ads", "ad_account_id": ACCOUNT, "external_account_id": ACCOUNT, "display_name": "حساب اختبار", "connection_status": "connected", "connection_provenance": "api_connection", "mezan_selected": True})
    yield db
    await client.drop_database(name)
    client.close()


def draft(**changes):
    return GoogleCampaignDraft.model_validate({"account_id": ACCOUNT, "campaign_type": "SEARCH", "name": "حملة اختبار", "budget_native": "20.123456", "bidding": {"strategy": "MAXIMIZE_CLICKS"}, "geo_target_ids": ["2682"], "language_ids": ["1019"],
        "groups": [{"name": "مجموعة", "headlines": ["العنوان الأول", "العنوان الثاني", "العنوان الثالث"], "descriptions": ["الوصف الأول للمنتج", "الوصف الثاني للمنتج"], "final_urls": ["https://example.com/product"], "keywords": [{"text": "منتج", "match_type": "EXACT"}]}], **changes})


class Provider:
    def __init__(self):
        self.preflights = self.writes = 0
        self.verified = True
        self.timeout = False
        self.delay = 0
        self.epoch = "credential-epoch"
        self.snapshot = {"campaign": {"id": "100", "resourceName": f"customers/{ACCOUNT}/campaigns/100", "name": "قبل", "status": "PAUSED", "advertisingChannelType": "SEARCH", "biddingStrategyType": "MAXIMIZE_CLICKS", "campaignBudget": f"customers/{ACCOUNT}/campaignBudgets/200"}, "campaign_budget": {"resourceName": f"customers/{ACCOUNT}/campaignBudgets/200", "amountMicros": "20000000", "period": "DAILY", "referenceCount": "1", "explicitlyShared": False}, "groups": [], "ads": [], "criteria": [], "conversion_goals": []}
        self.operations = []
    async def close(self):
        pass
    async def account_metadata(self):
        return {"account_id": ACCOUNT, "customer_id": ACCOUNT, "name": "اختبار", "currency_code": "USD", "time_zone": "America/New_York"}
    async def campaign_snapshot(self, campaign_id, **kwargs):
        result = copy.deepcopy(self.snapshot)
        result["campaign"].update(id=str(campaign_id), resourceName=f"customers/{ACCOUNT}/campaigns/{campaign_id}")
        return result
    async def mutate(self, operations, *, validate_only=False):
        if validate_only:
            self.preflights += 1
            return {}
        self.writes += 1
        self.operations = copy.deepcopy(operations)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.timeout:
            raise TimeoutError("provider response lost")
        responses = []
        for index, operation in enumerate(operations):
            root, spec = next(iter(operation.items()))
            name = spec.get("remove") or (spec.get("update") or spec.get("create") or {}).get("resourceName")
            if not name:
                collection = {"campaignLabelOperation": "campaignLabels", "campaignCriterionOperation": "campaignCriteria", "adGroupCriterionOperation": "adGroupCriteria", "adGroupAdOperation": "adGroupAds"}[root]
                name = f"customers/{ACCOUNT}/{collection}/100~{1000 + index}"
            name = re.sub(r"(?<=[/~])-[0-9]+", str(1000 + index), name)
            responses.append({root.removesuffix("Operation") + "Result": {"resourceName": name}})
        return {"mutateOperationResponses": responses}
    async def verify_operations(self, operations, names, *, accepted_receipt=False):
        return self.verified, {"verified": self.verified, "coverage_gaps": [] if self.verified else ["readback_pending"]}


import re


@pytest.fixture
def provider(monkeypatch):
    provider = Provider()
    async def client(db, user_id, account_id, **kwargs):
        if user_id != "owner-a" or account_id != ACCOUNT:
            raise HTTPException(404, {"code": "account_not_owned"})
        return provider, provider.epoch
    monkeypatch.setattr(gateway, "account_client", client)
    return provider


async def preview(db, action="rename", **changes):
    return await gateway.preview_google_action(db, OWNER, GoogleCampaignActionInput(action=action, account_id=ACCOUNT, campaign_id="100", changes=changes or {"name": "بعد"}, idempotency_key=uuid.uuid4().hex))


@pytest.mark.asyncio
async def test_create_saved_revision_preflight_then_verified_once(db, provider):
    saved = await save_draft(db, OWNER, DraftCreate(draft=draft(), idempotency_key="save-1"))
    body = GoogleCampaignActionInput(action="create_campaign", account_id=ACCOUNT, draft_id=saved["draft_id"], draft_revision=1, idempotency_key="create-1")
    proposal = await gateway.preview_google_action(db, OWNER, body)
    assert provider.preflights == 1 and provider.writes == 0
    assert proposal["currency"] == "USD" and proposal["financial_bound"]["budget_native"] == "20.123456"
    assert not {"operations", "auth_epoch", "before_snapshot"} & set(proposal)
    same = await gateway.preview_google_action(db, OWNER, body)
    assert same["proposal_id"] == proposal["proposal_id"] and provider.preflights == 1
    done = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert done["status"] == "completed" and done["verified"] and done["created_campaign_id"]
    assert provider.writes == 1
    assert any(op.get("campaignOperation", {}).get("create", {}).get("status") == "PAUSED" for op in provider.operations)
    again = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert again["verified"] and provider.writes == 1
    assert (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "released"


@pytest.mark.asyncio
async def test_concurrent_double_approval_has_one_provider_write(db, provider):
    proposal = await preview(db)
    provider.delay = .03
    results = await asyncio.gather(*(gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"]) for _ in range(4)), return_exceptions=True)
    assert provider.writes == 1
    assert sum(isinstance(result, dict) and result["verified"] for result in results) >= 1
    assert all(isinstance(result, (dict, HTTPException)) for result in results)


@pytest.mark.asyncio
async def test_timeout_retains_fence_and_reconcile_never_resubmits(db, provider):
    proposal = await preview(db)
    provider.timeout = True
    pending = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert pending["status"] == "uncertain" and pending["submission_started"] and provider.writes == 1
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    second = await preview(db, name="آخر")
    with pytest.raises(HTTPException) as error:
        await gateway.execute_google_proposal(db, OWNER, second["proposal_id"], second["confirmation_digest"])
    assert error.value.detail["code"] == "google_ads_account_execution_pending"
    await db[gateway.PROPOSALS].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"reconcile_after": "2000-01-01T00:00:00+00:00"}})
    result = await gateway.reconcile_google_proposal(db, OWNER, proposal["proposal_id"])
    assert result["verified"] and provider.writes == 1


@pytest.mark.asyncio
async def test_unverified_readback_never_claims_completion(db, provider):
    proposal = await preview(db)
    provider.verified = False
    result = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert result["status"] == "uncertain" and result["verified"] is False
    assert (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "claimed"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["digest", "drift", "credential", "expired"])
async def test_changed_preconditions_never_write(db, provider, change):
    proposal = await preview(db)
    confirmation = proposal["confirmation_digest"]
    if change == "digest":
        confirmation = "0" * 64
    elif change == "drift":
        provider.snapshot["campaign"]["name"] = "external change"
    elif change == "credential":
        provider.epoch = "reauthorized"
    else:
        await db[gateway.PROPOSALS].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}})
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], confirmation)
    assert provider.writes == 0


@pytest.mark.asyncio
async def test_saved_draft_cas_idempotency_and_tenant_isolation(db, provider):
    body = DraftCreate(draft=draft(), idempotency_key="draft-id")
    saved, same = await save_draft(db, OWNER, body), await save_draft(db, OWNER, body)
    assert saved["draft_id"] == same["draft_id"]
    with pytest.raises(HTTPException):
        await save_draft(db, OWNER, DraftCreate(draft=draft(name="different"), idempotency_key="draft-id"))
    async def current_user():
        return {"id": "owner-a", "role": "owner"}
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        update = {"draft": draft(name="new").model_dump(mode="json"), "expected_revision": 1}
        results = await asyncio.gather(*(client.put(f"/integrations-v2/google_ads/management/drafts/{saved['draft_id']}", json=update) for _ in range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        app.dependency_overrides[current_user] = lambda: {"id": "owner-b", "role": "owner"}
        assert (await client.get(f"/integrations-v2/google_ads/management/drafts/{saved['draft_id']}")).status_code == 404


@pytest.mark.asyncio
async def test_marketer_delegation_rechecked_and_budget_bound_enforced(db, provider):
    staff = await gateway.principal(db, {"id": "staff-a", "role": "admin"}, write=True)
    assert staff["user_id"] == "owner-a"
    proposal = await preview(db, action="set_budget", budget_native="60")
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, staff, proposal["proposal_id"], proposal["confirmation_digest"])
    await db[gateway.POLICIES].insert_one({"user_id": "owner-a", "account_id": ACCOUNT, "subject_id": "staff-a", "enabled": True, "allowed_actions": ["set_budget"], "max_daily_budget_native": "50"})
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, staff, proposal["proposal_id"], proposal["confirmation_digest"])
    assert provider.writes == 0


@pytest.mark.asyncio
async def test_stale_saved_draft_cannot_be_executed(db, provider):
    saved = await save_draft(db, OWNER, DraftCreate(draft=draft(), idempotency_key="stale-draft"))
    proposal = await gateway.preview_google_action(db, OWNER, GoogleCampaignActionInput(action="create_campaign", account_id=ACCOUNT, draft_id=saved["draft_id"], draft_revision=1, idempotency_key="stale-create"))
    await db[gateway.DRAFTS].update_one({"draft_id": saved["draft_id"]}, {"$inc": {"revision": 1}})
    with pytest.raises(HTTPException) as error:
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert error.value.detail["code"] == "google_ads_draft_stale" and provider.writes == 0


@pytest.mark.asyncio
async def test_stale_parallel_worker_cannot_downgrade_completed_proposal(db, provider, monkeypatch):
    proposal = await preview(db)
    read, finished = asyncio.Event(), asyncio.Event()
    original = gateway.get_proposal
    async def interleaved(*args, **kwargs):
        row = await original(*args, **kwargs)
        if asyncio.current_task().get_name() == "late-google-worker":
            read.set()
            await finished.wait()
        return row
    monkeypatch.setattr(gateway, "get_proposal", interleaved)
    late = asyncio.create_task(gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"]), name="late-google-worker")
    await read.wait()
    done = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    finished.set()
    with pytest.raises(HTTPException):
        await late
    persisted = await original(db, OWNER["user_id"], proposal["proposal_id"])
    assert done["verified"] and persisted["status"] == "completed" and persisted["verified"]
    assert provider.writes == 1
    assert (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "released"


@pytest.mark.asyncio
async def test_reconcile_during_pre_submission_cannot_release_active_fence(db, provider):
    proposal = await preview(db)
    await db[gateway.PROPOSALS].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"status": "executing", "execution_claim_id": "active-worker"}})
    await db[gateway.FENCES].insert_one({"user_id": OWNER["user_id"], "account_id": ACCOUNT, "proposal_id": proposal["proposal_id"], "execution_claim_id": "active-worker", "status": "claimed"})
    with pytest.raises(HTTPException):
        await gateway.reconcile_google_proposal(db, OWNER, proposal["proposal_id"])
    assert provider.writes == 0
    assert (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "claimed"


@pytest.mark.asyncio
async def test_actor_revoked_after_preconditions_before_dispatch_cannot_write(db, provider, monkeypatch):
    proposal = await preview(db)
    original = gateway.record_event
    async def revoke_after_approval(db, row, status, **kwargs):
        await original(db, row, status, **kwargs)
        if status == "approved":
            await db.users.update_one({"id": OWNER["actor_id"]}, {"$set": {"is_active": False}})
    monkeypatch.setattr(gateway, "record_event", revoke_after_approval)
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert provider.writes == 0


@pytest.mark.asyncio
async def test_accepted_receipt_then_readback_rejection_remains_uncertain(db, provider, monkeypatch):
    proposal = await preview(db)
    async def rejected_readback(*args, **kwargs):
        raise HTTPException(status_code=409, detail={"code": "google_ads_provider_rejected", "provider_status": 400})
    monkeypatch.setattr(provider, "verify_operations", rejected_readback)
    result = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert provider.writes == 1 and result["status"] == "uncertain"
    persisted = await gateway.get_proposal(db, OWNER["user_id"], proposal["proposal_id"])
    assert persisted["accepted_receipt"] and persisted["accepted_operations_digest"] == gateway.digest(persisted["operations"])
    assert (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "claimed"


@pytest.mark.asyncio
async def test_verified_creation_persists_product_intent_in_shared_v2_graph(db, provider):
    from integrations_control_center.campaign_product_associations import list_effective_campaign_products
    await db.mezan_products_v2.insert_one({"user_id": "owner-a", "salla_product_id": "1", "mezan_product_id": "native-1", "name": "منتج المتجر", "status": "active"})
    saved = await save_draft(db, OWNER, DraftCreate(draft=draft(product_ids=["1"]), idempotency_key="linked-product"))
    proposal = await gateway.preview_google_action(db, OWNER, GoogleCampaignActionInput(action="create_campaign", account_id=ACCOUNT, draft_id=saved["draft_id"], draft_revision=1, idempotency_key="linked-create"))
    done = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    links = await list_effective_campaign_products(db, "owner-a", provider="google_ads", account_id=ACCOUNT, campaign_id=done["created_campaign_id"], include_unverified=False)
    assert done["verified"] and [row["product_id"] for row in links] == ["1"]
    assert links[0]["confirmed"] is True and links[0]["evidence"]["source"] == "campaign_creation"
    again = await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert again["verified"] and provider.writes == 1


@pytest.mark.asyncio
async def test_other_tenant_product_cannot_be_previewed_or_adopted(db, provider):
    await db.mezan_products_v2.insert_one({"user_id": "owner-b", "salla_product_id": "foreign", "mezan_product_id": "foreign", "name": "خاص"})
    saved = await save_draft(db, OWNER, DraftCreate(draft=draft(product_ids=["foreign"]), idempotency_key="foreign-product"))
    with pytest.raises(HTTPException) as error:
        await gateway.preview_google_action(db, OWNER, GoogleCampaignActionInput(action="create_campaign", account_id=ACCOUNT, draft_id=saved["draft_id"], draft_revision=1, idempotency_key="foreign-create"))
    assert error.value.detail["code"] == "google_ads_product_intent_unavailable"
    assert provider.preflights == provider.writes == 0


@pytest.mark.asyncio
async def test_selected_product_retail_cannot_fall_back_to_all_feed(db, provider):
    from integrations_control_center.google_ads_campaign_contract import GoogleShoppingInput
    await db.mezan_products_v2.insert_one({"user_id": "owner-a", "salla_product_id": "1", "name": "منتج"})
    value = draft(product_ids=["1"], shopping=GoogleShoppingInput(merchant_id="900"))
    saved = await save_draft(db, OWNER, DraftCreate(draft=value, idempotency_key="all-feed-product"))
    with pytest.raises(HTTPException) as error:
        await gateway.preview_google_action(db, OWNER, GoogleCampaignActionInput(action="create_campaign", account_id=ACCOUNT, draft_id=saved["draft_id"], draft_revision=1, idempotency_key="all-feed-create"))
    assert error.value.detail["code"] == "google_ads_product_feed_scope_unproved"
    assert provider.preflights == provider.writes == 0


@pytest.mark.asyncio
async def test_product_picker_reads_only_owned_v2_catalog_without_provider_calls(db, provider):
    await db.mezan_products_v2.insert_many([{"user_id": "owner-a", "salla_product_id": "1", "name": "منتج ظاهر", "archived": False}, {"user_id": "owner-b", "salla_product_id": "2", "name": "منتج خاص", "archived": False}])
    async def current_user():
        return {"id": "owner-a", "role": "owner"}
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/integrations-v2/google_ads/products", params={"query": "ظاهر"})
    assert response.status_code == 200
    assert [row["product_id"] for row in response.json()["items"]] == ["1"]
    assert response.json()["source"] == "mezan_products_v2" and provider.preflights == provider.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["previewed", "executing", "approved"])
async def test_owner_reconcile_safely_cancels_stale_gate_before_submission(db, provider, stage):
    proposal = await preview(db)
    await db[gateway.PROPOSALS].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"status": stage, "execution_claim_id": "stale-worker"}})
    await db[gateway.FENCES].insert_one({"user_id": OWNER["user_id"], "account_id": ACCOUNT, "proposal_id": proposal["proposal_id"], "execution_claim_id": "stale-worker", "status": "claimed", "claimed_at": "2000-01-01T00:00:00+00:00"})
    result = await gateway.reconcile_google_proposal(db, OWNER, proposal["proposal_id"])
    assert result["status"] == "failed" and result["safe_error"] == "execution_cancelled_before_dispatch"
    assert provider.writes == 0 and (await db[gateway.FENCES].find_one({"account_id": ACCOUNT}))["status"] == "released"
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert provider.writes == 0


@pytest.mark.asyncio
async def test_delivery_expansion_respects_staff_budget_ceiling_without_budget_edit(db, provider):
    staff = await gateway.principal(db, {"id": "staff-a", "role": "admin"}, write=True)
    await db[gateway.POLICIES].insert_one({"user_id": "owner-a", "account_id": ACCOUNT, "subject_id": "staff-a", "enabled": True, "allowed_actions": ["set_targeting"], "max_daily_budget_native": "10"})
    proposal = await preview(db, action="set_targeting", geo_target_ids=["2682"])
    assert proposal["financial_bound"]["budget_native"] == "20"
    with pytest.raises(HTTPException):
        await gateway.execute_google_proposal(db, staff, proposal["proposal_id"], proposal["confirmation_digest"])
    assert provider.writes == 0


@pytest.mark.asyncio
async def test_saved_ai_advice_uses_real_gateway_storage_and_one_approved_dispatch(db, provider):
    row = saved_native_advice()
    await db[gateway.ANALYSES].insert_one(copy.deepcopy(row))
    app, router = FastAPI(), APIRouter()

    async def current_user():
        return {"id": "owner-a", "role": "owner"}

    attach_google_ads_management_routes(router, db, current_user)
    app.include_router(router)
    payload = {"analysis_id": row["analysis_id"], "recommendation_id": row["result"]["recommendations"][0]["recommendation_id"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
        first = await http.post("/google_ads/ai/recommendations/preview", json=payload)
        assert first.status_code == 200, first.text
        proposal = first.json()
        repeated = await http.post("/google_ads/ai/recommendations/preview", json=payload)
        assert repeated.status_code == 200 and repeated.json()["proposal_id"] == proposal["proposal_id"]
        assert provider.preflights == 1 and provider.writes == 0
        assert proposal["action"] == "enable" and proposal["status"] == "previewed"
        assert proposal["ai_source"]["analysis_id"] == row["analysis_id"]
        assert await db[gateway.PROPOSALS].count_documents({"user_id": "owner-a"}) == 1
        endpoint = f"/google_ads/management/proposals/{proposal['proposal_id']}/approve-and-execute"
        approved = {"confirmation_digest": proposal["confirmation_digest"]}
        done = await http.post(endpoint, json=approved)
        assert done.status_code == 200 and done.json()["verified"] is True, done.text
        assert provider.writes == 1
        replay = await http.post(endpoint, json=approved)
        assert replay.status_code == 200 and provider.writes == 1
    audit = await db[gateway.AUDIT].find({"proposal_id": proposal["proposal_id"]}).to_list(length=30)
    assert any(event["status"] == "completed" for event in audit)


@pytest.mark.asyncio
async def test_ai_source_tampering_cannot_reuse_approval_digest(db, provider):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_native_advice()
    action, source = recommendation_action(row, OWNER, row["result"]["recommendations"][0]["recommendation_id"], current=gateway.now())
    proposal = await gateway.preview_google_action(db, OWNER, action, ai_source=source)
    await db[gateway.PROPOSALS].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"ai_source.analysis_id": "different-analysis"}})
    with pytest.raises(HTTPException) as error:
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert error.value.detail["code"] == "google_ads_approval_changed" and provider.writes == 0


@pytest.mark.asyncio
async def test_ai_disable_switch_blocks_new_dispatch_but_preserves_manual_proposals(db, provider, monkeypatch):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_native_advice()
    action, source = recommendation_action(row, OWNER, row["result"]["recommendations"][0]["recommendation_id"], current=gateway.now())
    proposal = await gateway.preview_google_action(db, OWNER, action, ai_source=source)
    monkeypatch.setenv("GOOGLE_ADS_AI_ACTIONS_ENABLED", "false")
    with pytest.raises(HTTPException) as error:
        await gateway.execute_google_proposal(db, OWNER, proposal["proposal_id"], proposal["confirmation_digest"])
    assert error.value.detail["code"] == "google_ai_action_review_disabled" and provider.writes == 0
    manual = await preview(db)
    result = await gateway.execute_google_proposal(db, OWNER, manual["proposal_id"], manual["confirmation_digest"])
    assert result["verified"] is True and provider.writes == 1


@pytest.mark.asyncio
async def test_provider_account_metadata_drift_rejects_ai_preview_before_validation_or_write(db, provider):
    from integrations_control_center.google_ads_ai_actions import recommendation_action
    row = saved_native_advice()
    action, source = recommendation_action(row, OWNER, row["result"]["recommendations"][0]["recommendation_id"], current=gateway.now())
    source["currency"] = "SAR"
    with pytest.raises(HTTPException) as error:
        await gateway.preview_google_action(db, OWNER, action, ai_source=source)
    assert error.value.detail["code"] == "google_ai_recommendation_account_changed"
    assert provider.preflights == provider.writes == 0
