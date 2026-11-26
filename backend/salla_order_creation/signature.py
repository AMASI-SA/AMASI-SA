"""Verify purpose-bound HMAC envelopes from the independent Chatwoot host.

Pure parsing/signature verification; no network and no Salla call.
The secret is operator-configured, NEVER in prompts or source control.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

STAMP = re.compile(r"[0-9]{10,12}\Z", re.ASCII)
SIG = re.compile(r"sha256=[0-9a-f]{64}\Z", re.ASCII)
REF = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
ID = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_BODY = 4096
MAX_CLOCK_SKEW = 300


class SignatureDenied(ValueError):
    pass


class AssistedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.create_pending_order"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    quote_ref: str = Field(pattern=REF)
    quote_digest: str = Field(pattern=DIGEST)
    quote_version: int = Field(gt=0)
    confirmation_message_id: int = Field(gt=0)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SignatureDenied("duplicate_action_field")
        result[key] = value
    return result


def _invalid_number(_value):
    raise SignatureDenied("invalid_action_json")


def sign_action(raw: bytes, timestamp: str, delivery: str, secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise SignatureDenied("invalid_action_secret")
    signed = timestamp.encode("ascii") + b"." + delivery.encode("ascii") + b"." + raw
    return "sha256=" + hmac.new(secret, signed, hashlib.sha256).hexdigest()


def verify_signed_json(
    raw: bytes,
    timestamp: str,
    delivery: str,
    signature: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> dict:
    """Verify signed raw JSON without trusting a caller-selected action.

    Specific consumers MUST validate the strict action model after this
    verification. Do not use this alone as authorization.
    """
    if type(raw) is not bytes or not (0 < len(raw) <= MAX_BODY):
        raise SignatureDenied("action_body_size")
    if type(timestamp) is not str or not STAMP.fullmatch(timestamp):
        raise SignatureDenied("invalid_action_timestamp")
    if type(delivery) is not str:
        raise SignatureDenied("invalid_action_delivery")
    try:
        if str(UUID(delivery)) != delivery:
            raise ValueError("noncanonical UUID")
    except (ValueError, AttributeError) as exc:
        raise SignatureDenied("invalid_action_delivery") from exc
    if type(signature) is not str or not SIG.fullmatch(signature):
        raise SignatureDenied("invalid_action_signature")
    clock = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    if type(clock) is not int or abs(clock - int(timestamp)) > MAX_CLOCK_SKEW:
        raise SignatureDenied("stale_action_signature")
    if not hmac.compare_digest(
        sign_action(raw, timestamp, delivery, secret), signature
    ):
        raise SignatureDenied("invalid_action_signature")
    try:
        parsed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_number,
        )
        if type(parsed) is not dict:
            raise TypeError("expected JSON object")
        return parsed
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SignatureDenied("invalid_action_body") from exc


def verify_action(
    raw: bytes,
    timestamp: str,
    delivery: str,
    signature: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> AssistedAction:
    parsed = verify_signed_json(
        raw, timestamp, delivery, signature, secret, now=now
    )
    try:
        return AssistedAction.model_validate(parsed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_action_body") from exc
