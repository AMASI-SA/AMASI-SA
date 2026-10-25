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
        self.catalog = []
        self.catalog_reads = 0
        self.catalog_hook = None

    async def management_role(self, account, action):
        assert action == "creative.create"
        self.roles += 1
        if self.role_hook:
            await self.role_hook(self.roles)
        return {"allowed": self.allowed}

    async def create_media(self, row):
        self.creates += 1
        self.medium = {"id": "media-test", "ad_account_id": row["account_id"],
            "type": "VIDEO", "name": row["provider_name"], "media_status": "PENDING_UPLOAD",
            "created_at": module._iso()}
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

    async def list_account_media_for_recovery(self, account_id):
        assert account_id == "account-test"
        self.catalog_reads += 1
        if self.catalog_hook:
            await self.catalog_hook(self.catalog_reads)
        return deepcopy(self.catalog)


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
@pytest.mark.parametrize("duration", [180, 181, 1800])
async def test_top_snap_duration_metadata_accepts_up_to_1800_seconds(duration):
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    original = provider.upload_media

    async def upload_with_duration(media_id, data, content_type):
        await original(media_id, data, content_type)
        provider.medium["duration_in_seconds"] = duration
        return deepcopy(provider.medium)

    provider.upload_media = upload_with_duration
    result = await upload(db, provider, preview)
    assert result["status"] == "ready" and result["ready_for_creative"] is True
    assert (provider.creates, provider.uploads, provider.reads) == (1, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [1801, float("nan"), float("inf"), float("-inf")])
async def test_top_snap_duration_metadata_rejects_over_limit_and_nonfinite_values(duration):
    db, provider = DB(), Provider()
    preview = await prepared(db, provider)
    original = provider.upload_media

    async def upload_with_duration(media_id, data, content_type):
        await original(media_id, data, content_type)
        provider.medium["duration_in_seconds"] = duration
        return deepcopy(provider.medium)

    provider.upload_media = upload_with_duration
    with pytest.raises(HTTPException) as raised:
        await upload(db, provider, preview)
    assert raised.value.detail["code"] == "snapchat_media_video_specs_conflict"
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "upload_unknown" and row["provider_readback_verified"] is False
    assert (provider.creates, provider.uploads) == (1, 1)


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


async def lost_create(db, provider):
    """The provider accepted create, but its response never reached our caller."""
    provider.create_timeout = True
    preview = await prepared(db, provider)
    with pytest.raises(HTTPException):
        await upload(db, provider, preview)
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "create_unknown" and row["source_verified"] is True
    provider.catalog = [deepcopy(provider.medium)]
    return preview


@pytest.mark.asyncio
async def test_single_pending_create_recovery_requires_new_confirmation_and_one_source_upload():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    recovered = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert recovered["status"] == "awaiting_file" and recovered["can_upload"] is True
    assert recovered["provider_media_id"] == "media-test"
    assert recovered["ready_for_creative"] is False
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["confirm_token_hash"] is None and row["expires_at"] is None
    assert row["provider_upload_attempted"] is False
    assert row["create_reconciliation"]["catalog_complete"] is True
    assert (provider.creates, provider.uploads, provider.catalog_reads) == (1, 0, 1)
    with pytest.raises(HTTPException):
        await upload(db, provider, preview)
    confirmed = await prepared(db, provider)
    assert confirmed["upload_id"] == preview["upload_id"] and confirmed["confirm_token"] != preview["confirm_token"]
    result = await upload(db, provider, confirmed)
    assert result["ready_for_creative"] is True and provider.sent == DATA
    assert (provider.creates, provider.uploads) == (1, 1)
    assert (await upload(db, provider, confirmed))["status"] == "ready"
    assert (provider.creates, provider.uploads) == (1, 1)


@pytest.mark.asyncio
async def test_recovered_media_rechecks_actual_digest_before_any_file_post():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    confirmed = await prepared(db, provider)
    with pytest.raises(HTTPException) as raised:
        await upload(db, provider, confirmed, DATA[:-1] + b"x")
    assert raised.value.detail["code"] == "snapchat_media_file_digest_mismatch"
    assert (provider.creates, provider.uploads) == (1, 0)
    renewed = await prepared(db, provider)
    assert (await upload(db, provider, renewed))["ready_for_creative"] is True
    assert (provider.creates, provider.uploads) == (1, 1)


