"""Encrypt full temporary Salla order payload before Mongo quote storage.

Mezan holds buyer/recipient phone, address and potentially engraving text only
inside encrypted, expiring quote records. The private customer fields key is
separate from the Salla OAuth token key and never leaves the Mezan server.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import InvalidToken

from .payload import PendingOrder
from .private_fields import _fernet_from_env


class EncryptedOrderUnavailable(ValueError):
    pass


def encrypt_order_payload(
    order: PendingOrder, *, owner_id: str, intent_id: str, cipher: Any = None,
) -> bytes:
    if not isinstance(order, PendingOrder) or not owner_id or not intent_id:
        raise EncryptedOrderUnavailable("trusted_order_payload_required")
    # No PII in an external log/return. The schema binds the ciphertext to
    # the merchant and immutable customer order intent to prevent swapping.
    plaintext = json.dumps({
        "owner_id": owner_id,
        "intent_id": intent_id,
        "order": order.model_dump(exclude_none=False),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(plaintext) > 64*1024:
        raise EncryptedOrderUnavailable("assisted_payload_too_large")
    try:
        return (cipher or _fernet_from_env()).encrypt(plaintext)
    except Exception as exc:
        raise EncryptedOrderUnavailable("assisted_payload_encryption_unavailable") from None


def decrypt_order_payload(
    ciphertext: bytes, *, owner_id: str, intent_id: str,
    cipher: Any = None,
) -> PendingOrder:
    if not isinstance(ciphertext, bytes) or len(ciphertext) > 96*1024:
        raise EncryptedOrderUnavailable("encrypted_assisted_order_required")
    try:
        raw=(cipher or _fernet_from_env()).decrypt(ciphertext)
        if len(raw) > 64*1024:
            raise ValueError("unexpected decrypted length")
        parsed=json.loads(raw.decode("utf-8"))
        if (type(parsed) is not dict or
                parsed.get("owner_id") != owner_id or
                parsed.get("intent_id") != intent_id):
            raise ValueError("encrypted scope mismatch")
        return PendingOrder.model_validate(parsed.get("order"))
    except Exception:
        # Never report decrypted customer PII in an API error or traceback.
        raise EncryptedOrderUnavailable("encrypted_assisted_order_unavailable") from None
