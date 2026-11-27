"""Read the active Salla *store payment bank*, not a guessed bank.

Official GET /payment/banks/{bank_id}, scoped payments.read.
Unlike merchant settlement payout accounts, these are bank-transfer accounts
enabled for customers. Customer-service AI may show only approved bank details
read from this trusted source, not fake/old IBAN or arbitrary WhatsApp input.

No order creation, no bank credit lookup, no payment mutation.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .readers import _scope, _reply_data, _resolve_salla_readers, SourceUnavailable
from .bank_receipt_review import _iban


async def read_active_salla_payment_bank(
    db: Any, owner_id: str, bank_id: int, *,
    request: Callable[..., Awaitable[dict]] | None = None,
    integration_reader: Callable[..., Awaitable[dict]] | None = None,
) -> dict:
    if type(bank_id) is not int or bank_id <= 0:
        raise SourceUnavailable("positive_salla_store_payment_bank_id_required")
    request, integration_reader = _resolve_salla_readers(
        request, integration_reader,
    )
    await _scope(db, owner_id, "payments.read", integration_reader)
    response = await request(
        db, owner_id, "GET", f"/payment/banks/{bank_id}",
    )
    data = _reply_data(response, expected_list=False)
    if (type(data.get("id")) is not int or data["id"] != bank_id
            or data.get("status") != "active"):
        raise SourceUnavailable("store_bank_not_active_or_id_mismatch")
    name = data.get("account_name")
    if not isinstance(name, str) or not 3 <= len(name.strip()) <= 160:
        raise SourceUnavailable("bank_beneficiary_name_missing")
    iban = _iban(data.get("iban"))
    if not iban:
        # Salla's public example for this endpoint omits IBAN; do not guess.
        # The merchant must verify its actual API response/capability first.
        raise SourceUnavailable("current_store_payment_bank_iban_unavailable")
    return {
        "source": "salla_store_payment_banks",
        "bank_id": bank_id,
        "status": "active",
        "bank_name": str(data.get("bank_name") or "")[:120],
        "account_name": name.strip(),
        "iban": iban,
    }
