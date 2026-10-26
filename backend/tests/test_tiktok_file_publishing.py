"""The file path sends metadata to Mezan and media only to TikTok."""
import httpx
import pytest
import pytest_asyncio
import json
from fastapi import APIRouter, FastAPI, HTTPException

from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes
from integrations_control_center import tiktok_file_publishing as publishing
from integrations_control_center import tiktok_file_api as transport
from tests.test_tiktok_campaign_management import environment


def app_for(user, db=None):
    async def current_user():
        return user

    def require_owner(value):
        if value.get("role") != "owner":
            raise HTTPException(403)
        return value

    app, router = FastAPI(), APIRouter(prefix="/api/integrations-v2")
    attach_tiktok_native_reporting_routes(router, db if db is not None else object(), current_user, require_owner)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_file_preview_is_a_typed_public_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post("/api/integrations-v2/tiktok/content/files/proposals", json={})
    assert response.status_code == 422


def value(**changes):
    return publishing.FilePostInput(**{"creator_ref": "a" * 64, "idempotency_key": "file_request_123456", "file_name": "winter.mp4",
        "file_manifest_sha256": "b" * 64, "file_size": 4194304, "mime_type": "video/mp4", "duration_seconds": 32.0,
        "width": 720, "height": 1280, "delivery": "direct", "privacy_level": "PUBLIC_TO_EVERYONE",
        "caption": "هدية الشتاء", "disclosure": "own_business", "media_rights_confirmed": True, **changes})


class Native:
    def __init__(self):
        self.calls, self.initializations = [], []
        self.identity = "native-owner"
        self.timeout = False
        self.rejection = None
        self.destination = "https://open-upload.tiktokapis.com/video/?upload_id=1&upload_token=fixture-capability-secret"
        self.settings = {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], "max_video_post_duration_sec": 900,
            "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}
        self.status = {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 0, "publicaly_available_post_id": []}

    async def handle(self, request):
        assert request.url.host == "open.tiktokapis.com"
        assert request.headers["Authorization"] == "Bearer fixture-access-secret"
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/v2/user/info/": data = {"user": {"open_id": self.identity}}
        elif path == "/v2/post/publish/creator_info/query/": data = self.settings
        elif path.endswith("/init/"):
            body = json.loads(request.content)
            assert body["source_info"]["source"] == "FILE_UPLOAD" and "video_url" not in str(body)
            assert set(body) <= {"source_info", "post_info"}
            self.initializations.append((path, body))
            if self.rejection:
                return httpx.Response(403, json={"error": {"code": self.rejection, "message": "fixture-private-upload-capability"}})
            if self.timeout: raise httpx.ReadTimeout("fixture-capability-secret", request=request)
            data = {"publish_id": "v_pub_file~v2.test", "upload_url": self.destination}
        else:
            assert path == "/v2/post/publish/status/fetch/"
            data = self.status
        return httpx.Response(200, json={"error": {"code": "ok"}, "data": data})

    def factory(self):
        return transport.TikTokFileAPI(transport=httpx.MockTransport(self.handle))


@pytest_asyncio.fixture
async def fileflow(environment, monkeypatch):
    db, _ = environment
    row = {"_id": "a" * 64, "user_id": "owner", "auth_epoch": "epoch-1", "app_id": "fixture-app", "status": "connected",
        "scopes": ["user.info.basic", "video.upload", "video.publish"]}
    await db[publishing.CREDENTIALS].insert_one(row)

    async def verified(db, user, creator, api, required_scope=None):
        current = await db[publishing.CREDENTIALS].find_one({"_id": creator, "user_id": user, "status": "connected"})
        if not current or required_scope not in current["scopes"]:
            raise HTTPException(409)
        return current, "fixture-access-secret", "native-owner"

    monkeypatch.setattr(publishing, "verified_creator", verified)
    monkeypatch.setenv("TIKTOK_FILE_UPLOAD_ENABLED", "true")
    state = Native()
    yield db, state


async def prepare(db, state, data=None):
    return await publishing.preview(db, "owner", data or value(), business_factory=state.factory, api_factory=state.factory)


async def initialize(db, state, preview):
    return await publishing.execute(db, "owner", preview["proposal_id"], preview["confirmation_digest"], business_factory=state.factory, api_factory=state.factory)


