"""Public seams for owner-initiated bounded TikTok comment ingestion."""
import httpx
import pytest

from tests.test_tiktok_content_routes import app_for

ROOT = "/api/integrations-v2/tiktok/content/creators/" + "a" * 64 + "/comments"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/connect", {"confirm_receive_only": True}),
    ("GET", "/posts", None),
    ("POST", "/pull", {"video_id": "6990565363377392901"}),
])
async def test_comment_operations_are_owner_only_before_any_database_or_provider(method, path, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "staff", "role": "staff"})), base_url="http://test") as client:
        response = await client.request(method, ROOT + path, json=body)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_comment_pull_has_a_real_typed_public_route():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post(ROOT + "/pull", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_comment_metadata_is_bounded_before_json_parsing():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id": "owner", "role": "owner"})), base_url="http://test") as client:
        response = await client.post(ROOT + "/pull", content=b'{"ignored":"' + b'x' * 32768 + b'"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_manual_tiktok_comments_are_visible_as_public_comments_in_the_existing_inbox():
    import customer_identity
    from cryptography.fernet import Fernet
    from customer_intelligence.foundation import CHANNELS_COLLECTION, CONVERSATION_MESSAGES_COLLECTION
    from customer_intelligence.inbox import CustomerIntelligenceInboxService
    from tests.test_customer_intelligence_inbox import _db, OWNER, NOW
    from pytest import MonkeyPatch

    with MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
        customer_identity._fernet = None
        db = _db()
        db.collections[CHANNELS_COLLECTION].documents[0]["provider"] = "tiktok"
        message = db.collections[CONVERSATION_MESSAGES_COLLECTION].documents[0]
        message["source_event"] = "tiktok.comments.public.manual"
        message["analysis_status"] = "not_requested"
        message["content_ciphertext"] = customer_identity.encrypt_private_payload({"content_type": "text", "payload": {"surface": "comment", "text": "هل المنتج متوفر؟", "tiktok_comment": {"video_id": "6990565363377392901", "comment_id": "6990565363377392999"}}})
        payload = (await CustomerIntelligenceInboxService(db, now=lambda: NOW).inbox(owner_user_id=OWNER["id"])).model_dump(mode="json")
        customer_identity._fernet = None
    assert payload["data_origin"] == "channel_providers"
    assert payload["conversations"][0]["channel"] == "tiktok"
    assert payload["conversations"][0]["surface"] == "comment"
    assert payload["conversations"][0]["messages"][0]["body"] == "هل المنتج متوفر؟"
    assert payload["safety_policy"]["ai_auto_reply_allowed"] is False
    assert "6990565363377392999" not in str(payload)
