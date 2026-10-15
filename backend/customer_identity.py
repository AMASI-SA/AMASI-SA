"""Tenant-scoped, encrypted customer identity for Mezan's shared memory.

The identity store is deliberately channel-neutral.  Salla abandoned carts are
the first producer, while WhatsApp, social conversations and future commerce
connectors can attach their own hashed aliases to the same identity later.

No customer name, email, mobile number or address is stored as plaintext.
Searchable aliases are keyed HMAC digests and the customer snapshot is Fernet
encrypted at rest.  Missing encryption configuration fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

CUSTOMER_IDENTITY_COLLECTION = "mezan_customer_identities_v1"
CUSTOMER_IDENTITY_SCHEMA_VERSION = 1
PRIVATE_PAYLOAD_SCHEMA_VERSION = 1

_fernet: MultiFernet | None = None


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    rendered = str(value).strip()
    return rendered or None


def _primary_key() -> str:
    primary = (
        os.environ.get("MEZAN_CUSTOMER_PII_ENC_KEY", "").strip()
        or os.environ.get("SALLA_TOKEN_ENC_KEY", "").strip()
    )
    if not primary:
        raise RuntimeError(
            "MEZAN_CUSTOMER_PII_ENC_KEY (or SALLA_TOKEN_ENC_KEY fallback) "
            "must be configured before customer PII can be ingested"
        )
    return primary


def _load_fernet() -> MultiFernet:
    primary = _primary_key()
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = (
        os.environ.get("MEZAN_CUSTOMER_PII_ENC_KEY_OLD", "").strip()
        or os.environ.get("SALLA_TOKEN_ENC_KEY_OLD", "").strip()
    )
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    return MultiFernet(keys)


def _get_fernet() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_fernet()
    return _fernet


def encrypt_private_payload(payload: dict[str, Any] | None) -> bytes | None:
    """Encrypt one bounded private payload, returning no plaintext fallback."""
    if not payload:
        return None
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _get_fernet().encrypt(rendered.encode("utf-8"))


def decrypt_private_payload(ciphertext: bytes | None) -> dict[str, Any]:
    """Internal-only decrypt helper for trusted services and hermetic tests."""
    if not ciphertext:
        return {}
    try:
        decoded = _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Customer PII decryption failed after key rotation") from exc
    value = json.loads(decoded)
    return value if isinstance(value, dict) else {}


def _normal_email(value: Any) -> str | None:
    rendered = _text(value)
    return rendered.casefold() if rendered and "@" in rendered else None


def _normal_phone(value: Any) -> str | None:
    rendered = _text(value)
    if not rendered:
        return None
    digits = re.sub(r"\D", "", rendered)
    if digits.startswith("00966"):
        digits = digits[2:]
    if digits.startswith("9660"):
        digits = f"966{digits[4:]}"
    elif re.fullmatch(r"05\d{8}", digits):
        digits = f"966{digits[1:]}"
    elif re.fullmatch(r"5\d{8}", digits):
        digits = f"966{digits}"
    return digits if len(digits) >= 7 else None


def _identity_secret() -> bytes:
    explicit = os.environ.get("MEZAN_CUSTOMER_IDENTITY_HMAC_KEY", "").strip()
    return (explicit or _primary_key()).encode("utf-8")


def _digest(*parts: str) -> str:
    message = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hmac.new(_identity_secret(), message, hashlib.sha256).hexdigest()


def build_identity_keys(
    *,
    user_id: str,
    merchant_id: str,
    source_system: str,
    external_customer_id: Any = None,
    email: Any = None,
    mobile: Any = None,
) -> list[str]:
    """Return stable, non-reversible aliases ordered by evidence strength."""
    candidates = (
        ("external", _text(external_customer_id)),
        ("mobile", _normal_phone(mobile)),
        ("email", _normal_email(email)),
    )
    keys: list[str] = []
    for kind, value in candidates:
        if not value:
            continue
        # Provider IDs are meaningful only inside that provider.  Email and
        # mobile aliases intentionally use a shared namespace so a future
        # WhatsApp or social conversation can resolve the same customer.
        identity_namespace = source_system if kind == "external" else "shared"
        digest = _digest(
            "mezan-customer-identity-v1",
            str(user_id),
            str(merchant_id),
            str(identity_namespace),
            kind,
            value,
        )
        keys.append(f"{kind}:v1:{digest}")
    return keys


def _merge_private(existing: Any, incoming: Any) -> Any:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return incoming if incoming not in (None, "", [], {}) else existing
    merged = dict(existing)
    for key, value in incoming.items():
        if isinstance(value, dict):
            merged[key] = _merge_private(merged.get(key), value)
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _private_field_paths(value: Any, *, prefix: str = "") -> list[str]:
    if not isinstance(value, dict):
        return []
    fields: list[str] = []
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            fields.extend(_private_field_paths(child, prefix=path))
        elif child not in (None, "", [], {}):
            fields.append(path)
    return sorted(set(fields))


async def resolve_customer_identity(
    db: Any,
    *,
    user_id: str,
    merchant_id: str,
    source_system: str,
    external_customer_id: Any = None,
    email: Any = None,
    mobile: Any = None,
    private_profile: dict[str, Any] | None = None,
    observed_at: Any = None,
) -> dict[str, Any] | None:
    """Resolve or create one encrypted identity and return only safe metadata."""
    identity_keys = build_identity_keys(
        user_id=str(user_id),
        merchant_id=str(merchant_id),
        source_system=str(source_system),
        external_customer_id=external_customer_id,
        email=email,
        mobile=mobile,
    )
    if not identity_keys:
        return None

    collection = getattr(db, CUSTOMER_IDENTITY_COLLECTION)
    existing = await collection.find_one(
        {
            "user_id": str(user_id),
            "merchant_id": str(merchant_id),
            "identity_keys": {"$in": identity_keys},
        },
        {
            "_id": 0,
            "customer_identity_id": 1,
            "identity_keys": 1,
            "private_profile_ciphertext": 1,
            "private_profile_observed_at": 1,
            "first_seen_at": 1,
        },
    )
    customer_identity_id = _text((existing or {}).get("customer_identity_id"))
    if not customer_identity_id:
        customer_identity_id = f"cust_{_digest('customer-id', identity_keys[0])[:32]}"

    prior_profile: dict[str, Any] = {}
    if (existing or {}).get("private_profile_ciphertext"):
        prior_profile = decrypt_private_payload(
            existing.get("private_profile_ciphertext")
        )
    incoming_observed_at = _utc_datetime(observed_at)
    existing_observed_at = _utc_datetime(
        (existing or {}).get("private_profile_observed_at")
    )
    if (
        existing_observed_at
        and incoming_observed_at
        and incoming_observed_at < existing_observed_at
    ):
        # Older events may fill a missing field but can never overwrite a more
        # recent customer fact.
        merged_profile = _merge_private(private_profile or {}, prior_profile)
        profile_observed_at = existing_observed_at
    else:
        merged_profile = _merge_private(prior_profile, private_profile or {})
        profile_observed_at = incoming_observed_at or existing_observed_at
    ciphertext = encrypt_private_payload(merged_profile)
    now = datetime.now(timezone.utc)
    safe_source = str(source_system).strip().lower()[:80]
    set_fields: dict[str, Any] = {
        "schema_version": CUSTOMER_IDENTITY_SCHEMA_VERSION,
        "identity_keys": sorted(
            set((existing or {}).get("identity_keys") or []).union(identity_keys)
        ),
        "private_profile_fields": _private_field_paths(merged_profile),
        "private_profile_encrypted": bool(ciphertext),
        "private_payload_schema_version": PRIVATE_PAYLOAD_SCHEMA_VERSION,
        "plaintext_pii_stored": False,
        "last_seen_at": now,
        "updated_at": now,
        "last_source_system": safe_source,
    }
    if profile_observed_at:
        set_fields["private_profile_observed_at"] = profile_observed_at
    if ciphertext:
        set_fields["private_profile_ciphertext"] = ciphertext

    await collection.update_one(
        {
            "user_id": str(user_id),
            "customer_identity_id": customer_identity_id,
        },
        {
            "$set": set_fields,
            "$setOnInsert": {
                "id": uuid.uuid4().hex,
                "user_id": str(user_id),
                "merchant_id": str(merchant_id),
                "customer_identity_id": customer_identity_id,
                "first_seen_at": now,
                "created_at": now,
            },
            "$addToSet": {"source_systems": safe_source},
        },
        upsert=True,
    )
    return {
        "customer_identity_id": customer_identity_id,
        "identity_alias_count": len(set_fields["identity_keys"]),
        "private_profile_encrypted": bool(ciphertext),
        "plaintext_pii_stored": False,
    }


async def attach_customer_activity(
    db: Any,
    *,
    user_id: str,
    customer_identity_id: str,
    cart_id: str,
    order_number: str | None = None,
    activity_at: Any = None,
) -> None:
    """Keep bounded pointers; detailed history stays in event collections."""
    now = datetime.now(timezone.utc)
    set_fields: dict[str, Any] = {
        "last_cart_id": str(cart_id),
        "last_activity_at": activity_at or now,
        "updated_at": now,
    }
    if order_number:
        set_fields["last_order_number"] = str(order_number)
        set_fields["last_conversion_at"] = activity_at or now
    await getattr(db, CUSTOMER_IDENTITY_COLLECTION).update_one(
        {
            "user_id": str(user_id),
            "customer_identity_id": str(customer_identity_id),
        },
        {"$set": set_fields},
    )


def _external_id_variants(value: Any) -> list[Any]:
    rendered = _text(value)
    if not rendered:
        return []
    variants: list[Any] = [rendered]
    if rendered.isdigit():
        variants.append(int(rendered))
    return variants


def _mobile_variants(value: Any) -> list[str]:
    rendered = _text(value)
    normalized = _normal_phone(value)
    variants = {item for item in (rendered, normalized) if item}
    if normalized and normalized.startswith("9665") and len(normalized) == 12:
        variants.add(f"0{normalized[3:]}")
        variants.add(normalized[3:])
        variants.add(f"+{normalized}")
    return sorted(variants)


async def link_unified_customer_orders(
    db: Any,
    *,
    user_id: str,
    customer_identity_id: str,
    external_customer_id: Any = None,
    email: Any = None,
    mobile: Any = None,
    order_number: Any = None,
) -> int:
    """Attach current and historical orders without changing commerce facts."""
    terms: list[dict[str, Any]] = []
    external_variants = _external_id_variants(external_customer_id)
    if external_variants:
        for field in (
            "customer_id",
            "raw_by_source.salla_direct.customer.id",
            "raw_by_source.make.customer.id",
            "raw_by_source.custom_app.customer.id",
        ):
            terms.append({field: {"$in": external_variants}})
    else:
        mobile_variants = _mobile_variants(mobile)
        if mobile_variants:
            for field in (
                "customer_mobile",
                "raw_by_source.salla_direct.customer.mobile",
                "raw_by_source.salla_direct.customer.phone",
                "raw_by_source.make.customer.mobile",
                "raw_by_source.make.customer.phone",
            ):
                terms.append({field: {"$in": mobile_variants}})
        normalized_email = _normal_email(email)
        if normalized_email:
            for field in (
                "customer_email",
                "raw_by_source.salla_direct.customer.email",
                "raw_by_source.make.customer.email",
            ):
                terms.append({field: normalized_email})
    if _text(order_number):
        terms.append({"order_number": str(order_number).strip()})
    if not terms:
        return 0

    now = datetime.now(timezone.utc)
    result = await db.unified_orders.update_many(
        {
            "user_id": str(user_id),
            "$and": [
                {"$or": terms},
                {
                    "$or": [
                        {"customer_identity_id": {"$exists": False}},
                        {"customer_identity_id": None},
                    ]
                },
            ],
        },
        {
            "$set": {
                "customer_identity_id": str(customer_identity_id),
                "customer_identity_linked_at": now,
                "customer_identity_link_source": "mezan_customer_memory_v1",
            }
        },
    )
    modified = int(getattr(result, "modified_count", 0) or 0)
    identity_update: dict[str, Any] = {
        "$set": {
            "orders_linked_at": now,
            "last_order_link_count": modified,
            "updated_at": now,
        }
    }
    if modified:
        identity_update["$inc"] = {"linked_order_count": modified}
    await getattr(db, CUSTOMER_IDENTITY_COLLECTION).update_one(
        {
            "user_id": str(user_id),
            "customer_identity_id": str(customer_identity_id),
        },
        identity_update,
    )
    return modified


async def ensure_customer_identity_indexes(db: Any) -> None:
    collection = getattr(db, CUSTOMER_IDENTITY_COLLECTION)
    await collection.create_index(
        [("user_id", 1), ("customer_identity_id", 1)],
        unique=True,
        name="mezan_customer_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("merchant_id", 1), ("identity_keys", 1)],
        name="mezan_customer_identity_alias_lookup",
    )
    await collection.create_index(
        [("user_id", 1), ("last_activity_at", -1)],
        name="mezan_customer_identity_recent_activity",
    )
    await db.unified_orders.create_index(
        [("user_id", 1), ("customer_identity_id", 1), ("order_date", -1)],
        name="unified_orders_customer_memory",
    )
    await db.unified_orders.create_index(
        [("user_id", 1), ("raw_by_source.salla_direct.customer.id", 1)],
        name="unified_orders_salla_customer_lookup",
    )


__all__ = [
    "CUSTOMER_IDENTITY_COLLECTION",
    "CUSTOMER_IDENTITY_SCHEMA_VERSION",
    "attach_customer_activity",
    "build_identity_keys",
    "decrypt_private_payload",
    "encrypt_private_payload",
    "ensure_customer_identity_indexes",
    "link_unified_customer_orders",
    "resolve_customer_identity",
]