@pytest.mark.asyncio
async def test_roundtrip_immutable_preview_encrypts_native_capability_and_never_relays_media(fileflow, caplog):
    db, state = fileflow
    preview = await prepare(db, state)
    assert preview["status"] == "previewed" and not state.initializations
    second = await prepare(db, state)
    assert second["proposal_id"] == preview["proposal_id"]
    result = await initialize(db, state, preview)
    assert result["status"] == "awaiting_upload" and result["transfer"]["url"] == state.destination
    row = await db[publishing.PROPOSALS].find_one({"_id": preview["proposal_id"]})
    assert "fixture-capability-secret" not in str(row) + caplog.text
    assert "upload_token" not in json.dumps(publishing.public(row), default=str)
    assert len(state.initializations) == 1
    assert state.initializations[0][1]["post_info"]["brand_organic_toggle"] is True
    assert all(method in {"GET", "POST"} and path.startswith("/v2/") for method, path in state.calls)
    with pytest.raises(HTTPException): await initialize(db, state, preview)
    assert len(state.initializations) == 1


@pytest.mark.asyncio
async def test_draft_initialization_has_only_file_metadata(fileflow):
    db, state = fileflow
    preview = await prepare(db, state, value(delivery="draft", privacy_level=None, caption="", disclosure="none"))
    await initialize(db, state, preview)
    assert state.initializations[0][0] == "/v2/post/publish/inbox/video/init/"
    assert set(state.initializations[0][1]) == {"source_info"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["identity", "privacy", "epoch", "owner"])
async def test_fresh_identity_privacy_authority_and_owner_block_writes(fileflow, change):
    db, state = fileflow
    preview = await prepare(db, state)
    if change == "identity": state.identity = "someone-else"
    elif change == "privacy": state.settings["privacy_level_options"] = ["SELF_ONLY"]
    elif change == "epoch": await db[publishing.CREDENTIALS].update_one({"_id": "a" * 64}, {"$set": {"auth_epoch": "new-epoch"}})
    else:
        with pytest.raises(HTTPException): await publishing.execute(db, "other-owner", preview["proposal_id"], preview["confirmation_digest"], api_factory=state.factory)
        assert not state.initializations
        return
    with pytest.raises(HTTPException): await initialize(db, state, preview)
    assert not state.initializations


@pytest.mark.asyncio
async def test_uncertain_init_is_fenced_across_new_idempotency_keys(fileflow):
    db, state = fileflow
    preview = await prepare(db, state)
    state.timeout = True
    with pytest.raises(HTTPException): await initialize(db, state, preview)
    row = await publishing.owned(db, "owner", preview["proposal_id"])
    assert row["status"] == "uncertain"
    other = await prepare(db, state, value(idempotency_key="different_request_123"))
    with pytest.raises(HTTPException): await initialize(db, state, other)
    assert len(state.initializations) == 1