@pytest.mark.asyncio
async def test_duplicate_exact_provider_names_are_ambiguous_even_if_one_is_newer():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    duplicate = {**provider.medium, "id": "media-other", "created_at": module._iso()}
    provider.catalog.append(duplicate)
    result = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert result["status"] == "create_unknown"
    assert result["error_code"] == "snapchat_media_create_ambiguous"
    assert result["provider_media_id"] is None and result["can_upload"] is False
    assert (provider.creates, provider.uploads, provider.reads) == (1, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"media_status": "READY"}, {"file_name": "previous.mp4"}, {"hash": "provider-hash"},
    {"download_link": "https://storage.example/previous.mp4"}, {"file_size_in_bytes": 1},
    {"duration_in_seconds": 5}, {"video_metadata": {"width_px": 1080}},
    {"visibility": "HIDDEN"}, {"ai_content_source": "SNAP_AI_GENERATED"},
    {"created_at": None}, {"created_at": "2000-01-01T00:00:00+00:00"},
])
async def test_unknown_create_refuses_ready_uploaded_hidden_or_unverifiable_candidates(change):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    provider.catalog[0].update(change)
    result = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert result["status"] == "create_unknown" and result["provider_media_id"] is None
    assert result["can_upload"] is False and result["ready_for_creative"] is False
    assert (provider.creates, provider.uploads, provider.reads) == (1, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"ad_account_id": "foreign-account"}, {"type": "IMAGE"}, {"deleted": True},
])
async def test_unknown_create_refuses_conflicting_candidate_identity(change):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    provider.catalog[0].update(change)
    with pytest.raises(HTTPException):
        await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "create_unknown" and row["provider_media_id"] is None
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"media_status": "READY"}, {"name": "someone-elses-video"}, {"file_name": "already.mp4"},
])
async def test_fresh_exact_media_get_must_still_match_pending_catalog_candidate(change):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    provider.medium.update(change)
    result = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert result["status"] == "create_unknown" and result["provider_media_id"] is None
    assert (provider.creates, provider.uploads, provider.reads) == (1, 0, 1)


@pytest.mark.asyncio
async def test_fresh_exact_media_get_cannot_change_the_account():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    provider.medium["ad_account_id"] = "foreign-account"
    with pytest.raises(HTTPException):
        await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert (await module._load(db, "owner", preview["upload_id"]))["provider_media_id"] is None
    assert (provider.creates, provider.uploads, provider.reads) == (1, 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("provider_name", "manual-name"), ("request_fingerprint", "invalid"),
    ("source_verified", False), ("provider_upload_attempted", True),
])
async def test_recovery_requires_immutable_server_create_intent(field, value):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    module._jobs(db).rows[preview["upload_id"]][field] = value
    with pytest.raises(HTTPException) as raised:
        await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert raised.value.detail["code"] == "snapchat_media_recovery_intent_unverified"
    assert (provider.creates, provider.uploads, provider.catalog_reads) == (1, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["catalog", "exact_get"])
@pytest.mark.parametrize("change", ["role", "account"])
async def test_recovery_rechecks_role_and_selected_account_before_adoption(monkeypatch, boundary, change):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    account_selected = True

    async def selected(db, user_id, account_id):
        if not account_selected:
            raise HTTPException(409, detail={"code": "account_not_selected"})
        return {"ad_account_id": account_id}

    def revoke():
        nonlocal account_selected
        if change == "role":
            provider.allowed = False
        else:
            account_selected = False

    async def after_catalog(count):
        revoke()

    original = provider.read_media

    async def after_get(media_id):
        entity = await original(media_id)
        revoke()
        return entity

    monkeypatch.setattr(module, "_selected_account", selected)
    if boundary == "catalog":
        provider.catalog_hook = after_catalog
    else:
        provider.read_media = after_get
    with pytest.raises(HTTPException):
        await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "create_unknown" and row["provider_media_id"] is None
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
async def test_recovery_reads_remain_available_with_kill_switch_but_file_post_is_blocked(monkeypatch):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    monkeypatch.setattr(module, "snapchat_campaign_mutations_enabled", lambda: False)
    result = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert result["status"] == "awaiting_file" and result["ready_for_creative"] is False
    confirmed = await prepared(db, provider)
    with pytest.raises(HTTPException) as raised:
        await upload(db, provider, confirmed)
    assert raised.value.detail["code"] == "snapchat_campaign_mutations_disabled"
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
async def test_foreign_owner_cannot_reconcile_or_scan_account_catalog():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    with pytest.raises(HTTPException) as raised:
        await module.reconcile_media_upload(db, "foreign-owner", preview["upload_id"], provider=provider)
    assert raised.value.status_code == 404 and provider.catalog_reads == 0


