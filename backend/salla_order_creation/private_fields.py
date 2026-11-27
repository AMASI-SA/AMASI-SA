"""Encrypted, scope-bound temporary customer order fields for Mezan.

Only the trusted Chatwoot service may propose a field from a *verified*
public incoming customer message; AI cannot read these values. This module
does NOT export customer data to a model, create a Salla order, send a
WhatsApp message or change accounting.

Requires a new independent MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY (Fernet)
and a real Mongo TTL index before writing any PII. Never use the Salla
OAuth-token encryption key for customer data. Quotes/order approvals must
be invalidated on any subsequent customer detail edit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .service import intent_storage_id

FIELDS = frozenset({
    "buyer_name", "buyer_mobile", "buyer_email",
    "recipient_name", "recipient_phone",
    "address_line", "street_number", "postal_code",
    "short_address", "building_number", "additional_number",
    "district_name", "city_name", "geo_lat", "geo_lng",
})
OPTION_TEXT = re.compile(r"^option_text_([1-9][0-9]{0,17})$")
def allowed_private_field_name(name: Any) -> bool:
    return type(name) is str and (
        name in FIELDS or OPTION_TEXT.fullmatch(name) is not None
    )

INTENT_MAX_AGE = timedelta(days=7)
_PRIVATE_TTL_NAME = "salla_assisted_private_fields_ttl_v1"
MAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")
CODE = re.compile(r"^[A-Za-z0-9-]{3,20}$")
SENSITIVE_PAYMENT = re.compile(
    r"(?i)(?:otp|cvv|cvc|رمز\s*(?:التحقق|الدفع|بطاقه|البطاقه)|"
    r"بطاق(?:ة|ه)\s*(?:بنكي|ائتمان))"
)


class PrivateOrderFieldDenied(ValueError):
    pass


def _scope_id(owner: str, account: int, inbox: int, conversation: int,
              intent: str) -> str:
    if not owner or not all(type(x) is int and x > 0 for x in (
        account, inbox, conversation,
    )):
        raise PrivateOrderFieldDenied("invalid_private_field_scope")
    key = intent_storage_id(owner, intent)
    return hashlib.sha256(
        f"private:{key}:{account}:{inbox}:{conversation}".encode("utf-8")
    ).hexdigest()


def _fernet_from_env() -> MultiFernet:
    primary = os.environ.get("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY", "")
    secondary = os.environ.get("MEZAN_ASSISTED_SALLA_PRIVATE_FIELDS_KEY_OLD", "")
    if not primary or len(primary) < 40:
        raise PrivateOrderFieldDenied("independent_private_encryption_key_missing")
    try:
        keys = [Fernet(primary.encode("ascii"))]
        if secondary:
            keys.append(Fernet(secondary.encode("ascii")))
        return MultiFernet(keys)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise PrivateOrderFieldDenied("private_encryption_key_invalid") from exc


def _safe_value(field: str, value: str) -> str:
    if not allowed_private_field_name(field) or type(value) is not str:
        raise PrivateOrderFieldDenied("untrusted_private_order_field")
    text = value.strip()
    max_chars = 500 if OPTION_TEXT.fullmatch(field) else 180
    if (not text or len(text) > max_chars or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in text) or
            "<" in text or ">" in text or SENSITIVE_PAYMENT.search(text)):
        raise PrivateOrderFieldDenied("unsafe_private_order_field")
    if field.endswith("_mobile") or field.endswith("_phone"):
        normalized = re.sub(r"[\s()\-]", "", text)
        local = re.fullmatch(r"(?:\+966|00966|966|0)?(5\d{8})", normalized)
        if local:
            normalized = "+966" + local.group(1)
        if not PHONE.fullmatch(normalized):
            raise PrivateOrderFieldDenied("invalid_private_phone")
        return normalized
    if field == "buyer_email":
        if len(text) > 254 or not MAIL.fullmatch(text):
            raise PrivateOrderFieldDenied("invalid_private_email")
        return text.casefold()
    if field in ("geo_lat", "geo_lng"):
        from decimal import Decimal, InvalidOperation
        try:
            coord = Decimal(text)
        except InvalidOperation as exc:
            raise PrivateOrderFieldDenied("invalid_private_coordinates") from exc
        limit = 90 if field == "geo_lat" else 180
        if not coord.is_finite() or coord < -limit or coord > limit:
            raise PrivateOrderFieldDenied("invalid_private_coordinates")
        if coord.as_tuple().exponent < -8:
            raise PrivateOrderFieldDenied("private_coordinates_too_precise")
        return str(coord)
    if field in ("postal_code", "short_address",
                 "building_number", "additional_number", "street_number"):
        min_chars = 1 if field in (
            "building_number", "additional_number", "street_number"
        ) else 3
        if (not min_chars <= len(text) <= 20 or
                not re.fullmatch(r"[A-Za-z0-9-]+", text)):
            raise PrivateOrderFieldDenied("invalid_private_address_part")
    return text


def _decode(cipher: Any, sealed: Any) -> dict:
    if not isinstance(sealed, bytes) or len(sealed) > 8192:
        raise PrivateOrderFieldDenied("private_field_cipher_unavailable")
    try:
        payload = json.loads(cipher.decrypt(sealed).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise PrivateOrderFieldDenied("private_field_integrity_failure") from exc
    if (type(payload) is not dict or
            any(not allowed_private_field_name(k) for k in payload) or
            any(type(x) is not str for x in payload.values())):
        raise PrivateOrderFieldDenied("private_field_payload_invalid")
    return payload


def _encode(cipher: Any, fields: dict) -> bytes:
    raw = json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(raw) > 4096:
        raise PrivateOrderFieldDenied("private_order_data_too_large")
    return cipher.encrypt(raw)


async def require_private_fields_ttl(db: Any) -> None:
    """Read metadata only. A purge_after field alone does not delete PII."""
    try:
        index = await db.salla_ai_private_order_fields.index_information()
        meta = index.get(_PRIVATE_TTL_NAME)
    except Exception as exc:
        raise PrivateOrderFieldDenied("private_field_ttl_unavailable") from exc
    if not isinstance(meta, dict) or meta.get("expireAfterSeconds") != 0:
        raise PrivateOrderFieldDenied("private_field_ttl_missing")
    keys = meta.get("key")
    if not isinstance(keys, (list, tuple)) or list(keys) != [("purge_after", 1)]:
        raise PrivateOrderFieldDenied("private_field_ttl_invalid")


async def install_private_fields_ttl(db: Any) -> str:
    """EXPLICIT approved one-time migration, never on normal requests."""
    await db.salla_ai_private_order_fields.create_index(
        [("purge_after", 1)],
        expireAfterSeconds=0, name=_PRIVATE_TTL_NAME,
    )
    await require_private_fields_ttl(db)
    return _PRIVATE_TTL_NAME


async def save_private_order_field(
    db: Any, *,
    owner_id: str, account_id: int, inbox_id: int, conversation_id: int,
    intent_id: str, contact_id: int, message_id: int,
    field: str, value: str, now: datetime | None = None,
    cipher: Any | None = None,
) -> dict:
    """CAS update: one ciphertext per intent, never raw PII in metadata.

    A revision conflict is *not* retried blindly. The Chatwoot service may
    re-read the latest customer message and reconcile/submit again safely.
    """
    if type(contact_id) is not int or contact_id < 1 or (
        type(message_id) is not int or message_id < 1
    ):
        raise PrivateOrderFieldDenied("signed_customer_source_required")
    value = _safe_value(field, value)
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None:
        raise PrivateOrderFieldDenied("private_field_clock_invalid")
    clock = clock.astimezone(timezone.utc)
    key = _scope_id(owner_id, account_id, inbox_id, conversation_id, intent_id)
    await require_private_fields_ttl(db)
    cipher = cipher or _fernet_from_env()

    quote = await db.salla_ai_order_quotes.find_one({
        "user_id": owner_id, "intent_id": intent_id,
    })
    order = await db.salla_ai_order_intents.find_one({
        "user_id": owner_id, "intent_id": intent_id,
    })
    if quote is not None or order is not None:
        raise PrivateOrderFieldDenied("order_details_frozen_after_quote")

    collection = db.salla_ai_private_order_fields
    current = await collection.find_one({"_id": key})
    if current is None:
        fields = {field: value}
        document = {
            "_id": key, "owner_id": owner_id, "account_id": account_id,
            "inbox_id": inbox_id, "conversation_id": conversation_id,
            "intent_id": intent_id, "contact_id": contact_id,
            "revision": 1, "last_message_id": message_id,
            "field_message_ids": {field: message_id},
            "encrypted_fields": _encode(cipher, fields),
            "created_at": clock, "updated_at": clock,
            "purge_after": clock + INTENT_MAX_AGE,
        }
        try:
            await collection.insert_one(document)
        except Exception:
            existing = await collection.find_one({"_id": key})
            if existing is None:
                raise
            raise PrivateOrderFieldDenied("concurrent_private_field_insert")
        return {"state": "saved", "revision": 1,
                "vault_ref": "vault." + key[:32],
                "fields_present": [field]}

    if (current.get("owner_id") != owner_id or
            current.get("account_id") != account_id or
            current.get("inbox_id") != inbox_id or
            current.get("conversation_id") != conversation_id or
            current.get("contact_id") != contact_id or
            current.get("intent_id") != intent_id):
        raise PrivateOrderFieldDenied("private_field_scope_conflict")
    expiry = current.get("purge_after")
    if isinstance(expiry, datetime):
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= clock:
            raise PrivateOrderFieldDenied("private_field_record_expired")
    last = current.get("last_message_id")
    rev = current.get("revision")
    if (type(last) is not int or type(rev) is not int or
            type(current.get("field_message_ids")) is not dict or
            type(current.get("purge_after")) is not datetime):
        raise PrivateOrderFieldDenied("private_field_state_corrupt")
    if message_id < last:
        raise PrivateOrderFieldDenied("stale_customer_field_message")
    fields = _decode(cipher, current.get("encrypted_fields"))
    proofs = current["field_message_ids"]
    if message_id == proofs.get(field):
        if fields.get(field) != value:
            raise PrivateOrderFieldDenied("same_message_field_conflict")
        return {"state": "saved", "revision": rev,
                "vault_ref": "vault." + key[:32],
                "fields_present": sorted(fields), "replayed": True}
    fields[field] = value
    proofs = {**proofs, field: message_id}
    changed = await collection.update_one(
        {"_id": key, "revision": rev, "contact_id": contact_id},
        {"$set": {
            "encrypted_fields": _encode(cipher, fields),
            "field_message_ids": proofs,
            "last_message_id": message_id,
            "updated_at": clock,
        }, "$inc": {"revision": 1}},
    )
    if getattr(changed, "modified_count", 0) != 1:
        raise PrivateOrderFieldDenied("concurrent_private_field_conflict")
    return {"state": "saved", "revision": rev + 1,
                "vault_ref": "vault." + key[:32],
            "fields_present": sorted(fields), "replayed": False}


async def load_private_order_fields(
    db: Any, *,
    owner_id: str, account_id: int, inbox_id: int, conversation_id: int,
    intent_id: str, contact_id: int, cipher: Any | None = None,
    now: datetime | None = None,
) -> dict:
    """Private Mezan ONLY. Never expose this return value in an API reply."""
    if type(contact_id) is not int or contact_id < 1:
        raise PrivateOrderFieldDenied("verified_contact_id_required")
    key = _scope_id(owner_id, account_id, inbox_id, conversation_id, intent_id)
    row = await db.salla_ai_private_order_fields.find_one({
        "_id": key, "owner_id": owner_id, "account_id": account_id,
        "inbox_id": inbox_id, "conversation_id": conversation_id,
        "contact_id": contact_id,
    })
    if row is None:
        raise PrivateOrderFieldDenied("private_customer_fields_not_found")
    expiry = row.get("purge_after")
    if not isinstance(expiry, datetime):
        raise PrivateOrderFieldDenied("private_customer_fields_expiration_missing")
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or expiry <= clock.astimezone(timezone.utc):
        raise PrivateOrderFieldDenied("private_customer_fields_expired")
    return _decode(cipher or _fernet_from_env(), row.get("encrypted_fields"))