@pytest.mark.asyncio
async def test_definitive_native_rejection_is_an_owner_visible_conflict():
    def handle(request):
        return httpx.Response(403, json={"error": {"code": "scope_not_authorized", "message": "fixture-private-upload-capability"}})
    async with transport.TikTokFileAPI(transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(HTTPException) as caught:
            await api.call("POST", "/v2/post/publish/video/init/", token="fixture-access-secret", body={})
    assert caught.value.status_code == 409
    assert caught.value.detail["provider_code"] == "scope_not_authorized"
    assert caught.value.detail["native_rejected"] is True
    assert "fixture-private-upload-capability" not in str(caught.value.detail)


@pytest.mark.parametrize("fields", [
    {"code": "private-secret", "provider_code": "private-secret", "stage": "private-secret"},
    {"code": "tiktok_file_provider_rejected", "provider_code": "private-secret", "message": "private-secret"},
])
def test_stored_diagnostics_never_echo_unknown_provider_codes_or_text(fields):
    result = publishing.initialization_diagnostic(fields)
    assert "private-secret" not in json.dumps(result)
    assert "provider_code" not in result
    assert result["stage"] == "unknown"


@pytest.mark.asyncio
async def test_rejected_initialization_reason_survives_owner_history_without_another_init(fileflow):
    db, state = fileflow
    preview = await prepare(db, state)
    state.rejection = "scope_not_authorized"
    with pytest.raises(HTTPException):
        await initialize(db, state, preview)
    row = await publishing.owned(db, "owner", preview["proposal_id"])
    assert row["status"] == "blocked"
    result = publishing.public(row)
    assert result["initialization_diagnostic"]["provider_code"] == "scope_not_authorized"
    assert result["initialization_diagnostic"]["stage"] == "native_initialization"
    assert result["initialization_diagnostic"]["message"]
    assert "fixture-private-upload-capability" not in json.dumps(result, default=str)
    await publishing.reconcile(db, "owner", preview["proposal_id"], business_factory=state.factory, api_factory=state.factory)
    with pytest.raises(HTTPException):
        await initialize(db, state, preview)
    assert len(state.initializations) == 1


@pytest.mark.asyncio
async def test_unknown_failure_diagnostic_does_not_release_uncertainty_fence(fileflow):
    db, state = fileflow
    preview = await prepare(db, state)
    state.timeout = True
    with pytest.raises(HTTPException):
        await initialize(db, state, preview)
    row = await publishing.owned(db, "owner", preview["proposal_id"])
    result = publishing.public(row)
    assert result["status"] == "uncertain"
    assert result["initialization_diagnostic"]["code"] == "tiktok_file_transport_failed"
    assert "provider_code" not in result["initialization_diagnostic"]
    assert "fixture-capability-secret" not in json.dumps(result, default=str)
    other = await prepare(db, state, value(idempotency_key="diagnostic_request_123"))
    with pytest.raises(HTTPException):
        await initialize(db, state, other)
    assert len(state.initializations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["missing_destination", "expired"])
async def test_native_pending_status_never_claims_an_unavailable_upload_session(fileflow, unavailable):
    from datetime import timedelta
    db, state = fileflow
    preview = await prepare(db, state, value(delivery="draft", privacy_level=None, caption="", disclosure="none"))
    if unavailable == "missing_destination":
        state.destination = "https://unapproved.test/video/?upload_token=fixture-private-capability"
        with pytest.raises(HTTPException):
            await initialize(db, state, preview)
    else:
        await initialize(db, state, preview)
        await db[publishing.PROPOSALS].update_one({"_id": preview["proposal_id"]}, {"$set": {"upload_expires_at": publishing.now() - timedelta(seconds=1)}})
    result = await publishing.reconcile(db, "owner", preview["proposal_id"], business_factory=state.factory, api_factory=state.factory)
    assert result["status"] == "uncertain"
    assert result["uploaded_bytes"] == 0
    assert result["initialization_diagnostic"]["code"] == ("tiktok_file_upload_destination_unproven" if unavailable == "missing_destination" else "tiktok_file_resume_unproven")
    assert "fixture-private-capability" not in json.dumps(result, default=str)
    with pytest.raises(HTTPException):
        await publishing.transfer(db, "owner", preview["proposal_id"], "b" * 64, business_factory=state.factory, api_factory=state.factory)
    row = await publishing.owned(db, "owner", preview["proposal_id"])
    fence = await db[publishing.FENCES].find_one({"_id": row["fence_id"]})
    assert fence["active"] is True
    assert len(state.initializations) == 1


@pytest.mark.asyncio
async def test_resume_reconciles_native_offset_and_returns_same_capability_without_another_init(fileflow):
    db, state = fileflow
    preview = await prepare(db, state, value(file_size=25 * 1024 * 1024))
    await initialize(db, state, preview)
    state.status["uploaded_bytes"] = 10 * 1024 * 1024
    result = await publishing.transfer(db, "owner", preview["proposal_id"], "b" * 64, business_factory=state.factory, api_factory=state.factory)
    assert result["transfer"]["offset"] == 10485760 and result["transfer"]["url"] == state.destination
    assert len(state.initializations) == 1 and state.calls[-1][1].endswith("/status/fetch/")


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["different_file", "partial_chunk", "expired", "fully_uploaded"])
async def test_resume_refuses_unproven_file_offset_or_expired_capability(fileflow, condition):
    from datetime import timedelta
    db, state = fileflow
    preview = await prepare(db, state, value(file_size=25 * 1024 * 1024))
    await initialize(db, state, preview)
    if condition == "partial_chunk": state.status["uploaded_bytes"] = 123
    if condition == "fully_uploaded": state.status["uploaded_bytes"] = 25 * 1024 * 1024
    if condition == "expired":
        await db[publishing.PROPOSALS].update_one({"_id": preview["proposal_id"]}, {"$set": {"upload_expires_at": publishing.now() - timedelta(seconds=1)}})
    with pytest.raises(HTTPException):
        await publishing.transfer(db, "owner", preview["proposal_id"], ("c" if condition == "different_file" else "b") * 64, business_factory=state.factory, api_factory=state.factory)
    assert len(state.initializations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["redirect", "compressed", "oversized", "malformed", "unknown_error"])
