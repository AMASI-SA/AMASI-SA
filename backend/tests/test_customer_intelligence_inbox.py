"""Live, receive-only read contract for Customer Intelligence conversations."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import customer_identity
from ai_store_access_contract import ROLE_ASSIGNMENTS
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
    ChannelRecord,
    ConversationMessageRecord,
    ConversationRecord,
)
from customer_intelligence.inbox import CustomerIntelligenceInboxService
from customer_intelligence.routes import make_customer_intelligence_router


NOW = datetime(2026, 8, 12, 1, 30, tzinfo=timezone.utc)
OWNER = {"id": "owner-live", "role": "owner"}
MERCHANT_ID = "merchant-live"
CHANNEL_ID = "channel-whatsapp-live"
CUSTOMER_ID = "cust_live_1"
CONVERSATION_ID = "conv_live_1"


def _matches(document, selector):
    if "$and" in selector and not all(
        _matches(document, option) for option in selector["$and"]
    ):
        return False
    if "$or" in selector and not any(
        _matches(document, option) for option in selector["$or"]
    ):
        return False
    for key, expected in selector.items():
        if key in {"$or", "$and"}:
            continue
        if isinstance(expected, dict) and "$exists" in expected:
            if (key in document) is not bool(expected["$exists"]):
                return False
        elif document.get(key) != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def sort(self, specification):
        for key, direction in reversed(specification):
            self.documents.sort(
                key=lambda row: (row.get(key) is not None, row.get(key)),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        self.documents = self.documents[:value]
        return self

    def skip(self, value):
        self.documents = self.documents[value:]
        return self

    async def to_list(self, *, length):
        return deepcopy(self.documents[:length])


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    def find(self, selector, projection=None):
        del projection
        return FakeCursor(
            [row for row in self.documents if _matches(row, selector)]
        )

    async def find_one(self, selector, projection=None):
        del projection
        document = next(
            (row for row in self.documents if _matches(row, selector)),
            None,
        )
        return deepcopy(document) if document is not None else None

    async def count_documents(self, selector):
        return sum(1 for row in self.documents if _matches(row, selector))


class FakeDB:
    def __init__(self, collections):
        self.collections = {
            name: FakeCollection(documents)
            for name, documents in collections.items()
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CUSTOMER_PII_ENC_KEY",
        Fernet.generate_key().decode("utf-8"),
    )
    monkeypatch.delenv("MEZAN_CUSTOMER_PII_ENC_KEY_OLD", raising=False)
    customer_identity._fernet = None
    yield
    customer_identity._fernet = None


def _db(*, unsafe_channel=False):
    channel = ChannelRecord(
        user_id=OWNER["id"],
        merchant_id=MERCHANT_ID,
        channel_id=CHANNEL_ID,
        provider="whatsapp",
        external_account_key="account:v1:hidden-binding",
        status="connected",
        ingress_enabled=True,
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW,
    ).model_dump()
    if unsafe_channel:
        channel["send_allowed"] = True

    conversation = ConversationRecord(
        user_id=OWNER["id"],
        merchant_id=MERCHANT_ID,
        conversation_id=CONVERSATION_ID,
        channel_id=CHANNEL_ID,
        customer_id=CUSTOMER_ID,
        external_conversation_key="external:v1:hidden-conversation",
        status="open",
        started_at=NOW - timedelta(minutes=10),
        last_message_at=NOW,
        created_at=NOW - timedelta(minutes=10),
        updated_at=NOW,
    ).model_dump()
    message = ConversationMessageRecord(
        user_id=OWNER["id"],
        merchant_id=MERCHANT_ID,
        message_id="msg_live_1",
        conversation_id=CONVERSATION_ID,
        channel_id=CHANNEL_ID,
        customer_id=CUSTOMER_ID,
        external_message_key="external:v1:hidden-message",
        direction="inbound",
        sender_type="customer",
        content_type="text",
        content_ciphertext=customer_identity.encrypt_private_payload(
            {"content_type": "text", "payload": {"text": "اختبار ربط ميزان 2"}}
        ),
        content_fields=["text"],
        source_event="whatsapp.messages.text",
        occurred_at=NOW,
        received_at=NOW,
        analysis_status="pending",
        delivery_state="received",
        created_at=NOW,
    ).model_dump()
    identity = {
        "user_id": OWNER["id"],
        "merchant_id": MERCHANT_ID,
        "customer_identity_id": CUSTOMER_ID,
        "private_profile_ciphertext": customer_identity.encrypt_private_payload(
            {"name": "عميل الاختبار", "mobile": "+966500000000"}
        ),
    }
    return FakeDB(
        {
            CHANNELS_COLLECTION: [channel],
            CONVERSATIONS_COLLECTION: [conversation],
            CONVERSATION_MESSAGES_COLLECTION: [message],
            customer_identity.CUSTOMER_IDENTITY_COLLECTION: [identity],
        }
    )


@pytest.mark.asyncio
async def test_live_inbox_decrypts_display_content_without_exposing_provider_pii():
    payload = (
        await CustomerIntelligenceInboxService(
            _db(),
            now=lambda: NOW,
        ).inbox(owner_user_id=OWNER["id"])
    ).model_dump(mode="json")

    assert payload["mode"] == "live_receive_only"
    assert payload["data_origin"] == "whatsapp_webhook"
    assert payload["connection"] == {
        "provider": "whatsapp",
        "connected_channels": 1,
        "receiving_channels": 1,
        "status": "connected",
    }
    assert payload["connections"] == [
        payload["connection"],
        {
            "provider": "instagram",
            "connected_channels": 0,
            "receiving_channels": 0,
            "status": "not_connected",
        },
    ]
    assert payload["conversation_count"] == 1
    assert payload["message_count"] == 1
    assert payload["content_unavailable_count"] == 0
    assert payload["has_more"] is False
    assert payload["next_offset"] is None
    assert payload["conversations"][0]["customer_name"] == "عميل الاختبار"
    assert payload["conversations"][0]["messages"][0]["body"] == "اختبار ربط ميزان 2"
    assert payload["safety_policy"] == {
        "mode": "observe_only",
        "receive_only": True,
        "writes_allowed": False,
        "whatsapp_send_allowed": False,
        "instagram_send_allowed": False,
        "instagram_comment_reply_allowed": False,
        "ai_auto_reply_allowed": False,
        "commerce_mutation_allowed": False,
    }

    rendered = json.dumps(payload, ensure_ascii=False)
    assert "+966500000000" not in rendered
    assert "hidden-binding" not in rendered
    assert "hidden-conversation" not in rendered
    assert "hidden-message" not in rendered
    assert "ciphertext" not in rendered
    assert "provider_media_id" not in rendered


@pytest.mark.asyncio
async def test_live_inbox_unifies_instagram_comments_without_treating_them_as_private():
    db = _db()
    channel_id = "channel-instagram-live"
    conversation_id = "conv-instagram-comment"
    customer_id = "cust-instagram-comment"
    db.collections[CHANNELS_COLLECTION].documents.append(
        ChannelRecord(
            user_id=OWNER["id"],
            merchant_id=MERCHANT_ID,
            channel_id=channel_id,
            provider="instagram",
            external_account_key="account:v1:hidden-instagram-binding",
            status="connected",
            ingress_enabled=True,
            created_at=NOW,
            updated_at=NOW,
        ).model_dump()
    )
    db.collections[CONVERSATIONS_COLLECTION].documents.append(
        ConversationRecord(
            user_id=OWNER["id"],
            merchant_id=MERCHANT_ID,
            conversation_id=conversation_id,
            channel_id=channel_id,
            customer_id=customer_id,
            external_conversation_key="external:v1:hidden-comment-thread",
            status="open",
            started_at=NOW + timedelta(seconds=1),
            last_message_at=NOW + timedelta(seconds=1),
            created_at=NOW + timedelta(seconds=1),
            updated_at=NOW + timedelta(seconds=1),
        ).model_dump()
    )
    db.collections[CONVERSATION_MESSAGES_COLLECTION].documents.append(
        ConversationMessageRecord(
            user_id=OWNER["id"],
            merchant_id=MERCHANT_ID,
            message_id="msg-instagram-comment",
            conversation_id=conversation_id,
            channel_id=channel_id,
            customer_id=customer_id,
            external_message_key="external:v1:hidden-instagram-comment",
            direction="inbound",
            sender_type="customer",
            content_type="text",
            content_ciphertext=customer_identity.encrypt_private_payload(
                {
                    "content_type": "text",
                    "payload": {
                        "surface": "comment",
                        "text": "هل يتوفر بلون آخر؟",
                        "comment_id": "private-provider-comment-id",
                    },
                }
            ),
            content_fields=["surface", "text", "comment_id"],
            source_event="instagram.comments.comment",
            occurred_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(seconds=1),
            delivery_state="received",
            created_at=NOW + timedelta(seconds=1),
        ).model_dump()
    )

    payload = await CustomerIntelligenceInboxService(
        db,
        now=lambda: NOW + timedelta(seconds=2),
    ).inbox(owner_user_id=OWNER["id"])

    assert payload.data_origin == "channel_webhooks"
    assert [row.status for row in payload.connections] == ["connected", "connected"]
    instagram = next(row for row in payload.conversations if row.channel == "instagram")
    assert instagram.surface == "comment"
    assert instagram.messages[0].surface == "comment"
    assert instagram.messages[0].body == "هل يتوفر بلون آخر؟"
    assert "private-provider-comment-id" not in payload.model_dump_json()
    assert payload.safety_policy.instagram_send_allowed is False
    assert payload.safety_policy.instagram_comment_reply_allowed is False


@pytest.mark.asyncio
async def test_live_inbox_fails_closed_when_channel_write_policy_is_unsafe():
    payload = await CustomerIntelligenceInboxService(
        _db(unsafe_channel=True),
        now=lambda: NOW,
    ).inbox(owner_user_id=OWNER["id"])

    assert payload.connection.status == "not_connected"
    assert payload.conversations == []
    assert payload.safety_policy.whatsapp_send_allowed is False


@pytest.mark.asyncio
async def test_live_inbox_pagination_reaches_older_conversations_without_overlap():
    db = _db()
    newest = db.collections[CONVERSATIONS_COLLECTION].documents[0]
    older = deepcopy(newest)
    older.update(
        {
            "conversation_id": "conv_live_older",
            "customer_id": "cust_live_older",
            "external_conversation_key": "external:v1:hidden-older",
            "last_message_at": NOW - timedelta(hours=1),
        }
    )
    db.collections[CONVERSATIONS_COLLECTION].documents.append(older)
    service = CustomerIntelligenceInboxService(db, now=lambda: NOW)

    first = await service.inbox(owner_user_id=OWNER["id"], limit=1, offset=0)
    second = await service.inbox(owner_user_id=OWNER["id"], limit=1, offset=1)

    assert first.conversation_count == 2
    assert first.has_more is True
    assert first.next_offset == 1
    assert [row.conversation_id for row in first.conversations] == [CONVERSATION_ID]
    assert second.has_more is False
    assert second.next_offset is None
    assert [row.conversation_id for row in second.conversations] == [
        "conv_live_older"
    ]


@pytest.mark.asyncio
async def test_live_inbox_never_reads_a_colliding_conversation_from_another_owner():
    db = _db()
    rogue = deepcopy(db.collections[CONVERSATIONS_COLLECTION].documents[0])
    rogue.update(
        {
            "user_id": "other-owner",
            "conversation_id": "conv_other_owner",
            "last_message_at": NOW + timedelta(minutes=1),
        }
    )
    db.collections[CONVERSATIONS_COLLECTION].documents.append(rogue)

    payload = await CustomerIntelligenceInboxService(
        db,
        now=lambda: NOW,
    ).inbox(owner_user_id=OWNER["id"])

    assert payload.conversation_count == 1
    assert [row.conversation_id for row in payload.conversations] == [
        CONVERSATION_ID
    ]


@pytest.mark.asyncio
async def test_live_inbox_marks_corrupt_ciphertext_without_leaking_it():
    db = _db()
    db.collections[CONVERSATION_MESSAGES_COLLECTION].documents[0][
        "content_ciphertext"
    ] = b"not-valid-fernet-ciphertext"

    payload = await CustomerIntelligenceInboxService(
        db,
        now=lambda: NOW,
    ).inbox(owner_user_id=OWNER["id"])

    assert payload.content_unavailable_count == 1
    conversation = payload.conversations[0]
    assert conversation.content_unavailable_count == 1
    assert conversation.last_message == "تعذر عرض محتوى الرسالة المشفّر"
    assert conversation.messages[0].content_available is False
    assert conversation.messages[0].body is None
    assert "not-valid-fernet" not in payload.model_dump_json()


@pytest.mark.asyncio
async def test_live_inbox_shows_employee_echo_as_history_not_as_ai_suggestion():
    db = _db()
    outbound = ConversationMessageRecord(
        user_id=OWNER["id"],
        merchant_id=MERCHANT_ID,
        message_id="msg_employee_echo_1",
        conversation_id=CONVERSATION_ID,
        channel_id=CHANNEL_ID,
        customer_id=CUSTOMER_ID,
        external_message_key="external:v1:hidden-echo",
        direction="outbound",
        sender_type="employee",
        content_type="text",
        content_ciphertext=customer_identity.encrypt_private_payload(
            {"content_type": "text", "payload": {"text": "أهلًا، تم الرد من واتساب."}}
        ),
        content_fields=["text"],
        source_event="360dialog.smb_message_echo",
        occurred_at=NOW + timedelta(seconds=1),
        received_at=NOW + timedelta(seconds=1),
        analysis_status="not_requested",
        delivery_state="read",
        created_at=NOW + timedelta(seconds=1),
    ).model_dump()
    db.collections[CONVERSATION_MESSAGES_COLLECTION].documents.append(outbound)

    payload = await CustomerIntelligenceInboxService(
        db,
        now=lambda: NOW + timedelta(seconds=2),
    ).inbox(owner_user_id=OWNER["id"])

    conversation = payload.conversations[0]
    assert conversation.message_count == 2
    assert conversation.messages[-1].direction == "outbound"
    assert conversation.messages[-1].sender == "employee"
    assert conversation.messages[-1].delivery_state == "read"
    assert conversation.reply_suggestion is None


@pytest.mark.asyncio
async def test_customer_service_inbox_only_contains_assigned_conversations():
    db = _db()
    assigned = db.collections[CONVERSATIONS_COLLECTION].documents[0]
    assigned["assigned_employee_id"] = "employee-live"
    unassigned = deepcopy(assigned)
    unassigned.update(
        {
            "conversation_id": "conv_unassigned",
            "customer_id": "cust_unassigned",
            "assigned_employee_id": None,
            "external_conversation_key": "external:v1:unassigned",
            "last_message_at": NOW - timedelta(minutes=1),
        }
    )
    assigned_to_other = deepcopy(assigned)
    assigned_to_other.update(
        {
            "conversation_id": "conv_not_assigned",
            "customer_id": "cust_not_assigned",
            "assigned_employee_id": "another-employee",
            "external_conversation_key": "external:v1:not-assigned",
            "last_message_at": NOW - timedelta(minutes=2),
        }
    )
    db.collections[CONVERSATIONS_COLLECTION].documents.extend(
        [unassigned, assigned_to_other]
    )
    db.collections[ROLE_ASSIGNMENTS] = FakeCollection(
        [
            {
                "owner_user_id": OWNER["id"],
                "user_id": "employee-live",
                "created_by": OWNER["id"],
                "role_key": "customer_service",
                "enabled": True,
            }
        ]
    )

    async def current_user():
        return {
            "id": "employee-live",
            "role": "employee",
            "created_by": OWNER["id"],
        }

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(current_user, db=db),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/customer-intelligence/v1/inbox")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["conversation_count"] == 2
    assert [row["conversation_id"] for row in payload["conversations"]] == [
        CONVERSATION_ID,
        "conv_unassigned",
    ]


@pytest.mark.asyncio
async def test_owner_inbox_route_is_get_only_and_disables_browser_caching():
    async def current_user():
        return deepcopy(OWNER)

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(current_user, db=_db()),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/customer-intelligence/v1/inbox")
        send = await client.post(
            "/api/customer-intelligence/v1/inbox/send",
            json={"message": "must remain blocked"},
        )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["conversations"][0]["messages"][0]["body"] == "اختبار ربط ميزان 2"
    assert send.status_code in {404, 405}


@pytest.mark.asyncio
async def test_live_inbox_is_owner_only_before_database_read():
    class SpyService:
        calls = 0

        async def inbox(self, **kwargs):
            del kwargs
            self.calls += 1
            raise AssertionError("unauthorized user reached live inbox")

    async def current_user():
        return {
            "id": "employee-live",
            "role": "employee",
            "created_by": OWNER["id"],
        }

    service = SpyService()
    app = FastAPI()
    db = _db()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
            inbox_service=service,
        ),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/customer-intelligence/v1/inbox")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "customer_intelligence_permission_required"
    assert response.json()["detail"]["permission"] == "customer_intelligence.inbox.read"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_live_inbox_flag_is_independent_from_synthetic_preview_flag(monkeypatch):
    monkeypatch.setenv("MEZAN_CUSTOMER_INTELLIGENCE_PHASE1_ENABLED", "false")
    monkeypatch.setenv("MEZAN_CUSTOMER_INTELLIGENCE_LIVE_INBOX_ENABLED", "true")

    async def current_user():
        return deepcopy(OWNER)

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(current_user, db=_db()),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        workspace = await client.get("/api/customer-intelligence/v1/workspace")
        inbox = await client.get("/api/customer-intelligence/v1/inbox")

    assert workspace.status_code == 404
    assert workspace.json()["detail"]["code"] == "feature_disabled"
    assert inbox.status_code == 200
    assert inbox.json()["mode"] == "live_receive_only"
