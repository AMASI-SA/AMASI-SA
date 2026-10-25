"""Owner-facing paused creation contracts, disposable Mongo, fixed TikTok IO."""
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

from integrations_control_center import tiktok_ad_builder as builder
from integrations_control_center import tiktok_creator_api as transport
from tests.test_tiktok_campaign_management import environment
from tests.test_tiktok_management_routes import app_for


def payload(kind="adgroup", **changes):
    value = {"kind": kind, "account_id": "70001", "name": "Winter test", "reason": "Owner selected winter test", "idempotency_key": "builder_request_1"}
    if kind == "adgroup":
        value.update({"campaign_id": "1111", "pixel_id": "9001", "location_ids": ["188"], "budget_native": 50.0, "spend_ceiling_native": 60.0,
            "schedule_start": (builder.now() + timedelta(days=1)).isoformat(), "schedule_end": (builder.now() + timedelta(days=2)).isoformat()})
    elif kind == "video_asset":
        value["video_url"] = "https://media.example.test/winter.mp4?private_signature=fixture"
    else:
        value.update({"campaign_id": "1111", "adgroup_id": "2222", "video_id": "video_1", "identity_id": "identity1", "identity_type": "TT_USER",
            "landing_page_url": "https://amasi-sa.com/ar/p123", "ad_text": "دفء الشتاء لمن تحب"})
    return builder.AdBuilderInput(**{**value, **changes})