async def test_native_transport_rejects_unproven_responses_without_logging_capabilities(condition, caplog):
    def handle(request):
        secret = "fixture-private-upload-capability"
        if condition == "redirect": return httpx.Response(302, headers={"Location": "https://evil.test/" + secret})
        if condition == "compressed": return httpx.Response(200, content=b"{}", headers={"Content-Type": "application/json", "Content-Encoding": "br"})
        if condition == "oversized": return httpx.Response(200, content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "999999"})
        if condition == "malformed": return httpx.Response(200, content=secret.encode(), headers={"Content-Type": "application/json"})
        return httpx.Response(400, json={"error": {"code": secret, "message": secret}})
    async with transport.TikTokFileAPI(transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(HTTPException) as caught:
            await api.call("POST", "/v2/post/publish/video/init/", token="fixture-access-secret", body={})
    assert "fixture-private-upload-capability" not in str(caught.value.detail) + caplog.text
    assert caught.value.detail.get("native_rejected") is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("status,ids,expected", [("PROCESSING_UPLOAD", [], "awaiting_upload"), ("PUBLISH_COMPLETE", [], "complete_pending_ids"),
    ("PUBLISH_COMPLETE", ["1234567891234567891"], "published_public"), ("FAILED", [], "failed")])
async def test_native_status_not_browser_claim_establishes_completion(fileflow, status, ids, expected):
    db, state = fileflow
    preview = await prepare(db, state)
    await initialize(db, state, preview)
    state.status = {"status": status, "uploaded_bytes": 0 if status == "PROCESSING_UPLOAD" else value().file_size, "publicaly_available_post_id": ids}
    result = await publishing.reconcile(db, "owner", preview["proposal_id"], business_factory=state.factory, api_factory=state.factory)
    assert result["status"] == expected and len(state.initializations) == 1
    assert "upload_token" not in json.dumps(result, default=str)


@pytest.mark.parametrize("size,chunk,count", [(1,1,1), (4194304,4194304,1), (10485760,10485760,1),
    (10485761,10485760,1), (50000123,10485760,4), (1073741824,10485760,102)])
def test_chunk_contract_merges_trailing_bytes_into_final_chunk(size, chunk, count):
    result = transport.source_info(size)
    assert result == {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    assert size - chunk * (count - 1) <= 128 * 1024 * 1024


@pytest.mark.parametrize("url", ["http://open-upload.tiktokapis.com/video/?x=1", "https://open-upload.tiktokapis.com.evil.test/video/?x=1",
    "https://attacker@open-upload.tiktokapis.com/video/?x=1", "https://open-upload.tiktokapis.com:443/video/?x=1",
    "https://open-upload.tiktokapis.com/video/?x=1#secret", "https://open-upload.tiktokapis.com/other/?x=1"])
def test_native_upload_capability_cannot_target_an_unreviewed_origin_or_path(url):
    with pytest.raises(HTTPException): transport.upload_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [("POST", "/proposals", {}), ("GET", "/proposals", None),
    ("POST", "/creators/" + "a" * 64 + "/verify", {}), ("POST", "/proposals/any/status", {}),
    ("POST", "/proposals/any/approve-and-initialize", {"confirmation_digest": "b" * 64})])
async def test_non_owner_cannot_use_file_upload_routes(method, path, body):
    if path == "/proposals" and method == "POST": body = value().model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id":"staff","role":"staff"})), base_url="http://test") as client:
        response = await client.request(method, "/api/integrations-v2/tiktok/content/files" + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_server_refuses_binary_or_media_sized_metadata():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id":"owner","role":"owner"})), base_url="http://test") as client:
        binary = await client.post("/api/integrations-v2/tiktok/content/files/proposals", content=b"not-a-video", headers={"Content-Type":"video/mp4"})
        large = await client.post("/api/integrations-v2/tiktok/content/files/proposals", content=b"x" * 32769, headers={"Content-Type":"application/json"})
    assert binary.status_code == 415 and large.status_code == 413
