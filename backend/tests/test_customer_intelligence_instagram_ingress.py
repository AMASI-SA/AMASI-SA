"""Regression contracts for receive-only Instagram webhook ingestion."""
from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy

import pytest
from cryptography.fernet import Fernet

from customer_identity import build_identity_keys
from customer_intelligence.channel_gateway import (
    ChannelGateway,
    build_channel_account_key,
)
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
)
from customer_intelligence.instagram import (
    InstagramInboundAdapter,
    InstagramSignatureError,
)


OWNER_ID = "owner-instagram-ingress"
MERCHANT_ID = "1014726301562776"
CHANNEL_ID = "channel-instagram-amasi"
RAW_INSTAGRAM_ID = "17841400000000001"


def _matches(document: dict, selector: dict) -> bool:
    return all(document.get(key) == value for key, value in selector.items())


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])

    async def find_one(self, selector, projection=None):
        del projection
        row = next((row for row in self.rows if _matches(row, selector)), None)
        return deepcopy(row) if row is not None else None


class FakeDB:
    def __init__(self):
        self.collections = {
            CHANNELS_COLLECTION: FakeCollection(
                [
                    {
                        "user_id": OWNER_ID,
                        "merchant_id": MERCHANT_ID,
                        "channel_id": CHANNEL_ID,
                        "provider": "instagram",
                        "external_account_key": build_channel_account_key(
                            "instagram", RAW_INSTAGRAM_ID
                        ),
                        "status": "connected",
                        "ingress_enabled": True,
                        "egress_mode": "disabled",
                        "send_allowed": False,
                        "ai_auto_reply_allowed": False,
                    }
                ]
            ),
            CONVERSATION_MESSAGES_COLLECTION: FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class LookupForbiddenDB:
    def __getattr__(self, name):
        raise AssertionError(f"database lookup occurred before verification: {name}")


@pytest.fixture(autouse=True)
def _identity_configuration(monkeypatch):
    monkeypatch.setenv("MEZAN_CHANNEL_BINDING_HMAC_KEY", "k" * 64)
    monkeypatch.setenv("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", "i" * 64)
    monkeypatch.setenv(
        "MEZAN_CUSTOMER_PII_ENC_KEY",
        Fernet.generate_key().decode(),
    )


def _instagram_webhook_body() -> bytes:
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": RAW_INSTAGRAM_ID,
                "time": 1786942800,
                "messaging": [
                    {
                        "sender": {"id": "17841409999990001"},
                        "recipient": {"id": RAW_INSTAGRAM_ID},
                        "timestamp": 1786942800000,
                        "message": {
                            "mid": "ig_mid_customer_question_1",
                            "text": "هل المنتج متوفر؟",
                        },
                    }
                ],
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "ig_comment_delivery_question_1",
                            "text": "كم مدة التوصيل؟",
                            "from": {
                                "id": "17841409999990002",
                                "username": "customer.sa",
                            },
                            "media": {
                                "id": "ig_media_product_1",
                                "media_product_type": "FEED",
                            },
                            "created_time": 1786942800,
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_headers(body: bytes, *, secret: str) -> dict[str, str]:
    signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return {"x-hub-signature-256": f"sha256={signature}"}


@pytest.mark.asyncio
async def test_signed_message_and_comment_are_normalized_for_bound_tenant():
    db = FakeDB()
    secret = "instagram-webhook-secret"
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        db,
        verify_token="instagram-verify-token",
        app_secret=secret,
    )

    batch = await adapter.verify_and_normalize(
        headers=_signed_headers(body, secret=secret),
        body=body,
    )

    assert len(batch.messages) == 2
    assert batch.direct_message_count == 1
    assert batch.comment_count == 1
    assert {
        (context.user_id, context.merchant_id, context.provider)
        for context, _message in batch.messages
    } == {(OWNER_ID, MERCHANT_ID, "instagram")}

    messages = {
        message.source_event: message for _context, message in batch.messages
    }
    direct_message = messages["instagram.messaging.message"]
    comment = messages["instagram.comments.comment"]

    assert direct_message.external_message_id.get_secret_value() == (
        "ig_mid_customer_question_1"
    )
    assert direct_message.content_payload["text"] == "هل المنتج متوفر؟"
    assert comment.external_message_id.get_secret_value() == (
        "comment:ig_comment_delivery_question_1"
    )
    assert comment.external_conversation_id.get_secret_value() == (
        "comment:ig_media_product_1:ig_comment_delivery_question_1"
    )
    assert comment.content_payload["text"] == "كم مدة التوصيل؟"
    assert comment.customer_profile["username"] == "customer.sa"


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_before_account_lookup():
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        LookupForbiddenDB(),
        verify_token="instagram-verify-token",
        app_secret="instagram-webhook-secret",
    )

    with pytest.raises(InstagramSignatureError):
        await adapter.verify_and_normalize(
            headers={"x-hub-signature-256": "sha256=invalid"},
            body=body,
        )


@pytest.mark.asyncio
async def test_retried_instagram_event_is_idempotent_in_channel_gateway():
    db = FakeDB()
    secret = "instagram-webhook-secret"
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        db,
        verify_token="instagram-verify-token",
        app_secret=secret,
    )
    batch = await adapter.verify_and_normalize(
        headers=_signed_headers(body, secret=secret),
        body=body,
    )
    context, message = next(
        item
        for item in batch.messages
        if item[1].source_event == "instagram.messaging.message"
    )
    external_message_id = message.external_message_id.get_secret_value()
    message_key = build_identity_keys(
        user_id=context.user_id,
        merchant_id=context.merchant_id,
        source_system=f"instagram:{context.channel_id}:message",
        external_customer_id=external_message_id,
    )[0]
    db.collections[CONVERSATION_MESSAGES_COLLECTION].rows.append(
        {
            "user_id": context.user_id,
            "merchant_id": context.merchant_id,
            "channel_id": context.channel_id,
            "external_message_key": message_key,
            "customer_id": "customer-existing",
            "conversation_id": "conversation-existing",
            "message_id": "message-existing",
        }
    )

    gateway = ChannelGateway(db)
    first_retry = await gateway.ingest_inbound(
        context=context,
        message=message,
    )
    second_retry = await gateway.ingest_inbound(
        context=context,
        message=message,
    )

    assert first_retry.duplicate is True
    assert second_retry.duplicate is True
    assert first_retry.message_id == "message-existing"
    assert second_retry.message_id == "message-existing"
    assert len(db.collections[CONVERSATION_MESSAGES_COLLECTION].rows) == 1
