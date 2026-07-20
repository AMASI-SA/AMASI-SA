"""Read Qoyod order facts through the same engine as the New Orders page."""
from __future__ import annotations

from typing import Any

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


async def get_order_payment_facts(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Return Qoyod facts from the same normalized order used by Orders V2."""
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
            # Qoyod needs the fee's gross contribution so its invoice closes
            # to Salla's final total.  Keep the pre-tax value for audit/UI.
            "cod_fee_amount": order.totals.cod_fee_total,
            "cod_fee_net_amount": order.totals.cod_fee,
            "cod_fee_tax_amount": order.totals.cod_fee_tax,
            "cod_fee_source": order.totals.cod_fee_source,
        }

    return {
        "payment_method": None,
        "payment_method_native": None,
        "receiving_bank_code": None,
        "receiving_bank_name": None,
        "cod_fee_amount": 0.0,
        "cod_fee_net_amount": 0.0,
        "cod_fee_tax_amount": 0.0,
        "cod_fee_source": None,
    }
