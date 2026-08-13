"""Receive-only contracts for Instagram comments and direct messages."""
from __future__ import annotations

import hashlib
import hmac
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from customer_intelligence.channel_gateway import (
    InboundIngestResult,
    build_channel_account_key,
)
from customer_intelligence.foundation import CHANNELS_COLLECTION, ChannelRecord
from customer_intelligence.instagram import (
    InstagramInboundAdapter,
    make_instagram_inbound_router,
)


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
APP_SECRET = "test-shared-meta-app-secret"
VERIFY_TOKEN = "test-instagram-verify-token"
INSTAGRAM_ACCOUNT_ID = "17841400000000000"
CUSTOMER_ID = "17841411111111111"


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    async def find_one(self, selector, projection=None):
        del projection
        return next(
            (
                deepcopy(row)
                for row in self.documents
                if all(row.get(key) == value for key, value in selector.items())
            ),
            None,
        )


class FakeDB:
    def __init__(self, channel=None):
        self.collections = {
            CHANNELS_COLLECTION: FakeCollection([channel] if channel else []),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class SpyGateway:
    def __init__(self):
        self.calls = []

    async def ingest_inbound(self, *, context, message):
        self.calls.append((context, message))
        return InboundIngestResult(
            duplicate=False,
            provider="instagram",
            customer_id="cust-instagram-1",
            conversation_id="conv-instagram-1",
            message_id="msg-instagram-1",
        )


@pytest.fixture(autouse=True)
def _binding_hmac_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CHANNEL_BINDING_HMAC_KEY",
        "test-only-instagram-binding-key",
    )


def _channel():
    return ChannelRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        channel_id="channel-instagram-1",
        provider="instagram",
        external_account_key=build_channel_account_key(
            "instagram",
            INSTAGRAM_ACCOUNT_ID,
        ),
        status="connected",
        ingress_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    ).model_dump()


def _payload():
    return {
        "object": "instagram",
        "entry": [
            {
                "id": INSTAGRAM_ACCOUNT_ID,
                "time": 1786615200,
                "messaging": [
                    {
                        "sender": {"id": CUSTOMER_ID},
                        "recipient": {"id": INSTAGRAM_ACCOUNT_ID},
                        "timestamp": 1786615200000,
                        "message": {
                            "mid": "ig_mid_message_1",
                            "text": "هل السلسال متوفر؟",
                        },
                    }
                ],
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {
                                "id": CUSTOMER_ID,
                                "username": "buyer_example",
                            },
                            "media": {
                                "id": "ig_media_1",
                                "media_product_type": "FEED",
                            },
                            "id": "ig_comment_1",
                            "text": "جميل، كم السعر؟",
                        },
                    }
                ],
            }
        ],
    }