@pytest.mark.asyncio
async def test_concurrent_catalog_recovery_has_one_cas_winner_and_fences_old_worker():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    old = await module._load(db, "owner", preview["upload_id"])
    both_reading = asyncio.Event()

    async def barrier(count):
        if count == 2:
            both_reading.set()
        await both_reading.wait()

    provider.catalog_hook = barrier
    results = await asyncio.wait_for(asyncio.gather(
        module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider),
        module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider),
        return_exceptions=True), timeout=1)
    assert sum(isinstance(item, dict) and item["status"] == "awaiting_file" for item in results) == 1
    assert sum(isinstance(item, HTTPException) and item.detail["code"] == "snapchat_media_upload_state_changed"
        for item in results) == 1
    with pytest.raises(HTTPException):
        await module._state(db, old, {"status": "creating", "provider_media_id": None})
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
async def test_stale_creating_claim_can_recover_pending_media_and_old_claim_cannot_resume():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    row = await module._load(db, "owner", preview["upload_id"])
    stale = await module._state(db, row, {"status": "creating", "claim_id": "stale-worker",
        "attempt_started_at": (module._now() - timedelta(seconds=181)).isoformat()})
    result = await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    assert result["status"] == "awaiting_file" and result["provider_media_id"] == "media-test"
    with pytest.raises(HTTPException):
        await module._state(db, stale, {"status": "media_created"})
    assert (provider.creates, provider.uploads) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"media_status": "READY"}, {"file_name": "other.mp4"}, {"name": "renamed"}])
async def test_recovered_media_is_freshly_rechecked_before_explicit_binary_upload(change):
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    confirmed = await prepared(db, provider)
    provider.medium.update(change)
    with pytest.raises(HTTPException):
        await upload(db, provider, confirmed)
    assert (provider.creates, provider.uploads) == (1, 0)


def catalog_entity(media_id="media-1"):
    return {"id": media_id, "name": "Unrelated media", "ad_account_id": "account-test",
        "type": "VIDEO", "media_status": "PENDING_UPLOAD", "created_at": module._iso()}


def catalog_page(*entities, next_link=None):
    page = {"request_status": "SUCCESS", "media": [
        {"sub_request_status": "SUCCESS", "media": entity} for entity in entities]}
    if next_link is not None:
        page["paging"] = {"next_link": next_link}
    return page


