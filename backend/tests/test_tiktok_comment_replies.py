"""Native owner-approved text replies; no live provider/model calls."""
import httpx
import pytest
import pytest_asyncio
import json
from datetime import timedelta

from customer_identity import encrypt_private_payload
from customer_intelligence.foundation import CONVERSATION_MESSAGES_COLLECTION
from customer_intelligence.reply_suggestions import REPLY_SUGGESTIONS_COLLECTION
from integrations_control_center import tiktok_comment_replies as replies
from integrations_control_center import tiktok_comments_ingress as comments
from integrations_control_center import tiktok_creator_accounts as accounts
from tests.test_tiktok_comments_ingress import comment_environment, bind, VIDEO, COMMENT, CommentProvider
from tests.test_tiktok_creator_accounts import environment

from tests.test_tiktok_content_routes import app_for

ROOT = "/api/integrations-v2/tiktok/content/comment-replies"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("/preview", {"conversation_id": "thread", "suggestion_id": "draft", "version": 1, "text": "كودك AM"}),
    ("/" + "a" * 32 + "/execute", {"confirmation_digest": "b" * 64}),
    ("/" + "a" * 32 + "/status", {}),
    ("/history", {"conversation_id": "thread"}),
])
async def test_reply_routes_reject_staff_before_any_database_or_provider(path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff", "role": "staff"})), base_url="http://test") as client:
        response = await client.post(ROOT + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reply_preview_has_typed_bounded_public_seam():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post(ROOT + "/preview", json={})
        oversized = await client.post(ROOT + "/preview", content=b'{"text":"' + b'x' * 32768 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert oversized.status_code == 413


class ReplyProvider(CommentProvider):
    def __init__(self):
        super().__init__()
        self.scopes += ",comment.list.manage"
        self.writes = []
        self.receipt = None
        self.fail_write = None
        self.visibility = "PUBLIC"
        self.before_parent = None

    async def handle(self, request):
        if request.url.path.endswith("/business/comment/reply/create/"):
            body = json.loads(request.content)
            assert request.method == "POST" and request.url.host == "business-api.tiktok.com"
            assert request.headers["Access-Token"] == "fixture-access-old"
            assert body["business_id"] == self.creator_id and body["video_id"] == VIDEO and body["comment_id"] == COMMENT
            self.writes.append(body)
            if self.fail_write == "timeout":
                raise httpx.ReadTimeout("fixture ambiguous response", request=request)
            self.receipt = {"comment_id": "6990565363377393000", "parent_comment_id": COMMENT,
                "video_id": VIDEO, "text": body["text"]}
            result = {**self.receipt}
            if self.fail_write == "mismatch":
                result["parent_comment_id"] = "123"
            return httpx.Response(200, json={"code": 0, "data": result})
        if request.url.path.endswith("/business/comment/list/") and int(request.url.params["max_count"]) == 1:
            assert request.method == "GET" and request.url.params["include_replies"] == "false"
            ids = json.loads(request.url.params["comment_ids"])
            if request.url.params["status"] == "ALL":
                assert ids == [self.receipt["comment_id"]]
                rows = [{**self.receipt, "owner": True, "status": self.visibility}]
            else:
                assert ids == [COMMENT]
                if self.before_parent:
                    await self.before_parent()
                rows = self.comments
            return httpx.Response(200, json={"code": 0, "data": {"comments": rows}})
        return await super().handle(request)


@pytest_asyncio.fixture
async def reply_environment(comment_environment, monkeypatch):
    db, _, ref = comment_environment
    provider = ReplyProvider()
    monkeypatch.setattr(comments, "TikTokCreatorAPI", provider.api)
    monkeypatch.setattr(replies, "TikTokCreatorAPI", provider.api)
    await bind(db, ref)
    await comments.pull_comments(db, "owner", ref, comments.CommentPull(video_id=VIDEO))
    source = await db[CONVERSATION_MESSAGES_COLLECTION].find_one({})
    await db[REPLY_SUGGESTIONS_COLLECTION].insert_one({
        "user_id": "owner", "merchant_id": source["merchant_id"], "conversation_id": source["conversation_id"],
        "suggestion_id": "fixture-draft", "source_message_id": source["message_id"], "version": 1,
        "status": "pending_approval", "generation_status": "ready", "surface": "comment",
        "text_ciphertext": encrypt_private_payload({"text": "كودك AM 😄"}), "created_at": accounts.now(),
    })
    payload = replies.ReplyPreview(conversation_id=source["conversation_id"], suggestion_id="fixture-draft", version=1, text="كودك AM 😄")
    yield db, provider, payload


@pytest.mark.asyncio
async def test_preview_execute_and_visibility_are_distinct_real_persistence_boundaries(reply_environment):
    db, provider, payload = reply_environment
    proposal = await replies.preview_reply(db, "owner", payload)
    assert proposal["status"] == "previewed" and provider.writes == []
    stored = await db[replies.PROPOSALS].find_one({})
    assert payload.text not in str(stored) and COMMENT not in str(stored)
    approved = replies.ReplyApproval(confirmation_digest=proposal["confirmation_digest"])
    result = await replies.execute_reply(db, "owner", proposal["proposal_id"], approved)
    assert result["status"] == "accepted" and not result["public_visibility_verified"]
    assert result["automatic_reply_enabled"] is False
    again = await replies.execute_reply(db, "owner", proposal["proposal_id"], approved)
    assert again["status"] == "accepted" and len(provider.writes) == 1
    visible = await replies.reply_status(db, "owner", proposal["proposal_id"])
    assert visible["status"] == "published_public" and visible["public_visibility_verified"] is True
    provider.visibility = "HIDDEN"
    await db[replies.PROPOSALS].update_one({}, {"$unset": {"next_check_at": ""}})
    hidden = await replies.reply_status(db, "owner", proposal["proposal_id"])
    assert hidden["status"] == "hidden" and not hidden["public_visibility_verified"]
    assert len(provider.writes) == 1
    source = await db[CONVERSATION_MESSAGES_COLLECTION].find_one({})
    assert source["analysis_status"] == "not_requested"
    assert await db[CONVERSATION_MESSAGES_COLLECTION].count_documents({}) == 1
    stored = await db[replies.PROPOSALS].find_one({})
    assert payload.text not in str(stored) and provider.receipt["comment_id"] not in str(stored)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "mismatch"])
async def test_ambiguous_result_never_retries_provider_even_via_new_preview(reply_environment, failure):
    db, provider, payload = reply_environment
    p = await replies.preview_reply(db, "owner", payload)
    provider.fail_write = failure
    approval = replies.ReplyApproval(confirmation_digest=p["confirmation_digest"])
    assert (await replies.execute_reply(db, "owner", p["proposal_id"], approval))["status"] == "uncertain"
    assert (await replies.execute_reply(db, "owner", p["proposal_id"], approval))["status"] == "uncertain"
    with pytest.raises(Exception):
        await replies.preview_reply(db, "owner", payload)
    assert len(provider.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "comment", "owner", "version", "expiry", "digest"])
