"""Real disposable Mongo + fixed provider transport; zero external calls."""
from datetime import timedelta
import json

import customer_identity
from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
import pytest
import pytest_asyncio

from customer_intelligence.foundation import CHANNELS_COLLECTION, CONVERSATION_MESSAGES_COLLECTION
from customer_intelligence.inbox import CustomerIntelligenceInboxService
from integrations_control_center import tiktok_comments_ingress as comments
from integrations_control_center import tiktok_creator_accounts as accounts
from tests.test_tiktok_creator_accounts import environment, seed_creator, CreatorProvider
from tests.test_tiktok_content_routes import app_for

VIDEO = "6990565363377392901"
COMMENT = "6990565363377392999"


class CommentProvider(CreatorProvider):
    def __init__(self):
        super().__init__()
        self.scopes = "video.list,comment.list"
        self.videos = [{"item_id": VIDEO, "media_type": "VIDEO", "caption": "منتج الاختبار", "thumbnail_url": "https://never-fetch.example.test/video.jpg"}]
        self.comments = [{"video_id": VIDEO, "comment_id": COMMENT, "unique_identifier": "+stableUser/identity=", "user_id": "deprecated-identity", "display_name": "عميل الاختبار", "username": "do-not-store-handle", "profile_image": "https://never-fetch.example.test/avatar.jpg?signature=private", "text": "هل المنتج متوفر؟", "create_time": str(int(accounts.now().timestamp()) - 30), "status": "PUBLIC", "owner": False}]
        self.more = False
        self.next_cursor = 0

    async def handle(self, request):
        if not request.url.path.endswith(("/business/video/list/", "/business/comment/list/")):
            return await super().handle(request)
        assert request.method == "GET" and request.url.host == "business-api.tiktok.com"
        assert request.headers["Accept-Encoding"] == "identity"
        assert request.headers["Access-Token"] == "fixture-access-old"
        params = dict(request.url.params)
        assert params["business_id"] == self.creator_id
        self.calls.append({"method": request.method, "path": request.url.path, "params": params})
        if request.url.path.endswith("/business/video/list/"):
            fields = json.loads(params["fields"])
            assert "item_id" in fields and set(fields) <= {"item_id", "media_type", "caption"}
            data = {"videos": self.videos, "cursor": 0, "has_more": False}
        else:
            assert params["video_id"] == VIDEO and params["status"] == "PUBLIC"
            assert params["include_replies"] == "false" and int(params["max_count"]) == 20
            data = {"comments": self.comments, "cursor": self.next_cursor, "has_more": self.more}
        return httpx.Response(200, json={"code": 0, "data": data})


@pytest_asyncio.fixture
async def comment_environment(environment, monkeypatch):
    db, _ = environment
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", "fixture-customer-identity-key")
    monkeypatch.setenv("MEZAN_CHANNEL_BINDING_HMAC_KEY", "fixture-channel-key")
    customer_identity._fernet = None
    provider = CommentProvider()
    monkeypatch.setattr(comments, "TikTokCreatorAPI", provider.api)
    ref = await seed_creator(db)
    await db.salla_integrations.insert_one({"user_id": "owner", "store_id": "fixture-store", "status": "connected"})
    try:
        yield db, provider, ref
    finally:
        customer_identity._fernet = None


async def bind(db, ref):
    return await comments.connect_comments(db, "owner", ref, comments.CommentConnect(confirm_receive_only=True))


@pytest.mark.asyncio
async def test_verified_comments_reach_real_encrypted_gateway_and_existing_inbox_without_ai_queue(comment_environment, monkeypatch):
    db, provider, ref = comment_environment
    result = await bind(db, ref)
    assert result["source"] == "owner_initiated_api" and result["ai_auto_reply_allowed"] is False
    channel = await db[CHANNELS_COLLECTION].find_one({"provider": "tiktok"})
    assert channel["webhook_subscription_status"] is None
    assert channel["send_allowed"] is False
    page = await comments.list_comment_posts(db, "owner", ref)
    assert page["items"] == [{"video_id": VIDEO, "media_type": "VIDEO", "caption": "منتج الاختبار"}]
    pulled = await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert pulled["imported"] == 1 and pulled["duplicates"] == 0
    stored = await db[CONVERSATION_MESSAGES_COLLECTION].find_one({})
    assert stored["analysis_status"] == "not_requested"
    assert "هل المنتج متوفر؟" not in str(stored)
    assert COMMENT not in str(stored) and "stableUser" not in str(stored)
    private = customer_identity.decrypt_private_payload(stored["content_ciphertext"])
    assert private["payload"]["tiktok_comment"] == {"video_id": VIDEO, "comment_id": COMMENT}
    inbox = (await CustomerIntelligenceInboxService(db).inbox(owner_user_id="owner")).model_dump(mode="json")
    assert inbox["conversations"][0]["channel"] == "tiktok"
    assert inbox["conversations"][0]["surface"] == "comment"
    assert inbox["conversations"][0]["messages"][0]["body"] == "هل المنتج متوفر؟"
    assert "stableUser" not in str(inbox) and "never-fetch" not in str(inbox)
    monkeypatch.setattr(comments, "now", lambda: accounts.now() + timedelta(seconds=30))
    repeated = await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert repeated["imported"] == 0 and repeated["duplicates"] == 1
    assert await db[CONVERSATION_MESSAGES_COLLECTION].count_documents({}) == 1
    assert all(call["path"].endswith(("/tt_user/token_info/get/", "/business/video/list/", "/business/comment/list/")) for call in provider.calls)


