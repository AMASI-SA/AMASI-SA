"""Receive-only Instagram adapter for comments and direct messages.

Instagram shares the same Meta application signature boundary as WhatsApp,
but it has a distinct webhook endpoint and channel binding. This adapter
accepts only signed callbacks, resolves the Instagram professional account to
one Mezan tenant, and normalizes public comments and private messages into the
channel-neutral Customer Intelligence gateway.

No Graph API client or send operation exists here. Public reply targets are
retained only inside encrypted message content for a later, separately
approved egress workflow.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from .channel_gateway import (
    ChannelGateway,
    ChannelGatewayError,
    ChannelNotReadyError,
    ChannelPolicyError,
    NormalizedInboundMessage,
    TrustedChannelContext,
    build_channel_account_key,
)
from .foundation import CHANNELS_COLLECTION


INSTAGRAM_INGRESS_FLAG = "MEZAN_INSTAGRAM_INGRESS_ENABLED"
INSTAGRAM_VERIFY_TOKEN_ENV = "MEZAN_INSTAGRAM_WEBHOOK_VERIFY_TOKEN"
INSTAGRAM_APP_SECRET_ENV = "MEZAN_INSTAGRAM_APP_SECRET"
SHARED_META_APP_SECRET_ENV = "META_BUSINESS_APP_SECRET"
WHATSAPP_INGRESS_FLAG_FALLBACK_ENV = "MEZAN_WHATSAPP_INGRESS_ENABLED"
WHATSAPP_APP_SECRET_FALLBACK_ENV = "MEZAN_WHATSAPP_APP_SECRET"
MAX_INSTAGRAM_WEBHOOK_BYTES = 1024 * 1024


class InstagramWebhookError(RuntimeError):
    """Base Instagram adapter error containing no customer content."""


class InstagramChallengeError(InstagramWebhookError):
    pass


class InstagramSignatureError(InstagramWebhookError):
    pass


class InstagramPayloadError(InstagramWebhookError):
    pass


class InstagramBodyTooLargeError(InstagramPayloadError):
    pass


@dataclass
class InstagramWebhookBatch:
    messages: list[tuple[TrustedChannelContext, NormalizedInboundMessage]]
    direct_message_count: int = 0
    comment_count: int = 0
    echo_count: int = 0
    unsupported_count: int = 0

    def __iter__(self):
        return iter(self.messages)

    def __len__(self) -> int:
        return len(self.messages)


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    rendered = str(value).strip()
    return rendered or None


def _occurred_at(value: Any) -> datetime:
    rendered = _text(value)
    if not rendered:
        raise InstagramPayloadError("Instagram event timestamp is missing")
    try:
        numeric = int(rendered)
        # Messaging callbacks use milliseconds; comment entries commonly use
        # epoch seconds. Normalize both to UTC.
        if numeric > 100_000_000_000:
            numeric //= 1000
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise InstagramPayloadError("Instagram event timestamp is invalid") from exc


async def read_bounded_instagram_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_INSTAGRAM_WEBHOOK_BYTES:
                raise InstagramBodyTooLargeError(
                    "Instagram webhook exceeds the size limit"
                )
        except ValueError:
            pass

    body = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(body) + len(chunk) > MAX_INSTAGRAM_WEBHOOK_BYTES:
            raise InstagramBodyTooLargeError(
                "Instagram webhook exceeds the size limit"
            )
        body.extend(chunk)
    return bytes(body)


def _direct_message_content(
    message: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    body = _text(message.get("text"))
    attachments = [
        item
        for item in (message.get("attachments") or [])
        if isinstance(item, dict)
    ]
    attachment_types = [
        item_type
        for item in attachments
        if (item_type := _text(item.get("type")))
    ]
    shared = {
        "surface": "direct_message",
        "attachment_types": attachment_types,
        "attachment_count": len(attachments),
    }
    if body:
        return "text", {"text": body, **shared}
    if not attachment_types:
        return None
    first = attachment_types[0].casefold()
    content_type = {
        "image": "image",
        "audio": "audio",
        "file": "document",
    }.get(first, "interactive")
    return content_type, shared


class InstagramInboundAdapter:
    provider = "instagram"

    def __init__(self, db: Any, *, verify_token: str, app_secret: str):
        if not str(verify_token).strip() or not str(app_secret).strip():
            raise ValueError("Instagram verify token and Meta app secret are required")
        self._db = db
        self._verify_token = str(verify_token).encode("utf-8")
        self._app_secret = str(app_secret).encode("utf-8")

    def verify_challenge(
        self,
        *,
        mode: str | None,
        verify_token: str | None,
        challenge: str | None,
    ) -> str:
        if mode != "subscribe" or not verify_token or not challenge:
            raise InstagramChallengeError("Instagram challenge is incomplete")
        if not hmac.compare_digest(
            verify_token.encode("utf-8"),
            self._verify_token,
        ):
            raise InstagramChallengeError("Instagram verify token does not match")
        return challenge

    def verify_signature(self, *, headers: dict[str, str], body: bytes) -> None:
        normalized_headers = {
            str(key).strip().casefold(): str(value).strip()
            for key, value in headers.items()
        }
        supplied = normalized_headers.get("x-hub-signature-256", "")
        if not supplied.startswith("sha256="):
            raise InstagramSignatureError("Instagram signature is missing")
        expected = "sha256=" + hmac.new(
            self._app_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise InstagramSignatureError("Instagram signature is invalid")

    async def _trusted_context(self, instagram_account_id: str) -> TrustedChannelContext:
        binding_key = build_channel_account_key("instagram", instagram_account_id)
        channel = await getattr(self._db, CHANNELS_COLLECTION).find_one(
            {"provider": "instagram", "external_account_key": binding_key},
            {
                "_id": 0,
                "user_id": 1,
                "merchant_id": 1,
                "channel_id": 1,
                "provider": 1,
            },
        )
        if not channel:
            raise ChannelNotReadyError(
                "signed Instagram account has no Mezan channel binding"
            )
        return TrustedChannelContext(
            user_id=str(channel["user_id"]),
            merchant_id=str(channel["merchant_id"]),
            channel_id=str(channel["channel_id"]),
            provider="instagram",
        )

    @staticmethod
    def _messaging_event(
        raw_event: Any,
        *,
        instagram_account_id: str,
    ) -> NormalizedInboundMessage | None:
        if not isinstance(raw_event, dict):
            return None
        sender = _text((raw_event.get("sender") or {}).get("id"))
        recipient = _text((raw_event.get("recipient") or {}).get("id"))
        raw_message = raw_event.get("message")
        if not sender or not recipient or not isinstance(raw_message, dict):
            return None
        message_id = _text(raw_message.get("mid"))
        content = _direct_message_content(raw_message)
        if not message_id or content is None:
            return None

        is_echo = raw_message.get("is_echo") is True or sender == instagram_account_id
        customer_id = recipient if is_echo else sender
        content_type, content_payload = content
        return NormalizedInboundMessage(
            provider="instagram",
            external_conversation_id=f"dm:{customer_id}",
            external_message_id=message_id,
            external_customer_id=customer_id,
            customer_profile={},
            preferred_language=None,
            content_type=content_type,
            content_payload=content_payload,
            occurred_at=_occurred_at(raw_event.get("timestamp")),
            source_event=(
                "instagram.messaging.message_echo"
                if is_echo
                else "instagram.messaging.message"
            ),
            direction="outbound" if is_echo else "inbound",
            sender_type="employee" if is_echo else "customer",
            analysis_status="pending",
            delivery_state="sent" if is_echo else "received",
        )

    @staticmethod
    def _postback_event(raw_event: Any) -> NormalizedInboundMessage | None:
        if not isinstance(raw_event, dict):
            return None
        sender = _text((raw_event.get("sender") or {}).get("id"))
        postback = raw_event.get("postback")
        if not sender or not isinstance(postback, dict):
            return None
        occurred = _occurred_at(raw_event.get("timestamp"))
        message_id = _text(postback.get("mid")) or (
            f"postback:{sender}:{int(occurred.timestamp() * 1000)}"
        )
        payload = {
            "surface": "direct_message",
            "title": _text(postback.get("title")),
            "button_text": _text(postback.get("title")),
            "postback_payload": _text(postback.get("payload")),
        }
        return NormalizedInboundMessage(
            provider="instagram",
            external_conversation_id=f"dm:{sender}",
            external_message_id=message_id,
            external_customer_id=sender,
            content_type="interactive",
            content_payload={
                key: value for key, value in payload.items() if value is not None
            },
            occurred_at=occurred,
            source_event="instagram.messaging.postback",
        )

    @staticmethod
    def _comment_event(
        raw_change: Any,
        *,
        entry_time: Any,
        instagram_account_id: str,
    ) -> NormalizedInboundMessage | None:
        if not isinstance(raw_change, dict) or _text(raw_change.get("field")) not in {
            "comments",
            "live_comments",
        }:
            return None
        value = raw_change.get("value")
        if not isinstance(value, dict):
            return None
        author = value.get("from") or {}
        customer_id = _text(author.get("id"))
        comment_id = _text(value.get("id"))
        text = _text(value.get("text"))
        media = value.get("media") or {}
        media_id = _text(media.get("id"))
        if (
            not customer_id
            or customer_id == instagram_account_id
            or not comment_id
            or not text
        ):
            return None
        parent_id = _text(value.get("parent_id"))
        thread_id = parent_id or comment_id
        customer_profile = {}
        if username := _text(author.get("username")):
            customer_profile["name"] = username
            customer_profile["username"] = username
        payload = {
            "surface": "comment",
            "text": text,
            "comment_id": comment_id,
            "parent_comment_id": parent_id,
            "media_id": media_id,
            "media_product_type": _text(media.get("media_product_type")),
        }
        return NormalizedInboundMessage(
            provider="instagram",
            external_conversation_id=f"comment:{media_id or 'unknown'}:{thread_id}",
            external_message_id=f"comment:{comment_id}",
            external_customer_id=customer_id,
            customer_profile=customer_profile,
            content_type="text",
            content_payload={
                key: value for key, value in payload.items() if value is not None
            },
            occurred_at=_occurred_at(value.get("created_time") or entry_time),
            source_event="instagram.comments.comment",
        )

    async def verify_and_normalize(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> InstagramWebhookBatch:
        if len(body) > MAX_INSTAGRAM_WEBHOOK_BYTES:
            raise InstagramBodyTooLargeError(
                "Instagram webhook exceeds the size limit"
            )
        self.verify_signature(headers=headers, body=body)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstagramPayloadError("Instagram webhook is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("object") != "instagram":
            raise InstagramPayloadError("Webhook object is not Instagram")

        batch = InstagramWebhookBatch(messages=[])
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                batch.unsupported_count += 1
                continue
            account_id = _text(entry.get("id"))
            if not account_id:
                batch.unsupported_count += 1
                continue
            context = await self._trusted_context(account_id)

            for raw_event in entry.get("messaging") or []:
                message = self._messaging_event(
                    raw_event,
                    instagram_account_id=account_id,
                ) or self._postback_event(raw_event)
                if message is None:
                    batch.unsupported_count += 1
                    continue
                batch.messages.append((context, message))
                if message.direction == "outbound":
                    batch.echo_count += 1
                else:
                    batch.direct_message_count += 1

            for raw_change in entry.get("changes") or []:
                message = self._comment_event(
                    raw_change,
                    entry_time=entry.get("time"),
                    instagram_account_id=account_id,
                )
                if message is None:
                    batch.unsupported_count += 1
                    continue
                batch.messages.append((context, message))
                batch.comment_count += 1
        return batch


def _configured_adapter(db: Any) -> InstagramInboundAdapter:
    ingress_flag = os.environ.get(INSTAGRAM_INGRESS_FLAG) or os.environ.get(
        WHATSAPP_INGRESS_FLAG_FALLBACK_ENV
    )
    if not _enabled(ingress_flag):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "instagram_ingress_disabled"},
        )
    verify_token = (
        os.environ.get(INSTAGRAM_VERIFY_TOKEN_ENV, "").strip()
        or os.environ.get("MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip()
    )
    app_secret = (
        os.environ.get(SHARED_META_APP_SECRET_ENV, "").strip()
        or os.environ.get(INSTAGRAM_APP_SECRET_ENV, "").strip()
        or os.environ.get(WHATSAPP_APP_SECRET_FALLBACK_ENV, "").strip()
    )
    if not verify_token or not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "instagram_ingress_not_configured"},
        )
    return InstagramInboundAdapter(
        db,
        verify_token=verify_token,
        app_secret=app_secret,
    )


def make_instagram_inbound_router(
    db: Any,
    *,
    adapter: InstagramInboundAdapter | None = None,
    gateway: ChannelGateway | None = None,
) -> APIRouter:
    """Expose Meta verification + signed Instagram ingress, with no send route."""
    router = APIRouter(
        prefix="/customer-intelligence/v1/channels/instagram",
        tags=["customer-intelligence-instagram-inbound"],
    )
    inbound_gateway = gateway or ChannelGateway(db)

    def current_adapter() -> InstagramInboundAdapter:
        return adapter or _configured_adapter(db)

    @router.get("/webhook", response_class=PlainTextResponse)
    async def verify_webhook(
        mode: str | None = Query(default=None, alias="hub.mode"),
        verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> str:
        try:
            return current_adapter().verify_challenge(
                mode=mode,
                verify_token=verify_token,
                challenge=challenge,
            )
        except InstagramChallengeError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "instagram_webhook_verification_failed"},
            ) from exc

    @router.post("/webhook")
    async def receive_webhook(request: Request) -> dict[str, Any]:
        try:
            body = await read_bounded_instagram_body(request)
            batch = await current_adapter().verify_and_normalize(
                headers=dict(request.headers),
                body=body,
            )
            results = [
                await inbound_gateway.ingest_inbound(
                    context=context,
                    message=message,
                )
                for context, message in batch.messages
            ]
        except InstagramBodyTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "instagram_webhook_too_large"},
            ) from exc
        except InstagramSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "instagram_webhook_signature_invalid"},
            ) from exc
        except InstagramPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "instagram_webhook_payload_invalid"},
            ) from exc
        except ChannelNotReadyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "instagram_channel_not_ready"},
            ) from exc
        except (ChannelPolicyError, ChannelGatewayError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "instagram_ingress_policy_blocked"},
            ) from exc

        return {
            "accepted": True,
            "events_seen": len(batch.messages),
            "direct_messages_seen": batch.direct_message_count,
            "comments_seen": batch.comment_count,
            "employee_echoes_seen": batch.echo_count,
            "events_created": sum(not result.duplicate for result in results),
            "duplicates": sum(result.duplicate for result in results),
            "unsupported_events": batch.unsupported_count,
            "message_send_allowed": False,
            "comment_reply_allowed": False,
            "ai_auto_reply_allowed": False,
            "commerce_mutation_allowed": False,
        }

    return router


__all__ = [
    "INSTAGRAM_APP_SECRET_ENV",
    "INSTAGRAM_INGRESS_FLAG",
    "INSTAGRAM_VERIFY_TOKEN_ENV",
    "MAX_INSTAGRAM_WEBHOOK_BYTES",
    "InstagramBodyTooLargeError",
    "InstagramChallengeError",
    "InstagramInboundAdapter",
    "InstagramPayloadError",
    "InstagramSignatureError",
    "InstagramWebhookBatch",
    "make_instagram_inbound_router",
]
