"""Safety and persistence contract for Customer Intelligence conversations."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from customer_identity import CUSTOMER_IDENTITY_COLLECTION
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
    CUSTOMERS_COLLECTION,
    CUSTOMER_IDENTITIES_COLLECTION,
    FOUNDATION_COLLECTIONS,
    ChannelRecord,
    ConversationMessageRecord,
    ConversationRecord,
    CustomerRecord,
    ensure_customer_intelligence_foundation_indexes,
)


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self):
        self.indexes = []

    async def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options.get("name")


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def _index(db, collection_name, index_name):
    return next(
        (keys, options)
        for keys, options in db.collections[collection_name].indexes
        if options.get("name") == index_name
    )


def test_foundation_reuses_existing_encrypted_customer_identity_store():
    assert CUSTOMER_IDENTITIES_COLLECTION == CUSTOMER_IDENTITY_COLLECTION
    assert FOUNDATION_COLLECTIONS == {
        "customers": CUSTOMERS_COLLECTION,
        "customer_identities": CUSTOMER_IDENTITY_COLLECTION,
        "channels": CHANNELS_COLLECTION,
        "conversations": CONVERSATIONS_COLLECTION,
        "conversation_messages": CONVERSATION_MESSAGES_COLLECTION,
    }
    assert len(set(FOUNDATION_COLLECTIONS.values())) == 5


def test_customer_profile_cannot_create_a_parallel_identity_or_store_plain_pii():
    customer = CustomerRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        customer_id="cust-1",
        customer_identity_id="cust-1",
        first_seen_at=NOW,
        last_activity_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    assert customer.customer_id == customer.customer_identity_id
    assert customer.plaintext_pii_stored is False

    with pytest.raises(ValidationError, match="canonical customer_identity_id"):
        CustomerRecord(
            user_id="owner-1",
            merchant_id="merchant-1",
            customer_id="parallel-customer",
            customer_identity_id="cust-1",
            first_seen_at=NOW,
            last_activity_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )

    with pytest.raises(ValidationError):
        CustomerRecord(
            user_id="owner-1",
            merchant_id="merchant-1",
            customer_id="cust-1",
            customer_identity_id="cust-1",
            first_seen_at=NOW,
            last_activity_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            phone="+966500000000",
        )


def test_channels_are_receive_ready_but_all_sending_remains_contractually_off():
    channel = ChannelRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        channel_id="channel-whatsapp-1",
        provider="whatsapp",
        external_account_key="hmac:account:1",
        status="planned",
        created_at=NOW,
        updated_at=NOW,
    )

    assert channel.ingress_enabled is False
    assert channel.egress_mode == "disabled"
    assert channel.send_allowed is False
    assert channel.ai_auto_reply_allowed is False

    with pytest.raises(ValidationError):
        ChannelRecord(
            **channel.model_dump(exclude={"send_allowed"}),
            send_allowed=True,
        )


def test_conversation_and_message_contracts_contain_no_plaintext_content_fields():
    conversation = ConversationRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        conversation_id="conversation-1",
        channel_id="channel-whatsapp-1",
        customer_id="cust-1",
        external_conversation_key="hmac:conversation:1",
        status="open",
        started_at=NOW,
        last_message_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    message = ConversationMessageRecord(
        user_id="owner-1",
        merchant_id="merchant-1",
        message_id="message-1",
        conversation_id=conversation.conversation_id,
        channel_id=conversation.channel_id,
        customer_id=conversation.customer_id,
        external_message_key="hmac:message:1",
        direction="inbound",
        sender_type="customer",
        content_type="text",
        content_ciphertext=b"encrypted-message",
        content_fields=["text"],
        source_event="messages",
        occurred_at=NOW,
        received_at=NOW,
        delivery_state="received",
        created_at=NOW,
    )

    assert conversation.plaintext_pii_stored is False
    assert message.plaintext_content_stored is False
    assert "text" not in ConversationMessageRecord.model_fields
    assert "transcript" not in ConversationMessageRecord.model_fields
    assert "media_url" not in ConversationMessageRecord.model_fields

    with pytest.raises(ValidationError):
        ConversationMessageRecord(
            **message.model_dump(),
            text="customer plaintext",
        )


@pytest.mark.parametrize(
    ("direction", "delivery_state"),
    [("inbound", "sent"), ("outbound", "received")],
)
def test_message_direction_rejects_impossible_delivery_states(
    direction,
    delivery_state,
):
    with pytest.raises(ValidationError):
        ConversationMessageRecord(
            user_id="owner-1",
            merchant_id="merchant-1",
            message_id="message-1",
            conversation_id="conversation-1",
            channel_id="channel-whatsapp-1",
            customer_id="cust-1",
            external_message_key="hmac:message:1",
            direction=direction,
            sender_type="customer" if direction == "inbound" else "employee",
            content_type="text",
            content_ciphertext=b"encrypted-message",
            source_event="messages",
            occurred_at=NOW,
            received_at=NOW,
            delivery_state=delivery_state,
            created_at=NOW,
        )


@pytest.mark.asyncio
async def test_all_foundation_indexes_are_tenant_and_merchant_scoped():
    db = FakeDB()

    await ensure_customer_intelligence_foundation_indexes(db)

    expected_collections = set(FOUNDATION_COLLECTIONS.values()) | {"unified_orders"}
    assert expected_collections.issubset(db.collections)

    for logical_name in (
        "customers",
        "conversations",
        "conversation_messages",
    ):
        collection_name = FOUNDATION_COLLECTIONS[logical_name]
        for keys, _options in db.collections[collection_name].indexes:
            assert keys[0] == ("user_id", 1)
            assert keys[1] == ("merchant_id", 1)

    for keys, options in db.collections[CHANNELS_COLLECTION].indexes:
        if options.get("name") == "mezan_customer_channels_inbound_binding_unique":
            assert keys == [("provider", 1), ("external_account_key", 1)]
            assert options["unique"] is True
        else:
            assert keys[0] == ("user_id", 1)
            assert keys[1] == ("merchant_id", 1)

    keys, options = _index(
        db,
        CONVERSATION_MESSAGES_COLLECTION,
        "mezan_customer_messages_provider_unique",
    )
    assert options["unique"] is True
    assert keys == [
        ("user_id", 1),
        ("merchant_id", 1),
        ("channel_id", 1),
        ("external_message_key", 1),
    ]

    keys, options = _index(
        db,
        CONVERSATIONS_COLLECTION,
        "mezan_customer_conversations_provider_unique",
    )
    assert options["unique"] is True
    assert ("external_conversation_key", 1) in keys

    keys, options = _index(
        db,
        CONVERSATIONS_COLLECTION,
        "mezan_customer_conversations_live_inbox",
    )
    assert options.get("unique") is not True
    assert keys == [
        ("user_id", 1),
        ("merchant_id", 1),
        ("channel_id", 1),
        ("last_message_at", -1),
        ("conversation_id", 1),
    ]
