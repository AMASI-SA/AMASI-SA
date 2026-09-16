"""Order-currency contract for Salla commerce and SAR accounting.

Customer-facing order amounts stay in the currency charged by Salla.  Every
financial aggregate uses the SAR amount derived from the exchange rate stored
on that exact Salla order.  A foreign amount without verifiable order-level FX
evidence is unknown; it is never relabelled as SAR.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable


ACCOUNTING_CURRENCY = "SAR"
SUPPORTED_GCC_FOREIGN_CURRENCIES = frozenset({"AED", "QAR", "KWD", "BHD", "OMR"})
SUPPORTED_ORDER_CURRENCIES = frozenset({ACCOUNTING_CURRENCY, *SUPPORTED_GCC_FOREIGN_CURRENCIES})
_TWO_PLACES = Decimal("0.01")
_RAW_SALLA_PATH = "raw_by_source.salla_direct"


SALLA_RAW_CURRENCY_PROJECTION: dict[str, int] = {
    "_id": 0,
    "order_number": 1,
    f"{_RAW_SALLA_PATH}.amounts.total": 1,
    f"{_RAW_SALLA_PATH}.currency": 1,
    f"{_RAW_SALLA_PATH}.total_amount": 1,
    f"{_RAW_SALLA_PATH}.exchange_rate": 1,
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def money_decimal(value: Any) -> Decimal | None:
    """Unwrap common Salla money envelopes without accepting arbitrary data."""
    node = value
    for _ in range(8):
        if not isinstance(node, dict):
            return _decimal(node)
        if "amount" in node:
            node = node.get("amount")
            continue
        if "value" in node:
            node = node.get("value")
            continue
        return None
    return None


def currency_code(value: Any, *, default: str = "") -> str:
    """Normalize a Salla currency scalar/object to its ISO-style code."""
    node = value
    for _ in range(8):
        if not isinstance(node, dict):
            break
        nested = next(
            (
                node.get(key)
                for key in ("code", "currency", "currency_code")
                if node.get(key) not in (None, "")
            ),
            None,
        )
        if nested is not None:
            node = nested
            continue
        if "amount" in node:
            node = node.get("amount")
            continue
        return default
    text = str(node or "").strip().upper().replace(".", "")
    compact = " ".join(text.split())
    if compact in {"ر س", "رس", "ريال سعودي", "SAUDI RIYAL"}:
        return ACCOUNTING_CURRENCY
    return compact or default


def _money_currency(value: Any) -> str:
    node = value
    for _ in range(8):
        if not isinstance(node, dict):
            return ""
        direct = currency_code(
            node.get("currency") or node.get("currency_code"),
            default="",
        )
        if direct:
            return direct
        if "amount" in node:
            node = node.get("amount")
            continue
        if "value" in node:
            node = node.get("value")
            continue
        return ""
    return ""


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _float_or_none(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _salla_raw(order: dict[str, Any]) -> dict[str, Any]:
    raw_by_source = order.get("raw_by_source")
    if not isinstance(raw_by_source, dict):
        return {}
    raw = raw_by_source.get("salla_direct")
    return raw if isinstance(raw, dict) else {}


def salla_order_currency_fields(
    salla_order: dict[str, Any],
    *,
    fallback_total: Any = None,
    fallback_currency: Any = None,
) -> dict[str, Any]:
    """Return auditable native and SAR fields from one Salla order payload."""
    order = salla_order if isinstance(salla_order, dict) else {}
    amounts = order.get("amounts") if isinstance(order.get("amounts"), dict) else {}
    total_object = amounts.get("total")
    if total_object in (None, ""):
        total_object = order.get("total_amount")
    if total_object in (None, ""):
        total_object = order.get("total")

    original_total = money_decimal(total_object)
    if original_total is None:
        original_total = money_decimal(fallback_total)
    original_currency = (
        _money_currency(total_object)
        or currency_code(amounts.get("currency"), default="")
        or currency_code(order.get("currency"), default="")
        or currency_code(fallback_currency, default="")
        or ACCOUNTING_CURRENCY
    )

    result: dict[str, Any] = {
        "original_total_amount": _float_or_none(original_total),
        "original_currency": original_currency,
        "exchange_rate_to_sar": None,
        "total_amount_sar": None,
        "accounting_currency": ACCOUNTING_CURRENCY,
        "currency_conversion_status": "missing_total",
        "currency_conversion_source": None,
        "currency_conversion_reason": "order_total_missing_or_invalid",
    }
    if original_total is None:
        return result

    if original_currency == ACCOUNTING_CURRENCY:
        result.update({
            "exchange_rate_to_sar": "1",
            "total_amount_sar": float(_q2(original_total)),
            "currency_conversion_status": "native_sar",
            "currency_conversion_source": "order_currency",
            "currency_conversion_reason": None,
        })
        return result

    if original_currency not in SUPPORTED_GCC_FOREIGN_CURRENCIES:
        result.update({
            "currency_conversion_status": "unsupported_currency",
            "currency_conversion_reason": "unsupported_order_currency",
        })
        return result

    exchange = order.get("exchange_rate")
    exchange = exchange if isinstance(exchange, dict) else {}
    rate = money_decimal(exchange.get("rate"))
    base_currency = currency_code(exchange.get("base_currency"), default="")
    exchange_currency = currency_code(
        exchange.get("exchange_currency"),
        default="",
    )
    if (
        rate is None
        or rate <= 0
        or base_currency != ACCOUNTING_CURRENCY
        or exchange_currency != original_currency
    ):
        result.update({
            "currency_conversion_status": "unverified_rate",
            "currency_conversion_reason": "salla_exchange_rate_unverified",
        })
        return result

    result.update({
        # Keep the decimal text so downstream calculations can reproduce the
        # exact Salla result rather than a shortened binary float.
        "exchange_rate_to_sar": format(rate, "f"),
        "total_amount_sar": float(_q2(original_total * rate)),
        "currency_conversion_status": "verified",
        "currency_conversion_source": "salla_order.exchange_rate",
        "currency_conversion_reason": None,
    })
    return result


def _stored_original_currency(order: dict[str, Any]) -> str:
    raw = _salla_raw(order)
    raw_fields = salla_order_currency_fields(raw) if raw else {}
    return (
        currency_code(order.get("original_currency"), default="")
        or currency_code(order.get("currency"), default="")
        or str(raw_fields.get("original_currency") or "")
        or ACCOUNTING_CURRENCY
    )


def _stored_original_total(order: dict[str, Any]) -> Decimal | None:
    for value in (
        order.get("original_total_amount"),
        order.get("total_amount"),
        order.get("total"),
    ):
        amount = money_decimal(value)
        if amount is not None:
            return amount
    raw = _salla_raw(order)
    if raw:
        return money_decimal(
            (raw.get("amounts") or {}).get("total")
            if isinstance(raw.get("amounts"), dict)
            else None
        )
    return None


def _verified_rate_to_sar(order: dict[str, Any]) -> Decimal | None:
    currency = _stored_original_currency(order)
    if currency == ACCOUNTING_CURRENCY:
        return Decimal("1")

    status = str(order.get("currency_conversion_status") or "").strip()
    accounting_currency = currency_code(
        order.get("accounting_currency"),
        default="",
    )
    promoted_rate = money_decimal(order.get("exchange_rate_to_sar"))
    if (
        status == "verified"
        and accounting_currency == ACCOUNTING_CURRENCY
        and promoted_rate is not None
        and promoted_rate > 0
    ):
        return promoted_rate

    raw = _salla_raw(order)
    if raw:
        fields = salla_order_currency_fields(
            raw,
            fallback_total=_stored_original_total(order),
            fallback_currency=currency,
        )
        if fields.get("currency_conversion_status") == "verified":
            return money_decimal(fields.get("exchange_rate_to_sar"))
    return None


def order_amount_to_sar(value: Any, order: dict[str, Any]) -> float | None:
    """Convert any amount expressed in the order currency to SAR."""
    amount = money_decimal(value)
    if amount is None:
        return None
    rate = _verified_rate_to_sar(order)
    if rate is None:
        return None
    return float(_q2(amount * rate))


def order_total_sar(order: dict[str, Any]) -> float | None:
    """Resolve an order total in SAR while preventing double conversion."""
    currency = _stored_original_currency(order)
    original_total = _stored_original_total(order)
    if currency == ACCOUNTING_CURRENCY:
        return _float_or_none(_q2(original_total)) if original_total is not None else None

    promoted_total = money_decimal(order.get("total_amount_sar"))
    if (
        promoted_total is not None
        and str(order.get("currency_conversion_status") or "").strip() == "verified"
        and currency_code(order.get("accounting_currency"), default="")
        == ACCOUNTING_CURRENCY
    ):
        return float(_q2(promoted_total))

    if original_total is None:
        return None
    return order_amount_to_sar(original_total, order)


def hydrate_order_currency_fields(
    orders: list[dict[str, Any]],
    projected_rows: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Hydrate promoted FX fields in memory from a narrow raw projection.

    This compatibility path fixes historical rows immediately after deploy and
    deliberately does not persist or expose the raw Salla payload.
    """
    raw_by_order = {
        str(row.get("order_number") or "").strip(): _salla_raw(row)
        for row in projected_rows
        if str(row.get("order_number") or "").strip()
    }
    for order in orders:
        order_number = str(order.get("order_number") or "").strip()
        raw = raw_by_order.get(order_number) or _salla_raw(order)
        if raw:
            fields = salla_order_currency_fields(
                raw,
                fallback_total=order.get("total_amount"),
                fallback_currency=order.get("currency"),
            )
        else:
            fields = salla_order_currency_fields(
                order,
                fallback_total=order.get("total_amount"),
                fallback_currency=order.get("currency"),
            )

        # A previously promoted, verified result is already the durable proof
        # and must not be multiplied by the rate again.
        existing_sar = order_total_sar(order)
        if existing_sar is not None:
            fields["total_amount_sar"] = existing_sar
            if _stored_original_currency(order) == ACCOUNTING_CURRENCY:
                fields.update({
                    "exchange_rate_to_sar": "1",
                    "currency_conversion_status": "native_sar",
                    "currency_conversion_source": "order_currency",
                    "currency_conversion_reason": None,
                })
            elif order.get("currency_conversion_status") == "verified":
                fields.update({
                    "exchange_rate_to_sar": order.get("exchange_rate_to_sar"),
                    "currency_conversion_status": "verified",
                    "currency_conversion_source": (
                        order.get("currency_conversion_source")
                        or "salla_order.exchange_rate"
                    ),
                    "currency_conversion_reason": None,
                })

        order.update(fields)
        if not order.get("currency"):
            order["currency"] = fields["original_currency"]
        order.pop("raw_by_source", None)
    return orders


