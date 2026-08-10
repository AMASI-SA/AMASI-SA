"""Hermetic receive-only tests for the unified Channel Gateway."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

import customer_identity
from customer_intelligence.channel_gateway import (
    ChannelGateway,
    ChannelNotReadyError,
    ChannelPolicyError,
    NormalizedInboundMessage,
    TrustedChannelContext,
)
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
    CUSTOMERS_COLLECTION,
    ChannelRecord,
)


NOW = datetime(2026, 8, 11, 13, 0, tzinfo=timezone.utc)


def _get_path(document, path):
    value = document
    for part in str(path).split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches(document, selector):
    for key, expected in selector.items():
        actual = _get_path(document, key)
        if isinstance(expected, dict) and "$in" in expected:
            if isinstance(actual, list):
                if not set(actual).intersection(expected["$in"]):
                    return False
            elif actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])

    async def find_one(self, selector, projection=None):
        del projection
        document = next(
            (row for row in self.documents if _matches(row, selector)),
            None,
        )
        return deepcopy(document) if document is not None else None

    async def update_one(self, selector, update, upsert=False):
        operator_paths = {
            operator: set(values)
            for operator, values in update.items()
            if isinstance(values, dict)
        }
        operators = list(operator_paths)
        for index, left in enumerate(operators):
            for right in operators[index + 1 :]:
                overlap = operator_paths[left].intersection(operator_paths[right])
                assert not overlap, (
                    f"MongoDB rejects paths shared by {left} and {right}: "
                    f"{sorted(overlap)}"
                )

        document = next(
            (row for row in self.documents if _matches(row, selector)),
            None,
        )
        inserted = document is None
        if inserted:
            if not upsert:
                return SimpleNamespace(upserted_id=None)
            document = deepcopy(selector)
            self.documents.append(document)
            for key, value in update.get("$setOnInsert", {}).items():
                document[key] = deepcopy(value)
        for key, value in update.get("$set", {}).items():
            document[key] = deepcopy(value)
        for key, value in update.get("$max", {}).items():
            if key not in document or document[key] < value:
                document[key] = deepcopy(value)
        for key, value in update.get("$addToSet", {}).items():
            values = document.setdefault(key, [])
            candidates = value.get("$each", []) if isinstance(value, dict) else [value]
            for candidate in candidates:
                if candidate not in values:
                    values.append(deepcopy(candidate))
        return SimpleNamespace(upserted_id="created" if inserted else None)

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")


class FakeDB:
    def __init__(self, channel):
        self.collections = {
            CHANNELS_COLLECTION: FakeCollection([channel]),
            CUSTOMERS_COLLECTION: FakeCollection(),
            CONVERSATIONS_COLLECTION: FakeCollection(),
            CONVERSATION_MESSAGES_COLLECTION: FakeCollection(),
            customer_identity.CUSTOMER_IDENTITY_COLLECTION: FakeCollection(),
            "unified_orders": FakeCollection(),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture(autouse=True)
def _customer_encryption_key(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CUSTOMER_PII_ENC_KEY",
        Fernet.generate_key().decode("utf-8"),
    )
    monkeypatch.delenv("MEZAN_CUSTOMER_PII_ENC_KEY_OLD", raising=False)
    monkeypatch.delenv("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", raising=False)
    customer_identity._fernet = None
    yield
    customer_identity._fernet = None


def _context(provider="whatsapp"):
    return TrustedChannelContext(
        user_id="owner-1",
        merchant_id="merchant-1",
        channel_id=f"channel-{provider}-1",
        provider=provider,
    )


def _channel(context, *, ingress_enabled=True):
    return ChannelRecord(
        user_id=context.user_id,
        merchant_id=context.merchant_id,
        channel_id=context.channel_id,
        provider=context.provider,
        external_account_key="hmac:provider-account",
        status="connected",
        ingress_enabled=ingress_enabled,
        created_at=NOW,
        updated_at=NOW,
    ).model_dump()


def _message(provider="whatsapp"):
    return NormalizedInboundMessage(
        provider=provider,
        external_conversation_id="raw-conversation-0500000000",
        external_message_id="raw-message-provider-1",
        external_customer_id="raw-provider-customer-1",
        customer_mobile="+966500000000",
        customer_email="buyer@example.test",
        customer_profile={"name": "Private Buyer"},
        preferred_language="ar",
        content_type="text",
        content_payload={"text": "هل المنتج متوفر؟"},
        occurred_at=NOW,
        source_event="messages",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["whatsapp", "instagram", "tiktok"])
async def test_gateway_uses_one_receive_path_for_all_supported_channels(provider):
    context = _context(provider)
    db = FakeDB(_channel(context))

    result = await ChannelGateway(db).ingest_inbound(
        context=context,
        message=_message(provider),
    )

    assert result.accepted is True
    assert result.provider == provider
    assert result.duplicate is False
    assert result.message_send_allowed is False
    assert result.ai_execution_allowed is False
    assert result.commerce_mutation_allowed is False
    assert len(db.collections[CUSTOMERS_COLLECTION].documents) == 1
    assert len(db.collections[CONVERSATIONS_COLLECTION].documents) == 1
    assert len(db.collections[CONVERSATION_MESSAGES_COLLECTION].documents) == 1


@pytest.mark.asyncio
async def test_gateway_encrypts_customer_and_message_content_and_is_idempotent():
    context = _context()
    db = FakeDB(_channel(context))
    gateway = ChannelGateway(db)
    incoming = _message()

    first = await gateway.ingest_inbound(context=context, message=incoming)
    second = await gateway.ingest_inbound(context=context, message=incoming)

    assert first.duplicate is False
    assert second.duplicate is True
    assert first.customer_id == second.customer_id
    assert first.conversation_id == second.conversation_id
    assert first.message_id == second.message_id

    identity_rows = db.collections[
        customer_identity.CUSTOMER_IDENTITY_COLLECTION
    ].documents
    message_rows = db.collections[CONVERSATION_MESSAGES_COLLECTION].documents
    assert len(identity_rows) == 1
    assert len(db.collections[CUSTOMERS_COLLECTION].documents) == 1
    assert len(db.collections[CONVERSATIONS_COLLECTION].documents) == 1
    assert len(message_rows) == 1

    serialized = repr(db.collections)
    for private_value in (
        "raw-conversation-0500000000",
        "raw-message-provider-1",
        "raw-provider-customer-1",
        "+966500000000",
        "buyer@example.test",
        "Private Buyer",
        "هل المنتج متوفر؟",
    ):
        assert private_value not in serialized

    private_profile = customer_identity.decrypt_private_payload(
        identity_rows[0]["private_profile_ciphertext"]
    )
    private_content = customer_identity.decrypt_private_payload(
        message_rows[0]["content_ciphertext"]
    )
    assert private_profile == {
        "email": "buyer@example.test",
        "mobile": "+966500000000",
        "name": "Private Buyer",
    }
    assert private_content == {
        "content_type": "text",
        "payload": {"text": "هل المنتج متوفر؟"},
    }
    assert message_rows[0]["direction"] == "inbound"
    assert message_rows[0]["delivery_state"] == "received"


@pytest.mark.asyncio
async def test_gateway_rejects_disabled_or_policy_unsafe_channel_before_writes():
    context = _context()
    disabled_db = FakeDB(_channel(context, ingress_enabled=False))

    with pytest.raises(ChannelNotReadyError):
        await ChannelGateway(disabled_db).ingest_inbound(
            context=context,
            message=_message(),
        )

    unsafe = _channel(context)
    unsafe["send_allowed"] = True
    unsafe_db = FakeDB(unsafe)
    with pytest.raises(ChannelPolicyError):
        await ChannelGateway(unsafe_db).ingest_inbound(
            context=context,
            message=_message(),
        )

    for db in (disabled_db, unsafe_db):
        assert db.collections[CUSTOMERS_COLLECTION].documents == []
        assert db.collections[CONVERSATIONS_COLLECTION].documents == []
        assert db.collections[CONVERSATION_MESSAGES_COLLECTION].documents == []
        assert db.collections[
            customer_identity.CUSTOMER_IDENTITY_COLLECTION
        ].documents == []


@pytest.mark.asyncio
async def test_gateway_rejects_provider_mismatch_before_writes():
    context = _context("whatsapp")
    db = FakeDB(_channel(context))

    with pytest.raises(ChannelPolicyError):
        await ChannelGateway(db).ingest_inbound(
            context=context,
            message=_message("instagram"),
        )

    assert db.collections[CONVERSATION_MESSAGES_COLLECTION].documents == []


def test_normalizer_rejects_provider_credentials_and_gateway_has_no_send_api():
    with pytest.raises(ValidationError, match="provider credentials"):
        NormalizedInboundMessage(
            provider="whatsapp",
            external_conversation_id="conversation-1",
            external_message_id="message-1",
            external_customer_id="customer-1",
            content_type="text",
            content_payload={"text": "hello", "access_token": "forbidden"},
            occurred_at=NOW,
            source_event="messages",
        )

    public_methods = {
        name for name in dir(ChannelGateway) if not name.startswith("_")
    }
    assert public_methods == {"ingest_inbound"}
