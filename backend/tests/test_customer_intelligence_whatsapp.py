"""Receive-only contract tests for the WhatsApp Cloud API adapter."""
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
from customer_intelligence.whatsapp import (
    MAX_WHATSAPP_WEBHOOK_BYTES,
    WhatsAppInboundAdapter,
    make_whatsapp_inbound_router,
)


NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
APP_SECRET = "test-meta-app-secret"
VERIFY_TOKEN = "test-webhook-verify-token"
PHONE_NUMBER_ID = "123456789012345"


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
            provider="whatsapp",
            customer_id="cust-1",
            conversation_id="conv-1",
            message_id="msg-1",
        )


@pytest.fixture(autouse=True)
def _binding_hmac_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CHANNEL_BINDING_HMAC_KEY",
        "test-only-channel-binding-hmac-key",
    )


def _channel():
    return ChannelRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        channel_id="channel-whatsapp-1",
        provider="whatsapp",
        external_account_key=build_channel_account_key(
            "whatsapp",
            PHONE_NUMBER_ID,
        ),
        status="connected",
        ingress_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    ).model_dump()


def _payload(*, message_type="text"):
    message = {
        "from": "966500000000",
        "id": "wamid.message-1",
        "timestamp": "1786456800",
        "type": message_type,
    }
    if message_type == "text":
        message["text"] = {"body": "هل المنتج متوفر؟"}
    elif message_type == "image":
        message["image"] = {
            "id": "media-image-1",
            "mime_type": "image/jpeg",
            "sha256": "media-sha256",
            "caption": "هل يوجد شيء مشابه؟",
        }
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+966 50 111 1111",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Private Buyer"},
                                    "wa_id": "966500000000",
                                }
                            ],
                            "messages": [message],
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
    return WhatsAppInboundAdapter(
        db,
        verify_token=VERIFY_TOKEN,
        app_secret=APP_SECRET,
    )


@pytest.mark.asyncio
async def test_adapter_verifies_raw_body_signature_and_normalizes_meta_payload():
    db = FakeDB(_channel())
    body = _body()

    events = await _adapter(db).verify_and_normalize(
        headers={"X-Hub-Signature-256": _signature(body)},
        body=body,
    )

    assert len(events) == 1
    context, message = events[0]
    assert context.model_dump() == {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "channel_id": "channel-whatsapp-1",
        "provider": "whatsapp",
    }
    assert message.external_conversation_id.get_secret_value() == "966500000000"
    assert message.external_message_id.get_secret_value() == "wamid.message-1"
    assert message.external_customer_id.get_secret_value() == "966500000000"
    assert message.customer_mobile.get_secret_value() == "966500000000"
    assert message.customer_profile == {"name": "Private Buyer"}
    assert message.content_type == "text"
    assert message.content_payload == {"text": "هل المنتج متوفر؟"}
    assert message.occurred_at == datetime.fromtimestamp(
        1786456800,
        tz=timezone.utc,
    )


@pytest.mark.asyncio
async def test_adapter_normalizes_media_reference_without_downloading_or_sending():
    db = FakeDB(_channel())
    body = _body(_payload(message_type="image"))

    events = await _adapter(db).verify_and_normalize(
        headers={"x-hub-signature-256": _signature(body)},
        body=body,
    )

    _context_value, message = events[0]
    assert message.content_type == "image"
    assert message.content_payload == {
        "provider_media_id": "media-image-1",
        "mime_type": "image/jpeg",
        "sha256": "media-sha256",
        "caption": "هل يوجد شيء مشابه؟",
    }
    assert not hasattr(_adapter(db), "send")


@pytest.mark.asyncio
async def test_webhook_routes_verify_challenge_and_ingest_only():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    router = make_whatsapp_inbound_router(
        db,
        adapter=_adapter(db),
        gateway=gateway,
    )
    app.include_router(router, prefix="/api")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        challenge = await client.get(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge-123",
            },
        )
        body = _body()
        received = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": _signature(body),
            },
        )

    assert challenge.status_code == 200
    assert challenge.text == "challenge-123"
    assert received.status_code == 200
    assert received.json() == {
        "accepted": True,
        "messages_seen": 1,
        "messages_created": 1,
        "duplicates": 0,
        "message_send_allowed": False,
        "ai_execution_allowed": False,
        "commerce_mutation_allowed": False,
    }
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_invalid_challenge_or_signature_never_reaches_gateway():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_whatsapp_inbound_router(
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
        challenge = await client.get(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-123",
            },
        )
        received = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            content=_body(),
            headers={"x-hub-signature-256": "sha256=invalid"},
        )

    assert challenge.status_code == 403
    assert received.status_code == 401
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_oversized_meta_webhook_is_rejected_before_gateway():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_whatsapp_inbound_router(
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
        received = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            content=b"x" * (MAX_WHATSAPP_WEBHOOK_BYTES + 1),
            headers={"x-hub-signature-256": "sha256=unused"},
        )

    assert received.status_code == 413
    assert received.json()["detail"]["code"] == "whatsapp_webhook_too_large"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_status_only_webhook_is_acknowledged_without_creating_a_message():
    db = FakeDB(_channel())
    payload = _payload()
    value = payload["entry"][0]["changes"][0]["value"]
    value.pop("messages")
    value.pop("contacts")
    value["statuses"] = [{"id": "wamid.outbound", "status": "delivered"}]
    body = _body(payload)

    events = await _adapter(db).verify_and_normalize(
        headers={"x-hub-signature-256": _signature(body)},
        body=body,
    )

    assert events == []


def test_router_exposes_webhook_get_and_post_but_no_send_operation():
    db = FakeDB(_channel())
    router = make_whatsapp_inbound_router(db, adapter=_adapter(db), gateway=SpyGateway())
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert {(route.path, frozenset(route.methods)) for route in routes} == {
        (
            "/customer-intelligence/v1/channels/whatsapp/webhook",
            frozenset({"GET"}),
        ),
        (
            "/customer-intelligence/v1/channels/whatsapp/webhook",
            frozenset({"POST"}),
        ),
    }


@pytest.mark.asyncio
async def test_production_router_is_disabled_by_default_and_fails_closed(
    monkeypatch,
):
    for key in (
        "MEZAN_WHATSAPP_INGRESS_ENABLED",
        "MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN",
        "MEZAN_WHATSAPP_APP_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    app = FastAPI()
    app.include_router(
        make_whatsapp_inbound_router(FakeDB()),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        disabled = await client.get(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge-123",
            },
        )
        monkeypatch.setenv("MEZAN_WHATSAPP_INGRESS_ENABLED", "true")
        unconfigured = await client.get(
            "/api/customer-intelligence/v1/channels/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "challenge-123",
            },
        )

    assert disabled.status_code == 404
    assert disabled.json()["detail"]["code"] == "whatsapp_ingress_disabled"
    assert unconfigured.status_code == 503
    assert unconfigured.json()["detail"]["code"] == (
        "whatsapp_ingress_not_configured"
    )
