"""Unified inbound Channel Gateway for Mezan Customer Intelligence.

Provider adapters (WhatsApp first, then Instagram and TikTok) must verify the
provider request and resolve it to a tenant-scoped ``TrustedChannelContext``.
Only then may they pass a normalized inbound message to this service.

The gateway owns identity resolution and encrypted persistence inside Mezan.
It deliberately has no provider client, GPT client, send method, order service,
discount service, payment service or product mutation capability.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import Field, SecretStr, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from customer_identity import (
    build_identity_keys,
    encrypt_private_payload,
    resolve_customer_identity,
)

from .foundation import (
    CHANNELS_COLLECTION,
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
    CUSTOMERS_COLLECTION,
    ConversationMessageRecord,
    ConversationRecord,
    CustomerRecord,
    FoundationRecord,
)
from .reply_suggestions import mark_pending_suggestions_stale

ProviderName = Literal["whatsapp", "instagram", "tiktok"]
MAX_NORMALIZED_PRIVATE_PAYLOAD_BYTES = 256 * 1024
_FORBIDDEN_PRIVATE_KEYS = {
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "password",
    "secret",
    "signature",
}


class ChannelGatewayError(RuntimeError):
    """Base error safe for adapters to map without exposing customer data."""


class ChannelNotReadyError(ChannelGatewayError):
    """The verified provider event has no active Mezan channel binding."""


class ChannelPolicyError(ChannelGatewayError):
    """The channel or normalized event violates the receive-only policy."""


class CustomerResolutionError(ChannelGatewayError):
    """The inbound event lacks enough identity evidence to link a customer."""


class TrustedChannelContext(FoundationRecord):
    """Tenant binding produced only after provider verification by an adapter."""

    user_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    provider: ProviderName


class NormalizedInboundMessage(FoundationRecord):
    """Provider-neutral message accepted by the gateway after verification."""

    provider: ProviderName
    external_conversation_id: SecretStr
    external_message_id: SecretStr
    external_customer_id: SecretStr | None = None
    customer_mobile: SecretStr | None = None
    customer_email: SecretStr | None = None
    customer_profile: dict[str, Any] = Field(default_factory=dict, repr=False)
    preferred_language: str | None = None
    content_type: Literal["text", "image", "audio", "document", "interactive"]
    content_payload: dict[str, Any] = Field(repr=False)
    occurred_at: datetime
    source_event: str = Field(min_length=1, max_length=120)
    direction: Literal["inbound", "outbound"] = "inbound"
    sender_type: Literal["customer", "employee"] = "customer"
    analysis_status: Literal["pending", "not_requested"] = "pending"
    delivery_state: Literal["received", "sent", "delivered", "read", "failed"] = (
        "received"
    )

    @field_validator("external_conversation_id", "external_message_id")
    @classmethod
    def required_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("external provider reference cannot be empty")
        return value

    @field_validator(
        "external_customer_id",
        "customer_mobile",
        "customer_email",
    )
    @classmethod
    def optional_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def bounded_secret_free_payload(self) -> "NormalizedInboundMessage":
        _reject_provider_credentials(self.content_payload)
        _reject_provider_credentials(self.customer_profile)
        rendered = json.dumps(
            {
                "content": self.content_payload,
                "customer_profile": self.customer_profile,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        if len(rendered) > MAX_NORMALIZED_PRIVATE_PAYLOAD_BYTES:
            raise ValueError("normalized private payload exceeds the gateway limit")
        if self.direction == "inbound":
            if self.sender_type != "customer" or self.delivery_state != "received":
                raise ValueError("inbound events must be received customer messages")
        elif (
            self.sender_type != "employee"
            or self.delivery_state == "received"
            or self.analysis_status != "pending"
        ):
            raise ValueError(
                "outbound echoes must be employee evidence queued for response-quality analysis"
            )
        return self


class ChannelAdapter(Protocol):
    """Boundary every provider adapter must implement before gateway ingest."""

    provider: ProviderName

    async def verify_and_normalize(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> list[tuple[TrustedChannelContext, NormalizedInboundMessage]]: ...


class InboundIngestResult(FoundationRecord):
    accepted: Literal[True] = True
    duplicate: bool
    provider: ProviderName
    customer_id: str
    conversation_id: str
    message_id: str
    message_send_allowed: Literal[False] = False
    ai_execution_allowed: Literal[False] = False
    commerce_mutation_allowed: Literal[False] = False


def _reject_provider_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().casefold() in _FORBIDDEN_PRIVATE_KEYS:
                raise ValueError("provider credentials are not normalized message data")
            _reject_provider_credentials(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_provider_credentials(child)


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    rendered = value.get_secret_value().strip()
    return rendered or None


def build_channel_account_key(provider: ProviderName, external_account_id: str) -> str:
    """Build the non-reversible key used to route a verified provider event."""
    secret = (
        os.environ.get("MEZAN_CHANNEL_BINDING_HMAC_KEY", "").strip()
        or os.environ.get("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", "").strip()
        or os.environ.get("MEZAN_CUSTOMER_PII_ENC_KEY", "").strip()
        or os.environ.get("SALLA_TOKEN_ENC_KEY", "").strip()
    )
    reference = str(external_account_id).strip()
    if not secret or not reference:
        raise ChannelPolicyError(
            "channel binding HMAC configuration and account reference are required"
        )
    digest = hmac.new(
        secret.encode("utf-8"),
        f"mezan-channel-account-v1\x1f{provider}\x1f{reference}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"account:v1:{digest}"


def _reference_key(
    *,
    context: TrustedChannelContext,
    namespace: str,
    external_reference: str,
) -> str:
    keys = build_identity_keys(
        user_id=context.user_id,
        merchant_id=context.merchant_id,
        # Provider message/conversation IDs are not guaranteed to be unique
        # across two phone numbers owned by the same tenant.
        source_system=f"{context.provider}:{context.channel_id}:{namespace}",
        external_customer_id=external_reference,
    )
    if not keys:  # defensive; required SecretStr validators normally prevent it
        raise ChannelPolicyError("provider reference could not be normalized")
    return keys[0]


def _legacy_reference_key(
    *,
    context: TrustedChannelContext,
    namespace: str,
    external_reference: str,
) -> str:
    """Read compatibility for rows created before channel-scoped keys."""
    keys = build_identity_keys(
        user_id=context.user_id,
        merchant_id=context.merchant_id,
        source_system=f"{context.provider}:{namespace}",
        external_customer_id=external_reference,
    )
    if not keys:
        raise ChannelPolicyError("provider reference could not be normalized")
    return keys[0]


def _stable_id(prefix: str, private_reference_key: str) -> str:
    return f"{prefix}_{private_reference_key.rsplit(':', 1)[-1][:32]}"


class ChannelGateway:
    """Receive, resolve and persist one verified inbound message in Mezan."""

    def __init__(self, db: Any):
        self._db = db

    async def ingest_inbound(
        self,
        *,
        context: TrustedChannelContext,
        message: NormalizedInboundMessage,
    ) -> InboundIngestResult:
        if context.provider != message.provider:
            raise ChannelPolicyError("adapter provider does not match channel binding")

        channel = await getattr(self._db, CHANNELS_COLLECTION).find_one(
            {
                "user_id": context.user_id,
                "merchant_id": context.merchant_id,
                "channel_id": context.channel_id,
                "provider": context.provider,
            },
            {"_id": 0},
        )
        if not channel or channel.get("status") != "connected":
            raise ChannelNotReadyError("channel is not connected in Mezan")
        if channel.get("ingress_enabled") is not True:
            raise ChannelNotReadyError("channel ingress is disabled in Mezan")

        # Receive-only must remain true even if a database row was changed
        # outside these strict models.
        if (
            channel.get("egress_mode") != "disabled"
            or channel.get("send_allowed") is not False
            or channel.get("ai_auto_reply_allowed") is not False
        ):
            raise ChannelPolicyError("channel violates the receive-only policy")

        conversation_key = _reference_key(
            context=context,
            namespace="conversation",
            external_reference=message.external_conversation_id.get_secret_value(),
        )
        message_key = _reference_key(
            context=context,
            namespace="message",
            external_reference=message.external_message_id.get_secret_value(),
        )
        legacy_message_key = _legacy_reference_key(
            context=context,
            namespace="message",
            external_reference=message.external_message_id.get_secret_value(),
        )
        message_collection = getattr(self._db, CONVERSATION_MESSAGES_COLLECTION)
        duplicate_projection = {
            "_id": 0,
            "customer_id": 1,
            "conversation_id": 1,
            "message_id": 1,
        }
        duplicate_scope = {
            "user_id": context.user_id,
            "merchant_id": context.merchant_id,
            "channel_id": context.channel_id,
        }
        duplicate = await message_collection.find_one(
            {**duplicate_scope, "external_message_key": message_key},
            duplicate_projection,
        )
        if not duplicate:
            duplicate = await message_collection.find_one(
                {**duplicate_scope, "external_message_key": legacy_message_key},
                duplicate_projection,
            )
        if duplicate:
            # A retry is an acknowledgement only.  In particular it must not
            # refresh identity/customer timestamps or reopen a closed thread.
            return InboundIngestResult(
                duplicate=True,
                provider=context.provider,
                customer_id=str(duplicate["customer_id"]),
                conversation_id=str(duplicate["conversation_id"]),
                message_id=str(duplicate["message_id"]),
            )

        conversation_collection = getattr(self._db, CONVERSATIONS_COLLECTION)
        legacy_conversation_key = _legacy_reference_key(
            context=context,
            namespace="conversation",
            external_reference=message.external_conversation_id.get_secret_value(),
        )
        conversation_scope = {
            "user_id": context.user_id,
            "merchant_id": context.merchant_id,
            "channel_id": context.channel_id,
        }
        conversation_projection = {"_id": 0, "status": 1, "last_message_at": 1}
        existing_conversation = await conversation_collection.find_one(
            {**conversation_scope, "external_conversation_key": conversation_key},
            conversation_projection,
        )
        if not existing_conversation:
            legacy_conversation = await conversation_collection.find_one(
                {
                    **conversation_scope,
                    "external_conversation_key": legacy_conversation_key,
                },
                conversation_projection,
            )
            if legacy_conversation:
                conversation_key = legacy_conversation_key
                existing_conversation = legacy_conversation

        existing_last_message_at = (
            existing_conversation.get("last_message_at")
            if existing_conversation
            else None
        )
        event_is_strictly_older = bool(
            isinstance(existing_last_message_at, datetime)
            and message.occurred_at < existing_last_message_at
        )
        event_is_not_newer = bool(
            isinstance(existing_last_message_at, datetime)
            and message.occurred_at <= existing_last_message_at
        )

        content_ciphertext = encrypt_private_payload(
            {
                "content_type": message.content_type,
                "payload": message.content_payload,
            }
        )
        if not content_ciphertext:
            raise ChannelPolicyError("normalized message content cannot be empty")

        # Invalidate a draft before later persistence steps.  This ordering is
        # intentionally conservative: if a later write fails, a potentially
        # obsolete draft is hidden and the provider retry can safely resume.
        # The duplicate pre-check above keeps normal retries side-effect free.
        if not event_is_strictly_older:
            await mark_pending_suggestions_stale(
                self._db,
                user_id=context.user_id,
                merchant_id=context.merchant_id,
                conversation_id=_stable_id("conv", conversation_key),
                reason=(
                    "smb_message_echo"
                    if message.direction == "outbound"
                    else "customer_message"
                ),
            )

        private_profile = dict(message.customer_profile)
        mobile = _secret(message.customer_mobile)
        email = _secret(message.customer_email)
        if mobile:
            private_profile["mobile"] = mobile
        if email:
            private_profile["email"] = email

        identity = await resolve_customer_identity(
            self._db,
            user_id=context.user_id,
            merchant_id=context.merchant_id,
            source_system=context.provider,
            external_customer_id=_secret(message.external_customer_id),
            email=email,
            mobile=mobile,
            private_profile=private_profile,
            observed_at=message.occurred_at,
        )
        if not identity:
            raise CustomerResolutionError(
                "inbound message has no resolvable customer identity evidence"
            )

        customer_id = str(identity["customer_identity_id"])
        conversation_id = _stable_id("conv", conversation_key)
        message_id = _stable_id("msg", message_key)
        now = datetime.now(timezone.utc)

        customer = CustomerRecord(
            user_id=context.user_id,
            merchant_id=context.merchant_id,
            customer_id=customer_id,
            customer_identity_id=customer_id,
            preferred_language=message.preferred_language,
            first_seen_at=message.occurred_at,
            last_activity_at=message.occurred_at,
            created_at=now,
            updated_at=now,
        ).model_dump()
        await getattr(self._db, CUSTOMERS_COLLECTION).update_one(
            {
                "user_id": context.user_id,
                "merchant_id": context.merchant_id,
                "customer_id": customer_id,
            },
            {
                "$setOnInsert": {
                    key: value
                    for key, value in customer.items()
                    if key
                    not in {
                        "schema_version",
                        "customer_identity_id",
                        "preferred_language",
                        "last_activity_at",
                        "updated_at",
                        "plaintext_pii_stored",
                    }
                },
                "$set": {
                    "schema_version": customer["schema_version"],
                    "customer_identity_id": customer_id,
                    "preferred_language": message.preferred_language,
                    "plaintext_pii_stored": False,
                    "updated_at": now,
                },
                "$max": {"last_activity_at": message.occurred_at},
            },
            upsert=True,
        )

        conversation = ConversationRecord(
            user_id=context.user_id,
            merchant_id=context.merchant_id,
            conversation_id=conversation_id,
            channel_id=context.channel_id,
            customer_id=customer_id,
            external_conversation_key=conversation_key,
            status=("open" if message.direction == "inbound" else "needs_human"),
            started_at=message.occurred_at,
            last_message_at=message.occurred_at,
            created_at=now,
            updated_at=now,
        ).model_dump()
        conversation_set = {
            "schema_version": conversation["schema_version"],
            "customer_id": customer_id,
            "plaintext_pii_stored": False,
            "updated_at": now,
        }
        if message.direction == "outbound" and not event_is_not_newer:
            conversation_set.update(
                {
                    "status": "needs_human",
                    "human_takeover_at": message.occurred_at,
                }
            )
        elif message.direction == "inbound" and not (
            event_is_not_newer
            and existing_conversation
            and existing_conversation.get("status") in {"resolved", "closed"}
        ):
            conversation_set["status"] = "open"

        await conversation_collection.update_one(
            {
                "user_id": context.user_id,
                "merchant_id": context.merchant_id,
                "channel_id": context.channel_id,
                "external_conversation_key": conversation_key,
            },
            {
                "$setOnInsert": {
                    key: value
                    for key, value in conversation.items()
                    if key
                    not in {
                        "schema_version",
                        "customer_id",
                        "status",
                        "last_message_at",
                        "updated_at",
                        "plaintext_pii_stored",
                    }
                },
                "$set": conversation_set,
                "$max": {"last_message_at": message.occurred_at},
            },
            upsert=True,
        )

        message_record = ConversationMessageRecord(
            user_id=context.user_id,
            merchant_id=context.merchant_id,
            message_id=message_id,
            conversation_id=conversation_id,
            channel_id=context.channel_id,
            customer_id=customer_id,
            external_message_key=message_key,
            direction=message.direction,
            sender_type=message.sender_type,
            content_type=message.content_type,
            content_ciphertext=content_ciphertext,
            content_fields=sorted(str(key) for key in message.content_payload),
            source_event=message.source_event,
            occurred_at=message.occurred_at,
            received_at=now,
            analysis_status=message.analysis_status,
            delivery_state=message.delivery_state,
            created_at=now,
        ).model_dump()
        try:
            result = await message_collection.update_one(
                {
                    "user_id": context.user_id,
                    "merchant_id": context.merchant_id,
                    "channel_id": context.channel_id,
                    "external_message_key": message_key,
                },
                {"$setOnInsert": message_record},
                upsert=True,
            )
        except DuplicateKeyError:
            # Another worker won the unique-key race.  Treat the callback as a
            # duplicate rather than surfacing a retry-inducing 500 response.
            result = None

        is_duplicate = result is None or getattr(result, "upserted_id", None) is None
        # A suggestion can be leased after the conservative pre-write stale
        # pass above but before this message becomes visible in the timeline.
        # Repeat invalidation only for the worker that actually inserted the
        # new evidence so that in-flight generation cannot publish an obsolete
        # draft from that narrow window. Ordinary retries remain side-effect
        # free because they return at the duplicate pre-check.
        if not is_duplicate and not event_is_strictly_older:
            await mark_pending_suggestions_stale(
                self._db,
                user_id=context.user_id,
                merchant_id=context.merchant_id,
                conversation_id=conversation_id,
                reason=(
                    "smb_message_echo"
                    if message.direction == "outbound"
                    else "customer_message"
                ),
            )
        return InboundIngestResult(
            duplicate=is_duplicate,
            provider=context.provider,
            customer_id=customer_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )

    async def record_outbound_status(
        self,
        *,
        context: TrustedChannelContext,
        external_message_id: str,
        delivery_state: Literal["sent", "delivered", "read", "failed"],
    ) -> bool:
        """Apply a channel-scoped status to an already observed outbound echo."""
        channel = await getattr(self._db, CHANNELS_COLLECTION).find_one(
            {
                "user_id": context.user_id,
                "merchant_id": context.merchant_id,
                "channel_id": context.channel_id,
                "provider": context.provider,
            },
            {"_id": 0},
        )
        if not channel or channel.get("status") != "connected":
            raise ChannelNotReadyError("channel is not connected in Mezan")
        if channel.get("ingress_enabled") is not True:
            raise ChannelNotReadyError("channel ingress is disabled in Mezan")
        if (
            channel.get("egress_mode") != "disabled"
            or channel.get("send_allowed") is not False
            or channel.get("ai_auto_reply_allowed") is not False
        ):
            raise ChannelPolicyError("channel violates the receive-only policy")

        message_key = _reference_key(
            context=context,
            namespace="message",
            external_reference=str(external_message_id),
        )
        legacy_message_key = _legacy_reference_key(
            context=context,
            namespace="message",
            external_reference=str(external_message_id),
        )
        collection = getattr(self._db, CONVERSATION_MESSAGES_COLLECTION)
        status_scope = {
            "user_id": context.user_id,
            "merchant_id": context.merchant_id,
            "channel_id": context.channel_id,
            "direction": "outbound",
            "sender_type": "employee",
        }
        existing = await collection.find_one(
            {**status_scope, "external_message_key": message_key},
            {"_id": 0, "delivery_state": 1},
        )
        if not existing:
            existing = await collection.find_one(
                {**status_scope, "external_message_key": legacy_message_key},
                {"_id": 0, "delivery_state": 1},
            )
            if existing:
                message_key = legacy_message_key
        if not existing:
            return False
        current = str(existing.get("delivery_state") or "sent")
        rank = {"sent": 1, "delivered": 2, "read": 3}
        if delivery_state != "failed" and rank.get(delivery_state, 0) <= rank.get(
            current, 0
        ):
            return False
        if delivery_state == "failed" and current in {"delivered", "read"}:
            return False
        await collection.update_one(
            {**status_scope, "external_message_key": message_key},
            {"$set": {"delivery_state": delivery_state}},
        )
        return True


__all__ = [
    "ChannelAdapter",
    "ChannelGateway",
    "ChannelGatewayError",
    "ChannelNotReadyError",
    "ChannelPolicyError",
    "CustomerResolutionError",
    "InboundIngestResult",
    "NormalizedInboundMessage",
    "TrustedChannelContext",
    "build_channel_account_key",
]
