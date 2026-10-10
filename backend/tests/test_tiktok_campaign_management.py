"""Real disposable Mongo + HTTP transport contracts; no live provider writes."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
import os
import uuid

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError
import pytest
import pytest_asyncio

from integrations_control_center import tiktok_campaign_management as management
from integrations_control_center.tiktok_oauth_security import encrypt_tiktok_token


def payload(action="rename", **values):
    result = {"action": action, "account_id": "70001", "campaign_id": "1111",
              "campaign_name": "New name", "reason": "Explicit owner review", "idempotency_key": "owner_request_1"}
    if action not in {"rename", "create"}:
        result.pop("campaign_name")
    if action == "create":
        result.pop("campaign_id"); result["budget_native"] = 100.0
    if action in {"enable", "set_budget"}:
        result["spend_ceiling_native"] = 200.0
    if action == "set_budget":
        result["budget_native"] = 150.0
    return management.TikTokCampaignProposalInput(**{**result, **values})


class ProviderState:
    def __init__(self):
        self.row = {"advertiser_id": "70001", "campaign_id": "1111", "campaign_name": "Old name",
                    "operation_status": "ENABLE", "secondary_status": "CAMPAIGN_STATUS_ENABLE",
                    "objective_type": "WEB_CONVERSIONS", "budget": "100.00", "budget_mode": "BUDGET_MODE_DAY",
                    "budget_optimize_on": False, "budget_auto_adjust_strategy": "UNSET", "sales_destination": "WEBSITE"}
        self.rows = {"1111": self.row}; self.manual = False; self.calls = []; self.writes = []
        self.behavior = None; self.currency = "SAR"; self.waiting = asyncio.Event(); self.release = asyncio.Event()

    async def handle(self, request):
        assert request.url.host == "business-api.tiktok.com"
        assert request.headers["Access-Token"] == "disposable-fixture-token"
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        path = request.url.path
        if path.endswith("/advertiser/info/"):
            assert json.loads(request.url.params["advertiser_ids"]) == ["70001"]
            data = {"list": [{"advertiser_id": "70001", "currency": self.currency}]}
        elif request.method == "GET":
            assert request.url.params["page_size"] == "1"
            ids = json.loads(request.url.params["filtering"])["campaign_ids"]
            assert len(ids) == 1
            rows = [] if self.manual and "/smart_plus/" in path else [deepcopy(self.rows[ids[0]])] if ids[0] in self.rows else []
            data = {"list": rows, "page_info": {"total_number": len(rows), "total_page": 1}}
            if self.behavior == "oversized":
                data["unused_provider_blob"] = "x" * management.MAX_RESPONSE_BYTES
        else:
            body = json.loads(request.content); self.writes.append((path, body))
            assert body["advertiser_id"] == "70001"
            if self.behavior == "wait":
                self.waiting.set(); await self.release.wait()
            if path.endswith("/campaign/create/"):
                assert body["operation_status"] == "DISABLE"
                assert body["request_id"].isdigit() and 0 < int(body["request_id"]) < 2**63
                self.rows["2222"] = {**deepcopy(self.row), **body, "campaign_id": "2222", "secondary_status": "CAMPAIGN_STATUS_DISABLE"}
                data = {} if self.behavior == "missing_id" else {"campaign_id": "2222"}
            else:
                identifier = body.get("campaign_id") or body["campaign_ids"][0]
                self.rows[identifier].update({key: value for key, value in body.items() if key in self.row})
                data = {"campaign_id": identifier}
            if self.behavior == "timeout_after_write":
                raise httpx.ReadTimeout("fixture timeout", request=request)
            if self.behavior == "wrong_verification":
                self.rows[body.get("campaign_id") or "2222"]["campaign_name"] = "Unconfirmed name"
        return httpx.Response(200, json={"code": 0, "data": data})

    def factory(self, token):
        return management.TikTokCampaignProvider(token, transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def environment(monkeypatch):
    mongo_url = os.environ.get("TEST_TIKTOK_MONGO_URL")
    if not mongo_url:
        pytest.skip("Explicit disposable TEST_TIKTOK_MONGO_URL required")
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=2000)
    await client.admin.command("ping")
    db = client["tiktok_management_test_" + uuid.uuid4().hex]
    for name, value in {"TIKTOK_MARKETING_APP_ID": "fixture-app", "TIKTOK_MARKETING_APP_SECRET": "fixture-secret",
        "TIKTOK_TOKEN_ENC_KEY": Fernet.generate_key().decode(), "JWT_SECRET": "fixture-state-secret",
        "TIKTOK_MARKETING_REDIRECT_URI": "https://example.test/api/tiktok/callback"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(management.governor, "peek", lambda: ("normal", None))
    await db.mezan_integrations_v2.insert_one({"user_id": "owner", "provider": "tiktok_ads", "connection_status": "connected", "connection_provenance": "api_connection"})
    await db.mezan_integration_accounts_v2.insert_one({"user_id": "owner", "provider": "tiktok_ads", "ad_account_id": "70001", "connection_status": "connected", "connection_provenance": "api_connection", "display_name": "Fixture account", "currency": "SAR"})
    await db[management.TIKTOK_CREDENTIALS_COLLECTION].insert_one({"user_id": "owner", "provider": "tiktok_ads", "advertiser_ids": ["70001"], "access_token_ciphertext": encrypt_tiktok_token("disposable-fixture-token"), "updated_at": "epoch-1"})
    state = ProviderState()
    try:
        yield db, state
    finally:
        await client.drop_database(db.name); client.close()


async def prepare(db, state, data=None):
    return await management.preview_tiktok_campaign(db, "owner", data or payload(), provider_factory=state.factory)


async def execute(db, state, proposal):
    return await management.execute_tiktok_campaign(db, "owner", proposal["proposal_id"], proposal["confirmation_digest"], provider_factory=state.factory)


@pytest.mark.asyncio
async def test_native_owner_preview_is_read_only_and_same_input_is_idempotent(environment):
    db, state = environment
    one = await prepare(db, state); two = await prepare(db, state)
    assert one["proposal_id"] == two["proposal_id"] and one["status"] == "previewed"
    assert one["provider_write_reached"] is False and state.writes == []
    assert all(method == "GET" for method, _, _ in state.calls)
    assert "user_id" not in one and "auth_epoch" not in one and "access_token_ciphertext" not in json.dumps(one)


@pytest.mark.asyncio
async def test_owner_can_execute_verified_name_change_once(environment):
    db, state = environment
    proposal = await prepare(db, state); one = await execute(db, state, proposal); two = await execute(db, state, proposal)
    assert one["status"] == two["status"] == "completed" and one["verified"] is True
    assert one["after"]["campaign_name"] == "New name" and len(state.writes) == 1
    assert state.writes[0] == ("/open_api/v1.3/smart_plus/campaign/update/", {"advertiser_id": "70001", "campaign_id": "1111", "campaign_name": "New name"})
    assert (await db[management.FENCE_COLLECTION].find_one({"user_id": "owner"}))["status"] == "released"


@pytest.mark.asyncio
async def test_new_website_campaign_is_disabled_has_finite_budget_and_signed64_request_id(environment):
    db, state = environment
    proposal = await prepare(db, state, payload("create")); result = await execute(db, state, proposal)
    assert result["status"] == "completed" and result["created_campaign_id"] == "2222"
    body = state.writes[0][1]
    assert body["sales_destination"] == "WEBSITE" and body["budget_mode"] == "BUDGET_MODE_DAY"
    assert body["budget_optimize_on"] is False and body["budget"] == 100 and body["operation_status"] == "DISABLE"
    assert 0 < int(body["request_id"]) < 2**63


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign_owner", "disconnected", "removed_advertiser", "revoked_connection"])
async def test_native_ownership_is_rechecked_before_every_provider_write(environment, change):
    db, state = environment
    proposal = await prepare(db, state)
    owner = "foreign" if change == "foreign_owner" else "owner"
    if change == "disconnected":
        await db.mezan_integration_accounts_v2.update_one({"user_id": "owner"}, {"$set": {"connection_status": "not_connected"}})
    if change == "removed_advertiser":
        await db[management.TIKTOK_CREDENTIALS_COLLECTION].update_one({"user_id": "owner"}, {"$set": {"advertiser_ids": []}})
    if change == "revoked_connection":
        await db.mezan_integrations_v2.update_one({"user_id": "owner"}, {"$set": {"connection_status": "not_connected"}})
    with pytest.raises(HTTPException) as error:
        await management.execute_tiktok_campaign(db, owner, proposal["proposal_id"], proposal["confirmation_digest"], provider_factory=state.factory)
    assert error.value.status_code == 404 and state.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["provider_drift", "expired", "modified_plan", "wrong_digest", "new_auth_epoch", "currency_changed"])
async def test_stale_or_modified_approval_never_reaches_provider_write(environment, change):
    db, state = environment
    proposal = await prepare(db, state)
    if change == "provider_drift": state.row["budget"] = "110.00"
    if change == "currency_changed": state.currency = "USD"
    if change == "expired":
        await db[management.COLLECTION].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"expires_at": (management._now() - timedelta(seconds=1)).isoformat()}})
    if change == "modified_plan":
        await db[management.COLLECTION].update_one({"proposal_id": proposal["proposal_id"]}, {"$set": {"planned.campaign_name": "Injected name"}})
    if change == "new_auth_epoch":
        await db[management.TIKTOK_CREDENTIALS_COLLECTION].update_one({"user_id": "owner"}, {"$set": {"updated_at": "epoch-2"}})
    if change == "wrong_digest": proposal["confirmation_digest"] = "0" * 64
    with pytest.raises(HTTPException): await execute(db, state, proposal)
    assert state.writes == []


@pytest.mark.asyncio
async def test_idempotency_key_cannot_bind_changed_intent(environment):
    db, state = environment; await prepare(db, state)
    with pytest.raises(HTTPException) as error: await prepare(db, state, payload(campaign_name="Different name"))
    assert error.value.detail["code"] == "tiktok_management_idempotency_conflict" and state.writes == []


@pytest.mark.asyncio
async def test_legacy_campaign_pause_uses_legacy_endpoint_and_deleted_entity_is_blocked(environment):
    db, state = environment; state.manual = True; state.row["budget"] = "0"; state.row["budget_mode"] = "BUDGET_MODE_INFINITE"
    proposal = await prepare(db, state, payload("pause")); result = await execute(db, state, proposal)
    assert result["status"] == "completed" and state.writes[0][0] == "/open_api/v1.3/campaign/status/update/"
    state.row["secondary_status"] = "CAMPAIGN_STATUS_DELETE"
    with pytest.raises(HTTPException) as error: await prepare(db, state, payload("pause", idempotency_key="deleted_request"))
    assert error.value.detail["code"] == "tiktok_management_campaign_not_mutable" and len(state.writes) == 1


@pytest.mark.asyncio
async def test_dynamic_daily_budget_requires_ceiling_covering_125_percent(environment):
    db, state = environment; state.row["budget_mode"] = "BUDGET_MODE_DYNAMIC_DAILY_BUDGET"; state.row["budget_optimize_on"] = True
    with pytest.raises(HTTPException) as error: await prepare(db, state, payload("set_budget", budget_native=100.0, spend_ceiling_native=124.99))
    assert error.value.detail["code"] == "tiktok_management_spend_ceiling_exceeded"
    proposal = await prepare(db, state, payload("set_budget", budget_native=100.0, spend_ceiling_native=125.0))
    assert proposal["financial_bound"]["maximum_native"] == "125.00"
    result = await execute(db, state, proposal); assert result["verified"] is True and len(state.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["auto_increase", "infinite", "unclassified_budget", "legacy_finance"])
async def test_unproven_financial_basis_blocks_activation(environment, case):
    db, state = environment
    if case == "auto_increase": state.row["budget_auto_adjust_strategy"] = "AUTO_BUDGET_INCREASE"
    if case == "infinite": state.row["budget_mode"] = "BUDGET_MODE_INFINITE"
    if case == "unclassified_budget": state.row["budget_optimize_on"] = None
    if case == "legacy_finance": state.manual = True
    with pytest.raises(HTTPException): await prepare(db, state, payload("enable"))
    assert state.writes == []


@pytest.mark.asyncio
async def test_provider_currency_is_proven_and_never_assumed_to_be_sar(environment):
    db, state = environment; state.currency = "USD"
    proposal = await prepare(db, state, payload("create"))
    assert proposal["currency"] == "USD" and proposal["planned"]["budget"] == 100


@pytest.mark.asyncio
async def test_timeout_after_write_is_durable_uncertain_and_read_only_reconciliation_can_verify(environment, monkeypatch):
    db, state = environment; proposal = await prepare(db, state); state.behavior = "timeout_after_write"
    with pytest.raises(HTTPException) as error: await execute(db, state, proposal)
    assert error.value.detail["code"] == "tiktok_management_result_uncertain"
    row = await db[management.COLLECTION].find_one({"proposal_id": proposal["proposal_id"]})
    assert row["status"] == "uncertain" and row["provider_write_reached"] is True
    with pytest.raises(HTTPException): await execute(db, state, proposal)
    with pytest.raises(HTTPException) as early:
        await management.reconcile_tiktok_campaign(db, "owner", proposal["proposal_id"], provider_factory=state.factory)
    assert early.value.detail["code"] == "tiktok_management_reconciliation_wait"
    now = management._now(); monkeypatch.setattr(management, "_now", lambda: now + timedelta(minutes=6))
    result = await management.reconcile_tiktok_campaign(db, "owner", proposal["proposal_id"], provider_factory=state.factory)
    assert result["status"] == "completed" and len(state.writes) == 1


@pytest.mark.asyncio
async def test_create_missing_id_never_retries_or_duplicates_same_intent(environment):
    db, state = environment; proposal = await prepare(db, state, payload("create")); state.behavior = "missing_id"
    with pytest.raises(HTTPException): await execute(db, state, proposal)
    assert (await db[management.COLLECTION].find_one({"proposal_id": proposal["proposal_id"]}))["status"] == "uncertain"
    another = await prepare(db, state, payload("create", idempotency_key="another_create_request"))
    with pytest.raises(HTTPException) as error: await execute(db, state, another)
    assert error.value.detail["code"] == "tiktok_management_campaign_busy" and len(state.writes) == 1


@pytest.mark.asyncio
async def test_cancellation_after_submission_preserves_fence_and_blocks_duplicate(environment):
    db, state = environment; proposal = await prepare(db, state); state.behavior = "wait"
    task = asyncio.create_task(execute(db, state, proposal)); await asyncio.wait_for(state.waiting.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    row = await db[management.COLLECTION].find_one({"proposal_id": proposal["proposal_id"]})
    assert row["status"] == "uncertain" and row["provider_write_reached"] is True
    with pytest.raises(HTTPException): await execute(db, state, proposal)
    assert len(state.writes) == 1 and (await db[management.FENCE_COLLECTION].find_one({"user_id": "owner"}))["status"] == "claimed"


@pytest.mark.asyncio
async def test_bounded_json_response_and_resource_admission_refuse_before_any_write(environment):
    db, state = environment; state.behavior = "oversized"
    with pytest.raises(HTTPException) as error: await prepare(db, state)
    assert error.value.detail["code"] == "tiktok_management_response_limit" and state.writes == []
    await management._slot.acquire()
    try:
        with pytest.raises(HTTPException) as busy: await management.preview_tiktok_campaign(object(), "owner", payload())
        assert busy.value.status_code == 503
    finally:
        management._slot.release()


def test_schema_forbids_tenant_override_and_unbounded_mutation_payload():
    with pytest.raises(ValidationError): payload(user_id="foreign")
    with pytest.raises(ValidationError): payload(operation_status="DELETE")
    with pytest.raises(ValidationError): payload("set_budget", spend_ceiling_native=None)
    with pytest.raises(ValidationError): payload("create", budget_native=float("inf"))
