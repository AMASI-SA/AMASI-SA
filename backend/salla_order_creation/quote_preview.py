"""Read-only preview of an assisted Salla order, NEVER a final quote.

Mezan can use this to show a customer which items, selected options and
shipping carrier are verifiably available. Prices are source *indications*:
Salla tax settings, offers, customer scope, checkout shipping and COD
fees may change the final payable amount. This module never records a
`verified` quote or authorizes a Salla POST.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from .catalog_validation import validate_selected_cart
from .payload import PendingOrder
from .shipping_selection import validate_shipping_selection


def preview_assisted_order(
    order: PendingOrder,
    *,
    catalog_by_product_id: Mapping[int, dict],
    selected_rate_id: int,
    shipping_rate_candidates: Sequence[dict],
    catalog_checked_at: datetime,
    carrier_checked_at: datetime,
    now: datetime,
    discount_code: str | None = None,
) -> dict:
    """Return only known facts, with no official grand total or payable URL.

    The provider's variant `price` may not have the same VAT treatment as
    `taxed_price` and shipping-rate estimates do not include all cart fees.
    Returning a grand total here would be materially misleading.
    """
    lines = validate_selected_cart(
        order,
        catalog_by_product_id=catalog_by_product_id,
        checked_at=catalog_checked_at,
        now=now,
    )
    carrier = validate_shipping_selection(
        order,
        selected_rate_id=selected_rate_id,
        rate_candidates=shipping_rate_candidates,
        checked_at=carrier_checked_at,
        now=now,
    )
    display_lines = [
        {
            "product_id": item["product_id"],
            "variant_id": item["variant_id"],
            "quantity": item["quantity"],
            "option_value_ids": item["option_value_ids"],
            "provider_list_price_sar": item["provider_list_price_sar"],
            "is_final_price": False,
        }
        for item in lines
    ]
    # Never add product listing amounts and estimate shipping into a
    # customer-promised total. The actual cart may have discounts or extra
    # taxable service fees that only Salla's completed checkout can confirm.
    return {
        "state": "awaiting_authoritative_final_quote",
        "item_lines": display_lines,
        "carrier_id": carrier["shipping_company_id"],
        "carrier_rate_id": carrier["rate_id"],
        "destination_country_id": carrier["destination_country_id"],
        "destination_city_id": carrier["destination_city_id"],
        "shipping_estimate_sar": carrier["shipping_estimate_sar"],
        "cod_fee_estimate_sar": carrier["cod_fee_estimate_sar"],
        "discount_code_requested": bool(discount_code),
        "verified_grand_total_sar": None,
        "vat_and_offer_eligibility_verified": False,
        "is_final_cart_quote": False,
        "can_create_order": False,
        "reasons": [
            "provider_cart_total_unverified",
            "shipping_estimate_not_final",
            "tax_and_option_treatment_requires_checkout",
            *(
                ["coupon_checkout_eligibility_unverified"]
                if discount_code else []
            ),
        ],
    }
