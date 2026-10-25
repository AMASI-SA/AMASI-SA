"""Upload behavior and fresh creative dependencies; only provider seams are fake."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest

from integrations_control_center import snapchat_media_upload as module

DATA = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16


class Collection:
    def __init__(self):
        self.rows = {}

    @staticmethod
    def matches(row, query):
        return all(row.get(key) == value for key, value in query.items())

    async def find_one(self, query):
        row = next((r for r in self.rows.values() if self.matches(r, query)), None)
        return deepcopy(row)

    async def update_one(self, query, update, upsert=False):
        row = next((r for r in self.rows.values() if self.matches(r, query)), None)
        if row is None:
            if not upsert:
                return SimpleNamespace(matched_count=0)
            row = deepcopy(update["$setOnInsert"])
            self.rows[row["_id"]] = row
            return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get("$set", {})))
        for key, value in update.get("$push", {}).items():
            row[key] = (row.get(key, []) + deepcopy(value["$each"]))[value["$slice"]:]
        return SimpleNamespace(matched_count=1)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection())


class Provider:
    def __init__(self):
        self.creates = self.uploads = self.reads = self.roles = 0
        self.allowed = True
        self.create_timeout = self.upload_timeout = False
        self.medium = self.sent = None
        self.role_hook = None

    async def management_role(self, account, action):
        assert action == "creative.create"
        self.roles += 1
        if self.role_hook:
            await self.role_hook(self.roles)
        return {"allowed": self.allowed}

    async def create_media(self, row):
        self.creates += 1
        self.medium = {"id": "media-test", "ad_account_id": row["account_id"],
            "type": "VIDEO", "name": row["provider_name"], "media_status": "PENDING_UPLOAD"}
        if self.create_timeout:
            raise module._failure(502, "simulated_create_timeout")
        return deepcopy(self.medium)

    async def upload_media(self, media_id, data, content_type):
        self.uploads += 1
        self.sent = data
        assert media_id == self.medium["id"]
        assert content_type == "video/mp4"
        self.medium.update({"media_status": "READY", "file_size_in_bytes": len(data),
            "duration_in_seconds": 5, "video_metadata": {"width_px": 1080, "height_px": 1920}})
        if self.upload_timeout:
            raise module._failure(502, "simulated_upload_timeout")
        return deepcopy(self.medium)

    async def read_media(self, media_id):
        self.reads += 1
        assert media_id == self.medium["id"]
        return deepcopy(self.medium)


@pytest.fixture(autouse=True)
def guards(monkeypatch):
    async def selected(db, user_id, account_id):
        if account_id != "account-test":
            raise HTTPException(409, detail={"code": "account_not_selected"})
        return {"ad_account_id": account_id}
    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "snapchat_campaign_mutations_enabled", lambda: True)
    monkeypatch.setattr(module, "_UPLOAD_SLOTS", asyncio.BoundedSemaphore(1))


def payload():
    return module.SnapchatMediaPreviewInput(account_id="account-test", name="Video",
        file_name="video.mp4", size_bytes=len(DATA), sha256=hashlib.sha256(DATA).hexdigest(),
        content_type="video/mp4")


async def source(data=DATA):
    await asyncio.sleep(0)
    yield data


async def prepared(db, provider):
    return await module.create_media_upload_preview(db, "owner", payload(), provider=provider)


async def upload(db, provider, preview, data=DATA):
    return await module.upload_media_body(db, "owner", preview["upload_id"], preview["confirm_token"],
        source(data), "video/mp4", provider=provider)


@pytest.mark.asyncio
async def test_ready_requires_provider_readback_and_replay_has_no_new_write():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    assert (provider.creates, provider.uploads) == (0, 0)
    result = await upload(db, provider, preview)
    assert result["ready_for_creative"] is True
    assert result["provider_media_id"] == "media-test"
    assert provider.sent == DATA
    assert (provider.creates, provider.uploads, provider.reads) == (1, 1, 1)
    again = await upload(db, provider, preview)
    assert again["status"] == "ready"
    assert (provider.creates, provider.uploads) == (1, 1)
    assert "confirm_token_hash" not in result and "user_id" not in result


@pytest.mark.asyncio
async def test_one_atomic_upload_winner_with_serialized_replay():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    results = await asyncio.gather(upload(db, provider, preview), upload(db, provider, preview),
        return_exceptions=True)
    assert any(isinstance(r, dict) and r["status"] == "ready" for r in results)
    assert all((isinstance(r, dict) and r["status"] == "ready")
        or (isinstance(r, HTTPException) and r.status_code in {409, 429}) for r in results)
    assert (provider.creates, provider.uploads) == (1, 1)


@pytest.mark.asyncio
async def test_capacity_refusal_precedes_claim_and_source_read():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    await module._UPLOAD_SLOTS.acquire()
    try:
        with pytest.raises(HTTPException) as raised:
            await upload(db, provider, preview)
        assert raised.value.status_code == 429
    finally:
        module._UPLOAD_SLOTS.release()
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "previewed" and row["claim_id"] is None
    assert (provider.creates, provider.uploads) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [DATA[:-1], DATA + b"x", b"x" * len(DATA)])
async def test_actual_size_and_digest_reject_before_provider_create(invalid):
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    with pytest.raises(HTTPException):
        await upload(db, provider, preview, invalid)
    assert (provider.creates, provider.uploads) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "switch"])
async def test_guard_revocation_after_body_prevents_create(monkeypatch, change):
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)

    async def revoked():
        if change == "role":
            provider.allowed = False
        else:
            monkeypatch.setattr(module, "snapchat_campaign_mutations_enabled", lambda: False)
        yield DATA

    with pytest.raises(HTTPException):
        await module.upload_media_body(db, "owner", preview["upload_id"], preview["confirm_token"],
            revoked(), "video/mp4", provider=provider)
    assert (provider.creates, provider.uploads) == (0, 0)


@pytest.mark.asyncio
async def test_wrong_account_create_response_never_uploads_or_becomes_ready():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    original = provider.create_media

    async def foreign(row):
        result = await original(row)
        result["ad_account_id"] = "another-account"
        return result

    provider.create_media = foreign
    with pytest.raises(HTTPException) as raised:
        await upload(db, provider, preview)
    assert raised.value.detail["code"] == "snapchat_media_identity_conflict"
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "create_unknown" and row["provider_readback_verified"] is False
    assert provider.uploads == 0


@pytest.mark.asyncio
async def test_created_media_stays_inflight_until_upload_or_caught_error():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause_after_create(count):
        if count == 4:
            entered.set()
            await release.wait()

    provider.role_hook = pause_after_create
    task = asyncio.create_task(upload(db, provider, preview))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        row = await module._load(db, "owner", preview["upload_id"])
        assert row["status"] == "media_created"
        interim = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
        assert interim["can_upload"] is False and provider.uploads == 0
    finally:
        release.set()
        result = await task
    assert result["ready_for_creative"] is True


@pytest.mark.asyncio
async def test_stale_created_media_recovery_cas_fences_the_old_worker():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    old = await module._load(db, "owner", preview["upload_id"])
    old = await module._state(db, old, {"status": "media_created", "claim_id": "old-worker",
        "provider_media_id": "media-test", "source_verified": True,
        "attempt_started_at": (module._now() - timedelta(seconds=181)).isoformat()})
    provider.medium = {"id": "media-test", "ad_account_id": "account-test",
        "type": "VIDEO", "media_status": "PENDING_UPLOAD"}
    recovered = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert recovered["status"] == "awaiting_file" and recovered["can_upload"] is True
    with pytest.raises(HTTPException):
        await module._state(db, old, {"status": "uploading"})
    assert (provider.creates, provider.uploads) == (0, 0)


@pytest.mark.asyncio
async def test_unknown_create_is_not_repeated_even_when_provider_created_it():
    db, provider = DB(), Provider()
    provider.create_timeout = True
    preview = await prepared(db, provider)
    with pytest.raises(HTTPException):
        await upload(db, provider, preview)
    recovered = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert recovered["status"] == "create_unknown"
    assert recovered["error_code"] == "snapchat_media_create_unresolved"
    assert recovered["can_upload"] is False
    same_content = await prepared(db, provider)
    assert same_content["upload_id"] == preview["upload_id"] and "confirm_token" not in same_content
    with pytest.raises(HTTPException):
        await upload(db, provider, preview)
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
async def test_upload_timeout_recovers_by_get_without_reupload():
    db, provider = DB(), Provider()
    provider.upload_timeout = True
    preview = await prepared(db, provider)
    with pytest.raises(HTTPException):
        await upload(db, provider, preview)
    recovered = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert recovered["status"] == "ready" and recovered["ready_for_creative"] is True
    assert (provider.creates, provider.uploads, provider.reads) == (1, 1, 1)


@pytest.mark.asyncio
async def test_foreign_owner_cannot_read_or_upload():
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    with pytest.raises(HTTPException) as raised:
        await module.upload_media_body(db, "other-owner", preview["upload_id"], preview["confirm_token"],
            source(), "video/mp4", provider=provider)
    assert raised.value.status_code == 404
    assert (provider.creates, provider.uploads) == (0, 0)


@pytest.mark.asyncio
async def test_raw_body_route_profile_dropdown_and_owner_boundary(monkeypatch):
    db, provider = DB(), Provider()
    monkeypatch.setattr(module, "available_public_profile_ids", AsyncMock(return_value={"profile-1"}))
    owner = {"id": "owner", "role": "owner"}

    async def current_user():
        return owner

    def require_owner(user):
        if user["role"] != "owner":
            raise HTTPException(403)
        return user

    app = FastAPI()
    router = APIRouter(prefix="/api/integrations-v2")
    module.attach_snapchat_media_upload_routes(router, db, current_user, require_owner,
        provider_factory=lambda db, user_id: provider)
    app.include_router(router)
    base = "/api/integrations-v2/snapchat_ads/management/media-uploads"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(base + "/profiles", params={"account_id": "account-test"})
        assert response.status_code == 200 and response.json()["complete"] is True
        assert response.json()["profiles"][0]["profile_id"] == "profile-1"
        response = await client.post(base + "/preview", json=payload().model_dump())
        assert response.status_code == 201
        preview = response.json()
        response = await client.post(base + "/" + preview["upload_id"] + "/upload", content=DATA,
            headers={"Content-Type": "video/mp4", "X-Mezan-Confirm-Token": preview["confirm_token"]})
        assert response.status_code == 200 and response.json()["ready_for_creative"] is True
        owner["role"] = "staff"
        assert (await client.get(base + "/" + preview["upload_id"])).status_code == 403


@pytest.mark.asyncio
async def test_provider_uses_multipart_without_json_content_type(monkeypatch):
    medium = {"id": "media-test", "ad_account_id": "account-test", "type": "VIDEO", "media_status": "READY"}
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return httpx.Response(200, json={"request_status": "SUCCESS", "result": medium})

    provider = module.SnapchatMediaProvider(DB(), "owner")
    provider.context.access_token = AsyncMock(return_value="test-token")
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    result = await provider.upload_media("media-test", DATA, "video/mp4")
    assert result["id"] == "media-test"
    assert captured["url"] == module.SNAPCHAT_API_BASE + "/media/media-test/upload"
    assert captured["files"]["file"][1] == DATA
    assert "json" not in captured and "Content-Type" not in captured["headers"]


def creative_operation():
    return {"body": {"creatives": [{"top_snap_media_id": "media-test",
        "profile_properties": {"profile_id": "profile-1"}}]}}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["account", "status", "type", "id", "deleted", "usage"])
async def test_fresh_creative_guard_rejects_changed_media(monkeypatch, mutation):
    monkeypatch.setattr(module, "available_public_profile_ids", AsyncMock(return_value={"profile-1"}))
    entity = {"id": "media-test", "ad_account_id": "account-test", "type": "VIDEO",
        "media_status": "READY", "media_usages": ["TOP_SNAP"]}
    replacements = {"account": ("ad_account_id", "foreign"), "status": ("media_status", "PENDING_UPLOAD"),
        "type": ("type", "LENS_PACKAGE"), "id": ("id", "other-media"),
        "deleted": ("deleted", True), "usage": ("media_usages", ["APP_INSTALL_ICON"])}
    key, value = replacements[mutation]
    entity[key] = value
    provider = SimpleNamespace(_request=AsyncMock(return_value={
        "request_status": "SUCCESS", "media": [{"sub_request_status": "SUCCESS", "media": entity}]}))
    with pytest.raises(HTTPException):
        await module.verify_creative_media_dependencies(provider, account_id="account-test", operation=creative_operation())
    provider._request.assert_awaited_once_with("GET", "/media/media-test")


@pytest.mark.asyncio
async def test_profile_membership_is_required_before_final_media_get(monkeypatch):
    reader = AsyncMock(return_value={"other-profile"})
    monkeypatch.setattr(module, "available_public_profile_ids", reader)
    provider = SimpleNamespace(_request=AsyncMock())
    with pytest.raises(HTTPException) as raised:
        await module.verify_creative_media_dependencies(provider, account_id="account-test", operation=creative_operation())
    assert raised.value.detail["code"] == "snapchat_media_profile_not_available_for_account"
    provider._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_dependency_proof_uses_current_provider_state(monkeypatch):
    monkeypatch.setattr(module, "available_public_profile_ids", AsyncMock(return_value={"profile-1"}))
    provider = SimpleNamespace(_request=AsyncMock(return_value={
        "request_status": "SUCCESS", "media": [{"sub_request_status": "SUCCESS", "media": {
            "id": "media-test", "ad_account_id": "account-test", "type": "VIDEO", "media_status": "READY"}}]}))
    result = await module.verify_creative_media_dependencies(provider, account_id="account-test", operation=creative_operation())
    assert result["verified"] is True and result["media_id"] == "media-test"
    assert result["profile_id"] == "profile-1" and result["account_id"] == "account-test"


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [
    "https://untrusted.example/steal", "https://[malformed", " ",
])
async def test_profile_catalog_uses_fixed_business_host_and_rejects_paging_escape(monkeypatch, next_link):
    urls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            urls.append(url)
            return httpx.Response(200, json={"request_status": "SUCCESS",
                "sharing_policies": [{"source": {"resource_type": "public_profiles", "resource_id": "profile-1"}}],
                "paging": {"next_link": next_link}})

    provider = SimpleNamespace(context=SimpleNamespace(access_token=AsyncMock(return_value="test-token")))
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    with pytest.raises(HTTPException):
        await module.available_public_profile_ids(provider, "account-test")
    assert urls == ["https://businessapi.snapchat.com/v1/adaccounts/account-test/sharing_policies"]
