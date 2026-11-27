"""Verify a Salla carrier-rate selection against the shipping address.

A Salla estimate is NOT the final cart shipping price; a later authoritative
cart-wide quote or owner-approved final price is still required before the
customer confirms and Mezan creates a pending-payment order.
"""

from __future__ import annotations

from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from typing import Any,Sequence

from .payload import PendingOrder


class ShippingSelectionUnverified(ValueError):
    pass


def _price(value: Any) -> str:
    try:
        number=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError) as exc:
        raise ShippingSelectionUnverified("invalid_shipping_price") from exc
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2:
        raise ShippingSelectionUnverified("invalid_shipping_price")
    return f"{number:.2f}"


def validate_shipping_selection(
    order: PendingOrder,
    *,
    selected_rate_id: int,
    rate_candidates: Sequence[dict],
    checked_at: datetime,
    now: datetime | None = None,
) -> dict:
    """Allowlisted destination/carrier/COD preflight, NEVER final amount."""
    moment=now or datetime.now(timezone.utc)
    if (
        not isinstance(order,PendingOrder) or
        type(selected_rate_id) is not int or selected_rate_id<=0 or
        not isinstance(rate_candidates, (list,tuple)) or len(rate_candidates)>100 or
        type(checked_at) is not datetime or checked_at.tzinfo is None or
        moment.tzinfo is None or
        not (timedelta(0)<=moment-checked_at<=timedelta(seconds=120))
    ):
        raise ShippingSelectionUnverified("fresh_salla_carrier_quote_required")
    matching=[
        row for row in rate_candidates
        if isinstance(row,dict) and row.get("rate_id")==selected_rate_id
    ]
    if len(matching)!=1:
        raise ShippingSelectionUnverified("unambiguous_carrier_rate_required")
    row=matching[0]
    if row.get("source")!="salla_shipping_rate_estimate" or row.get("is_final_cart_quote") is not False:
        raise ShippingSelectionUnverified("non_authoritative_shipping_rate")
    if (
        row.get("destination_country_id")!=order.ship_to.country or
        row.get("destination_city_id")!=order.ship_to.city
    ):
        raise ShippingSelectionUnverified("shipping_destination_changed")
    if type(row.get("shipping_company_id")) is not int:
        raise ShippingSelectionUnverified("invalid_carrier_id")
    if str(row["shipping_company_id"])!=str(order.courier_id):
        raise ShippingSelectionUnverified("unselected_carrier")
    base=_price(row.get("estimated_shipping_sar"))
    cod_fee=None
    if "cod" in order.accepted_methods:
        if row.get("cod_service_found") is not True:
            raise ShippingSelectionUnverified("cod_not_available_for_carrier")
        cod_fee=_price(row.get("cod_fee_estimate_sar"))
    return {
        "rate_id": selected_rate_id,
        "shipping_company_id": row["shipping_company_id"],
        "destination_country_id": order.ship_to.country,
        "destination_city_id": order.ship_to.city,
        "shipping_estimate_sar": base,
        "cod_fee_estimate_sar": cod_fee,
        "source": "salla_shipping_rate_estimate",
        "is_final_cart_quote": False,
        "can_create_order": False,
    }
