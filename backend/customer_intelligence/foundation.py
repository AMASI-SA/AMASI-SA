"""Persistence contracts for Mezan's channel-neutral customer memory.

This module establishes the five logical entities required before a channel
gateway can ingest real conversations.  The existing encrypted
``mezan_customer_identities_v1`` collection remains the canonical identity
vault; it is referenced here instead of being copied into a parallel store.

The foundation intentionally exposes no routes, provider clients, send
methods, commerce mutations or AI execution.  Message content is represented
only as encrypted bytes, while the surrounding records keep non-PII routing
and audit metadata needed by Mezan.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from customer_identity import (
    CUSTOMER_IDENTITY_COLLECTION,
    ensure_customer_identity_indexes,
)


FOUNDATION_SCHEMA_VERSION = 1

CUSTOMERS_COLLECTION = "mezan_customers_v1"
# This is an alias to the already-deployed encrypted identity vault.  Do not
# create another identity collection for Customer Intelligence.
CUSTOMER_IDENTITIES_COLLECTION = CUSTOMER_IDENTITY_COLLECTION
CHANNELS_COLLECTION = "mezan_customer_channels_v1"
CONVERSATIONS_COLLECTION = "mezan_customer_conversations_v1"
CONVERSATION_MESSAGES_COLLECTION = "mezan_customer_conversation_messages_v1"

CUSTOMER_INTELLIGENCE_ANALYSIS_TARGETS = (
    "reply_context",
    "customer_signal",
    "problem_detection",
    "sales_opportunity",
    "product_feedback",
    "service_quality",
    "decision_support",
)
EMPLOYEE_RESPONSE_ANALYSIS_TARGETS = (
    "reply_context",
    "service_quality",
    "decision_support",
)

FOUNDATION_COLLECTIONS = {
    "customers": CUSTOMERS_COLLECTION,
    "customer_identities": CUSTOMER_IDENTITIES_COLLECTION,
    "channels": CHANNELS_COLLECTION,
    "conversations": CONVERSATIONS_COLLECTION,
    "conversation_messages": CONVERSATION_MESSAGES_COLLECTION,
}


class FoundationRecord(BaseModel):
    """Strict Mongo persistence contract, separate from public API models."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class CustomerRecord(FoundationRecord):
    """Non-PII customer profile keyed by the canonical encrypted identity."""

    schema_version: Literal[1] = FOUNDATION_SCHEMA_VERSION
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    customer_identity_id: str = Field(min_length=1)
    status: Literal["active", "merged", "blocked"] = "active"
    preferred_language: str | None = None
    contact_permission: Literal["unknown", "allowed", "declined"] = "unknown"
    first_seen_at: datetime
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    plaintext_pii_stored: Literal[False] = False

    @model_validator(mode="after")
    def canonical_identity_is_customer_id(self) -> "CustomerRecord":
        # A separate customer profile collection must not introduce a second
        # identity graph.  The current identity-vault ID is the Mezan customer
        # ID until an explicit, audited merge model is approved later.
        if self.customer_id != self.customer_identity_id:
            raise ValueError(
                "customer_id must equal the canonical customer_identity_id"
            )
        return self


class ChannelRecord(FoundationRecord):
    """One provider account connected to a Mezan tenant and merchant."""

    schema_version: Literal[1] = FOUNDATION_SCHEMA_VERSION
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    provider: Literal["whatsapp", "instagram", "tiktok"]
    # Stable global HMAC of the provider account reference; never a phone
    # number, handle, access token or provider secret in plaintext.  Its only
    # global use is resolving a signed inbound webhook to its tenant binding.
    external_account_key: str = Field(min_length=1)
    status: Literal["planned", "disconnected", "connected", "paused", "error"]
    ingress_enabled: bool = False
    egress_mode: Literal["disabled"] = "disabled"
    send_allowed: Literal[False] = False
    ai_auto_reply_allowed: Literal[False] = False
    created_at: datetime
    updated_at: datetime
    plaintext_credentials_stored: Literal[False] = False


class ConversationRecord(FoundationRecord):
    """A channel thread linked to exactly one canonical Mezan customer."""

    schema_version: Literal[1] = FOUNDATION_SCHEMA_VERSION
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    # HMAC of the provider conversation/contact reference.  The raw provider
    # reference belongs inside encrypted content or the provider adapter.
    external_conversation_key: str = Field(min_length=1)
    status: Literal["open", "needs_human", "follow_up_due", "resolved", "closed"]
    contact_permission: Literal["unknown", "allowed", "declined"] = "unknown"
    assigned_employee_id: str | None = None
    started_at: datetime
    last_message_at: datetime
    created_at: datetime
    updated_at: datetime
    plaintext_pii_stored: Literal[False] = False