async def test_changed_authority_or_evidence_prevents_write(reply_environment, change):
    from fastapi import HTTPException
    db, provider, payload = reply_environment
    p = await replies.preview_reply(db, "owner", payload)
    user_id = "owner"
    digest = p["confirmation_digest"]
    if change == "permission":
        provider.scopes = "video.list,comment.list"
    elif change == "comment":
        provider.comments[0]["text"] = "تغير التعليق"
    elif change == "owner":
        user_id = "other-owner"
    elif change == "version":
        await db[REPLY_SUGGESTIONS_COLLECTION].update_one({}, {"$inc": {"version": 1}})
    elif change == "expiry":
        await db[replies.PROPOSALS].update_one({}, {"$set": {"expires_at": accounts.now() - timedelta(seconds=1)}})
    else:
        digest = "0" * 64
    with pytest.raises(HTTPException):
        await replies.execute_reply(db, user_id, p["proposal_id"], replies.ReplyApproval(confirmation_digest=digest))
    assert provider.writes == []


@pytest.mark.asyncio
async def test_source_invalidated_during_remote_proof_cannot_send(reply_environment):
    from fastapi import HTTPException
    db, provider, payload = reply_environment
    p = await replies.preview_reply(db, "owner", payload)
    async def invalidate():
        await db[REPLY_SUGGESTIONS_COLLECTION].update_one({}, {"$set": {"status": "stale"}, "$inc": {"version": 1}})
    provider.before_parent = invalidate
    with pytest.raises(HTTPException):
        await replies.execute_reply(db, "owner", p["proposal_id"], replies.ReplyApproval(confirmation_digest=p["confirmation_digest"]))
    assert not provider.writes


@pytest.mark.asyncio
async def test_public_reply_limit_is_1200_characters_not_150(reply_environment):
    from pydantic import ValidationError
    db, provider, payload = reply_environment
    payload.text = "أ" * 1200
    p = await replies.preview_reply(db, "owner", payload)
    assert len(p["text"]) == 1200 and not provider.writes
    with pytest.raises(ValidationError):
        replies.ReplyPreview(**{**payload.model_dump(), "text": "أ" * 1201})