def _body(payload=None):
    return json.dumps(
        payload or _payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(body):
    return "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _adapter(db):
    return InstagramInboundAdapter(
        db,
        verify_token=VERIFY_TOKEN,
        app_secret=APP_SECRET,
    )


@pytest.mark.asyncio
async def test_signed_instagram_payload_normalizes_dm_and_public_comment():
    db = FakeDB(_channel())
    body = _body()

    batch = await _adapter(db).verify_and_normalize(
        headers={"X-Hub-Signature-256": _signature(body)},
        body=body,
    )

    assert batch.direct_message_count == 1
    assert batch.comment_count == 1
    assert batch.echo_count == 0
    assert batch.unsupported_count == 0
    assert len(batch.messages) == 2

    context, direct = batch.messages[0]
    assert context.model_dump() == {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "channel_id": "channel-instagram-1",
        "provider": "instagram",
    }
    assert direct.external_conversation_id.get_secret_value() == f"dm:{CUSTOMER_ID}"
    assert direct.external_message_id.get_secret_value() == "ig_mid_message_1"
    assert direct.external_customer_id.get_secret_value() == CUSTOMER_ID
    assert direct.content_type == "text"
    assert direct.content_payload == {
        "text": "هل السلسال متوفر؟",
        "surface": "direct_message",
        "attachment_types": [],
        "attachment_count": 0,
    }
    assert direct.occurred_at == datetime.fromtimestamp(1786615200, tz=timezone.utc)

    _context, comment = batch.messages[1]
    assert comment.external_conversation_id.get_secret_value() == (
        "comment:ig_media_1:ig_comment_1"
    )
    assert comment.external_message_id.get_secret_value() == "comment:ig_comment_1"
    assert comment.customer_profile == {
        "name": "buyer_example",
        "username": "buyer_example",
    }
    assert comment.content_payload == {
        "surface": "comment",
        "text": "جميل، كم السعر؟",
        "comment_id": "ig_comment_1",
        "media_id": "ig_media_1",
        "media_product_type": "FEED",
    }
    assert comment.source_event == "instagram.comments.comment"


@pytest.mark.asyncio
async def test_instagram_message_echo_is_history_evidence_not_customer_input():
    payload = _payload()
    payload["entry"][0]["changes"] = []
    event = payload["entry"][0]["messaging"][0]
    event["sender"] = {"id": INSTAGRAM_ACCOUNT_ID}
    event["recipient"] = {"id": CUSTOMER_ID}
    event["message"]["is_echo"] = True
    body = _body(payload)

    batch = await _adapter(FakeDB(_channel())).verify_and_normalize(
        headers={"x-hub-signature-256": _signature(body)},
        body=body,
    )

    assert batch.echo_count == 1
    message = batch.messages[0][1]
    assert message.direction == "outbound"
    assert message.sender_type == "employee"
    assert message.analysis_status == "pending"
    assert message.delivery_state == "sent"
    assert message.external_conversation_id.get_secret_value() == f"dm:{CUSTOMER_ID}"


@pytest.mark.asyncio
async def test_instagram_webhook_routes_verify_and_ingest_without_send_operation():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_instagram_inbound_router(
            db,
            adapter=_adapter(db),
            gateway=gateway,
        ),
        prefix="/api",
    )
    body = _body()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        challenge = await client.get(
            "/api/customer-intelligence/v1/channels/instagram/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge-instagram-123",
            },
        )
        received = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/webhook",
            content=body,
            headers={"x-hub-signature-256": _signature(body)},
        )

    assert challenge.status_code == 200
    assert challenge.text == "challenge-instagram-123"
    assert received.status_code == 200
    assert received.json() == {
        "accepted": True,
        "events_seen": 2,
        "direct_messages_seen": 1,
        "comments_seen": 1,
        "employee_echoes_seen": 0,
        "events_created": 2,
        "duplicates": 0,
        "unsupported_events": 0,
        "message_send_allowed": False,
        "comment_reply_allowed": False,
        "ai_auto_reply_allowed": False,
        "commerce_mutation_allowed": False,
    }
    assert len(gateway.calls) == 2

    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and "instagram/webhook" in route.path
    ]
    assert {(route.path, frozenset(route.methods)) for route in routes} == {
        (
            "/api/customer-intelligence/v1/channels/instagram/webhook",
            frozenset({"GET"}),
        ),
        (
            "/api/customer-intelligence/v1/channels/instagram/webhook",
            frozenset({"POST"}),
        ),
    }


@pytest.mark.asyncio
async def test_instagram_router_reuses_shared_meta_secret_and_fails_closed(
    monkeypatch,
):
    for key in (
        "MEZAN_INSTAGRAM_INGRESS_ENABLED",
        "MEZAN_INSTAGRAM_WEBHOOK_VERIFY_TOKEN",
        "MEZAN_INSTAGRAM_APP_SECRET",
        "META_BUSINESS_APP_SECRET",
        "MEZAN_WHATSAPP_APP_SECRET",
        "MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    app.include_router(
        make_instagram_inbound_router(FakeDB()),
        prefix="/api",
    )
    path = "/api/customer-intelligence/v1/channels/instagram/webhook"
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "challenge",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        disabled = await client.get(path, params=params)
        monkeypatch.setenv("MEZAN_INSTAGRAM_INGRESS_ENABLED", "true")
        unconfigured = await client.get(path, params=params)
        monkeypatch.setenv("MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN", VERIFY_TOKEN)
        monkeypatch.setenv("META_BUSINESS_APP_SECRET", APP_SECRET)
        shared = await client.get(path, params=params)

    assert disabled.status_code == 404
    assert disabled.json()["detail"]["code"] == "instagram_ingress_disabled"
    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"]["code"] == "instagram_ingress_not_configured"
    assert shared.status_code == 200
    assert shared.text == "challenge"


@pytest.mark.asyncio
async def test_invalid_signature_never_reaches_gateway():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_instagram_inbound_router(
            db,
            adapter=_adapter(db),
            gateway=gateway,
        ),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/webhook",
            content=_body(),
            headers={"x-hub-signature-256": "sha256=invalid"},
        )

    assert response.status_code == 401
    assert gateway.calls == []
