"""Build the unchanged Plan-B canonical from unified_orders."""
from __future__ import annotations

from typing import Any

from .common import (
    _date_value, _first_money, _item_rows, _raw_salla, _text, _now,
)


def _canonical_item(item: dict[str, Any]) -> dict[str, Any]:
    product = item.get("product") or {}
    product = product if isinstance(product, dict) else {}
    amounts = item.get("amounts") or {}
    amounts = amounts if isinstance(amounts, dict) else {}
    quantity = _first_money(item.get("quantity"), default=1.0)
    if quantity <= 0:
        quantity = 1.0
    unit_price = _first_money(
        item.get("unit_price"),
        item.get("price"),
        amounts.get("price_without_tax"),
        amounts.get("price"),
        default=0.0,
    )
    discount = _first_money(
        item.get("discount_amount"),
        item.get("discount"),
        amounts.get("total_discount"),
        amounts.get("discount"),
        default=0.0,
    )
    tax = _first_money(
        item.get("tax_amount"),
        item.get("tax"),
        amounts.get("tax"),
        default=0.0,
    )
    total = _first_money(
        item.get("total"),
        amounts.get("total"),
        default=max(0.0, round(unit_price * quantity - discount + tax, 2)),
    )
    return {
        "order_item_id": _text(item.get("order_item_id"), item.get("id")),
        "product_id": _text(item.get("product_id"), product.get("id")),
        "variant_id": _text(item.get("variant_id"), item.get("product_sku_id")),
        "sku": _text(item.get("sku"), product.get("sku")) or "",
        "name": _text(item.get("name"), product.get("name")) or "منتج",
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_amount": discount,
        "tax_amount": tax,
        "total": total,
    }


def _canonical_from_unified(row: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical consumed by the unchanged Plan-B sender."""
    raw_salla = _raw_salla(row)
    totals = row.get("totals") or {}
    totals = totals if isinstance(totals, dict) else {}
    payment = row.get("payment") or {}
    payment = payment if isinstance(payment, dict) else {}
    customer = row.get("customer") or {}
    customer = customer if isinstance(customer, dict) else {}

    status_slug = _text(
        row.get("order_status_slug"),
        row.get("status_slug"),
        payment.get("order_status_slug"),
    )
    status_native = _text(
        row.get("order_status_native"),
        row.get("status_native"),
        row.get("order_status"),
    )
    order_date = _date_value(
        raw_salla.get("date")
        or raw_salla.get("created_at")
        or row.get("order_date_raw")
        or row.get("order_date")
        or row.get("created_at")
    )
    items = [
        _canonical_item(item)
        for item in _item_rows(row, raw_salla)
    ]

    total = _first_money(row.get("total_amount"), totals.get("total"), default=0.0)
    subtotal = _first_money(row.get("subtotal"), totals.get("subtotal"), default=0.0)
    shipping = _first_money(
        row.get("shipping_amount"),
        row.get("shipping_cost"),
        totals.get("shipping"),
        default=0.0,
    )
    tax = _first_money(
        row.get("tax_amount"), row.get("tax"), totals.get("tax_reported_by_source"),
        default=0.0,
    )
    discount = _first_money(
        row.get("discount_amount"), row.get("discount"), totals.get("discount"),
        default=0.0,
    )
    cod_fee = _first_money(
        row.get("cod_fee_amount"),
        row.get("cod_fee_total"),
        totals.get("cod_fee_total"),
        totals.get("cod_fee"),
        default=0.0,
    )

    return {
        "order_id": _text(row.get("order_id"), raw_salla.get("id")),
        "order_number": str(row.get("order_number") or "").strip(),
        "order_date": order_date,
        "created_at": order_date,
        "order_status": status_slug or status_native or "",
        "order_status_native": status_native or status_slug or "",
        "payment_method": _text(
            row.get("payment_method"), payment.get("method")
        ),
        "payment_method_native": _text(
            row.get("payment_method_native"), payment.get("method_native")
        ),
        "payment_status": _text(
            row.get("payment_collection_status"),
            row.get("payment_status"),
            payment.get("collection_status"),
            payment.get("status"),
        ),
        "paid_amount": _first_money(
            row.get("paid_amount"), payment.get("paid_amount"), default=0.0
        ),
        "remaining_amount": _first_money(
            row.get("remaining_amount"), payment.get("remaining_amount"), default=0.0
        ),
        "has_remaining_amount": bool(
            row.get("has_remaining_amount")
            or payment.get("has_remaining_amount")
        ),
        "receiving_bank_name": _text(
            row.get("receiving_bank_name"), payment.get("receiving_bank_name")
        ),
        "receiving_bank_id": _text(row.get("receiving_bank_id")),
        "payment_receipt_url": _text(
            row.get("payment_receipt_url"), payment.get("receipt_url")
        ),
        "currency": _text(row.get("currency"), totals.get("currency")) or "SAR",
        "currency_code": _text(row.get("currency"), totals.get("currency")) or "SAR",
        "total_amount": total,
        "subtotal": subtotal,
        "shipping_amount": shipping,
        "tax_amount": tax,
        "discount_amount": discount,
        "cod_fee_amount": cod_fee,
        "items": items,
        "customer": {
            "name": _text(row.get("customer_name"), customer.get("name")),
            "phone": _text(
                row.get("customer_mobile"),
                customer.get("phone"),
                customer.get("mobile"),
            ),
            "email": _text(row.get("customer_email"), customer.get("email")),
        },
        "metadata": {
            "source": "unified_orders",
            "source_authority": "unified_orders",
            "prepared_for": "qoyod_auto_send",
            "prepared_at": _now().isoformat(),
        },
    }
