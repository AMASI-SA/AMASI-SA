"""Read Qoyod order facts through the same engine as the New Orders page."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


_TWO_PLACES = Decimal("0.01")


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(
            _TWO_PLACES,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _cod_fee_gross(
    *,
    net_amount: Any,
    gross_amount: Any,
    tax_amount: Any,
    tax_percent: Any,
) -> float:
    """Return the explicit COD fee's gross contribution for Qoyod.

    New unified rows persist ``cod_fee_total`` directly.  Rows normalized
    before that field was introduced can still contain the explicit Salla
    ``cod_fee`` displayed by Orders V2, so rebuild only its tax-inclusive
    value from the row's own fee/tax fields.  The order-total residual is
    deliberately never used.
    """
    gross = _money(gross_amount)
    if gross > 0:
        return float(gross)

    net = _money(net_amount)
    if net <= 0:
        return 0.0

    tax = _money(tax_amount)
    if tax > 0:
        return float(_money(net + tax))

    try:
        percent = Decimal(str(tax_percent or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal("0")
    if percent <= 0:
        return float(net)
    return float(_money(net * (Decimal("1") + percent / Decimal("100"))))


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
        cod_fee_net_amount = float(order.totals.cod_fee or 0.0)
        cod_fee_tax_amount = float(order.totals.cod_fee_tax or 0.0)
        cod_fee_amount = _cod_fee_gross(
            net_amount=cod_fee_net_amount,
            gross_amount=order.totals.cod_fee_total,
            tax_amount=cod_fee_tax_amount,
            tax_percent=order.totals.tax_percent,
        )
        cod_fee_source = order.totals.cod_fee_source
        return {
            "payment_method": order.payment.method,
            "payment_method_native": order.payment.method_native,
            "receiving_bank_code": order.payment.receiving_bank_code,
            "receiving_bank_name": order.payment.receiving_bank_name,
            # Qoyod needs the fee's gross contribution so its invoice closes
            # to Salla's final total.  Keep the pre-tax value for audit/UI.
            "cod_fee_amount": cod_fee_amount,
            "cod_fee_net_amount": cod_fee_net_amount,
            "cod_fee_tax_amount": cod_fee_tax_amount,
            "cod_fee_source": cod_fee_source,
            # The Order Engine never derives this amount from the order-total
            # residual: a positive value can only come from an explicit Salla
            # COD-fee field.  Older unified rows may not have retained the
            # textual audit path, so keep an independent trust flag instead
            # of dropping a real fee merely because that optional string is
            # absent.
            "cod_fee_is_explicit": cod_fee_net_amount > 0 or cod_fee_amount > 0,
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
        "cod_fee_is_explicit": False,
    }