@pytest.mark.asyncio
async def test_real_public_route_imports_a_bounded_page_with_no_store_response(comment_environment):
    db, _, ref = comment_environment
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"}, db)), base_url="http://test") as client:
        root = f"/api/integrations-v2/tiktok/content/creators/{ref}/comments"
        connected = await client.post(root + "/connect", json={"confirm_receive_only": True})
        assert connected.status_code == 200 and connected.headers["cache-control"] == "no-store"
        pulled = await client.post(root + "/pull", json={"video_id": VIDEO})
    assert pulled.status_code == 200 and pulled.json()["imported"] == 1
    assert pulled.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_stored_scopes_cannot_replace_fresh_provider_scope_evidence(comment_environment):
    db, provider, ref = comment_environment
    await bind(db, ref)
    provider.scopes = "video.list"
    with pytest.raises(HTTPException) as exc:
        await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert exc.value.detail["code"] == "tiktok_creator_missing_permission"
    assert not any(call["path"].endswith("/business/comment/list/") for call in provider.calls)
    assert await db[CONVERSATION_MESSAGES_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_another_owner_cannot_bind_or_pull_this_creator(comment_environment):
    db, provider, ref = comment_environment
    with pytest.raises(HTTPException):
        await comments.connect_comments(db, "foreign-owner", ref, comments.CommentConnect(confirm_receive_only=True))
    assert provider.calls == []
    await bind(db, ref)
    before = len(provider.calls)
    with pytest.raises(HTTPException):
        await comments.pull_comments(db, "foreign-owner", ref, comments.CommentPull(video_id=VIDEO))
    assert len(provider.calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("second_store", ["other-store", "fixture-store"])
async def test_ambiguous_salla_store_binding_is_rejected(comment_environment, second_store):
    db, _, ref = comment_environment
    await db.salla_integrations.insert_one({"user_id": "owner", "status": "connected", "store_id": second_store})
    with pytest.raises(HTTPException) as exc:
        await bind(db, ref)
    assert exc.value.detail["code"] == "tiktok_comments_unique_store_required"
    assert await db[CHANNELS_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_video_id_is_freshly_proven_owned_before_any_comment_read(comment_environment):
    db, provider, ref = comment_environment
    await bind(db, ref)
    provider.videos = []
    with pytest.raises(HTTPException) as exc:
        await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert exc.value.detail["code"] == "tiktok_comments_owned_video_required"
    assert not any(call["path"].endswith("/business/comment/list/") for call in provider.calls)


@pytest.mark.asyncio
async def test_provider_read_throttle_is_durable_and_no_background_retry_occurs(comment_environment):
    db, provider, ref = comment_environment
    await bind(db, ref)
    await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    before = len(provider.calls)
    with pytest.raises(HTTPException) as exc:
        await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert exc.value.status_code == 429
    # Fresh token_info is allowed; no extra post or comment page is read.
    assert len(provider.calls) == before + 1
    assert provider.calls[-1]["path"].endswith("/tt_user/token_info/get/")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign_video", "missing_identity", "future_timestamp", "too_many", "bad_cursor"])
async def test_malformed_bounded_page_is_rejected_before_any_message_persistence(comment_environment, change):
    db, provider, ref = comment_environment
    await bind(db, ref)
    row = dict(provider.comments[0])
    if change == "foreign_video": row["video_id"] = "12345"
    if change == "missing_identity": row.pop("unique_identifier")
    if change == "future_timestamp": row["create_time"] = str(int(accounts.now().timestamp()) + 3600)
    provider.comments.append(row)
    if change == "too_many": provider.comments = [row] * 21
    if change == "bad_cursor": provider.more = True; provider.next_cursor = 0
    with pytest.raises(HTTPException) as exc:
        await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert exc.value.status_code == 502
    assert await db[CONVERSATION_MESSAGES_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_owner_hidden_and_image_only_comments_are_not_customer_ai_evidence(comment_environment):
    db, provider, ref = comment_environment
    await bind(db, ref)
    row = provider.comments[0]
    provider.comments += [{**row, "owner": True}, {**row, "status": "HIDDEN"}, {**row, "text": "", "image_url": "https://never-fetch.example.test/customer.jpg"}]
    result = await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert result["imported"] == 1 and result["skipped"] == 3
    assert await db[CONVERSATION_MESSAGES_COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_unsafe_out_of_band_channel_edits_fail_closed(comment_environment):
    db, provider, ref = comment_environment
    await bind(db, ref)
    await db[CHANNELS_COLLECTION].update_one({"provider": "tiktok"}, {"$set": {"send_allowed": True}})
    with pytest.raises(HTTPException) as exc:
        await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    assert exc.value.detail["code"] == "tiktok_comments_binding_conflict"
    assert not any(call["path"].endswith("/business/comment/list/") for call in provider.calls)
