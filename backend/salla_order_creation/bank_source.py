"""Read merchant receiving bank from the existing Salla OAuth client.

No staff-supplied bank ID/IBAN, AI-supplied destination or stale hardcoded
account can authorize a bank receipt. Caller must use scoped Mezan Salla
GET /payment/banks/{id}; actual OAuth availability needs staging evidence.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from .bank_receipt import MerchantBankFact


class ReceivingBankUnavailable(ValueError):
    pass


async def get_authorized_receiving_bank(
    db: Any,
    *,
    user_id: str,
    store_bank_id: int,
    request: Callable[..., Awaitable[dict]],
) -> MerchantBankFact:
    if (not user_id or type(store_bank_id) is not int or
            store_bank_id <= 0 or not callable(request)):
        raise ReceivingBankUnavailable("trusted_store_bank_reference_required")
    try:
        response=await request(
            db, user_id, "GET", f"/payment/banks/{store_bank_id}"
        )
    except Exception:
        raise ReceivingBankUnavailable("salla_store_bank_not_readable") from None
    if not isinstance(response,dict) or response.get("success") is not True:
        raise ReceivingBankUnavailable("salla_store_bank_not_verified")
    row=response.get("data")
    if not isinstance(row,dict) or str(row.get("id"))!=str(store_bank_id):
        raise ReceivingBankUnavailable("salla_bank_id_mismatch")
    iban=str(row.get("iban_number") or "").replace(" ","").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}",iban):
        raise ReceivingBankUnavailable("bank_iban_unavailable")
    if str(row.get("status") or "").lower() != "active":
        raise ReceivingBankUnavailable("bank_account_not_active")
    return MerchantBankFact(
        user_id=user_id,store_bank_id=store_bank_id,
        iban_last4=iban[-4:],active=True,
        source="salla_active_merchant_bank",
    )
