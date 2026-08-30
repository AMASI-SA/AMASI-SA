"""Canonical Order Engine DTOs.

Architecture rules
------------------
1. These models are API contracts, not MongoDB models.
2. Frontend code must not depend on `unified_orders`, `raw_by_source`,
   Salla response nesting, or any database-specific shape.
3. Salla owns external order facts.
4. Mezan owns future operational projections.
5. Qoyod owns accounting records.
6. This module performs no I/O and contains no business calculations.

See:
- docs/MEZAN_OS_ARCHITECTURE.md
- docs/PROJECT_DECISIONS.md
- docs/ORDER_CAPABILITY_AUDIT.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CanonicalDTO(BaseModel):
    """Strict base class for all public Order Engine contracts."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class OrderSourceDTO(CanonicalDTO):
    """Traceability and marketing attribution without raw provider payloads."""

    provider: Literal["salla"] = "salla"
    source_order_id: Optional[str] = None
    source_reference: Optional[str] = None
    source_event: Optional[str] = None
    fetched_at: Optional[datetime] = None
    received_at: Optional[datetime] = None

    source: Optional[str] = None
    channel: Optional[str] = None
    platform: Optional[str] = None
    source_native: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    utm_raw: dict[str, Optional[str]] = Field(default_factory=dict)
    utm_normalized: dict[str, Optional[str]] = Field(default_factory=dict)
    click_ids: dict[str, str] = Field(default_factory=dict)
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    ad_squad_id: Optional[str] = None
    ad_squad_name: Optional[str] = None
    ad_id: Optional[str] = None
    ad_name: Optional[str] = None
    match_status: Literal["matched", "unattributed", "conflicted"] = "unattributed"
    match_method: Optional[str] = None
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    unmatched_reason: Optional[str] = None
    attribution_window: Optional[str] = None
    order_created_at_riyadh: Optional[datetime] = None
    order_created_at_account: Optional[datetime] = None
    account_timezone: Optional[str] = None
    entity_url: Optional[str] = None
    device: Optional[str] = None


class AddressDTO(CanonicalDTO):
    """Canonical customer or shipping address."""

    country: Optional[str] = None
    country_code: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    street: Optional[str] = None
    postal_code: Optional[str] = None
    building_number: Optional[str] = None
    additional_number: Optional[str] = None
    short_address: Optional[str] = None
    formatted: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CustomerDTO(CanonicalDTO):
    """Customer snapshot attached to the order."""

    customer_id: Optional[str] = None
    name: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None
    is_guest: bool = False
    shipping_address: Optional[AddressDTO] = None
    billing_address: Optional[AddressDTO] = None


class PaymentDTO(CanonicalDTO):
    """Payment facts supplied by the commerce source.

    `receiving_bank_*` is intentionally explicit. A generic bank transfer
    must not silently collapse Al Rajhi, Alinma and SNB/Ahli into one account.
    """

    method: Optional[str] = None
    method_native: Optional[str] = None
    status: Optional[str] = None
    paid_amount: float = 0.0
    remaining_amount: float = 0.0
    has_remaining_amount: bool = False
    collection_status: Optional[Literal["unknown", "unpaid", "partial", "paid"]] = None
    checkout_url: Optional[str] = None

    receiving_bank_code: Optional[
        Literal["bank_rajhi", "bank_inma", "bank_ahli"]
    ] = None
    receiving_bank_name: Optional[str] = None
    receipt_url: Optional[str] = None

    transaction_reference: Optional[str] = None
    paid_at: Optional[datetime] = None

    card_brand: Optional[str] = None
    card_last_four: Optional[str] = Field(
        default=None,
        min_length=4,
        max_length=4,
    )


class ShippingDTO(CanonicalDTO):
    """Shipping snapshot supplied by Salla."""

    company: Optional[str] = None
    company_code: Optional[str] = None
    method: Optional[str] = None
    status: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    label_url: Optional[str] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    address: Optional[AddressDTO] = None
    # Independent recipient selected by the buyer. Kept separate from customer
    # so gift orders and deliveries to another person remain operationally safe.
    recipient: Optional[dict[str, Any]] = None


class OrderDiscountDTO(CanonicalDTO):
    """One source-reported order discount, including its customer label."""

    title: Optional[str] = None
    code: Optional[str] = None
    type: Optional[str] = None
    amount: float = 0.0
    discounted_shipping: float = 0.0


class MoneyTotalsDTO(CanonicalDTO):
    """Commercial order totals."""

    currency: str = "SAR"
    subtotal: float = 0.0
    options: float = 0.0
    shipping: float = 0.0
    # Explicit order-level fee reported by Salla for cash-on-delivery.
    # Never derive this value from ``total - items`` because that difference
    # can also contain shipping, options, discounts or another source charge.
    cod_fee: float = 0.0
    # Gross contribution of the COD fee to the order total after source tax.
    # Qoyod consumes this value, while ``cod_fee`` remains the amount displayed
    # by Salla before tax in the order summary.
    cod_fee_total: float = 0.0
    cod_fee_tax: float = 0.0
    cod_fee_source: Optional[str] = None
    discount: float = 0.0
    discounts: list[OrderDiscountDTO] = Field(default_factory=list)
    tax_percent: Optional[float] = None
    tax_reported_by_source: float = 0.0
    total: float = 0.0


class OrderItemDTO(CanonicalDTO):
    """Canonical item inside one specific order."""

    order_item_id: str = Field(min_length=1)

    source_item_id: Optional[str] = None
    product_id: Optional[str] = None
    parent_product_id: Optional[str] = None
    variant_id: Optional[str] = None

    sku: Optional[str] = None
    barcode: Optional[str] = None

    name: str = Field(min_length=1)
    quantity: float = Field(default=1.0, gt=0)

    image_url: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    product_url: Optional[str] = None

    unit_price: float = 0.0
    discount: float = 0.0
    tax_reported_by_source: float = 0.0
    total: float = 0.0

    weight: Optional[float] = None
    weight_unit: Optional[str] = None

    options_raw: list[dict[str, Any]] = Field(default_factory=list)
    options_normalized: dict[str, Any] = Field(default_factory=dict)

    color: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)

    preparation_status: Optional[str] = None
    availability_status: Optional[str] = None
    fulfillment_source: Optional[
        Literal[
            "operational_inventory",
            "returned_item",
            "previously_prepared",
            "supplier",
        ]
    ] = None


class OrderDTO(CanonicalDTO):
    """Single canonical order contract for list and detail consumers."""

    schema_version: Literal[1] = 1

    order_id: str = Field(min_length=1)
    order_number: str = Field(min_length=1)

    created_at: datetime

    status: Optional[str] = None
    status_native: Optional[str] = None
    is_new: bool = False
    is_gift: bool = False

    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None

    source: OrderSourceDTO
    customer: CustomerDTO = Field(default_factory=CustomerDTO)
    payment: PaymentDTO = Field(default_factory=PaymentDTO)
    shipping: ShippingDTO = Field(default_factory=ShippingDTO)
    totals: MoneyTotalsDTO = Field(default_factory=MoneyTotalsDTO)
    items: list[OrderItemDTO] = Field(default_factory=list)
    total_weight: Optional[float] = None
    total_weight_unit: Optional[str] = None

    customer_notes: Optional[str] = None
    staff_notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    timeline: list[dict[str, Any]] = Field(default_factory=list)

    engine_updated_at: Optional[datetime] = None