def summarize_orders_sar(orders: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return a fail-closed SAR aggregate and bounded conversion diagnostics."""
    known_total = Decimal("0")
    orders_count = 0
    converted_orders = 0
    missing_order_numbers: list[str] = []
    for order in orders:
        orders_count += 1
        amount = order_total_sar(order)
        if amount is None:
            if len(missing_order_numbers) < 100:
                missing_order_numbers.append(
                    str(order.get("order_number") or "unknown")
                )
            continue
        known_total += Decimal(str(amount))
        converted_orders += 1
    known_total = _q2(known_total)
    complete = converted_orders == orders_count
    return {
        "accounting_currency": ACCOUNTING_CURRENCY,
        "total_sar": float(known_total) if complete else None,
        "known_total_sar": float(known_total),
        "orders_count": orders_count,
        "converted_orders_count": converted_orders,
        "unverified_orders_count": orders_count - converted_orders,
        "missing_order_numbers": missing_order_numbers,
        "conversion_complete": complete,
        "unknown_is_zero": False,
    }


__all__ = [
    "ACCOUNTING_CURRENCY",
    "SALLA_RAW_CURRENCY_PROJECTION",
    "SUPPORTED_GCC_FOREIGN_CURRENCIES",
    "currency_code",
    "hydrate_order_currency_fields",
    "money_decimal",
    "order_amount_to_sar",
    "order_total_sar",
    "salla_order_currency_fields",
    "summarize_orders_sar",
]
