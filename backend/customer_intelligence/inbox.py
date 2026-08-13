"""Permission-scoped, no-egress view of persisted customer conversations.

Channel webhooks store customer identity and message content encrypted at rest.
This module is the only read boundary for the live inbox: it scopes every
query to channels owned by the authenticated Mezan owner, decrypts only the
fields required by the screen, and never exposes provider identifiers, phone
numbers, credentials, or a send capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from customer_identity import (
    CUSTOMER_IDENTITY_COLLECTION,
    decrypt_private_payload,
)

from .foundation import (
    CHANNELS_COLLECTION,
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
)
from .reply_suggestions import (
    CustomerIntelligenceActor,
    ReplySuggestionPublic,
    ReplySuggestionService,
)


class InboxResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class LiveInboxMessage(InboxResponseModel):
    message_id: str
    direction: Literal["inbound", "outbound"]
    sender: Literal["customer", "employee"]
    kind: Literal["text", "image", "audio", "document", "interactive"]
    body: str | None = None
    caption: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    occurred_at: datetime
    delivery_state: Literal["received", "sent", "delivered", "read", "failed"]
    surface: Literal["direct_message", "comment", "unknown"] = "direct_message"
    content_available: bool = True


class LiveInboxConversation(InboxResponseModel):
    conversation_id: str
    customer_id: str
    customer_name: str
    channel: Literal["whatsapp", "instagram"]
    surface: Literal["direct_message", "comment", "unknown"] = "direct_message"
    status: Literal["open", "needs_human", "follow_up_due", "resolved", "closed"]
    last_message: str
    last_message_at: datetime
    message_count: int = Field(ge=0)
    content_unavailable_count: int = Field(default=0, ge=0)
    messages: list[LiveInboxMessage]
    reply_suggestion: ReplySuggestionPublic | None = None


class LiveInboxConnection(InboxResponseModel):
    provider: Literal["whatsapp", "instagram"]
    connected_channels: int = Field(ge=0)
    receiving_channels: int = Field(ge=0)
    status: Literal["connected", "not_connected"]


class LiveInboxSafetyPolicy(InboxResponseModel):
    mode: Literal["observe_only"] = "observe_only"
    receive_only: Literal[True] = True
    writes_allowed: Literal[False] = False
    whatsapp_send_allowed: Literal[False] = False
    instagram_send_allowed: Literal[False] = False
    instagram_comment_reply_allowed: Literal[False] = False
    ai_auto_reply_allowed: Literal[False] = False
    commerce_mutation_allowed: Literal[False] = False


class LiveInboxResponse(InboxResponseModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    mode: Literal["live_receive_only"] = "live_receive_only"
    data_origin: Literal["whatsapp_webhook", "channel_webhooks"] = "whatsapp_webhook"
    connection: LiveInboxConnection
    connections: list[LiveInboxConnection] = Field(default_factory=list)
    conversation_count: int = Field(ge=0)
    message_count: int = Field(ge=0)
    content_unavailable_count: int = Field(default=0, ge=0)
    has_more: bool = False
    next_offset: int | None = Field(default=None, ge=0)
    conversations: list[LiveInboxConversation]
    safety_policy: LiveInboxSafetyPolicy = Field(default_factory=LiveInboxSafetyPolicy)


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    rendered = str(value).strip()
    return rendered or None


def _datetime(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _find_many(
    collection: Any,
    query: dict[str, Any],
    projection: dict[str, int],
    *,
    sort: list[tuple[str, int]] | None = None,
    skip: int = 0,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = collection.find(query, projection)
    if sort:
        cursor = cursor.sort(sort)
    if skip:
        cursor = cursor.skip(skip)
    cursor = cursor.limit(limit)
    return await cursor.to_list(length=limit)


def _decrypt(ciphertext: Any) -> tuple[dict[str, Any], bool]:
    try:
        return decrypt_private_payload(ciphertext), True
    except (RuntimeError, TypeError, ValueError):
        # A key-rotation/configuration problem must not leak ciphertext or take
        # down the whole inbox.  The row remains visible with a neutral marker.
        return {}, False


def _customer_name(identity: dict[str, Any] | None, *, provider: str) -> str:
    profile, available = _decrypt((identity or {}).get("private_profile_ciphertext"))
    name = _text(profile.get("name")) if available else None
    return name or ("عميل إنستغرام" if provider == "instagram" else "عميل واتساب")


def _message_view(document: dict[str, Any], *, fallback: datetime) -> LiveInboxMessage:
    private, available = _decrypt(document.get("content_ciphertext"))
    payload = private.get("payload") if isinstance(private.get("payload"), dict) else {}
    kind = _text(document.get("content_type")) or "interactive"
    if kind not in {"text", "image", "audio", "document", "interactive"}:
        kind = "interactive"

    body = _text(payload.get("text")) if kind == "text" else None
    caption = _text(payload.get("caption"))
    filename = _text(payload.get("filename"))
    mime_type = _text(payload.get("mime_type"))
    surface = _text(payload.get("surface")) or "direct_message"
    if surface not in {"direct_message", "comment", "unknown"}:
        surface = "unknown"
    return LiveInboxMessage(
        message_id=str(document.get("message_id") or "message"),
        direction=(
            "outbound" if document.get("direction") == "outbound" else "inbound"
        ),
        sender=(
            "employee" if document.get("sender_type") == "employee" else "customer"
        ),
        kind=kind,
        body=body,
        caption=caption,
        filename=filename,
        mime_type=mime_type,
        occurred_at=_datetime(document.get("occurred_at"), fallback=fallback),
        delivery_state=(
            document.get("delivery_state")
            if document.get("delivery_state")
            in {"received", "sent", "delivered", "read", "failed"}
            else ("sent" if document.get("direction") == "outbound" else "received")
        ),
        surface=surface,
        content_available=available,
    )


def _message_summary(message: LiveInboxMessage, *, provider: str) -> str:
    if not message.content_available:
        return "تعذر عرض محتوى الرسالة المشفّر"
    if message.body:
        return message.body
    if message.caption:
        return message.caption
    labels = {
        "image": "صورة",
        "audio": "رسالة صوتية",
        "document": message.filename or "مستند",
        "interactive": "تفاعل إنستغرام" if provider == "instagram" else "تفاعل واتساب",
    }
    return labels.get(message.kind, "رسالة واردة")


class CustomerIntelligenceInboxService:
    """Read persisted WhatsApp and Instagram evidence for one owner."""

    def __init__(self, db: Any, *, now: Any | None = None):
        self._db = db
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def inbox(
        self,
        *,
        owner_user_id: str,
        actor_id: str | None = None,
        is_owner: bool = True,
        limit: int = 20,
        messages_limit: int = 30,
        offset: int = 0,
    ) -> LiveInboxResponse:
        generated_at = self._now()
        owner_id = str(owner_user_id).strip()
        resolved_actor_id = str(actor_id or owner_id).strip()
        channel_collection = getattr(self._db, CHANNELS_COLLECTION)
        channel_projection = {
            "_id": 0,
            "user_id": 1,
            "merchant_id": 1,
            "channel_id": 1,
            "provider": 1,
            "status": 1,
            "ingress_enabled": 1,
            "egress_mode": 1,
            "send_allowed": 1,
            "ai_auto_reply_allowed": 1,
            "updated_at": 1,
        }
        channel_documents: list[dict[str, Any]] = []
        for provider in ("whatsapp", "instagram"):
            channel_documents.extend(
                await _find_many(
                    channel_collection,
                    {"user_id": owner_id, "provider": provider},
                    channel_projection,
                    sort=[("updated_at", -1)],
                    limit=20,
                )
            )

        # Fail closed if an out-of-band database edit violates receive-only.
        safe_channels = [
            channel
            for channel in channel_documents
            if channel.get("egress_mode") == "disabled"
            and channel.get("send_allowed") is False
            and channel.get("ai_auto_reply_allowed") is False
        ]
        connections: list[LiveInboxConnection] = []
        receiving_channels: list[dict[str, Any]] = []
        for provider in ("whatsapp", "instagram"):
            provider_safe = [
                channel for channel in safe_channels if channel.get("provider") == provider
            ]
            provider_connected = [
                channel for channel in provider_safe if channel.get("status") == "connected"
            ]
            provider_receiving = [
                channel
                for channel in provider_connected
                if channel.get("ingress_enabled") is True
            ]
            receiving_channels.extend(provider_receiving)
            connections.append(
                LiveInboxConnection(
                    provider=provider,
                    connected_channels=len(provider_connected),
                    receiving_channels=len(provider_receiving),
                    status="connected" if provider_receiving else "not_connected",
                )
            )
        # ``connection`` remains the WhatsApp compatibility field for older
        # clients. New clients use the complete provider list above.
        connection = connections[0]
        data_origin = (
            "channel_webhooks"
            if any(row.get("provider") == "instagram" for row in channel_documents)
            else "whatsapp_webhook"
        )
        if not receiving_channels:
            return LiveInboxResponse(
                generated_at=generated_at,
                data_origin=data_origin,
                connection=connection,
                connections=connections,
                conversation_count=0,
                message_count=0,
                conversations=[],
            )

        scopes = [
            {
                "user_id": owner_id,
                "merchant_id": str(channel.get("merchant_id") or ""),
                "channel_id": str(channel.get("channel_id") or ""),
            }
            for channel in receiving_channels
            if channel.get("merchant_id") and channel.get("channel_id")
        ]
        if not scopes:
            return LiveInboxResponse(
                generated_at=generated_at,
                data_origin=data_origin,
                connection=connection,
                connections=connections,
                conversation_count=0,
                message_count=0,
                conversations=[],
            )

        conversation_collection = getattr(self._db, CONVERSATIONS_COLLECTION)
        conversation_query: dict[str, Any] = {"$or": scopes}
        if not is_owner:
            # Customer Service can work the shared unassigned queue and its
            # own threads, but cannot see conversations assigned to another
            # employee. New gateway conversations are intentionally unassigned.
            conversation_query["$and"] = [
                {
                    "$or": [
                        {"assigned_employee_id": resolved_actor_id},
                        {"assigned_employee_id": None},
                        {"assigned_employee_id": {"$exists": False}},
                    ]
                }
            ]
        conversation_count = await conversation_collection.count_documents(
            conversation_query
        )
        conversation_documents = await _find_many(
            conversation_collection,
            conversation_query,
            {
                "_id": 0,
                "user_id": 1,
                "merchant_id": 1,
                "conversation_id": 1,
                "channel_id": 1,
                "customer_id": 1,
                "status": 1,
                "assigned_employee_id": 1,
                "last_message_at": 1,
            },
            sort=[("last_message_at", -1), ("conversation_id", 1)],
            skip=offset,
            limit=limit,
        )
        has_more = offset + len(conversation_documents) < conversation_count

        message_collection = getattr(self._db, CONVERSATION_MESSAGES_COLLECTION)
        total_message_query = {"$or": scopes}
        # Employees must never infer traffic volumes outside their assigned
        # conversations. Count only the fully scoped visible IDs.
        if not is_owner:
            visible_message_scopes = [
                {
                    "user_id": owner_id,
                    "merchant_id": str(row.get("merchant_id") or ""),
                    "channel_id": str(row.get("channel_id") or ""),
                    "conversation_id": str(row.get("conversation_id") or ""),
                }
                for row in conversation_documents
                if row.get("merchant_id")
                and row.get("channel_id")
                and row.get("conversation_id")
            ]
            total_message_query = (
                {"$or": visible_message_scopes}
                if visible_message_scopes
                else {"conversation_id": "__none__"}
            )
        total_message_count = await message_collection.count_documents(
            total_message_query
        )

        conversations: list[LiveInboxConversation] = []
        content_unavailable_count = 0
        channel_providers = {
            str(channel.get("channel_id") or ""): str(channel.get("provider") or "")
            for channel in receiving_channels
        }
        for conversation in conversation_documents:
            scope = {
                "user_id": owner_id,
                "merchant_id": str(conversation.get("merchant_id") or ""),
                "channel_id": str(conversation.get("channel_id") or ""),
                "conversation_id": str(conversation.get("conversation_id") or ""),
            }
            conversation_message_count = await message_collection.count_documents(scope)
            message_documents = await _find_many(
                message_collection,
                scope,
                {
                    "_id": 0,
                    "message_id": 1,
                    "content_type": 1,
                    "content_ciphertext": 1,
                    "occurred_at": 1,
                    "direction": 1,
                    "sender_type": 1,
                    "delivery_state": 1,
                },
                sort=[("occurred_at", -1), ("message_id", -1)],
                limit=messages_limit,
            )
            messages = [
                _message_view(document, fallback=generated_at)
                for document in reversed(message_documents)
            ]
            content_unavailable_count += sum(
                not message.content_available for message in messages
            )

            identity = await getattr(
                self._db,
                CUSTOMER_IDENTITY_COLLECTION,
            ).find_one(
                {
                    "user_id": owner_id,
                    "merchant_id": scope["merchant_id"],
                    "customer_identity_id": str(conversation.get("customer_id") or ""),
                },
                {"_id": 0, "private_profile_ciphertext": 1},
            )
            last_message_at = _datetime(
                conversation.get("last_message_at"),
                fallback=messages[-1].occurred_at if messages else generated_at,
            )
            status_value = _text(conversation.get("status")) or "open"
            if status_value not in {
                "open",
                "needs_human",
                "follow_up_due",
                "resolved",
                "closed",
            }:
                status_value = "open"
            provider = channel_providers.get(scope["channel_id"], "whatsapp")
            if provider not in {"whatsapp", "instagram"}:
                provider = "whatsapp"
            surface = messages[-1].surface if messages else "unknown"
            conversations.append(
                LiveInboxConversation(
                    conversation_id=scope["conversation_id"],
                    customer_id=str(conversation.get("customer_id") or "customer"),
                    customer_name=_customer_name(identity, provider=provider),
                    channel=provider,
                    surface=surface,
                    status=status_value,
                    last_message=(
                        _message_summary(messages[-1], provider=provider)
                        if messages
                        else "لا توجد رسالة قابلة للعرض"
                    ),
                    last_message_at=last_message_at,
                    message_count=conversation_message_count,
                    content_unavailable_count=sum(
                        not message.content_available for message in messages
                    ),
                    messages=messages,
                )
            )

        suggestion_actor = CustomerIntelligenceActor(
            actor_id=resolved_actor_id,
            owner_user_id=owner_id,
            permissions=frozenset(),
            is_owner=is_owner,
        )
        suggestion_service = ReplySuggestionService(self._db)
        for conversation in conversations:
            conversation.reply_suggestion = await suggestion_service.latest(
                actor=suggestion_actor,
                conversation_id=conversation.conversation_id,
                pending_only=True,
            )

        return LiveInboxResponse(
            generated_at=generated_at,
            data_origin=data_origin,
            connection=connection,
            connections=connections,
            conversation_count=conversation_count,
            message_count=total_message_count,
            content_unavailable_count=content_unavailable_count,
            has_more=has_more,
            next_offset=(offset + len(conversations)) if has_more else None,
            conversations=conversations,
        )


__all__ = [
    "CustomerIntelligenceInboxService",
    "LiveInboxConversation",
    "LiveInboxMessage",
    "LiveInboxResponse",
]
