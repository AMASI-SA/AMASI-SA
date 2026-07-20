"""Read Qoyod order facts through the same engine as the New Orders page."""
from __future__ import annotations

from typing import Any, Optional

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


async def get_order_payment_facts(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Optional[str]]:
    """Return the normalized payment facts displayed by Order Details."""
    repository = MongoOrderRepository(db)
    tenant_ids = [str(user_id)]
    if str(user_id) != "main":
        tenant_ids.append("main")

    for tenant_id in tenant_ids:
        try:
            order = await get_order(
                repository,
                user_id=tenant_id,
                order_number=str(order_number),
            )
        except OrderNotFoundError:
            continue
        return {
            "payment_method": order.payment.method,
            "payment_method_native": order.payment.method_native,
            "receiving_bank_code": order.payment.receiving_bank_code,
            "receiving_bank_name": order.payment.receiving_bank_name,
        }

    return {
        "payment_method": None,
        "payment_method_native": None,
        "receiving_bank_code": None,
        "receiving_bank_name": None,
    }
