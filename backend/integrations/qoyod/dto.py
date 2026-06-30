"""Canonical Domain — `SalesOrderDTO` and friends.

This is the **single internal representation** of a sale order across
the entire integration platform (ADR-001 #4). Connector adapters
(Salla webhook, Make.com, future direct integrations) all normalize
into this shape; downstream pipeline steps (rules → customer →
product → invoice → receipt) consume only this DTO.

Day 3 scope:
    - Define the DTO + customer/line-item sub-models.
    - Pure Pydantic — no DB, no IO.
    - Provenance carried in `metadata` so the operator can always
      trace a DTO back to the raw inbox row.

Locked invariants:
    1. All monetary amounts are floats in the order's `currency`
       (default SAR). VAT is stored alongside, never folded in.
    2. Phone numbers and emails are normalized at the boundary
       (`normalize_phone`, `normalize_email`) — downstream code
       trusts the format.
    3. The DTO is `extra="forbid"` so a typo never silently swallows
       a field. Adding a new field requires editing this model.
    4. `schema_version` defaults to 1 — future migrations bump it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CustomerDTO(BaseModel):
    """Buyer-side identification. The pipeline uses (phone | email |
    name) to find or create the matching Qoyod contact (4a)."""
    model_config = ConfigDict(extra="forbid")
    name:     str
    phone:    Optional[str] = None      # E.164 ("+9665…") or None
    email:    Optional[str] = None      # lower-case or None
    is_guest: bool = False
    # Free-form notes from Salla we want to preserve (city, country…)
    city:     Optional[str] = None
    country:  Optional[str] = None


class LineItemDTO(BaseModel):
    """One row on the invoice.

    Line-level math (Iter-276): `total = unit_price * quantity
    − discount_amount + tax_amount`. We don't enforce this — we just
    store what Salla sent and let the Totals Guard reconcile. Qoyod
    receives `unit_price` and `discount` as separate columns so the
    discount stays auditable in the merchant's books.
    """
    model_config = ConfigDict(extra="forbid")
    sku:              str
    name:             str
    quantity:         float
    unit_price:       float          # price excluding tax, BEFORE discount
    tax_amount:       float = 0.0
    discount_amount:  float = 0.0    # per-line discount (e.g. promo codes)
    total:            float = 0.0
    product_id:       Optional[str] = None     # Salla product id (for mapping)


class AddressDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line1:    Optional[str] = None
    line2:    Optional[str] = None
    city:     Optional[str] = None
    region:   Optional[str] = None
    country:  Optional[str] = None
    postal:   Optional[str] = None


class SalesOrderDTO(BaseModel):
    """The canonical sales order. Lives in `integration_inbox.canonical_payload`
    after the NORMALIZED stage.
    """
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1

    # Identity
    order_id:        str                     # Salla `reference_id` (the merchant-visible number)
    source_order_id: Optional[str] = None    # Salla internal `id` if different from reference
    order_number:    Optional[str] = None    # human-friendly label

    # Status — both the canonical token and the original Salla string.
    # We never normalize *away* the original signal.
    order_status:         str                # e.g. "completed", "shipped", "cancelled"
    order_status_native:  str                # raw Salla text, e.g. "تم التنفيذ"

    # Timestamps
    order_date:    Optional[datetime] = None
    completed_at:  Optional[datetime] = None
    paid_at:       Optional[datetime] = None

    # Money
    currency:         str   = "SAR"
    subtotal:         float = 0.0
    tax_amount:       float = 0.0
    shipping_amount:  float = 0.0
    discount_amount:  float = 0.0
    total_amount:     float = 0.0
    # Iter-293.1 — Order-level extra charges (NOT in line-items).
    #
    # `cod_fee_amount` — Cash-on-Delivery service fee that Salla charges
    # the customer separately from items. Appears in payload as
    # `amounts.cash_on_delivery` (Salla's canonical field name) but we
    # also accept `cod_fee` / `payment_fee` as fallbacks. When > 0 the
    # invoice_builder MUST add a dedicated line ("رسوم الدفع عند الاستلام")
    # so the Qoyod total matches Salla's total — otherwise the totals
    # guard refuses to send the invoice.
    cod_fee_amount:   float = 0.0
    # `extra_charges` — any *other* unrecognised key inside `amounts`,
    # captured verbatim for diagnostics. The pipeline does NOT consume
    # these for arithmetic; they exist so operators can spot a new
    # Salla field that's silently inflating the total.
    extra_charges:    dict  = Field(default_factory=dict)

    # Parties
    customer: CustomerDTO

    # Lines
    items: list[LineItemDTO] = Field(default_factory=list)

    # Payment
    payment_method:        Optional[str] = None   # canonical key (e.g. "mada")
    payment_method_native: Optional[str] = None   # raw Salla string

    # Optional addresses (used later for Qoyod customer enrichment)
    shipping_address: Optional[AddressDTO] = None
    billing_address:  Optional[AddressDTO] = None

    # Provenance — every DTO carries a back-pointer to where it came from.
    metadata: dict = Field(default_factory=dict)
