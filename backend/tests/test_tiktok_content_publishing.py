"""Public creator workflow with real disposable Mongo and a fixed HTTP boundary."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import uuid

from fastapi import APIRouter, FastAPI, HTTPException
import httpx
import pytest
import pytest_asyncio

from integrations_control_center import tiktok_content_publishing as publishing
from integrations_control_center import tiktok_creator_accounts as accounts
from integrations_control_center.tiktok_native_reporting_routes import attach_tiktok_native_reporting_routes
from integrations_control_center.tiktok_connections import attach_tiktok_connection_routes
from test_tiktok_creator_accounts import CreatorProvider, environment, seed_creator


class ContentProvider(CreatorProvider):
    def __init__(self):
        super().__init__()
        self.properties = [{"property_type": 1, "url": "media.example.test", "property_status": 1, "signature": "private-signature"}]
        self.settings = {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"], "comment_disabled": True, "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 60}
        self.status = {"status": "PROCESSING_DOWNLOAD"}
        self.publish_calls = []
        self.post_started = asyncio.Event()
        self.post_release = None

    async def handle(self, request):
        path = request.url.path
        if path.endswith("/business/video/settings/"):
            assert request.method == "GET" and dict(request.url.params) == {"business_id": self.creator_id}
            data = self.settings
        elif path.endswith("/business/property/list/"):
            assert dict(request.url.params) == {"app_id": "fixture-app", "secret": "fixture-secret"}
            data = {"url_property_info_list": self.properties}
        elif path.endswith(("/business/video/publish/", "/business/photo/publish/")):
            assert request.method == "POST" and request.headers["Access-Token"] == "fixture-access-old"
            body = json.loads(request.content)
            assert body["business_id"] == self.creator_id
            self.publish_calls.append({"path": path, "body": body})
            self.post_started.set()
            if self.post_release:
                await self.post_release.wait()
            if self.behavior == "post_timeout":
                raise httpx.ReadTimeout("fixture transmission interrupted", request=request)
            data = {"share_id": None if self.behavior == "malformed_share" else "p_pub_url~v1.2345123456789123456"}
        elif path.endswith("/business/publish/status/"):
            assert request.method == "GET" and dict(request.url.params) == {"business_id": self.creator_id, "publish_id": "p_pub_url~v1.2345123456789123456"}
            data = self.status
        else:
            return await super().handle(request)
        assert request.url.host == "business-api.tiktok.com"
        self.calls.append({"method": request.method, "path": path, "body": json.loads(request.content) if request.content else None})
        return httpx.Response(200, json={"code": 0, "data": data})


@pytest_asyncio.fixture
async def workflow(environment, monkeypatch):
    db, _ = environment
    provider = ContentProvider()
    monkeypatch.setattr(accounts, "TikTokCreatorAPI", provider.api)
    monkeypatch.setattr(publishing, "TikTokCreatorAPI", provider.api)
    ref = await seed_creator(db)
    app = FastAPI()
    router = APIRouter(prefix="/api/integrations-v2")
    actor = {"id": "owner", "role": "owner"}

    async def current_user():
        return dict(actor)

    def require_owner(user):
        if user.get("role") != "owner":
            raise HTTPException(403, "owner required")
        return user

    attach_tiktok_connection_routes(router, db, current_user, require_owner)
    attach_tiktok_native_reporting_routes(router, db, current_user, require_owner)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mezan.example.test") as client:
        yield db, provider, ref, client, actor


BASE = "/api/integrations-v2/tiktok/content"


def content(ref, **changes):
    result = {"creator_ref": ref, "idempotency_key": uuid.uuid4().hex, "kind": "video", "delivery": "publish", "video_url": "https://cdn.media.example.test/products/video.mp4?signature=private-media-signature", "photo_urls": [], "video_duration_seconds": 10, "caption": "Product fixture", "privacy_level": "PUBLIC_TO_EVERYONE", "is_brand_organic": True, "is_branded_content": False, "is_ai_generated": False, "disable_comment": False, "disable_duet": False, "disable_stitch": False, "media_requirements_confirmed": True}
    return {**result, **changes}


async def preview(client, value):
    response = await client.post(BASE + "/proposals", json=value)
    assert response.status_code == 200, response.text
    return response.json()


async def approve(client, proposal):
    return await client.post(BASE + "/proposals/" + proposal["proposal_id"] + "/approve-and-publish", json={"confirmation_digest": proposal["confirmation_digest"]})


async def status_ready(db, proposal):
    await db[publishing.PROPOSALS].update_one({"_id": proposal["proposal_id"]}, {"$set": {"next_status_check_at": accounts.now() - timedelta(seconds=1)}})


@pytest.mark.asyncio
async def test_public_preview_approval_status_and_history_do_not_transfer_media(workflow, caplog):
    db, provider, ref, client, _ = workflow
    caplog.set_level("DEBUG")
    assert (await client.get(BASE + "/creators")).status_code == 200
    assert not provider.calls
    readiness = await client.post(BASE + f"/creators/{ref}/verify")
    assert readiness.status_code == 200 and readiness.json()["limits"]["photos"] == 10
    proof = readiness.json()
    assert "private-signature" not in json.dumps(proof) and "fixture-creator-id" not in json.dumps(proof)
    value = content(ref)
    proposal = await preview(client, value)
    assert proposal["status"] == "previewed" and not provider.publish_calls
    assert proposal["effective_post_info"]["disable_comment"] is True
    assert proposal["effective_post_info"]["disable_duet"] is True
    assert proposal["effective_post_info"]["disable_stitch"] is False
    row = await db[publishing.PROPOSALS].find_one({"_id": proposal["proposal_id"]})
    assert value["video_url"] not in str(row) and "Product fixture" not in str(row)
    response = await approve(client, proposal)
    assert response.status_code == 200 and response.json()["status"] == "accepted"
    assert response.json()["publish_task_id"] == "p_pub_url~v1.2345123456789123456" and response.json()["post_ids"] == []
    again = await approve(client, proposal)
    assert again.status_code == 200 and len(provider.publish_calls) == 1
    body = provider.publish_calls[0]["body"]
    assert body["video_url"] == value["video_url"] and "privacy_level" not in body["post_info"]
    provider.status = {"status": "PUBLISH_COMPLETE", "post_ids": ["1234567890"]}
    await status_ready(db, proposal)
    result = await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")
    assert result.status_code == 200 and result.json()["status"] == "published_public"
    assert result.json()["public_post_verified"] is True and result.json()["post_ids"] == ["1234567890"]
    assert await db[publishing.FENCES].count_documents({}) == 0
    reads_before = len(provider.calls)
    history = (await client.get(BASE + "/proposals?limit=12")).json()
    assert len(history["items"]) == 1 and len(provider.calls) == reads_before
    assert "video_url" not in json.dumps(history) and "confirmation_digest" not in json.dumps(history)
    assert "private-media-signature" not in caplog.text and "fixture-secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"video_url": "http://media.example.test/video.mp4"}, {"video_url": "https://127.0.0.1/video.mp4"}, {"video_url": "https://media.example.test/private/%252e%252e/video.mp4"}, {"video_url": "https://media.example.test/private%252fvideo.mp4"}, {"video_url": "https://user:pass@media.example.test/video.mp4"}, {"video_url": "https://media.example.test/video.mp4#fragment"}, {"video_url": "https://media.example.test/video.exe"}, {"video_duration_seconds": True}, {"caption": "😀" * 1101}, {"caption": "@x " * 31}, {"media_requirements_confirmed": False}, {"kind": "photo", "video_url": None, "video_duration_seconds": None, "photo_urls": ["https://media.example.test/a.jpg"] * 11}])
async def test_invalid_metadata_is_rejected_before_any_provider_request(workflow, changes):
    _, provider, ref, client, _ = workflow
    response = await client.post(BASE + "/proposals", json=content(ref, **changes))
    assert response.status_code == 422 and not provider.calls


@pytest.mark.asyncio
async def test_chunked_request_and_multipart_are_bounded_before_json_parsing(workflow):
    _, provider, _, client, _ = workflow

    async def chunks():
        for _ in range(5):
            yield b"x" * 8192

    result = await client.post(BASE + "/proposals", content=chunks(), headers={"Content-Type": "application/json"})
    assert result.status_code == 413 and not provider.calls
    result = await client.post(BASE + "/proposals", files={"video": ("video.mp4", b"small fixture")})
    assert result.status_code == 415 and not provider.calls


@pytest.mark.asyncio
async def test_only_verified_domain_or_prefix_with_boundary_allows_media(workflow):
    _, provider, ref, client, _ = workflow
    for address in ("https://media.example.test.evil.test/video.mp4", "https://evilmedia.example.test/video.mp4"):
        response = await client.post(BASE + "/proposals", json=content(ref, video_url=address))
        assert response.status_code == 409
    provider.properties = [{"property_type": 2, "url": "https://media.example.test/products/", "property_status": 1}]
    await preview(client, content(ref, video_url="https://media.example.test/products/video.mp4"))
    response = await client.post(BASE + "/proposals", json=content(ref, video_url="https://media.example.test/products-other/video.mp4"))
    assert response.status_code == 409
    provider.properties[0]["property_status"] = 0
    response = await client.post(BASE + "/proposals", json=content(ref, video_url="https://media.example.test/products/video.mp4"))
    assert response.status_code == 409 and not provider.publish_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["settings", "ownership", "scope", "epoch", "expired", "digest"])
async def test_execution_rechecks_exact_preview_and_never_sends_after_drift(workflow, change):
    db, provider, ref, client, _ = workflow
    proposal = await preview(client, content(ref))
    if change == "settings":
        provider.settings["stitch_disabled"] = True
    elif change == "ownership":
        provider.properties[0]["property_status"] = 0
    elif change == "scope":
        provider.scopes = "video.list"
    elif change == "epoch":
        await db[accounts.CREDENTIALS].update_one({"_id": ref}, {"$set": {"auth_epoch": "different-authorization"}})
    elif change == "expired":
        await db[publishing.PROPOSALS].update_one({"_id": proposal["proposal_id"]}, {"$set": {"expires_at": accounts.now() - timedelta(seconds=1)}})
    else:
        proposal["confirmation_digest"] = "0" * 64
    response = await approve(client, proposal)
    assert response.status_code == 409 and not provider.publish_calls
    assert await db[publishing.FENCES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_tenant_and_owner_are_checked_at_each_public_route(workflow):
    _, provider, ref, client, actor = workflow
    proposal = await preview(client, content(ref))
    calls = len(provider.calls)
    actor["id"] = "foreign-owner"
    assert (await approve(client, proposal)).status_code == 404
    assert (await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")).status_code == 404
    assert (await client.post(BASE + f"/creators/{ref}/verify")).status_code == 409
    assert len(provider.calls) == calls
    actor["role"] = "staff"
    assert (await approve(client, proposal)).status_code == 403
    assert (await client.get(BASE + "/creators")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior", ["post_timeout", "malformed_share"])
async def test_ambiguous_publish_blocks_retry_including_a_new_key(workflow, behavior):
    db, provider, ref, client, _ = workflow
    proposal = await preview(client, content(ref))
    provider.behavior = behavior
    result = await approve(client, proposal)
    assert result.status_code == 200 and result.json()["status"] == "uncertain"
    assert (await approve(client, proposal)).json()["status"] == "uncertain"
    provider.behavior = None
    second = await preview(client, content(ref))
    assert (await approve(client, second)).status_code == 409
    result = await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")
    assert result.json()["status"] == "uncertain"
    assert len(provider.publish_calls) == 1 and await db[publishing.FENCES].count_documents({}) == 1


@pytest.mark.asyncio
async def test_distributed_double_approval_and_different_proposal_share_durable_fence(workflow, monkeypatch):
    db, provider, ref, client, _ = workflow
    first = await preview(client, content(ref))
    second = await preview(client, content(ref, caption="Second product"))

    @asynccontextmanager
    async def separate_workers():
        yield

    # Simulate distinct API workers, which cannot share a process semaphore.
    monkeypatch.setattr(publishing, "admission", separate_workers)
    provider.post_release = asyncio.Event()
    job = asyncio.create_task(approve(client, first))
    await asyncio.wait_for(provider.post_started.wait(), 3)
    repeated = await approve(client, first)
    competing = await approve(client, second)
    assert repeated.status_code == 200 and repeated.json()["status"] == "submitted"
    assert competing.status_code == 409
    provider.post_release.set()
    result = await job
    assert result.json()["status"] == "accepted" and len(provider.publish_calls) == 1
    assert await db[publishing.FENCES].count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("minute,day", [(6, 6), (0, 15)])
async def test_creator_combined_video_photo_quota_is_reserved_before_post(workflow, minute, day):
    db, provider, ref, client, _ = workflow
    stamps = [accounts.now() - timedelta(seconds=20)] * minute + [accounts.now() - timedelta(hours=1)] * (day - minute)
    await db[publishing.QUOTAS].insert_one({"_id": ref, "revision": 1, "reservations": stamps})
    proposal = await preview(client, content(ref))
    result = await approve(client, proposal)
    assert result.status_code == 429 and not provider.publish_calls
    assert await db[publishing.FENCES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["video", "photo"])
async def test_draft_delivery_is_not_publication_and_effective_fields_are_explicit(workflow, kind):
    db, provider, ref, client, _ = workflow
    changes = {"kind": kind, "delivery": "draft"}
    if kind == "photo":
        changes.update({"video_url": None, "video_duration_seconds": None, "photo_urls": ["https://media.example.test/products/photo.jpg"], "title": "Draft title"})
    proposal = await preview(client, content(ref, **changes))
    assert proposal["draft_notice"]
    assert (await approve(client, proposal)).json()["status"] == "accepted"
    info = provider.publish_calls[0]["body"]["post_info"]
    if kind == "video":
        assert info == {"upload_to_draft": True}
    else:
        assert info["is_draft"] is True and info["title"] == "Draft title"
    provider.status = {"status": "SEND_TO_USER_INBOX", "post_ids": ["999"]}
    await status_ready(db, proposal)
    result = (await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")).json()
    assert result["status"] == "draft_delivered" and result["post_ids"] == [] and result["public_post_verified"] is False
    assert await db[publishing.FENCES].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy", ["PUBLIC_TO_EVERYONE", "SELF_ONLY"])
async def test_photo_privacy_and_delayed_public_post_ids_are_not_fabricated(workflow, privacy):
    db, provider, ref, client, _ = workflow
    proposal = await preview(client, content(ref, kind="photo", video_url=None, video_duration_seconds=None, photo_urls=["https://media.example.test/product.webp"], title="Product photo", privacy_level=privacy))
    assert (await approve(client, proposal)).status_code == 200
    body = provider.publish_calls[0]["body"]
    assert body["photo_images"] == ["https://media.example.test/product.webp"]
    assert body["post_info"]["privacy_level"] == privacy
    assert "is_ai_generated" not in body["post_info"]
    provider.status = {"status": "PUBLISH_COMPLETE", "post_ids": []}
    await status_ready(db, proposal)
    response = await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == ("complete_pending_ids" if privacy == "PUBLIC_TO_EVERYONE" else "published_private")
    assert result["public_post_verified"] is False and result["post_ids"] == []
    if privacy == "PUBLIC_TO_EVERYONE":
        throttled = await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")
        assert throttled.status_code == 429


@pytest.mark.asyncio
async def test_owner_key_is_immutable_and_failed_provider_status_releases_only_own_fence(workflow):
    db, provider, ref, client, _ = workflow
    value = content(ref)
    proposal = await preview(client, value)
    before = len(provider.calls)
    same = await preview(client, value)
    assert same["proposal_id"] == proposal["proposal_id"] and len(provider.calls) == before
    conflict = await client.post(BASE + "/proposals", json={**value, "caption": "Different approval"})
    assert conflict.status_code == 409
    assert (await approve(client, proposal)).status_code == 200
    provider.status = {"status": "FAILED", "reason": "frame_rate_check_failed"}
    await status_ready(db, proposal)
    result = (await client.get(BASE + f"/proposals/{proposal['proposal_id']}/status")).json()
    assert result["status"] == "failed" and result["safe_failure_reason"] == "frame_rate_check_failed"
    assert await db[publishing.FENCES].count_documents({}) == 0
    assert (await approve(client, proposal)).json()["status"] == "failed" and len(provider.publish_calls) == 1
