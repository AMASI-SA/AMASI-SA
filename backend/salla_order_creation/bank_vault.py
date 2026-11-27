"""Encrypted Mezan customer fields → bank-transfer pending review quote.

The trusted Chatwoot worker sends only tenant/receipt/cart/catalog IDs, never
raw buyer/recipient name, phone, email, address, IBAN or receipt image bytes.
The Mezan server decrypts scoped customer data and verifies receipt metadata,
merchant bank and Salla product/shipping source before preparing an UNPAID
bank-order quote. No Salla POST or GL happens in this function.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable

from .bank_quote_writer import prepare_bank_receipt_order_intent
from .private_fields import (
    PrivateOrderFieldDenied,
    load_private_order_fields,
)
from .private_order_builder import (
    PrivateOrderAssemblyDenied,
    assemble_pending_from_private_fields,
)


async def prepare_bank_from_private_vault(
    db: Any, *,
    owner_id: str,
    account_id: int, inbox_id: int, conversation_id: int,
    intent_id: str, contact_id: int, message_id: int,
    receiver_country_code: str,
    country_id: int, city_id: int, district_id: int,
    courier_id: int, selected_rate_id: int,
    products: list[dict], branch_id: int | None = None,
    enabled: bool = False,
    now: datetime | None = None,
    **readers,
) -> dict:
    if not enabled:
        raise PrivateOrderAssemblyDenied("bank_private_vault_quote_disabled")
    try:
        stored = await load_private_order_fields(
            db, owner_id=owner_id,
            account_id=account_id, inbox_id=inbox_id,
            conversation_id=conversation_id,
            intent_id=intent_id, contact_id=contact_id,
            now=now, with_revision=True,
        )
    except PrivateOrderFieldDenied:
        raise PrivateOrderAssemblyDenied("bank_private_fields_unavailable") from None
    pending = assemble_pending_from_private_fields(
        stored["fields"],
        receiver_country_code=receiver_country_code,
        country_id=country_id, city_id=city_id,
        district_id=district_id, courier_id=courier_id,
        products=products, accepted_methods=["bank"],
        branch_id=branch_id,
    )
    return await prepare_bank_receipt_order_intent(
        db, owner_id=owner_id, intent_id=intent_id,
        inbox_id=str(inbox_id), conversation_id=str(conversation_id),
        source_contact_id=contact_id, source_message_id=message_id,
        order=pending, selected_rate_id=selected_rate_id,
        private_vault_revision=stored["revision"],
        private_vault_ref=stored["vault_ref"],
        enabled=True,
        **({"now": now} if now is not None else {}),
        **readers,
    )