class ConversationMessageRecord(FoundationRecord):
    """Immutable channel evidence; customer content is encrypted at rest."""

    schema_version: Literal[1] = FOUNDATION_SCHEMA_VERSION
    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    # HMAC/idempotency key for the provider's message ID.
    external_message_key: str = Field(min_length=1)
    direction: Literal["inbound", "outbound"]
    sender_type: Literal["customer", "employee"]
    content_type: Literal["text", "image", "audio", "document", "interactive"]
    content_ciphertext: bytes = Field(min_length=1)
    content_fields: list[str] = Field(default_factory=list)
    source_event: str = Field(min_length=1)
    occurred_at: datetime
    received_at: datetime
    analysis_status: Literal["pending", "ready", "failed", "not_requested"] = (
        "pending"
    )
    analysis_contract_version: Literal[1] = 1
    analysis_targets: list[
        Literal[
            "reply_context",
            "customer_signal",
            "problem_detection",
            "sales_opportunity",
            "product_feedback",
            "service_quality",
            "decision_support",
        ]
    ] = Field(default_factory=list)
    analysis_requested_at: datetime | None = None
    delivery_state: Literal["received", "sent", "delivered", "read", "failed"]
    created_at: datetime
    plaintext_content_stored: Literal[False] = False

    @model_validator(mode="after")
    def direction_matches_delivery_state(self) -> "ConversationMessageRecord":
        if self.direction == "inbound" and self.delivery_state != "received":
            raise ValueError("inbound messages must use delivery_state=received")
        if self.direction == "outbound" and self.delivery_state == "received":
            raise ValueError("outbound messages cannot use delivery_state=received")
        if self.analysis_status == "pending":
            targets = (
                EMPLOYEE_RESPONSE_ANALYSIS_TARGETS
                if self.direction == "outbound" and self.sender_type == "employee"
                else CUSTOMER_INTELLIGENCE_ANALYSIS_TARGETS
            )
            if self.analysis_targets and set(self.analysis_targets) != set(targets):
                raise ValueError("pending messages must feed their governed intelligence targets")
            self.analysis_targets = list(targets)
            self.analysis_requested_at = self.analysis_requested_at or self.received_at
        elif self.analysis_status == "not_requested":
            self.analysis_targets = []
            self.analysis_requested_at = None
        return self


async def ensure_customer_intelligence_foundation_indexes(db: Any) -> None:
    """Create tenant-scoped, idempotent indexes for the conversation core."""
    # The identity vault and its unified-order memory indexes are part of this
    # logical foundation and must exist before channel records are accepted.
    await ensure_customer_identity_indexes(db)
    # Kept as a lazy import so the channel foundation stays usable in
    # receive-only deployments where the optional OpenAI SDK is absent.
    from .reply_suggestions import ensure_reply_suggestion_indexes
    from .learning_contract import ensure_customer_learning_indexes

    await ensure_reply_suggestion_indexes(db)
    await ensure_customer_learning_indexes(db)

    customers = getattr(db, CUSTOMERS_COLLECTION)
    await customers.create_index(
        [("user_id", 1), ("merchant_id", 1), ("customer_id", 1)],
        unique=True,
        name="mezan_customers_identity_unique",
    )
    await customers.create_index(
        [("user_id", 1), ("merchant_id", 1), ("customer_identity_id", 1)],
        unique=True,
        name="mezan_customers_vault_link_unique",
    )
    await customers.create_index(
        [("user_id", 1), ("merchant_id", 1), ("last_activity_at", -1)],
        name="mezan_customers_recent_activity",
    )

    channels = getattr(db, CHANNELS_COLLECTION)
    await channels.create_index(
        [("user_id", 1), ("merchant_id", 1), ("channel_id", 1)],
        unique=True,
        name="mezan_customer_channels_identity_unique",
    )
    await channels.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("provider", 1),
            ("external_account_key", 1),
        ],
        unique=True,
        name="mezan_customer_channels_provider_unique",
    )
    await channels.create_index(
        [("provider", 1), ("external_account_key", 1)],
        unique=True,
        name="mezan_customer_channels_inbound_binding_unique",
    )
    await channels.create_index(
        [("user_id", 1), ("merchant_id", 1), ("provider", 1), ("status", 1)],
        name="mezan_customer_channels_status",
    )

    conversations = getattr(db, CONVERSATIONS_COLLECTION)
    await conversations.create_index(
        [("user_id", 1), ("merchant_id", 1), ("conversation_id", 1)],
        unique=True,
        name="mezan_customer_conversations_identity_unique",
    )
    await conversations.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("channel_id", 1),
            ("external_conversation_key", 1),
        ],
        unique=True,
        name="mezan_customer_conversations_provider_unique",
    )
    await conversations.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("customer_id", 1),
            ("last_message_at", -1),
        ],
        name="mezan_customer_conversations_customer_recent",
    )
    await conversations.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("status", 1),
            ("last_message_at", -1),
        ],
        name="mezan_customer_conversations_queue",
    )
    await conversations.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("channel_id", 1),
            ("last_message_at", -1),
            ("conversation_id", 1),
        ],
        name="mezan_customer_conversations_live_inbox",
    )

    messages = getattr(db, CONVERSATION_MESSAGES_COLLECTION)
    await messages.create_index(
        [("user_id", 1), ("merchant_id", 1), ("message_id", 1)],
        unique=True,
        name="mezan_customer_messages_identity_unique",
    )
    await messages.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("channel_id", 1),
            ("external_message_key", 1),
        ],
        unique=True,
        name="mezan_customer_messages_provider_unique",
    )
    await messages.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("conversation_id", 1),
            ("occurred_at", 1),
            ("message_id", 1),
        ],
        name="mezan_customer_messages_timeline",
    )
    await messages.create_index(
        [
            ("user_id", 1),
            ("merchant_id", 1),
            ("analysis_status", 1),
            ("received_at", 1),
        ],
        name="mezan_customer_messages_analysis_queue",
    )


__all__ = [
    "CHANNELS_COLLECTION",
    "CONVERSATIONS_COLLECTION",
    "CONVERSATION_MESSAGES_COLLECTION",
    "CUSTOMERS_COLLECTION",
    "CUSTOMER_INTELLIGENCE_ANALYSIS_TARGETS",
    "EMPLOYEE_RESPONSE_ANALYSIS_TARGETS",
    "CUSTOMER_IDENTITIES_COLLECTION",
    "FOUNDATION_COLLECTIONS",
    "FOUNDATION_SCHEMA_VERSION",
    "ChannelRecord",
    "ConversationMessageRecord",
    "ConversationRecord",
    "CustomerRecord",
    "ensure_customer_intelligence_foundation_indexes",
]
