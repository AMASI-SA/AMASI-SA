"""Salla bank-transfer pending order payload and active receiving bank lookup.

The customer may claim a transfer was sent, but a WhatsApp receipt image
DOES NOT prove settlement. Create Order MUST remain pending_payment; never
send payment.status=paid, never auto-post GL or auto-start fulfillment.

Official Merchant API:
- POST /orders payment.method=bank, store_bank_id
  https://docs.salla.dev/5394145e0
- GET /payment/banks/{bank_id} and List Banks need payments.read
  https://docs.salla.dev/5394166e0
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable

from .payload import PendingOrder, build_pending_order_payload
from .readers import SourceUnavailable, _resolve_salla_readers, _scope


class BankSelectionDenied(ValueError):
    pass


def build_pending_bank_order_payload(
    order: PendingOrder, *, store_bank_id: int,
) -> dict:
    if not isinstance(order, PendingOrder):
        raise BankSelectionDenied("verified_order_required")
    if order.accepted_methods != ["bank"]:
        raise BankSelectionDenied("bank_payment_method_only")
    if type(store_bank_id) is not int or store_bank_id <= 0:
        raise BankSelectionDenied("active_store_bank_id_required")
    payload = build_pending_order_payload(order)
    payload["payment"] = {
        "status": "pending_payment",
        "method": "bank",
        "store_bank_id": store_bank_id,
        "accepted_methods": ["bank"],
    }
    # The original WhatsApp attachment is private. Never expose an invented
    # image URL as receipt_image_path in the Salla request.
    assert "receipt_image_path" not in payload["payment"]
    return payload


def bank_order_digest(order: PendingOrder, *, store_bank_id: int) -> str:
    body = build_pending_bank_order_payload(order, store_bank_id=store_bank_id)
    raw = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def get_verified_receiving_bank(
    db: Any, user_id: str, bank_id: int,
    *, request: Callable[..., Awaitable[dict]] | None = None,
    integration_reader: Callable[..., Awaitable[dict]] | None = None,
) -> dict:
    """Read actual active Salla merchant bank, without leaking IBAN details."""
    if type(bank_id) is not int or bank_id <= 0:
        raise BankSelectionDenied("invalid_store_bank_id")
    request, integration_reader = _resolve_salla_readers(
        request, integration_reader,
    )
    await _scope(db, user_id, "payments.read", integration_reader)
    response = await request(
        db, user_id, "GET", f"/payment/banks/{bank_id}",
    )
    if not isinstance(response, dict) or response.get("success") is False:
        raise BankSelectionDenied("salla_receiving_bank_unavailable")
    data = response.get("data")
    if not isinstance(data, dict) or data.get("id") != bank_id:
        raise BankSelectionDenied("bank_identity_unverified")
    if data.get("status") != "active":
        raise BankSelectionDenied("bank_is_not_active")
    name = data.get("bank_name")
    beneficiary = data.get("account_name")
    if (not isinstance(name, str) or not name.strip() or
            not isinstance(beneficiary, str) or not beneficiary.strip()):
        raise BankSelectionDenied("bank_beneficiary_unverified")
    return {
        "store_bank_id": bank_id,
        "bank_name": name.strip()[:120],
        "beneficiary_name": beneficiary.strip()[:140],
        "source": "salla_active_store_bank_details",
        "active": True,
        "payment_funds_verified": False,
    }