@pytest.mark.asyncio
async def test_real_catalog_reader_consumes_all_pages_using_only_fixed_account_gets():
    provider = module.SnapchatMediaProvider(DB(), "owner")
    next_link = module.SNAPCHAT_API_BASE + "/adaccounts/account-test/media?cursor=page-2&limit=1000"
    provider._request = AsyncMock(side_effect=[
        catalog_page(catalog_entity(), next_link=next_link), catalog_page(catalog_entity("media-2"))])
    rows = await provider.list_account_media_for_recovery("account-test")
    assert {row["id"] for row in rows} == {"media-1", "media-2"}
    assert provider._request.await_count == 2
    assert provider._request.await_args_list[0].args == ("GET", "/adaccounts/account-test/media")
    assert provider._request.await_args_list[0].kwargs == {"params": {"limit": 1000}}
    assert provider._request.await_args_list[1].args == ("GET", "/adaccounts/account-test/media?cursor=page-2&limit=1000")
    assert provider._request.await_args_list[1].kwargs == {"params": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [
    "https://foreign.example/v1/adaccounts/account-test/media?cursor=x",
    "https://adsapi.snapchat.com/v1/adaccounts/foreign/media?cursor=x",
    "/v1/adaccounts/account-test/media?cursor=x",
    "https://adsapi.snapchat.com/v1/adaccounts/account-test/media?cursor=x#fragment",
    "https://adsapi.snapchat.com/v1/adaccounts/account-test/media?cursor=x&type=VIDEO",
    "https://adsapi.snapchat.com/v1/adaccounts/account-test/media?limit=1000",
    "https://adsapi.snapchat.com/v1/adaccounts/account-test/media?cursor=x&cursor=y",
    "https://adsapi.snapchat.com/v1/adaccounts/account-test/media?cursor=x&limit=1001",
    "https://[malformed",
])
async def test_catalog_pagination_cannot_escape_or_silently_filter_the_account(next_link):
    provider = module.SnapchatMediaProvider(DB(), "owner")
    provider._request = AsyncMock(return_value=catalog_page(catalog_entity(), next_link=next_link))
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")
    assert provider._request.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [
    {"request_status": "PARTIAL", "media": []},
    {"request_status": "SUCCESS", "media": [{"sub_request_status": "ERROR"}]},
    {"request_status": "SUCCESS", "media": [{}]},
    {"request_status": "SUCCESS", "media": None},
    {"request_status": "SUCCESS", "media": [], "paging": {"has_more": True}},
    {"request_status": "SUCCESS", "media": [], "paging": {"next_page_id": "unread-page"}},
])
async def test_partial_or_unread_catalog_is_not_complete(page):
    provider = module.SnapchatMediaProvider(DB(), "owner")
    provider._request = AsyncMock(return_value=page)
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")
    assert provider._request.await_count == 1


@pytest.mark.asyncio
async def test_catalog_page_and_row_bounds_refuse_incomplete_results(monkeypatch):
    provider = module.SnapchatMediaProvider(DB(), "owner")
    monkeypatch.setattr(module, "MEDIA_RECOVERY_MAX_PAGES", 2)
    provider._request = AsyncMock(side_effect=[
        catalog_page(catalog_entity(), next_link=module.SNAPCHAT_API_BASE + "/adaccounts/account-test/media?cursor=2"),
        catalog_page(catalog_entity("media-2"), next_link=module.SNAPCHAT_API_BASE + "/adaccounts/account-test/media?cursor=3"),
    ])
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")
    assert provider._request.await_count == 2
    monkeypatch.setattr(module, "MEDIA_RECOVERY_MAX_ROWS", 2)
    provider._request = AsyncMock(return_value=catalog_page(
        catalog_entity(), catalog_entity("media-2"), catalog_entity("media-3")))
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")
    assert provider._request.await_count == 1


@pytest.mark.asyncio
async def test_repeated_page_or_media_id_never_proves_catalog_uniqueness():
    provider = module.SnapchatMediaProvider(DB(), "owner")
    next_link = module.SNAPCHAT_API_BASE + "/adaccounts/account-test/media?cursor=loop"
    provider._request = AsyncMock(return_value=catalog_page(catalog_entity(), next_link=next_link))
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")
    assert provider._request.await_count == 2
    provider._request = AsyncMock(return_value=catalog_page(catalog_entity(), catalog_entity()))
    with pytest.raises(HTTPException):
        await provider.list_account_media_for_recovery("account-test")


@pytest.mark.asyncio
async def test_incomplete_catalog_exception_cannot_adopt_an_earlier_candidate():
    db, provider = DB(), Provider()
    preview = await lost_create(db, provider)
    provider.list_account_media_for_recovery = AsyncMock(side_effect=module._failure(409, "snapchat_media_catalog_limit"))
    with pytest.raises(HTTPException):
        await module.reconcile_media_upload(db, "owner", preview["upload_id"], provider=provider)
    row = await module._load(db, "owner", preview["upload_id"])
    assert row["status"] == "create_unknown" and row["provider_media_id"] is None
    assert (provider.creates, provider.uploads, provider.reads) == (1, 0, 0)
