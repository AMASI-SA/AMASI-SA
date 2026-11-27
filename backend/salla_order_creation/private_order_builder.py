"""Assemble a Salla pending order from private Mezan fields, not an LLM.

The customer-facing AI may identify a product and desired options without
receiving customer phone, email, full address or location. The trusted Mezan
backend decrypts temporary customer fields ONLY for a purpose-scoped Salla
order preparation, validates mandatory address fields and uses the existing
provider-backed product/carrier/payment eligibility verifier.

This module does not create a Salla order or generate a payment URL.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable
from datetime import datetime

from .payload import PendingOrder
from .private_fields import PrivateOrderFieldDenied, load_private_order_fields
from .intent_writer import prepare_salla_unpaid_order_intent

ALLOWED_COUNTRIES = frozenset({"SA", "QA", "KW", "AE", "BH", "OM"})


class PrivateOrderAssemblyDenied(ValueError):
    pass


def assemble_pending_from_private_fields(
    data: dict, *,
    receiver_country_code: str,
    country_id: int,
    city_id: int,
    district_id: int,
    courier_id: int,
    products: list[dict],
    accepted_methods: list[str],
    branch_id: int | None = None,
) -> PendingOrder:
    """Return full Pydantic payload privately inside Mezan, never to bot."""
    if type(data) is not dict or receiver_country_code not in ALLOWED_COUNTRIES:
        raise PrivateOrderAssemblyDenied("unverified_private_order_scope")
    if (not all(type(v) is int and v > 0 for v in (
        country_id, city_id, district_id, courier_id,
    )) or (branch_id is not None and
           (type(branch_id) is not int or branch_id < 1))):
        raise PrivateOrderAssemblyDenied("valid_salla_region_ids_required")
    if type(products) is not list or len(products) < 1 or len(products) > 50:
        raise PrivateOrderAssemblyDenied("trusted_catalog_selection_required")
    if type(accepted_methods) is not list or not accepted_methods:
        raise PrivateOrderAssemblyDenied("verified_online_method_required")

    # ChatGPT/Chatwoot may reference a personalized option by the exact
    # opaque token private.option_text_<salla_option_id>. The actual engraved
    # name is resolved ONLY from Mezan's encrypted private customer vault,
    # then checked against the authoritative Salla product option schema.
    resolved_products=[]
    for row in products:
        if type(row) is not dict:
            raise PrivateOrderAssemblyDenied("invalid_private_catalog_row")
        item=dict(row)
        raw_options=item.get("options") or []
        if type(raw_options) is not list:
            raise PrivateOrderAssemblyDenied("invalid_private_catalog_options")
        options=[]
        for option in raw_options:
            if type(option) is not dict or type(option.get("id")) is not int:
                raise PrivateOrderAssemblyDenied("invalid_private_catalog_option")
            raw_values=option.get("value")
            if type(raw_values) is not list or not raw_values:
                raise PrivateOrderAssemblyDenied("invalid_private_option_values")
            values=[]
            for value in raw_values:
                if type(value) is not str:
                    raise PrivateOrderAssemblyDenied("invalid_private_option_value")
                if (value.isascii() and value.isdecimal() and
                        str(int(value)) == value):
                    values.append(value)
                    continue
                expected=f"private.option_text_{option['id']}"
                if value != expected:
                    raise PrivateOrderAssemblyDenied("private_personalization_not_yet_supported")
                actual=data.get(f"option_text_{option['id']}")
                if type(actual) is not str or not actual.strip():
                    raise PrivateOrderAssemblyDenied("private_option_text_missing")
                values.append(actual)
            options.append({**option,"value":values})
        if raw_options:
            item["options"]=options
        resolved_products.append(item)

    fields = {
        "customer": {
            "name": data.get("buyer_name"),
            "mobile": data.get("buyer_mobile"),
            "email": data.get("buyer_email"),
        },
        "receiver": {
            "name": data.get("recipient_name"),
            "phone": data.get("recipient_phone"),
            "country_code": receiver_country_code,
        },
        "courier_id": str(courier_id),
        "ship_to": {
            "country": country_id,
            "city": city_id,
            "district": district_id,
            "block": None,
            "street_number": data.get("street_number"),
            "address_line": data.get("address_line"),
            "postal_code": data.get("postal_code"),
            "short_address": data.get("short_address"),
            "building_number": data.get("building_number"),
            "additional_number": data.get("additional_number"),
        },
        "accepted_methods": accepted_methods,
        "products": resolved_products,
    }
    if receiver_country_code == "SA":
        if not data.get("geo_lat") or not data.get("geo_lng"):
            raise PrivateOrderAssemblyDenied("customer_location_coordinates_required")
        try:
            fields["ship_to"]["geo_coordinates"] = {
                "lat": float(data["geo_lat"]),
                "lng": float(data["geo_lng"]),
            }
        except (ValueError, TypeError) as exc:
            raise PrivateOrderAssemblyDenied("invalid_location_coordinates") from exc
    if branch_id is not None:
        fields["branch_id"] = branch_id
    try:
        return PendingOrder.model_validate(fields)
    except (TypeError, ValueError) as exc:
        # No raw private field values in exceptions.
        raise PrivateOrderAssemblyDenied("required_private_order_fields_incomplete") from exc


async def prepare_from_private_vault(
    db: Any, *,
    owner_id: str,
    account_id: int,
    inbox_id: int,
    conversation_id: int,
    intent_id: str,
    contact_id: int,
    current_message_id: int,
    receiver_country_code: str,
    country_id: int,
    city_id: int,
    district_id: int,
    courier_id: int,
    selected_rate_id: int,
    products: list[dict],
    accepted_methods: list[str],
    branch_id: int | None = None,
    enabled: bool = False,
    product_reader: Callable[..., Awaitable[dict]] | None = None,
    rate_reader: Callable[..., Awaitable[list[dict]]] | None = None,
    methods_reader: Callable[..., Awaitable[dict]] | None = None,
    now: datetime | None = None,
) -> dict:
    """Read private scope from Mezan then use provider-backed quote verifier.

    This is a PRIVILEGED server-only operation. No LLM may invoke it directly;
    /signed-prepare-vault requires a separate trusted Chatwoot HMAC.
    """
    if not enabled:
        raise PrivateOrderAssemblyDenied("private_quote_preparation_disabled")
    try:
        private = await load_private_order_fields(
            db, owner_id=owner_id,
            account_id=account_id, inbox_id=inbox_id,
            conversation_id=conversation_id,
            intent_id=intent_id, contact_id=contact_id,
            now=now, with_revision=True,
        )
    except PrivateOrderFieldDenied as exc:
        raise PrivateOrderAssemblyDenied("private_order_fields_unavailable") from exc
    payload = assemble_pending_from_private_fields(
        private["fields"], receiver_country_code=receiver_country_code,
        country_id=country_id, city_id=city_id, district_id=district_id,
        courier_id=courier_id, products=products,
        accepted_methods=accepted_methods, branch_id=branch_id,
    )
    return await prepare_salla_unpaid_order_intent(
        db, user_id=owner_id,
        intent_id=intent_id, inbox_id=str(inbox_id),
        conversation_id=str(conversation_id),
        source_message_id=current_message_id,
        source_contact_id=contact_id,
        private_vault_revision=private["revision"],
        private_vault_ref=private["vault_ref"],
        order=payload, selected_rate_id=selected_rate_id,
        enabled=enabled,
        **({"product_reader": product_reader} if product_reader else {}),
        **({"rate_reader": rate_reader} if rate_reader else {}),
        **({"methods_reader": methods_reader} if methods_reader else {}),
        **({"now": now} if now else {}),
    )
