"""Owner-confirmed Instagram binding from secret-safe Meta discovery assets."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from pymongo.errors import DuplicateKeyError

from customer_identity import build_identity_keys
from customer_intelligence.channel_gateway import ChannelGateway
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
)
from customer_intelligence.instagram import (
    InstagramInboundAdapter,
    InstagramSignatureError,
)
from customer_intelligence.instagram_provisioning import (
    INSTAGRAM_PROVISION_CONFIRMATION,
    INSTAGRAM_REQUIRED_PERMISSIONS,
    META_ASSETS_COLLECTION,
    META_CREDENTIALS_COLLECTION,
    InstagramProvisionIn,
    InstagramProvisioningError,
    InstagramProvisioningService,
)
from customer_intelligence.routes import make_customer_intelligence_router
from meta_instagram_webhooks import (
    INSTAGRAM_WEBHOOK_FIELDS,
    MetaInstagramWebhookError,
    subscribe_instagram_webhooks,
)


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
OWNER_ID = "owner-instagram-setup"
RAW_INSTAGRAM_ID = "17841400000000001"
PAGE_ID = "104200000000001"
MERCHANT_ID = "1014726301562776"
APP_ID = "953625110827548"


def _matches(document, selector):
    return all(document.get(key) == value for key, value in selector.items())


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, *, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])

    def find(self, selector, projection=None):
        del projection
        return FakeCursor([row for row in self.rows if _matches(row, selector)])

    async def find_one(self, selector, projection=None):
        del projection
        row = next((row for row in self.rows if _matches(row, selector)), None)
        return deepcopy(row) if row is not None else None

    async def insert_one(self, document):
        if any(
            row.get("external_account_key") == document.get("external_account_key")
            and row.get("provider") == document.get("provider")
            for row in self.rows
        ):
            raise DuplicateKeyError("duplicate binding")
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id="created")

    async def update_one(self, selector, update):
        row = next((row for row in self.rows if _matches(row, selector)), None)
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        row.update(deepcopy(update.get("$set") or {}))
        return SimpleNamespace(matched_count=1, modified_count=1)


class RecordingSubscriber:
    def __init__(self, *, error_code=None):
        self.calls = []
        self.error_code = error_code

    async def __call__(self, db, **kwargs):
        del db
        self.calls.append(deepcopy(kwargs))
        if self.error_code:
            raise MetaInstagramWebhookError(self.error_code)
        return INSTAGRAM_WEBHOOK_FIELDS


class FakeDB:
    def __init__(self, *, scopes=None):
        self.collections = {
            META_CREDENTIALS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "scope": list(
                        scopes
                        if scopes is not None
                        else INSTAGRAM_REQUIRED_PERMISSIONS
                    ),
                }]
            ),
            META_ASSETS_COLLECTION: FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "provider": "meta_ads",
                    "asset_type": "instagram_account",
                    "connection_status": "connected",
                    "external_asset_id": RAW_INSTAGRAM_ID,
                    "display_name": "amasi_store",
                    "page_id": PAGE_ID,
                }]
            ),
            "salla_integrations": FakeCollection(
                [{
                    "user_id": OWNER_ID,
                    "status": "connected",
                    "store_id": MERCHANT_ID,
                }]
            ),
            CHANNELS_COLLECTION: FakeCollection(),
            CONVERSATION_MESSAGES_COLLECTION: FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _binding_key(monkeypatch):
    monkeypatch.setenv("MEZAN_CHANNEL_BINDING_HMAC_KEY", "k" * 64)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_setup_exposes_opaque_candidate_and_provisions_receive_only_binding():
    db = FakeDB()
    subscriber = RecordingSubscriber()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=subscriber
    )

    setup = await service.setup(owner_user_id=OWNER_ID)
    result = await service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=setup.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )

    assert setup.state == "ready"
    assert setup.required_permissions_ready is True
    assert RAW_INSTAGRAM_ID not in setup.model_dump_json()
    assert result.status == "connected"
    assert result.send_allowed is False
    assert result.comment_reply_allowed is False
    assert subscriber.calls == [{
        "owner_user_id": OWNER_ID,
        "instagram_account_id": RAW_INSTAGRAM_ID,
        "page_id": PAGE_ID,
    }]
    stored = db.collections[CHANNELS_COLLECTION].rows[0]
    assert stored["provider"] == "instagram"
    assert stored["status"] == "connected"
    assert stored["ingress_enabled"] is True
    assert stored["egress_mode"] == "disabled"
    assert stored["send_allowed"] is False
    assert stored["ai_auto_reply_allowed"] is False
    assert stored["webhook_subscription_status"] == "confirmed"
    assert stored["webhook_subscription_checked_at"] == NOW
    assert RAW_INSTAGRAM_ID not in repr(stored)

    connected = await service.setup(owner_user_id=OWNER_ID)
    assert connected.state == "connected"


@pytest.mark.asyncio
async def test_existing_local_binding_is_not_connected_until_meta_subscription_is_repaired():
    db = FakeDB()
    initial_subscriber = RecordingSubscriber()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=initial_subscriber
    )
    setup = await service.setup(owner_user_id=OWNER_ID)
    await service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=setup.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )
    stored = db.collections[CHANNELS_COLLECTION].rows[0]
    stored.pop("webhook_subscription_status")
    stored.pop("webhook_subscription_checked_at")

    repair_subscriber = RecordingSubscriber()
    repair_service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=repair_subscriber
    )
    before = await repair_service.setup(owner_user_id=OWNER_ID)
    repaired = await repair_service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=before.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )

    assert before.state == "ready"
    assert repaired.status == "connected"
    assert repair_subscriber.calls[0]["page_id"] == PAGE_ID
    assert stored["webhook_subscription_status"] == "confirmed"
    assert (await repair_service.setup(owner_user_id=OWNER_ID)).state == "connected"


@pytest.mark.asyncio
async def test_provider_subscription_failure_does_not_create_local_connected_binding():
    db = FakeDB()
    service = InstagramProvisioningService(
        db,
        now=lambda: NOW,
        webhook_subscriber=RecordingSubscriber(
            error_code="instagram_webhook_subscription_failed"
        ),
    )
    setup = await service.setup(owner_user_id=OWNER_ID)

    with pytest.raises(InstagramProvisioningError) as failure:
        await service.provision(
            owner_user_id=OWNER_ID,
            request=InstagramProvisionIn(
                candidate_ref=setup.candidates[0].candidate_ref,
                confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
            ),
        )

    assert str(failure.value) == "instagram_webhook_subscription_failed"
    assert db.collections[CHANNELS_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_meta_subscription_uses_instagram_account_edge_and_verifies_app(monkeypatch):
    db = FakeDB()
    token_key = Fernet.generate_key()
    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", APP_ID)
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    user_token = "encrypted-user-token"
    page_token = "transient-page-token"
    db.collections[META_CREDENTIALS_COLLECTION].rows[0][
        "access_token_ciphertext"
    ] = Fernet(token_key).encrypt(user_token.encode())
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/me/accounts"):
            assert request.url.params["access_token"] == user_token
            return Response(
                200,
                json={
                    "data": [{
                        "id": PAGE_ID,
                        "access_token": page_token,
                        "instagram_business_account": {"id": RAW_INSTAGRAM_ID},
                    }]
                },
            )
        assert request.url.path.endswith(
            f"/{RAW_INSTAGRAM_ID}/subscribed_apps"
        )
        assert not request.url.path.endswith(f"/{PAGE_ID}/subscribed_apps")
        assert request.url.params["access_token"] == page_token
        if request.method == "POST":
            assert request.url.params["subscribed_fields"] == "messages,comments"
            return Response(200, json={"success": True})
        return Response(
            200,
            json={
                "data": [{
                    "id": APP_ID,
                    "subscribed_fields": ["comments", "messages"],
                }]
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        fields = await subscribe_instagram_webhooks(
            db,
            owner_user_id=OWNER_ID,
            instagram_account_id=RAW_INSTAGRAM_ID,
            page_id=PAGE_ID,
            client=client,
        )

    assert fields == ("messages", "comments")
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert all(
        f"/{RAW_INSTAGRAM_ID}/subscribed_apps" in request.url.path
        for request in requests[1:]
    )
    assert user_token not in repr(db.collections[META_CREDENTIALS_COLLECTION].rows)
    assert page_token not in repr(db.collections[META_CREDENTIALS_COLLECTION].rows)


@pytest.mark.asyncio
async def test_meta_subscription_verification_requires_both_instagram_fields(monkeypatch):
    db = FakeDB()
    token_key = Fernet.generate_key()
    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", APP_ID)
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    db.collections[META_CREDENTIALS_COLLECTION].rows[0][
        "access_token_ciphertext"
    ] = Fernet(token_key).encrypt(b"user-token")

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/me/accounts"):
            return Response(
                200,
                json={
                    "data": [{
                        "id": PAGE_ID,
                        "access_token": "page-token",
                        "instagram_business_account": {"id": RAW_INSTAGRAM_ID},
                    }]
                },
            )
        if request.method == "POST":
            return Response(200, json={"success": True})
        return Response(
            200,
            json={
                "data": [{
                    "id": APP_ID,
                    "subscribed_fields": ["messages"],
                }]
            },
        )

    async with AsyncClient(transport=MockTransport(handler)) as client:
        with pytest.raises(MetaInstagramWebhookError) as failure:
            await subscribe_instagram_webhooks(
                db,
                owner_user_id=OWNER_ID,
                instagram_account_id=RAW_INSTAGRAM_ID,
                page_id=PAGE_ID,
                client=client,
            )

    assert str(failure.value) == "instagram_webhook_subscription_failed"
    assert failure.value.http_status == 200


@pytest.mark.asyncio
async def test_meta_provider_error_logs_only_safe_diagnostics(monkeypatch, caplog):
    db = FakeDB()
    token_key = Fernet.generate_key()
    monkeypatch.setenv("META_TOKEN_ENC_KEY", token_key.decode())
    monkeypatch.setenv("META_BUSINESS_APP_ID", APP_ID)
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "provider-secret")
    user_token = "must-not-leak-user-token"
    page_token = "must-not-leak-page-token"
    db.collections[META_CREDENTIALS_COLLECTION].rows[0][
        "access_token_ciphertext"
    ] = Fernet(token_key).encrypt(user_token.encode())

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/me/accounts"):
            return Response(
                200,
                json={
                    "data": [{
                        "id": PAGE_ID,
                        "access_token": page_token,
                        "instagram_business_account": {"id": RAW_INSTAGRAM_ID},
                    }]
                },
            )
        return Response(
            400,
            json={
                "error": {
                    "message": f"provider rejected {page_token}",
                    "code": 100,
                    "error_subcode": 33,
                    "fbtrace_id": "SAFE_TRACE_2",
                }
            },
        )

    with caplog.at_level(logging.WARNING, logger="mezan.meta_instagram_webhooks"):
        async with AsyncClient(transport=MockTransport(handler)) as client:
            with pytest.raises(MetaInstagramWebhookError) as failure:
                await subscribe_instagram_webhooks(
                    db,
                    owner_user_id=OWNER_ID,
                    instagram_account_id=RAW_INSTAGRAM_ID,
                    page_id=PAGE_ID,
                    client=client,
                )

    error = failure.value
    assert error.safe_diagnostics == {
        "http_status": 400,
        "meta_error_code": 100,
        "error_subcode": 33,
        "trace_id": "SAFE_TRACE_2",
    }
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "http_status=400" in log_text
    assert "meta_error_code=100" in log_text
    assert "error_subcode=33" in log_text
    assert "trace_id=SAFE_TRACE_2" in log_text
    assert user_token not in log_text
    assert page_token not in log_text
    assert "provider rejected" not in log_text
    assert user_token not in str(error)
    assert page_token not in str(error)
    assert user_token not in repr(error.__dict__)
    assert page_token not in repr(error.__dict__)
    assert error.__cause__ is None


@pytest.mark.asyncio
async def test_pages_messaging_is_not_required_for_instagram_account_subscription():
    db = FakeDB(
        scopes={
            "instagram_basic",
            "instagram_manage_comments",
            "instagram_manage_messages",
            "pages_manage_metadata",
        }
    )
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=RecordingSubscriber()
    )

    setup = await service.setup(owner_user_id=OWNER_ID)

    assert "pages_messaging" not in INSTAGRAM_REQUIRED_PERMISSIONS
    assert setup.state == "ready"
    assert setup.required_permissions_ready is True


@pytest.mark.asyncio
async def test_missing_required_meta_permission_requires_reauthorization_without_write():
    db = FakeDB(
        scopes=INSTAGRAM_REQUIRED_PERMISSIONS - {"pages_manage_metadata"}
    )
    service = InstagramProvisioningService(db, now=lambda: NOW)

    setup = await service.setup(owner_user_id=OWNER_ID)

    assert setup.state == "meta_reauthorization_required"
    assert setup.required_permissions_ready is False
    assert db.collections[CHANNELS_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_owner_route_is_confirmed_and_has_no_instagram_send_operation():
    db = FakeDB()
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=RecordingSubscriber()
    )

    async def current_user():
        return {"id": OWNER_ID, "role": "owner"}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
            instagram_provisioning_service=service,
        ),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        setup = await client.get(
            "/api/customer-intelligence/v1/channels/instagram/setup"
        )
        candidate_ref = setup.json()["candidates"][0]["candidate_ref"]
        invalid = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/setup",
            json={"candidate_ref": candidate_ref, "confirmation": "CONNECT"},
        )
        connected = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/setup",
            json={
                "candidate_ref": candidate_ref,
                "confirmation": INSTAGRAM_PROVISION_CONFIRMATION,
            },
        )
        send = await client.post(
            "/api/customer-intelligence/v1/channels/instagram/send",
            json={"text": "must never leave Mezan"},
        )

    assert setup.status_code == 200
    assert RAW_INSTAGRAM_ID not in setup.text
    assert invalid.status_code == 422
    assert connected.status_code == 201
    assert connected.json()["send_allowed"] is False
    assert send.status_code in {404, 405}


def _instagram_webhook_body() -> bytes:
    payload = {
        "object": "instagram",
        "entry": [{
            "id": RAW_INSTAGRAM_ID,
            "time": 1786942800,
            "messaging": [{
                "sender": {"id": "17841409999990001"},
                "recipient": {"id": RAW_INSTAGRAM_ID},
                "timestamp": 1786942800000,
                "message": {
                    "mid": "ig_mid_customer_question_1",
                    "text": "هل المنتج متوفر؟",
                },
            }],
            "changes": [{
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
            }],
        }],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_instagram_headers(body: bytes, *, secret: str) -> dict[str, str]:
    signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return {"x-hub-signature-256": f"sha256={signature}"}


async def _provision_instagram_binding(db: FakeDB) -> None:
    service = InstagramProvisioningService(
        db, now=lambda: NOW, webhook_subscriber=RecordingSubscriber()
    )
    setup = await service.setup(owner_user_id=OWNER_ID)
    await service.provision(
        owner_user_id=OWNER_ID,
        request=InstagramProvisionIn(
            candidate_ref=setup.candidates[0].candidate_ref,
            confirmation=INSTAGRAM_PROVISION_CONFIRMATION,
        ),
    )


@pytest.mark.asyncio
async def test_signed_realistic_instagram_message_and_comment_are_normalized():
    db = FakeDB()
    await _provision_instagram_binding(db)
    secret = "instagram-webhook-secret"
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        db,
        verify_token="instagram-verify-token",
        app_secret=secret,
    )

    batch = await adapter.verify_and_normalize(
        headers=_signed_instagram_headers(body, secret=secret),
        body=body,
    )

    assert len(batch.messages) == 2
    assert batch.direct_message_count == 1
    assert batch.comment_count == 1
    assert {
        (context.user_id, context.merchant_id, context.provider)
        for context, _message in batch.messages
    } == {(OWNER_ID, MERCHANT_ID, "instagram")}
    messages = {message.source_event: message for _context, message in batch.messages}
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
async def test_instagram_webhook_rejects_bad_signature_before_account_lookup():
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        FakeDB(),
        verify_token="instagram-verify-token",
        app_secret="instagram-webhook-secret",
    )

    with pytest.raises(InstagramSignatureError):
        await adapter.verify_and_normalize(
            headers={"x-hub-signature-256": "sha256=invalid"},
            body=body,
        )


@pytest.mark.asyncio
async def test_instagram_event_id_is_idempotent_in_channel_gateway():
    db = FakeDB()
    await _provision_instagram_binding(db)
    secret = "instagram-webhook-secret"
    body = _instagram_webhook_body()
    adapter = InstagramInboundAdapter(
        db,
        verify_token="instagram-verify-token",
        app_secret=secret,
    )
    batch = await adapter.verify_and_normalize(
        headers=_signed_instagram_headers(body, secret=secret),
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
    first_retry = await gateway.ingest_inbound(context=context, message=message)
    second_retry = await gateway.ingest_inbound(context=context, message=message)

    assert first_retry.duplicate is True
    assert second_retry.duplicate is True
    assert first_retry.message_id == "message-existing"
    assert second_retry.message_id == "message-existing"
    assert len(db.collections[CONVERSATION_MESSAGES_COLLECTION].rows) == 1
