"""Optional receive-only 360dialog Coexistence webhook transport.

This adapter is deliberately separate from the direct Meta webhook.  Basic
Auth selects one server-side phone-number binding before the request JSON is
parsed; tenant identity is then resolved only from that trusted binding.
There is no WhatsApp send client or send route in this module.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status

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
from .whatsapp import (
    WhatsAppBodyTooLargeError,
    WhatsAppPayloadError,
    _content,
    _enabled,
    _occurred_at,
    _text,
    read_bounded_webhook_body,
)

D360_INGRESS_FLAG = "MEZAN_360DIALOG_INGRESS_ENABLED"
D360_BINDINGS_ENV = "MEZAN_360DIALOG_WEBHOOK_BINDINGS_JSON"
MAX_D360_BINDINGS = 50


class D360WebhookError(RuntimeError):
    """Safe base error which never contains credentials or customer data."""


class D360AuthenticationError(D360WebhookError):
    pass


class D360ConfigurationError(D360WebhookError):
    pass


class D360PayloadError(D360WebhookError):
    pass


@dataclass(frozen=True)
class D360WebhookBinding:
    username: str
    password: str
    phone_number_id: str
    channel_id: str


@dataclass(frozen=True)
class D360StatusEvent:
    external_message_id: str
    delivery_state: Literal["sent", "delivered", "read", "failed"]


@dataclass
class D360WebhookBatch:
    context: TrustedChannelContext | None
    messages: list[NormalizedInboundMessage]
    statuses: list[D360StatusEvent]
    inbound_count: int = 0
    echo_count: int = 0
    unsupported_count: int = 0


def _load_bindings(raw: str) -> list[D360WebhookBinding]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise D360ConfigurationError("360dialog bindings are invalid") from exc
    if not isinstance(value, list) or not value or len(value) > MAX_D360_BINDINGS:
        raise D360ConfigurationError("360dialog bindings are invalid")

    bindings: list[D360WebhookBinding] = []
    usernames: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise D360ConfigurationError("360dialog bindings are invalid")
        fields = {
            key: _text(item.get(key))
            for key in ("username", "password", "phone_number_id", "channel_id")
        }
        if any(not fields[key] for key in fields):
            raise D360ConfigurationError("360dialog bindings are invalid")
        username = str(fields["username"])
        if username in usernames:
            raise D360ConfigurationError("360dialog binding usernames must be unique")
        usernames.add(username)
        bindings.append(
            D360WebhookBinding(
                username=username,
                password=str(fields["password"]),
                phone_number_id=str(fields["phone_number_id"]),
                channel_id=str(fields["channel_id"]),
            )
        )
    return bindings


def _basic_credentials(headers: dict[str, str]) -> tuple[str, str]:
    normalized = {
        str(key).strip().casefold(): str(value).strip()
        for key, value in headers.items()
    }
    authorization = normalized.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "basic" or not token:
        raise D360AuthenticationError("360dialog webhook authentication failed")
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise D360AuthenticationError(
            "360dialog webhook authentication failed"
        ) from exc
    username, separator, password = decoded.partition(":")
    if separator != ":" or not username or not password:
        raise D360AuthenticationError("360dialog webhook authentication failed")
    return username, password


class D360InboundAdapter:
    provider = "whatsapp"

    def __init__(self, db: Any, *, bindings: list[D360WebhookBinding]):
        if not bindings:
            raise ValueError("at least one 360dialog webhook binding is required")
        self._db = db
        self._bindings = tuple(bindings)

    def authenticate(self, headers: dict[str, str]) -> D360WebhookBinding:
        # Required order: decode username, select its server-side channel, and
        # only then compare the password in constant time.
        username, password = _basic_credentials(headers)
        binding = next(
            (
                candidate
                for candidate in self._bindings
                if candidate.username == username
            ),
            None,
        )
        if binding is None or not hmac.compare_digest(password, binding.password):
            raise D360AuthenticationError("360dialog webhook authentication failed")
        return binding

    async def _trusted_context(
        self,
        binding: D360WebhookBinding,
    ) -> TrustedChannelContext:
        channel = await getattr(self._db, CHANNELS_COLLECTION).find_one(
            {
                "provider": "whatsapp",
                "channel_id": binding.channel_id,
                "external_account_key": build_channel_account_key(
                    "whatsapp",
                    binding.phone_number_id,
                ),
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
                "authenticated 360dialog phone has no matching Mezan channel"
            )
        return TrustedChannelContext(
            user_id=str(channel["user_id"]),
            merchant_id=str(channel["merchant_id"]),
            channel_id=str(channel["channel_id"]),
            provider="whatsapp",
        )

    async def authenticate_context(
        self,
        headers: dict[str, str],
    ) -> tuple[D360WebhookBinding, TrustedChannelContext]:
        """Resolve the complete trusted server-side scope before body parsing."""
        binding = self.authenticate(headers)
        context = await self._trusted_context(binding)
        return binding, context

    async def verify_and_normalize(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> D360WebhookBatch:
        binding, context = await self.authenticate_context(headers)
        return self.normalize_authenticated(
            binding=binding,
            context=context,
            body=body,
        )

    def normalize_authenticated(
        self,
        *,
        binding: D360WebhookBinding,
        context: TrustedChannelContext,
        body: bytes,
    ) -> D360WebhookBatch:
        """Parse only after Basic Auth and database channel binding succeed."""
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise D360PayloadError("360dialog webhook is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise D360PayloadError("webhook object is not WhatsApp Business")

        batch = D360WebhookBatch(context=context, messages=[], statuses=[])
        event_name = str(_text(payload.get("event")) or "").casefold()
        if event_name in {"history", "smb_app_state_sync"}:
            # Coexistence history/app-state callbacks use 360dialog's
            # top-level {id,event,data} envelope instead of Meta's WABA
            # wrapper. They are authenticated and phone-bound here, then
            # acknowledged without importing old history or contact PII.
            data = payload.get("data")
            metadata = data.get("metadata") if isinstance(data, dict) else None
            phone_number_id = _text(
                metadata.get("phone_number_id") if isinstance(metadata, dict) else None
            )
            if not phone_number_id or not hmac.compare_digest(
                phone_number_id,
                binding.phone_number_id,
            ):
                raise D360AuthenticationError(
                    "360dialog webhook phone binding does not match"
                )
            batch.unsupported_count = 1
            return batch

        if payload.get("object") != "whatsapp_business_account":
            raise D360PayloadError("webhook object is not WhatsApp Business")

        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                batch.unsupported_count += 1
                continue
            for change in entry.get("changes") or []:
                if not isinstance(change, dict):
                    batch.unsupported_count += 1
                    continue
                field = _text(change.get("field"))
                value = change.get("value")
                if field not in {"messages", "smb_message_echoes"} or not isinstance(
                    value, dict
                ):
                    # Authenticated but unsupported callbacks are acknowledged
                    # without creating customer/message evidence.
                    batch.unsupported_count += 1
                    continue
                metadata = value.get("metadata")
                phone_number_id = _text(
                    metadata.get("phone_number_id")
                    if isinstance(metadata, dict)
                    else None
                )
                if not phone_number_id or not hmac.compare_digest(
                    phone_number_id,
                    binding.phone_number_id,
                ):
                    raise D360AuthenticationError(
                        "360dialog webhook phone binding does not match"
                    )
                contacts = {
                    _text(contact.get("wa_id")): contact
                    for contact in value.get("contacts") or []
                    if isinstance(contact, dict) and _text(contact.get("wa_id"))
                }
                if field == "messages":
                    for raw_message in value.get("messages") or []:
                        message = self._inbound_message(raw_message, contacts)
                        if message is None:
                            batch.unsupported_count += 1
                            continue
                        batch.messages.append(message)
                        batch.inbound_count += 1
                    for raw_status in value.get("statuses") or []:
                        status_event = self._status_event(raw_status)
                        if status_event is None:
                            batch.unsupported_count += 1
                            continue
                        batch.statuses.append(status_event)
                else:
                    for raw_echo in value.get("message_echoes") or []:
                        message = self._echo_message(raw_echo)
                        if message is None:
                            batch.unsupported_count += 1
                            continue
                        batch.messages.append(message)
                        batch.echo_count += 1
        return batch

    @staticmethod
    def _inbound_message(
        raw_message: Any,
        contacts: dict[str | None, dict[str, Any]],
    ) -> NormalizedInboundMessage | None:
        if not isinstance(raw_message, dict):
            return None
        message_id = _text(raw_message.get("id"))
        sender = _text(raw_message.get("from"))
        content = _content(raw_message)
        if not message_id or not sender or content is None:
            return None
        contact = contacts.get(sender) or {}
        profile = contact.get("profile") or {}
        customer_profile = {}
        if isinstance(profile, dict) and _text(profile.get("name")):
            customer_profile["name"] = _text(profile.get("name"))
        content_type, content_payload = content
        return NormalizedInboundMessage(
            provider="whatsapp",
            external_conversation_id=sender,
            external_message_id=message_id,
            external_customer_id=_text(contact.get("wa_id")) or sender,
            customer_mobile=sender,
            customer_profile=customer_profile,
            content_type=content_type,
            content_payload=content_payload,
            occurred_at=_occurred_at(raw_message.get("timestamp")),
            source_event=f"360dialog.messages.{_text(raw_message.get('type')) or 'unknown'}",
        )

    @staticmethod
    def _echo_message(raw_echo: Any) -> NormalizedInboundMessage | None:
        if not isinstance(raw_echo, dict):
            return None
        message_id = _text(raw_echo.get("id"))
        recipient = _text(raw_echo.get("to"))
        content = _content(raw_echo)
        if not message_id or not recipient or content is None:
            return None
        content_type, content_payload = content
        return NormalizedInboundMessage(
            provider="whatsapp",
            external_conversation_id=recipient,
            external_message_id=message_id,
            external_customer_id=recipient,
            customer_mobile=recipient,
            content_type=content_type,
            content_payload=content_payload,
            occurred_at=_occurred_at(raw_echo.get("timestamp")),
            source_event=f"360dialog.smb_message_echoes.{_text(raw_echo.get('type')) or 'unknown'}",
            direction="outbound",
            sender_type="employee",
            analysis_status="pending",
            delivery_state="sent",
        )

    @staticmethod
    def _status_event(raw_status: Any) -> D360StatusEvent | None:
        if not isinstance(raw_status, dict):
            return None
        message_id = _text(raw_status.get("id"))
        state = _text(raw_status.get("status"))
        mapped = {
            "sent": "sent",
            "delivered": "delivered",
            "read": "read",
            "failed": "failed",
        }.get(str(state or "").casefold())
        if not message_id or mapped is None:
            return None
        return D360StatusEvent(
            external_message_id=message_id,
            delivery_state=mapped,
        )


def _configured_adapter(db: Any) -> D360InboundAdapter:
    if not _enabled(os.environ.get(D360_INGRESS_FLAG)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "360dialog_ingress_disabled"},
        )
    raw = os.environ.get(D360_BINDINGS_ENV, "").strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "360dialog_ingress_not_configured"},
        )
    try:
        return D360InboundAdapter(db, bindings=_load_bindings(raw))
    except D360ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "360dialog_ingress_not_configured"},
        ) from exc


def make_360dialog_inbound_router(
    db: Any,
    *,
    adapter: D360InboundAdapter | None = None,
    gateway: ChannelGateway | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/customer-intelligence/v1/channels/whatsapp/360dialog",
        tags=["customer-intelligence-whatsapp-360dialog-inbound"],
    )
    inbound_gateway = gateway or ChannelGateway(db)

    def current_adapter() -> D360InboundAdapter:
        return adapter or _configured_adapter(db)

    @router.post("/webhook")
    async def receive_webhook(request: Request) -> dict[str, Any]:
        try:
            selected_adapter = current_adapter()
            binding, trusted_context = await selected_adapter.authenticate_context(
                dict(request.headers)
            )
            body = await read_bounded_webhook_body(request)
            batch = selected_adapter.normalize_authenticated(
                binding=binding,
                context=trusted_context,
                body=body,
            )
            results = []
            if batch.context is not None:
                for message in batch.messages:
                    result = await inbound_gateway.ingest_inbound(
                        context=batch.context,
                        message=message,
                    )
                    results.append(result)
                statuses_updated = 0
                for event in batch.statuses:
                    statuses_updated += bool(
                        await inbound_gateway.record_outbound_status(
                            context=batch.context,
                            external_message_id=event.external_message_id,
                            delivery_state=event.delivery_state,
                        )
                    )
            else:
                statuses_updated = 0
        except WhatsAppBodyTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"code": "360dialog_webhook_too_large"},
            ) from exc
        except D360AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "360dialog_webhook_authentication_failed"},
                headers={"WWW-Authenticate": "Basic"},
            ) from exc
        except (D360PayloadError, WhatsAppPayloadError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "360dialog_webhook_payload_invalid"},
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
            "inbound_messages_seen": batch.inbound_count,
            "employee_echoes_seen": batch.echo_count,
            "messages_created": sum(not result.duplicate for result in results),
            "duplicates": sum(result.duplicate for result in results),
            "statuses_seen": len(batch.statuses),
            "statuses_updated": statuses_updated,
            "unsupported_events": batch.unsupported_count,
            "message_send_allowed": False,
            "ai_execution_allowed": False,
            "commerce_mutation_allowed": False,
        }

    return router


__all__ = [
    "D360_BINDINGS_ENV",
    "D360_INGRESS_FLAG",
    "D360AuthenticationError",
    "D360InboundAdapter",
    "D360PayloadError",
    "D360WebhookBinding",
    "make_360dialog_inbound_router",
]