class State:
    def __init__(self):
        self.calls, self.writes = [], []
        self.status = "DISABLE"
        self.timeout = False
        self.bad_verification = False
        self.rows = {}

    async def handle(self, request):
        assert request.url.host == "business-api.tiktok.com"
        assert request.headers["Access-Token"] == "disposable-fixture-token"
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/advertiser/info/"):
            data = {"list": [{"advertiser_id": "70001", "currency": "SAR", "timezone": "Asia/Riyadh"}]}
        elif path.endswith("/identity/get/"):
            data = {"identity_list": [{"identity_id": "identity1", "identity_type": "TT_USER"}]}
        elif path.endswith("/file/video/ad/info/"):
            data = {"list": [{"video_id": "video_1"}]}
        elif path.endswith("/file/video/ad/upload/"):
            assert request.headers["Content-Type"].startswith("multipart/form-data;")
            assert b'filename=' not in request.content
            assert b'UPLOAD_BY_URL' in request.content and b'private_signature=fixture' in request.content
            self.writes.append(path)
            data = [{"video_id": "video_1"}]
        elif request.method == "POST":
            body = json.loads(request.content)
            assert body["operation_status"] == "DISABLE"
            self.writes.append(path)
            kind = "adgroup" if "/adgroup/" in path else "ad"
            key = "adgroup_id" if kind == "adgroup" else "smart_plus_ad_id"
            identifier = "3333" if kind == "adgroup" else "4444"
            self.rows[kind, identifier] = {**body, key: identifier}
            if self.timeout:
                raise httpx.ReadTimeout("private transmission result", request=request)
            data = {key: identifier}
        else:
            kind = "campaign" if "/campaign/" in path else "adgroup" if "/adgroup/" in path else "ad"
            filtering = json.loads(request.url.params["filtering"])
            key = "smart_plus_ad_id" if kind == "ad" else kind + "_id"
            identifier = filtering[key + "s"][0]
            row = self.rows.get((kind, identifier))
            if row is None and kind == "campaign":
                row = {"advertiser_id": "70001", "campaign_id": "1111", "operation_status": self.status, "objective_type": "WEB_CONVERSIONS", "sales_destination": "WEBSITE", "budget_optimize_on": False}
            if row is None and kind == "adgroup":
                row = {"advertiser_id": "70001", "campaign_id": "1111", "adgroup_id": "2222", "operation_status": "DISABLE"}
            if self.bad_verification and (kind, identifier) in self.rows:
                row = {**row, "operation_status": "ENABLE"}
            data = {"list": [row], "page_info": {"total_number": 1}}
        return httpx.Response(200, json={"code": 0, "data": data})

    def api(self):
        return transport.TikTokCreatorAPI(transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def workflow(environment, monkeypatch):
    db, _ = environment
    state = State()
    monkeypatch.setenv("TIKTOK_AD_BUILDER_ENABLED", "true")
    monkeypatch.setattr(transport.governor, "peek", lambda: ("normal", None))
    monkeypatch.setattr(builder, "TikTokCreatorAPI", state.api)
    return db, state


@pytest.mark.asyncio
async def test_new_builder_is_reachable_through_the_public_owner_workspace():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok_ads/ad-builder/proposals", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["adgroup", "ad", "video_asset"])
async def test_preview_is_encrypted_no_write_then_one_verified_paused_creation(workflow, kind):
    db, state = workflow
    value = payload(kind)
    preview = await builder.preview_builder(db, "owner", value)
    assert preview["status"] == "previewed" and state.writes == []
    saved = await db[builder.PROPOSALS].find_one({"_id": preview["proposal_id"]})
    assert "private_signature" not in str(saved)
    result = await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert result["status"] == "completed" and result["verified"] is True
    assert result["created_entity_id"] == {"adgroup": "3333", "ad": "4444", "video_asset": "video_1"}[kind]
    repeated = await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert repeated == result and len(state.writes) == 1


@pytest.mark.asyncio
async def test_active_or_changed_parent_never_reaches_provider_write(workflow):
    db, state = workflow
    preview = await builder.preview_builder(db, "owner", payload())
    state.status = "ENABLE"
    with pytest.raises(HTTPException):
        await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert not state.writes


@pytest.mark.asyncio
async def test_uncertain_post_cannot_repeat_even_with_new_idempotency_key(workflow):
    db, state = workflow
    value = payload()
    preview = await builder.preview_builder(db, "owner", value)
    state.timeout = True
    with pytest.raises(HTTPException):
        await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    new = await builder.preview_builder(db, "owner", value.model_copy(update={"idempotency_key": "another_request"}))
    with pytest.raises(HTTPException):
        await builder.execute_builder(db, "owner", new["proposal_id"], new["confirmation_digest"])
    assert len(state.writes) == 1
    assert (await db[builder.PROPOSALS].find_one({"_id": preview["proposal_id"]}))["status"] == "uncertain"


@pytest.mark.asyncio
async def test_foreign_owner_and_excess_budget_are_rejected(workflow):
    db, state = workflow
    preview = await builder.preview_builder(db, "owner", payload())
    with pytest.raises(HTTPException) as caught:
        await builder.execute_builder(db, "other", preview["proposal_id"], preview["confirmation_digest"])
    assert caught.value.status_code == 404 and not state.writes
    with pytest.raises(HTTPException):
        await builder.preview_builder(db, "owner", payload(budget_native=80.0))
    assert not state.writes


@pytest.mark.asyncio
async def test_multipart_transport_rejects_a_file_or_extra_field_without_network():
    calls = []
    async def handle(request):
        calls.append(request)
        raise AssertionError("network must not be reached")
    async with transport.TikTokCreatorAPI(transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(HTTPException):
            await api.call("POST", "/file/video/ad/upload/", form={"video_file": "private bytes"})
    assert not calls


@pytest.mark.asyncio
async def test_known_created_id_reconciles_readonly_without_second_creation(workflow):
    db, state = workflow
    preview = await builder.preview_builder(db, "owner", payload())
    state.bad_verification = True
    with pytest.raises(HTTPException):
        await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    saved = await db[builder.PROPOSALS].find_one({"_id": preview["proposal_id"]})
    assert saved["status"] == "uncertain" and saved["created_entity_id"] == "3333"
    state.bad_verification = False
    result = await builder.reconcile_builder(db, "owner", preview["proposal_id"])
    assert result["status"] == "completed" and result["verified"] is True
    assert len(state.writes) == 1


@pytest.mark.asyncio
async def test_default_disabled_gate_and_tampered_digest_prevent_write(workflow, monkeypatch):
    db, state = workflow
    preview = await builder.preview_builder(db, "owner", payload())
    monkeypatch.delenv("TIKTOK_AD_BUILDER_ENABLED")
    with pytest.raises(HTTPException) as caught:
        await builder.execute_builder(db, "owner", preview["proposal_id"], preview["confirmation_digest"])
    assert caught.value.detail["code"] == "tiktok_builder_disabled"
    monkeypatch.setenv("TIKTOK_AD_BUILDER_ENABLED", "true")
    with pytest.raises(HTTPException):
        await builder.execute_builder(db, "owner", preview["proposal_id"], "0" * 64)
    assert not state.writes
