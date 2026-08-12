"""Security and Coexistence contracts for the optional 360dialog transport."""

from __future__ import annotations

import base64
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
from customer_intelligence.whatsapp import MAX_WHATSAPP_WEBHOOK_BYTES
from customer_intelligence.whatsapp_360dialog import (
    D360InboundAdapter,
    D360WebhookBinding,
    make_360dialog_inbound_router,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
PHONE_NUMBER_ID = "109876543210987"
USERNAME = "mezan-channel-1"
PASSWORD = "test-webhook-password"


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
        self.messages = []
        self.statuses = []

    async def ingest_inbound(self, *, context, message):
        self.messages.append((context, message))
        suffix = str(len(self.messages))
        return InboundIngestResult(
            duplicate=False,
            provider="whatsapp",
            customer_id=f"cust-{suffix}",
            conversation_id=f"conv-{suffix}",
            message_id=f"msg-{suffix}",
        )

    async def record_outbound_status(
        self,
        *,
        context,
        external_message_id,
        delivery_state,
    ):
        self.statuses.append((context, external_message_id, delivery_state))
        return True


@pytest.fixture(autouse=True)
def _binding_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CHANNEL_BINDING_HMAC_KEY",
        "test-only-channel-binding-key",
    )


def _channel():
    return ChannelRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        channel_id="whatsapp-channel-1",
        provider="whatsapp",
        external_account_key=build_channel_account_key("whatsapp", PHONE_NUMBER_ID),
        status="connected",
        ingress_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    ).model_dump()


def _adapter(db):
    return D360InboundAdapter(
        db,
        bindings=[
            D360WebhookBinding(
                username=USERNAME,
                password=PASSWORD,
                phone_number_id=PHONE_NUMBER_ID,
                channel_id="whatsapp-channel-1",
            )
        ],
    )


def _authorization(username=USERNAME, password=PASSWORD):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def _payload(*, phone_number_id=PHONE_NUMBER_ID):
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
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [
                                {
                                    "wa_id": "966500000000",
                                    "profile": {"name": "Buyer"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "966500000000",
                                    "id": "wamid.inbound-1",
                                    "timestamp": "1786536000",
                                    "type": "text",
                                    "text": {"body": "هل المنتج متوفر؟"},
                                }
                            ],
                            "statuses": [{"id": "wamid.echo-1", "status": "delivered"}],
                        },
                    },
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "message_echoes": [
                                {
                                    "from": "966501111111",
                                    "to": "966500000000",
                                    "id": "wamid.echo-1",
                                    "timestamp": "1786536060",
                                    "type": "text",
                                    "text": {"body": "نعم، متوفر الآن"},
                                }
                            ],
                        },
                    },
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_channel_basic_auth_routes_inbound_echo_and_status_without_send():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(
            db,
            adapter=_adapter(db),
            gateway=gateway,
        ),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "accepted": True,
        "inbound_messages_seen": 1,
        "employee_echoes_seen": 1,
        "messages_created": 2,
        "duplicates": 0,
        "statuses_seen": 1,
        "statuses_updated": 1,
        "unsupported_events": 0,
        "message_send_allowed": False,
        "ai_execution_allowed": False,
        "commerce_mutation_allowed": False,
    }
    inbound = gateway.messages[0][1]
    echo = gateway.messages[1][1]
    assert inbound.direction == "inbound" and inbound.sender_type == "customer"
    assert echo.direction == "outbound" and echo.sender_type == "employee"
    assert echo.analysis_status == "not_requested"
    assert echo.external_conversation_id.get_secret_value() == "966500000000"
    assert gateway.statuses[0][1:] == ("wamid.echo-1", "delivered")
    assert not hasattr(_adapter(db), "send")


@pytest.mark.asyncio
async def test_authentication_precedes_json_and_phone_binding_is_fail_closed():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(db, adapter=_adapter(db), gateway=gateway),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        wrong_password = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(password="wrong"),
            content=b"not-json",
        )
        wrong_phone = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=_payload(phone_number_id="different-phone-id"),
        )

    assert wrong_password.status_code == 401
    assert wrong_phone.status_code == 401
    assert gateway.messages == []
    assert gateway.statuses == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", ["smb_app_state_sync", "history"])
async def test_authenticated_top_level_coexistence_event_is_safely_acknowledged(
    event_name,
):
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(db, adapter=_adapter(db), gateway=gateway),
        prefix="/api",
    )
    payload = {
        "id": "coexistence-event-1",
        "event": event_name,
        "data": {
            "id": "waba-1",
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": PHONE_NUMBER_ID},
            # Contact/history content is deliberately ignored by this phase.
            "state_sync": [{"contact": {"full_name": "must-not-be-stored"}}],
            "history": [{"threads": [{"id": "must-not-be-stored"}]}],
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["unsupported_events"] == 1
    assert response.json()["messages_created"] == 0
    assert gateway.messages == []


@pytest.mark.asyncio
async def test_top_level_coexistence_event_rejects_wrong_phone_binding():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(db, adapter=_adapter(db), gateway=gateway),
        prefix="/api",
    )
    payload = {
        "id": "coexistence-event-2",
        "event": "smb_app_state_sync",
        "data": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "different-phone-id"},
            "state_sync": [],
        },
    }

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=payload,
        )

    assert response.status_code == 401
    assert gateway.messages == []
    assert gateway.statuses == []


@pytest.mark.asyncio
async def test_oversized_body_is_413_and_never_reaches_gateway():
    db = FakeDB(_channel())
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(db, adapter=_adapter(db), gateway=gateway),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            content=b"x" * (MAX_WHATSAPP_WEBHOOK_BYTES + 1),
        )

    assert response.status_code == 413
    assert gateway.messages == []


def test_router_exposes_only_one_receive_operation():
    db = FakeDB(_channel())
    routes = [
        route
        for route in make_360dialog_inbound_router(
            db,
            adapter=_adapter(db),
            gateway=SpyGateway(),
        ).routes
        if isinstance(route, APIRoute)
    ]
    assert [(route.path, route.methods) for route in routes] == [
        (
            "/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            {"POST"},
        )
    ]


@pytest.mark.asyncio
async def test_production_router_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEZAN_360DIALOG_INGRESS_ENABLED", raising=False)
    monkeypatch.delenv("MEZAN_360DIALOG_WEBHOOK_BINDINGS_JSON", raising=False)
    app = FastAPI()
    app.include_router(make_360dialog_inbound_router(FakeDB()), prefix="/api")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=_payload(),
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "360dialog_ingress_disabled"


@pytest.mark.asyncio
async def test_production_router_loads_channel_scoped_binding_list(monkeypatch):
    monkeypatch.setenv("MEZAN_360DIALOG_INGRESS_ENABLED", "true")
    monkeypatch.setenv(
        "MEZAN_360DIALOG_WEBHOOK_BINDINGS_JSON",
        json.dumps(
            [
                {
                    "username": USERNAME,
                    "password": PASSWORD,
                    "phone_number_id": PHONE_NUMBER_ID,
                    "channel_id": "whatsapp-channel-1",
                }
            ]
        ),
    )
    gateway = SpyGateway()
    app = FastAPI()
    app.include_router(
        make_360dialog_inbound_router(FakeDB(_channel()), gateway=gateway),
        prefix="/api",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/customer-intelligence/v1/channels/whatsapp/360dialog/webhook",
            headers=_authorization(),
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json()["messages_created"] == 2
    assert len(gateway.messages) == 2
