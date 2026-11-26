"""Strict preflight of selected catalog option IDs and variant stock.

Salla product details are the authority for IDs/availability. No customer
price, shipping quote or VAT is certified by this validator. It never writes
to Salla or generates a checkout URL.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .payload import PendingOrder


class CartUnverified(ValueError):
    pass


_ALLOWED_SELECTABLE = frozenset({"radio", "select", "checkbox", "image", "button"})
_TEXT_TYPES = frozenset({"text", "textarea"})
_FALLBACK_MAX_TEXT_LENGTH = 120
_FRESHNESS = timedelta(seconds=120)


def _price(value: Any) -> str:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CartUnverified("invalid_provider_catalog_price") from exc
    if price < 0 or not price.is_finite() or price.as_tuple().exponent < -2:
        raise CartUnverified("invalid_provider_catalog_price")
    return f"{price:.2f}"


def validate_selected_cart(
    order: PendingOrder,
    *,
    catalog_by_product_id: Mapping[int,dict],
    checked_at: datetime,
    now: datetime | None = None,
) -> list[dict]:
    """Only accepts recently obtained product-details facts from Mezan.

    Caller must fetch catalog using the trusted server-side Salla read client.
    A checked_at supplied by a model or client is not source verification.
    """
    moment = now or datetime.now(timezone.utc)
    if (
        not isinstance(order, PendingOrder)
        or not isinstance(catalog_by_product_id, Mapping)
        or type(checked_at) is not datetime
        or checked_at.tzinfo is None
        or moment.tzinfo is None
        or not (timedelta(0) <= moment - checked_at <= _FRESHNESS)
    ):
        raise CartUnverified("fresh_authoritative_catalog_required")
    total_by_product = defaultdict(int)
    total_by_variant = defaultdict(int)
    facts = []

    for product in order.products:
        if product.identifier_type != "id" or type(product.identifier) is not int:
            # SKU-only selection can resolve to multiple variants or records.
            # Resolve to a canonical product ID before automation.
            raise CartUnverified("canonical_product_id_required")
        provider = catalog_by_product_id.get(product.identifier)
        if (
            not isinstance(provider,dict)
            or provider.get("source") != "salla_product_details"
            or provider.get("product_id") != product.identifier
            or provider.get("can_use_as_final_quote") is not False
        ):
            raise CartUnverified("product_source_unverified")
        _price(provider.get("taxed_base_price_sar"))
        choice_map = {}
        for option in provider.get("options") or []:
            if not isinstance(option,dict) or type(option.get("option_id")) is not int:
                raise CartUnverified("invalid_catalog_options")
            if option["option_id"] in choice_map:
                raise CartUnverified("duplicate_catalog_option")
            choice_map[option["option_id"]] = option
        selected_value_ids = []
        selected_options = set()
        selected_text_option_ids = []
        picked_map = {picked.id: picked.value for picked in product.options}

        def is_active(option: dict) -> bool:
            visibility = option.get("visibility") or "always"
            if visibility == "always":
                return True
            if visibility != "on_condition":
                raise CartUnverified("unsupported_option_visibility")
            operation = option.get("visibility_condition_type")
            ref_id = option.get("visibility_condition_option")
            ref_value = option.get("visibility_condition_value")
            if (operation not in ("=", "!=")
                    or type(ref_id) is not int
                    or type(ref_value) is not int
                    or ref_id not in choice_map
                    or ref_id == option.get("option_id")):
                raise CartUnverified("unsupported_conditional_option")
            selected = picked_map.get(ref_id)
            # No base choice means this conditional field cannot be active.
            if not selected:
                return False
            equal = str(ref_value) in selected
            return equal if operation == "=" else not equal

        for picked in product.options:
            option = choice_map.get(picked.id)
            if not option:
                raise CartUnverified("unknown_product_option")
            if not is_active(option):
                raise CartUnverified("hidden_option_submitted")
            opt_type = option.get("type")
            if opt_type in _TEXT_TYPES:
                if len(picked.value) != 1 or type(picked.value[0]) is not str:
                    raise CartUnverified("text_option_must_be_one_string")
                text_value = picked.value[0]
                if (not text_value.strip() or "<" in text_value or ">" in text_value
                        or any(unicodedata.category(ch).startswith("C")
                               for ch in text_value)):
                    raise CartUnverified("unsafe_personalization_text")
                chars_limit = option.get("characters_limit")
                if chars_limit is None:
                    max_chars = _FALLBACK_MAX_TEXT_LENGTH
                elif (type(chars_limit) is int and
                        0 < chars_limit <= 500):
                    max_chars = chars_limit
                elif (isinstance(chars_limit, str) and chars_limit.isascii()
                      and chars_limit.isdecimal() and
                      0 < int(chars_limit) <= 500):
                    max_chars = int(chars_limit)
                else:
                    raise CartUnverified("text_option_limit_unverified")
                if len(text_value) > max_chars:
                    raise CartUnverified("personalization_text_too_long")
                selected_text_option_ids.append(picked.id)
                selected_options.add(picked.id)
                continue
            if opt_type not in _ALLOWED_SELECTABLE:
                raise CartUnverified("unsupported_product_option")
            if opt_type != "checkbox" and len(picked.value) != 1:
                raise CartUnverified("multiple_choice_for_single_option")
            known_values = {}
            for v in option.get("values") or []:
                if (not isinstance(v, dict)
                        or type(v.get("value_id")) is not int
                        or v["value_id"] in known_values):
                    raise CartUnverified("invalid_catalog_values")
                known_values[v["value_id"]] = v
            if not known_values:
                raise CartUnverified("option_values_not_verified")
            for raw in picked.value:
                if (not isinstance(raw, str) or not raw.isascii()
                        or not raw.isdecimal() or str(int(raw)) != raw):
                    raise CartUnverified("noncanonical_option_value")
                value = known_values.get(int(raw))
                if not value or value.get("out_of_stock") is True:
                    raise CartUnverified("unavailable_option_value")
                _price(value.get("price_delta_sar"))
                selected_value_ids.append(int(raw))
            selected_options.add(picked.id)
        for option_id, opt in choice_map.items():
            if (opt.get("required") is True
                    and is_active(opt)
                    and option_id not in selected_options):
                raise CartUnverified("required_option_missing")
        if len(selected_value_ids) != len(set(selected_value_ids)):
            raise CartUnverified("duplicate_option_value")

        # Match exact variant option set. Do not accept product-wide stock as
        # proof that a selected color/size SKU can be sold.
        variant_id = None
        price_hint = _price(provider["taxed_base_price_sar"])
        variants = provider.get("variants") or []
        if variants:
            matches = [
                v for v in variants
                if isinstance(v,dict)
                and v.get("selected_option_value_ids") == sorted(selected_value_ids)
            ]
            if len(matches) != 1:
                raise CartUnverified("unique_variant_match_required")
            variant = matches[0]
            if type(variant.get("variant_id")) is not int:
                raise CartUnverified("variant_identity_invalid")
            variant_id = variant["variant_id"]
            price_hint = _price(variant.get("provider_variant_price_sar"))
            total_by_variant[(product.identifier, variant_id)] += product.quantity
            variant_qty = variant.get("available_quantity")
            if variant_qty is not None and (
                type(variant_qty) is not int or
                total_by_variant[(product.identifier, variant_id)] > variant_qty
            ):
                raise CartUnverified("selected_variant_out_of_stock")
        total_by_product[product.identifier] += product.quantity
        available = provider.get("available_quantity")
        if available is not None and (
            type(available) is not int or total_by_product[product.identifier] > available
        ):
            raise CartUnverified("product_inventory_insufficient")
        facts.append({
            "product_id": product.identifier,
            "variant_id": variant_id,
            "quantity": product.quantity,
            "option_value_ids": sorted(selected_value_ids),
            "selected_text_option_ids": sorted(selected_text_option_ids),
            "provider_list_price_sar": price_hint,
            "verified_catalog_reference": "salla_product_details",
            "is_final_cart_quote": False,
        })
    return facts
