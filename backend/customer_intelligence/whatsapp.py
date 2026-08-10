"""Receive-only WhatsApp Cloud API adapter and webhook routes.

The adapter follows Meta's webhook boundary: GET challenge verification and
POST payload authentication via ``X-Hub-Signature-256`` over the exact raw
request body.  It normalizes inbound message evidence into the shared Channel
Gateway and never calls a WhatsApp send endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
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


WHATSAPP_INGRESS_FLAG = "MEZAN_WHATSAPP_INGRESS_ENABLED"
WHATSAPP_VERIFY_TOKEN_ENV = "MEZAN_WHATSAPP_WEBHOOK_VERIFY_TOKEN"
WHATSAPP_APP_SECRET_ENV = "MEZAN_WHATSAPP_APP_SECRET"
MAX_WHATSAPP_WEBHOOK_BYTES = 1024 * 1024


class WhatsAppWebhookError(RuntimeError):
    """Base WhatsApp adapter error containing no webhook customer data."""


class WhatsAppChallengeError(WhatsAppWebhookError):
    pass


class WhatsAppSignatureError(WhatsAppWebhookError):
    pass


class WhatsAppPayloadError(WhatsAppWebhookError):
    pass


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
        raise WhatsAppPayloadError("WhatsApp message timestamp is missing")
    try:
        return datetime.fromtimestamp(int(rendered), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise WhatsAppPayloadError("WhatsApp message timestamp is invalid") from exc


def _content(message: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    message_type = _text(message.get("type"))
    if message_type == "text":
        body = _text((message.get("text") or {}).get("body"))
        return ("text", {"text": body}) if body else None

    if message_type in {"image", "audio", "document"}:
        media = message.get(message_type)
        if not isinstance(media, dict) or not _text(media.get("id")):
            return None
        allowed = {
            "provider_media_id": _text(media.get("id")),
            "mime_type": _text(media.get("mime_type")),
            "sha256": _text(media.get("sha256")),
            "caption": _text(media.get("caption")),
            "filename": _text(media.get("filename")),
            "voice": media.get("voice") if isinstance(media.get("voice"), bool) else None,
        }
        return message_type, {key: value for key, value in allowed.items() if value is not None}

    if message_type == "interactive" and isinstance(message.get("interactive"), dict):
        return "interactive", {"interactive": message["interactive"]}

    if message_type == "button" and isinstance(message.get("button"), dict):
        button = message["button"]
        return "interactive", {
            "button": {
                key: value
                for key in ("text", "payload")
                if (value := _text(button.get(key))) is not None
            }
        }

    if message_type in {"location", "contacts", "reaction"}:
        value = message.get(message_type)
        if isinstance(value, (dict, list)):
            return "interactive", {message_type: value}

    # Unsupported events remain acknowledged but are not persisted as a fake
    # text message. A later adapter version may add a typed normalizer.
    return None


class WhatsAppInboundAdapter:
    provider = "whatsapp"

    def __init__(self, db: Any, *, verify_token: str, app_secret: str):
        if not str(verify_token).strip() or not str(app_secret).strip():
            raise ValueError("WhatsApp verify token and app secret are required")
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
        if mode != "subscribe" or not challenge or not verify_token:
            raise WhatsAppChallengeError("WhatsApp challenge is incomplete")
        if not hmac.compare_digest(
            verify_token.encode("utf-8"),
            self._verify_token,
        ):
            raise WhatsAppChallengeError("WhatsApp verify token does not match")
        return challenge

    def verify_signature(self, *, headers: dict[str, str], body: bytes) -> None:
        normalized_headers = {
            str(key).strip().casefold(): str(value).strip()
            for key, value in headers.items()
        }
        supplied = normalized_headers.get("x-hub-signature-256", "")
        if not supplied.startswith("sha256="):
            raise WhatsAppSignatureError("WhatsApp signature is missing")
        expected = "sha256=" + hmac.new(
            self._app_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise WhatsAppSignatureError("WhatsApp signature is invalid")

    async def verify_and_normalize(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> list[tuple[TrustedChannelContext, NormalizedInboundMessage]]:
        if len(body) > MAX_WHATSAPP_WEBHOOK_BYTES:
            raise WhatsAppPayloadError("WhatsApp webhook exceeds the size limit")
        self.verify_signature(headers=headers, body=body)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhatsAppPayloadError("WhatsApp webhook is not valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            raise WhatsAppPayloadError("Webhook object is not WhatsApp Business")

        normalized: list[tuple[TrustedChannelContext, NormalizedInboundMessage]] = []
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes") or []:
                if not isinstance(change, dict) or change.get("field") != "messages":
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                metadata = value.get("metadata") or {}
                phone_number_id = _text(metadata.get("phone_number_id"))
                if not phone_number_id:
                    continue
                binding_key = build_channel_account_key(
                    "whatsapp",
                    phone_number_id,
                )
                channel = await getattr(self._db, CHANNELS_COLLECTION).find_one(
                    {
                        "provider": "whatsapp",
                        "external_account_key": binding_key,
                    },
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
                        "signed WhatsApp account has no Mezan channel binding"
                    )
                context = TrustedChannelContext(
                    user_id=str(channel["user_id"]),
                    merchant_id=str(channel["merchant_id"]),
                    channel_id=str(channel["channel_id"]),
                    provider="whatsapp",
                )
                contacts = {
                    _text(contact.get("wa_id")): contact
                    for contact in value.get("contacts") or []
                    if isinstance(contact, dict) and _text(contact.get("wa_id"))
                }
                for raw_message in value.get("messages") or []:
                    if not isinstance(raw_message, dict):
                        continue
                    message_id = _text(raw_message.get("id"))
                    sender = _text(raw_message.get("from"))
                    content = _content(raw_message)
                    if not message_id or not sender or content is None:
                        continue
                    contact = contacts.get(sender) or {}
                    profile = contact.get("profile") or {}
                    customer_profile = {}
                    if isinstance(profile, dict) and _text(profile.get("name")):
                        customer_profile["name"] = _text(profile.get("name"))
                    content_type, content_payload = content
                    normalized.append(
                        (
                            context,
                            NormalizedInboundMessage(
                                provider="whatsapp",
                                external_conversation_id=sender,
                                external_message_id=message_id,
                                external_customer_id=_text(contact.get("wa_id")) or sender,
                                customer_mobile=sender,
                                customer_profile=customer_profile,
                                preferred_language=None,
                                content_type=content_type,
                                content_payload=content_payload,
                                occurred_at=_occurred_at(raw_message.get("timestamp")),
                                source_event=f"whatsapp.messages.{_text(raw_message.get('type')) or 'unknown'}",
                            ),
                        )
                    )
        return normalized


def _configured_adapter(db: Any) -> WhatsAppInboundAdapter:
    if not _enabled(os.environ.get(WHATSAPP_INGRESS_FLAG)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "whatsapp_ingress_disabled"},
        )
    verify_token = os.environ.get(WHATSAPP_VERIFY_TOKEN_ENV, "").strip()
    app_secret = os.environ.get(WHATSAPP_APP_SECRET_ENV, "").strip()
    if not verify_token or not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "whatsapp_ingress_not_configured"},
        )
    return WhatsAppInboundAdapter(
        db,
        verify_token=verify_token,
        app_secret=app_secret,
    )


def make_whatsapp_inbound_router(
    db: Any,
    *,
    adapter: WhatsAppInboundAdapter | None = None,
    gateway: ChannelGateway | None = None,
) -> APIRouter:
    """Expose Meta's verification + inbound webhook, with no send route."""
    router = APIRouter(
        prefix="/customer-intelligence/v1/channels/whatsapp",
        tags=["customer-intelligence-whatsapp-inbound"],
    )
    inbound_gateway = gateway or ChannelGateway(db)

    def current_adapter() -> WhatsAppInboundAdapter:
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
        except WhatsAppChallengeError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "whatsapp_webhook_verification_failed"},
            ) from exc

    @router.post("/webhook")
    async def receive_webhook(request: Request) -> dict[str, Any]:
        body = await request.body()
        try:
            events = await current_adapter().verify_and_normalize(
                headers=dict(request.headers),
                body=body,
            )
            results = [
                await inbound_gateway.ingest_inbound(
                    context=context,
                    message=message,
                )
                for context, message in events
            ]
        except WhatsAppSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "whatsapp_webhook_signature_invalid"},
            ) from exc
        except WhatsAppPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "whatsapp_webhook_payload_invalid"},
            ) from exc
        except ChannelNotReadyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "whatsapp_channel_not_ready"},
            ) from exc
        except (ChannelPolicyError, ChannelGatewayError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "whatsapp_ingress_policy_blocked"},
            ) from exc

        return {
            "accepted": True,
            "messages_seen": len(events),
            "messages_created": sum(not result.duplicate for result in results),
            "duplicates": sum(result.duplicate for result in results),
            "message_send_allowed": False,
            "ai_execution_allowed": False,
            "commerce_mutation_allowed": False,
        }

    return router


__all__ = [
    "MAX_WHATSAPP_WEBHOOK_BYTES",
    "WHATSAPP_APP_SECRET_ENV",
    "WHATSAPP_INGRESS_FLAG",
    "WHATSAPP_VERIFY_TOKEN_ENV",
    "WhatsAppChallengeError",
    "WhatsAppInboundAdapter",
    "WhatsAppPayloadError",
    "WhatsAppSignatureError",
    "make_whatsapp_inbound_router",
]
