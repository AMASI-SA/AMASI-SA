"""Pure Salla payload → canonical OrderDTO mapper.

This module performs no database, HTTP, FastAPI or Qoyod operations.

Input:
    Raw Salla order payload.

Output:
    Canonical OrderDTO.

Architecture:
- Salla owns external order facts.
- Mezan creates a stable operational `order_item_id`.
- Frontend consumers never depend on Salla or MongoDB response shapes.

See:
- docs/MEZAN_OS_ARCHITECTURE.md
- docs/PROJECT_DECISIONS.md
- docs/ORDER_CAPABILITY_AUDIT.md
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    AddressDTO,
    CustomerDTO,
    MoneyTotalsDTO,
    OrderDiscountDTO,
    OrderDTO,
    OrderItemDTO,
    OrderSourceDTO,
    PaymentDTO,
    ShippingDTO,
)


BUSINESS_TIMEZONE_NAME = "Asia/Riyadh"
BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)


class OrderMappingError(ValueError):
    """Raised when the provider payload cannot produce a valid OrderDTO."""


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, bool):
        return str(value).lower()

    text = str(value).strip()
    return text or None


def _media_url(value: Any, *, _depth: int = 0) -> Optional[str]:
    """Extract the first printable URL from Salla media/label shapes."""
    if value in (None, "") or _depth > 8:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in (
            "url",
            "pdf",
            "label_url",
            "download_url",
            "original",
            "src",
        ):
            result = _media_url(value.get(key), _depth=_depth + 1)
            if result:
                return result
        return None
    if isinstance(value, (list, tuple)):
        for candidate in value:
            result = _media_url(candidate, _depth=_depth + 1)
            if result:
                return result
    return None


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default

    if isinstance(value, dict):
        for key in ("amount", "value", "total"):
            if key in value:
                return _number(value.get(key), default)

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data

    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def _localized_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").translate(
        str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
    )
    text = text.replace("٬", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else default


def _discount_rows(
    raw_order: dict[str, Any],
    amounts: dict[str, Any],
) -> list[OrderDiscountDTO]:
    candidates = raw_order.get("discounts")
    if not isinstance(candidates, list):
        candidates = amounts.get("discounts")
    rows = candidates if isinstance(candidates, list) else []
    discounts: list[OrderDiscountDTO] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        amount = abs(_number(_first(
            row.get("discount"),
            row.get("amount"),
            row.get("value"),
        )))
        if amount <= 0:
            continue
        discounts.append(OrderDiscountDTO(
            title=_text(_first(row.get("title"), row.get("name"), row.get("label"))),
            code=_text(row.get("code")),
            type=_text(row.get("type")),
            amount=amount,
            discounted_shipping=abs(_number(row.get("discounted_shipping"))),
        ))

    if not discounts:
        fallback = abs(_number(_first(
            amounts.get("discount"),
            raw_order.get("discount"),
        )))
        if fallback > 0:
            discounts.append(OrderDiscountDTO(title="الخصم", amount=fallback))
    return discounts


def _order_options_total(
    raw_order: dict[str, Any],
    amounts: dict[str, Any],
) -> float:
    """Return only Salla's explicit order-level options amount.

    Product choice prices are already included in the product/subtotal values.
    Summing them here would count the same amount twice and would not match the
    ``خيارات الطلب`` row in Salla's order summary.
    """
    direct = _first(
        amounts.get("options"),
        amounts.get("options_total"),
        amounts.get("order_options"),
        raw_order.get("options_total"),
        raw_order.get("order_options_total"),
    )
    if direct is not None:
        return _number(direct)
    return 0.0


def _explicit_cod_fee(
    raw_order: dict[str, Any],
    amounts: dict[str, Any],
) -> tuple[float, Optional[str]]:
    """Return only a source-reported COD fee and its audit path.

    Salla exposes this order-level amount under ``amounts`` in full order
    details and under ``payment`` in some stored/webhook shapes.  A positive
    value is accepted only when an explicit source field exists; the
    order-total residual is deliberately not used as a fallback.
    """
    payment = _dict(raw_order.get("payment"))
    candidates = (
        ("amounts.cash_on_delivery", amounts.get("cash_on_delivery")),
        ("payment.cash_on_delivery", payment.get("cash_on_delivery")),
        ("amounts.cod_fee", amounts.get("cod_fee")),
        ("payment.cod_fee", payment.get("cod_fee")),
        ("amounts.payment_fee", amounts.get("payment_fee")),
        ("payment.payment_fee", payment.get("payment_fee")),
        ("amounts.cash_on_delivery_cost", amounts.get("cash_on_delivery_cost")),
        ("payment.cash_on_delivery_cost", payment.get("cash_on_delivery_cost")),
        ("cash_on_delivery", raw_order.get("cash_on_delivery")),
        ("cod_fee", raw_order.get("cod_fee")),
        ("payment_fee", raw_order.get("payment_fee")),
        ("cash_on_delivery_cost", raw_order.get("cash_on_delivery_cost")),
    )
    for source, value in candidates:
        if value in (None, "", [], {}):
            continue
        amount = _number(value)
        if amount > 0:
            return amount, source
    return 0.0, None


def _money_round(value: Any) -> float:
    """Round a source monetary value to halalah using decimal arithmetic."""
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    return float(decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _cod_fee_with_tax(cod_fee: float, tax_percent: Optional[float]) -> tuple[float, float]:
    """Return (gross fee, fee tax) from Salla's pre-tax COD commission.

    Salla reports ``amounts.cash_on_delivery`` alongside the order tax and
    adds both to the final total.  This calculation uses the tax percentage
    reported by that same order; it never derives the fee from a residual.
    """
    fee = _money_round(cod_fee)
    if fee <= 0:
        return 0.0, 0.0
    try:
        percent = Decimal(str(tax_percent)) if tax_percent is not None else Decimal("0")
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal("0")
    if percent <= 0:
        return fee, 0.0
    gross = _money_round(Decimal(str(fee)) * (Decimal("1") + percent / Decimal("100")))
    return gross, _money_round(Decimal(str(gross)) - Decimal(str(fee)))


def _weight_parts(value: Any) -> tuple[Optional[float], Optional[str]]:
    if value in (None, "", [], {}):
        return None, None
    if isinstance(value, dict):
        amount = _number(_first(value.get("value"), value.get("amount"), value.get("weight")))
        unit = _text(_first(value.get("units"), value.get("unit")))
    else:
        amount = _localized_number(value)
        unit = _text(value)
    normalized_unit = str(unit or "").strip().casefold()
    if any(token in normalized_unit for token in ("kg", "كجم", "كيلو")):
        normalized_unit = "kg"
    elif normalized_unit in {"g", "gm", "gram", "جرام", "غرام"}:
        normalized_unit = "g"
    else:
        normalized_unit = normalized_unit or "kg"
    return amount, normalized_unit


def _timezone_from_name(value: Any) -> ZoneInfo:
    name = _text(value) or BUSINESS_TIMEZONE_NAME
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return BUSINESS_TIMEZONE


def _parse_datetime(
    value: Any,
    *,
    default_timezone: ZoneInfo = BUSINESS_TIMEZONE,
) -> Optional[datetime]:
    """Parse a Salla timestamp and normalize it to aware UTC.

    Salla commonly returns local Riyadh wall-clock timestamps in objects such as
    ``{"date": "2026-07-15 19:23:00", "timezone": "Asia/Riyadh"}``.
    Treating that naive value as UTC adds three hours in the UI.  Explicit
    offsets/Z values remain authoritative.  Naive values default to Mezan's
    business timezone (Asia/Riyadh), then all canonical datetimes are converted
    to UTC.
    """
    if not value:
        return None

    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=default_timezone)
        return parsed.astimezone(timezone.utc)

    if isinstance(value, dict):
        source_timezone = _timezone_from_name(
            _first(
                value.get("timezone"),
                value.get("timezone_name"),
                value.get("tz"),
            )
        )
        return _parse_datetime(
            _first(
                value.get("date"),
                value.get("datetime"),
                value.get("value"),
            ),
            default_timezone=source_timezone,
        )

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        formats = (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        )

        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_timezone)

    return parsed.astimezone(timezone.utc)


def _canonical_bank(value: Any) -> tuple[Optional[str], Optional[str]]:
    text = (_text(value) or "").lower()

    if not text:
        return None, None

    aliases = {
        "bank_rajhi": (
            "الراجحي",
            "مصرف الراجحي",
            "بنك الراجحي",
            "rajhi",
            "al rajhi",
        ),
        "bank_inma": (
            "الإنماء",
            "الانماء",
            "بنك الإنماء",
            "بنك الانماء",
            "alinma",
            "al inma",
            "inma",
        ),
        "bank_ahli": (
            "الأهلي",
            "الاهلي",
            "البنك الأهلي",
            "البنك الاهلي",
            "بنك الأهلي",
            "بنك الاهلي",
            "ahli",
            "snb",
            "ncb",
            "saudi national bank",
        ),
    }

    names = {
        "bank_rajhi": "مصرف الراجحي",
        "bank_inma": "مصرف الإنماء",
        "bank_ahli": "البنك الأهلي السعودي",
    }

    for code, values in aliases.items():
        if any(alias.lower() in text for alias in values):
            return code, names[code]

    return None, _text(value)


def _address_from(value: Any) -> Optional[AddressDTO]:
    data = _dict(value)
    if not data:
        return None

    location = _dict(data.get("location"))
    coordinates = _dict(
        _first(
            data.get("coordinates"),
            location.get("coordinates"),
        )
    )

    latitude = _first(
        data.get("latitude"),
        data.get("lat"),
        location.get("latitude"),
        location.get("lat"),
        coordinates.get("latitude"),
        coordinates.get("lat"),
    )
    longitude = _first(
        data.get("longitude"),
        data.get("lng"),
        location.get("longitude"),
        location.get("lng"),
        coordinates.get("longitude"),
        coordinates.get("lng"),
    )

    country = _dict(data.get("country"))
    city = _dict(data.get("city"))
    district = _dict(data.get("district"))

    address = AddressDTO(
        country=_text(_first(country.get("name"), data.get("country"))),
        country_code=_text(
            _first(
                country.get("code"),
                country.get("country_code"),
                data.get("country_code"),
            )
        ),
        city=_text(_first(city.get("name"), data.get("city"))),
        district=_text(
            _first(
                district.get("name"),
                data.get("district_name"),
                data.get("neighborhood"),
                data.get("block"),
            )
        ),
        street=_text(
            _first(
                data.get("street"),
                data.get("street_name"),
                data.get("street_number"),
                data.get("address_line"),
            )
        ),
        postal_code=_text(
            _first(
                data.get("postal_code"),
                data.get("zip_code"),
            )
        ),
        building_number=_text(
            _first(
                data.get("building_number"),
                data.get("building_no"),
            )
        ),
        additional_number=_text(data.get("additional_number")),
        short_address=_text(
            _first(
                data.get("short_address"),
                data.get("national_address"),
                data.get("national_address_code"),
            )
        ),
        formatted=_text(
            _first(
                data.get("formatted"),
                data.get("formatted_address"),
                data.get("description"),
                data.get("address_line_two"),
                data.get("address_line"),
                data.get("address"),
            )
        ),
        latitude=_number(latitude, default=0.0) if latitude is not None else None,
        longitude=_number(longitude, default=0.0) if longitude is not None else None,
    )

    if all(value is None for value in address.model_dump().values()):
        return None

    return address


def _normalise_option_name(value: Any) -> str:
    return (_text(value) or "").lower().replace("_", " ").strip()


def _display_option_value(value: Any) -> Any:
    """Extract the customer-visible value from Salla option objects.

    Salla order items may return option values as nested objects containing
    identifiers, prices and metadata. Operational screens need the selected
    human-readable value while the original provider object remains preserved
    inside options_raw.
    """
    if isinstance(value, dict):
        for key in (
            "value",
            "name",
            "label",
            "text",
            "option_value",
            "title",
        ):
            candidate = value.get(key)

            if candidate in (None, "", [], {}):
                continue

            extracted = _display_option_value(candidate)

            if extracted not in (None, "", [], {}):
                return extracted

        return None

    if isinstance(value, list):
        extracted_values = []

        for entry in value:
            extracted = _display_option_value(entry)

            if extracted not in (None, "", [], {}):
                extracted_values.append(extracted)

        if not extracted_values:
            return None

        return " / ".join(str(entry) for entry in extracted_values)

    return value


def _normalise_options(
    options: Iterable[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}

    for option in options:
        if not isinstance(option, dict):
            continue

        clean = deepcopy(option)
        raw.append(clean)

        name = _first(
            option.get("name"),
            option.get("label"),
            option.get("title"),
            option.get("question"),
            option.get("key"),
            option.get("option"),
        )
        raw_value = next(
            (
                candidate
                for candidate in (
                    option.get("value"),
                    option.get("values"),
                    option.get("selected"),
                    option.get("choice"),
                    option.get("answer"),
                    option.get("option_value"),
                    option.get("text"),
                )
                if candidate not in (None, "", [], {})
            ),
            None,
        )
        value = _display_option_value(raw_value)

        key = _normalise_option_name(name)
        if key and value is not None:
            normalized[key] = value

        # Keep raw option structure for auditing, but expose a normalized
        # customer-visible value to downstream Order Item consumers.
        if value is not None:
            clean["value"] = value

    return raw, normalized


def _option_value(
    normalized: dict[str, Any],
    aliases: Iterable[str],
) -> Optional[str]:
    aliases_normalized = {
        _normalise_option_name(alias)
        for alias in aliases
    }

    for key, value in normalized.items():
        if _normalise_option_name(key) in aliases_normalized:
            return _text(value)

    return None


def _image_urls(item: dict[str, Any], product: dict[str, Any]) -> list[str]:
    candidates: list[Any] = [
        item.get("image_url"),
        item.get("image"),
        item.get("thumbnail"),
        item.get("product_thumbnail"),
        product.get("main_image"),
        product.get("image"),
        product.get("thumbnail"),
        product.get("images"),
        item.get("images"),
    ]

    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in urls:
                urls.append(text)
            return

        if isinstance(value, dict):
            add(
                _first(
                    value.get("url"),
                    value.get("original"),
                    value.get("medium"),
                    value.get("thumbnail"),
                )
            )
            return

        if isinstance(value, list):
            for entry in value:
                add(entry)

    for candidate in candidates:
        add(candidate)

    return urls


def _custom_fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        item.get("custom_fields"),
        item.get("customizations"),
        item.get("personalization"),
        item.get("fields"),
        item.get("questions"),
        item.get("attachments"),
        item.get("files"),
    ]

    result: list[dict[str, Any]] = []

    for candidate in candidates:
        if isinstance(candidate, dict):
            result.append(deepcopy(candidate))
        elif isinstance(candidate, list):
            result.extend(
                deepcopy(entry)
                for entry in candidate
                if isinstance(entry, dict)
            )

    return result


def _stable_order_item_id(
    *,
    order_number: str,
    item: dict[str, Any],
    index: int,
    product_id: Optional[str],
    variant_id: Optional[str],
    sku: Optional[str],
    name: str,
    options: list[dict[str, Any]],
    custom_fields: list[dict[str, Any]],
) -> str:
    source_item_id = _text(
        _first(
            item.get("id"),
            item.get("item_id"),
            item.get("order_item_id"),
        )
    )

    if source_item_id:
        return f"salla:{order_number}:{source_item_id}"

    signature = {
        "order_number": order_number,
        "index": index,
        "product_id": product_id,
        "variant_id": variant_id,
        "sku": sku,
        "name": name,
        "options": options,
        "custom_fields": custom_fields,
    }

    canonical = json.dumps(
        signature,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"salla:{order_number}:generated:{digest}"


def _map_item(
    *,
    order_number: str,
    item: dict[str, Any],
    index: int,
) -> OrderItemDTO:
    product = _dict(item.get("product"))
    variant = _dict(
        _first(
            item.get("variant"),
            product.get("variant"),
        )
    )
    amounts = _dict(item.get("amounts"))

    product_id = _text(
        _first(
            product.get("id"),
            item.get("product_id"),
        )
    )
    parent_product_id = _text(
        _first(
            product.get("parent_id"),
            item.get("parent_product_id"),
        )
    )
    variant_id = _text(
        _first(
            variant.get("id"),
            item.get("variant_id"),
            item.get("product_sku_id"),
            product.get("variant_id"),
        )
    )

    sku = _text(
        _first(
            variant.get("sku"),
            product.get("sku"),
            item.get("sku"),
        )
    )

    name = _text(
        _first(
            variant.get("name"),
            product.get("name"),
            item.get("name"),
        )
    )
    if not name:
        name = "منتج بدون اسم"

    option_candidates: list[Any] = []
    for candidate in (
        item.get("options"),
        item.get("choices"),
        item.get("attributes"),
        item.get("product_options"),
        item.get("selected_options"),
        item.get("customer_options"),
        variant.get("options"),
        product.get("options"),
    ):
        if isinstance(candidate, list):
            option_candidates.extend(candidate)
        elif isinstance(candidate, dict):
            option_candidates.append(candidate)

    options_raw, options_normalized = _normalise_options(option_candidates)
    custom_fields = _custom_fields(item)

    color = _text(
        _first(
            item.get("color"),
            variant.get("color"),
            _option_value(
                options_normalized,
                ("color", "colour", "اللون", "لون"),
            ),
        )
    )
    size = _text(
        _first(
            item.get("size"),
            variant.get("size"),
            _option_value(
                options_normalized,
                ("size", "المقاس", "مقاس"),
            ),
        )
    )
    material = _text(
        _first(
            item.get("material"),
            variant.get("material"),
            _option_value(
                options_normalized,
                ("material", "الخامة", "المادة"),
            ),
        )
    )

    images = _image_urls(item, product)

    order_item_id = _stable_order_item_id(
        order_number=order_number,
        item=item,
        index=index,
        product_id=product_id,
        variant_id=variant_id,
        sku=sku,
        name=name,
        options=options_raw,
        custom_fields=custom_fields,
    )

    return OrderItemDTO(
        order_item_id=order_item_id,
        source_item_id=_text(
            _first(
                item.get("id"),
                item.get("item_id"),
                item.get("order_item_id"),
            )
        ),
        product_id=product_id,
        parent_product_id=parent_product_id,
        variant_id=variant_id,
        sku=sku,
        barcode=_text(
            _first(
                variant.get("barcode"),
                product.get("barcode"),
                item.get("barcode"),
                item.get("gtin"),
                item.get("mpn"),
            )
        ),
        name=name,
        quantity=max(_number(item.get("quantity"), 1.0), 0.000001),
        image_url=images[0] if images else None,
        image_urls=images,
        product_url=_text(
            _first(
                product.get("url"),
                item.get("product_url"),
            )
        ),
        unit_price=_number(
            _first(
                amounts.get("price_without_tax"),
                amounts.get("price"),
                item.get("price"),
            )
        ),
        discount=_number(
            _first(
                amounts.get("total_discount"),
                amounts.get("discount"),
                item.get("discount"),
            )
        ),
        tax_reported_by_source=_number(
            _first(
                amounts.get("tax"),
                item.get("tax"),
            )
        ),
        total=_number(
            _first(
                amounts.get("total"),
                item.get("total"),
            )
        ),
        weight=_number(
            _first(
                item.get("weight"),
                variant.get("weight"),
                product.get("weight"),
            ),
            default=0.0,
        )
        if _first(
            item.get("weight"),
            variant.get("weight"),
            product.get("weight"),
        )
        is not None
        else None,
        weight_unit=_text(
            _first(
                item.get("weight_unit"),
                variant.get("weight_unit"),
                product.get("weight_unit"),
            )
        ),
        options_raw=options_raw,
        options_normalized=options_normalized,
        color=color,
        size=size,
        material=material,
        custom_fields=custom_fields,
    )


def map_salla_order(raw_order: dict[str, Any]) -> OrderDTO:
    """Convert one raw Salla order payload into the canonical OrderDTO."""

    if not isinstance(raw_order, dict):
        raise OrderMappingError("Salla order payload must be an object")

    source_order_id = _text(raw_order.get("id"))
    order_number = _text(
        _first(
            raw_order.get("reference_id"),
            raw_order.get("order_number"),
            raw_order.get("id"),
        )
    )

    if not order_number:
        raise OrderMappingError("Salla order is missing reference_id/order number")

    if not source_order_id:
        source_order_id = order_number

    created_at = _parse_datetime(
        _first(
            raw_order.get("date"),
            raw_order.get("created_at"),
            raw_order.get("order_date"),
        )
    )
    if created_at is None:
        raise OrderMappingError(
            f"Salla order {order_number} is missing a valid creation date"
        )

    customer_raw = _dict(raw_order.get("customer"))
    shipping_raw = _dict(
        _first(
            raw_order.get("shipping"),
            raw_order.get("shipping_address"),
        )
    )
    payment_raw = _dict(raw_order.get("payment"))

    status_raw = raw_order.get("status")
    status_obj = _dict(status_raw)

    status_native = _text(
        _first(
            status_obj.get("name"),
            status_obj.get("customized"),
            status_raw if isinstance(status_raw, str) else None,
        )
    )
    status = _text(
        _first(
            status_obj.get("slug"),
            raw_order.get("status_slug"),
            status_native,
        )
    )

    payment_method_raw = _first(
        raw_order.get("payment_method"),
        payment_raw.get("method"),
    )
    payment_method_obj = _dict(payment_method_raw)

    method_native = _text(
        _first(
            payment_method_obj.get("name"),
            payment_method_obj.get("label"),
            payment_method_raw
            if isinstance(payment_method_raw, str)
            else None,
        )
    )
    method = _text(
        _first(
            payment_method_obj.get("code"),
            payment_method_obj.get("slug"),
            method_native,
        )
    )

    bank_raw = _dict(raw_order.get("bank"))
    bank_candidate = _first(
        raw_order.get("receiving_bank_name"),
        raw_order.get("receiving_bank"),
        raw_order.get("bank_name"),
        bank_raw.get("bank_name"),
        bank_raw.get("name"),
        payment_raw.get("receiving_bank_name"),
        payment_raw.get("receiving_bank"),
        payment_raw.get("bank_name"),
        payment_raw.get("bank"),
        payment_method_obj.get("name"),
        payment_method_obj.get("label"),
    )
    receiving_bank_code, receiving_bank_name = _canonical_bank(bank_candidate)

    payment_actions = _dict(raw_order.get("payment_actions"))
    remaining_action = _dict(payment_actions.get("remaining_action"))
    refund_action = _dict(payment_actions.get("refund_action"))

    paid_amount = _number(
        _first(
            raw_order.get("paid_amount"),
            payment_raw.get("paid_amount"),
            remaining_action.get("paid_amount"),
            refund_action.get("paid_amount"),
        )
    )
    remaining_amount = _number(
        _first(
            raw_order.get("remaining_amount"),
            payment_raw.get("remaining_amount"),
            remaining_action.get("remaining_amount"),
        )
    )
    has_remaining_amount = bool(
        _first(
            raw_order.get("has_remaining_amount"),
            payment_raw.get("has_remaining_amount"),
            remaining_action.get("has_remaining_amount"),
            remaining_amount > 0,
        )
    )
    collection_status = _text(
        _first(
            raw_order.get("payment_collection_status"),
            payment_raw.get("collection_status"),
        )
    )
    if collection_status not in {"unknown", "unpaid", "partial", "paid"}:
        if remaining_amount > 0:
            collection_status = "partial" if paid_amount > 0 else "unpaid"
        elif paid_amount > 0:
            collection_status = "paid"
        else:
            collection_status = "unknown"
    checkout_url = _text(
        _first(
            raw_order.get("payment_checkout_url"),
            payment_raw.get("checkout_url"),
            remaining_action.get("checkout_url"),
        )
    )
    receipt_url = (
        _media_url(raw_order.get("payment_receipt_url"))
        or _media_url(raw_order.get("receipt_image"))
        or _media_url(raw_order.get("transfer_receipt"))
        or _media_url(payment_raw.get("receipt_url"))
        or _media_url(payment_raw.get("receipt_image"))
        or _media_url(payment_raw.get("attachment_url"))
        or _media_url(payment_raw.get("proof_url"))
        or _media_url(payment_raw.get("proof"))
        or _media_url(payment_raw.get("transfer_receipt_url"))
        or _media_url(bank_raw.get("receipt_url"))
        or _media_url(bank_raw.get("receipt_image"))
    )

    amounts = _dict(raw_order.get("amounts"))
    total_obj = _first(
        amounts.get("total"),
        raw_order.get("total"),
        raw_order.get("total_amount"),
    )

    currency = _text(
        _first(
            _nested(_dict(total_obj), "currency"),
            amounts.get("currency"),
            raw_order.get("currency"),
        )
    ) or "SAR"

    shipments = _list(raw_order.get("shipments"))
    first_shipment = _dict(shipments[0]) if shipments else {}
    shipping_label_url = (
        _media_url(first_shipment.get("label_url"))
        or _media_url(first_shipment.get("label"))
        or _media_url(first_shipment.get("awb_url"))
        or _media_url(first_shipment.get("waybill_url"))
        or _media_url(raw_order.get("shipping_label_url"))
    )

    courier = _dict(
        _first(
            first_shipment.get("courier"),
            shipping_raw.get("company"),
        )
    )

    shipping_address_raw = _first(
        first_shipment.get("ship_to"),
        first_shipment.get("shipping_address"),
        first_shipment.get("address"),
        shipping_raw.get("address"),
        raw_order.get("shipping_address"),
        customer_raw.get("shipping_address"),
        customer_raw.get("address"),
    )

    item_rows = _list(raw_order.get("items"))
    items = [
        _map_item(
            order_number=order_number,
            item=item,
            index=index,
        )
        for index, item in enumerate(item_rows)
        if isinstance(item, dict)
    ]
    discounts = _discount_rows(raw_order, amounts)
    options_total = _order_options_total(raw_order, amounts)
    tax_obj = _dict(amounts.get("tax"))
    tax_percent_source = _first(
        tax_obj.get("percent"),
        amounts.get("tax_percent"),
        raw_order.get("tax_percent"),
    )
    source_tax_percent = (
        _number(tax_percent_source)
        if tax_percent_source not in (None, "")
        else None
    )
    cod_fee, cod_fee_source = _explicit_cod_fee(raw_order, amounts)
    cod_fee_total, cod_fee_tax = _cod_fee_with_tax(cod_fee, source_tax_percent)
    total_weight, total_weight_unit = _weight_parts(_first(
        first_shipment.get("total_weight"),
        raw_order.get("total_weight"),
    ))

    tags: list[str] = []
    for tag in _list(raw_order.get("tags")):
        if isinstance(tag, dict):
            value = _text(_first(tag.get("name"), tag.get("value")))
        else:
            value = _text(tag)

        if value and value not in tags:
            tags.append(value)

    return OrderDTO(
        order_id=source_order_id,
        order_number=order_number,
        created_at=created_at,
        status=status,
        status_native=status_native,
        completed_at=_parse_datetime(raw_order.get("completed_at")),
        cancelled_at=_parse_datetime(raw_order.get("cancelled_at")),
        refunded_at=_parse_datetime(raw_order.get("refunded_at")),
        source=OrderSourceDTO(
            source_order_id=source_order_id,
            source_reference=order_number,
            source_event=_text(
                _first(
                    raw_order.get("event_type"),
                    raw_order.get("event"),
                )
            ),
            fetched_at=_parse_datetime(raw_order.get("fetched_at")),
            received_at=_parse_datetime(raw_order.get("received_at")),
        ),
        customer=CustomerDTO(
            customer_id=_text(customer_raw.get("id")),
            name=_text(
                _first(
                    customer_raw.get("full_name"),
                    customer_raw.get("name"),
                    customer_raw.get("first_name"),
                )
            ),
            mobile=_text(
                _first(
                    customer_raw.get("mobile"),
                    customer_raw.get("phone"),
                )
            ),
            email=_text(customer_raw.get("email")),
            is_guest=bool(
                _first(
                    customer_raw.get("is_guest"),
                    customer_raw.get("guest"),
                    False,
                )
            ),
            shipping_address=_address_from(shipping_address_raw),
            billing_address=_address_from(
                _first(
                    raw_order.get("billing_address"),
                    customer_raw.get("billing_address"),
                )
            ),
        ),
        payment=PaymentDTO(
            method=method,
            method_native=method_native,
            status=_text(payment_raw.get("status")),
            paid_amount=paid_amount,
            remaining_amount=remaining_amount,
            has_remaining_amount=has_remaining_amount,
            collection_status=collection_status,
            checkout_url=checkout_url,
            receiving_bank_code=receiving_bank_code,
            receiving_bank_name=receiving_bank_name,
            receipt_url=receipt_url,
            transaction_reference=_text(
                _first(
                    payment_raw.get("reference"),
                    payment_raw.get("transaction_reference"),
                    raw_order.get("transaction_reference"),
                )
            ),
            paid_at=_parse_datetime(
                _first(
                    payment_raw.get("paid_at"),
                    raw_order.get("paid_at"),
                )
            ),
            card_brand=_text(
                _first(
                    payment_raw.get("card_brand"),
                    _nested(payment_raw, "card", "brand"),
                )
            ),
            card_last_four=_text(
                _first(
                    payment_raw.get("card_last_four"),
                    _nested(payment_raw, "card", "last_four"),
                )
            ),
        ),
        shipping=ShippingDTO(
            company=_text(
                _first(
                    courier.get("name"),
                    first_shipment.get("courier_name"),
                    shipping_raw.get("company_name"),
                )
            ),
            company_code=_text(
                _first(
                    courier.get("code"),
                    first_shipment.get("courier_code"),
                    shipping_raw.get("company_code"),
                )
            ),
            method=_text(
                _first(
                    first_shipment.get("method"),
                    shipping_raw.get("method"),
                )
            ),
            status=_text(
                _first(
                    first_shipment.get("status"),
                    shipping_raw.get("status"),
                )
            ),
            tracking_number=_text(
                _first(
                    first_shipment.get("tracking_number"),
                    first_shipment.get("tracking_id"),
                    first_shipment.get("shipping_number"),
                    first_shipment.get("reference"),
                    shipping_raw.get("tracking_number"),
                    shipping_raw.get("shipping_number"),
                )
            ),
            tracking_url=_text(
                _first(
                    first_shipment.get("tracking_url"),
                    first_shipment.get("tracking_link"),
                    shipping_raw.get("tracking_url"),
                    shipping_raw.get("tracking_link"),
                )
            ),
            label_url=(
                shipping_label_url
                or _media_url(shipping_raw.get("label_url"))
                or _media_url(shipping_raw.get("label"))
            ),
            shipped_at=_parse_datetime(
                _first(
                    first_shipment.get("shipped_at"),
                    shipping_raw.get("shipped_at"),
                )
            ),
            delivered_at=_parse_datetime(
                _first(
                    first_shipment.get("delivered_at"),
                    shipping_raw.get("delivered_at"),
                )
            ),
            address=_address_from(shipping_address_raw),
        ),
        totals=MoneyTotalsDTO(
            currency=currency,
            subtotal=_number(
                _first(
                    amounts.get("sub_total"),
                    amounts.get("subtotal"),
                    raw_order.get("subtotal"),
                )
            ),
            options=options_total,
            shipping=_number(
                _first(
                    amounts.get("shipping_cost"),
                    amounts.get("shipping"),
                    raw_order.get("shipping_cost"),
                )
            ),
            cod_fee=cod_fee,
            cod_fee_total=cod_fee_total,
            cod_fee_tax=cod_fee_tax,
            cod_fee_source=cod_fee_source,
            discount=sum(row.amount for row in discounts),
            discounts=discounts,
            tax_percent=(
                source_tax_percent
            ),
            tax_reported_by_source=_number(
                _first(
                    amounts.get("tax"),
                    raw_order.get("tax"),
                )
            ),
            total=_number(total_obj),
        ),
        items=items,
        total_weight=total_weight,
        total_weight_unit=total_weight_unit,
        customer_notes=_text(raw_order.get("customer_notes")),
        staff_notes=_text(raw_order.get("staff_notes")),
        tags=tags,
        timeline=deepcopy(_list(raw_order.get("timeline"))),
        engine_updated_at=_parse_datetime(
            _first(
                raw_order.get("updated_at"),
                raw_order.get("fetched_at"),
                raw_order.get("received_at"),
            )
        ),
    )
