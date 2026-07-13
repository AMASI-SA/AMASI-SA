"""Canonical operational identity contract for one order item.

Architecture rules
------------------
1. Order Item is the smallest operational unit in Mezan OS.
2. Identity is immutable after creation.
3. One order may contain many independently managed order items.
4. Supplier, employee, preparation, inventory, receiving, purchase batch,
   shipping, marketing and AI state are not part of this identity contract.
5. This module performs no database, HTTP or business operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CanonicalOrderItemDTO(BaseModel):
    """Strict base model for Order Item Engine public contracts."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class OrderItemSourceDTO(CanonicalOrderItemDTO):
    """Provider traceability without exposing provider or Mongo documents."""

    provider: Literal["salla"] = "salla"
    source_order_id: Optional[str] = None
    source_order_item_id: Optional[str] = None
    source_product_id: Optional[str] = None
    source_variant_id: Optional[str] = None


class OrderItemOptionDTO(CanonicalOrderItemDTO):
    """One immutable option selected by the customer."""

    name: str = Field(min_length=1)
    value: Any


class OrderItemIdentityDTO(CanonicalOrderItemDTO):
    """Permanent operational identity for one purchased line.

    `order_item_id` is the primary identity used by all future engines.

    It must never be replaced by:
    - product_id
    - variant_id
    - SKU
    - barcode
    - order number

    The same catalogue product may appear in several orders, suppliers,
    preparation workflows and inventory states. Each occurrence remains a
    separate OrderItemIdentityDTO.
    """

    schema_version: Literal[1] = 1

    order_item_id: str = Field(min_length=1)

    order_id: str = Field(min_length=1)
    order_number: str = Field(min_length=1)
    order_created_at: datetime

    line_index: int = Field(ge=0)

    source: OrderItemSourceDTO

    product_id: Optional[str] = None
    parent_product_id: Optional[str] = None
    variant_id: Optional[str] = None

    sku: Optional[str] = None
    barcode: Optional[str] = None

    name: str = Field(min_length=1)
    quantity: float = Field(gt=0)

    image_url: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    product_url: Optional[str] = None

    color: Optional[str] = None
    size: Optional[str] = None
    material: Optional[str] = None

    options: list[OrderItemOptionDTO] = Field(default_factory=list)
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)

    # Commercial snapshot copied from the originating order item.
    currency: str = "SAR"
    unit_price: float = 0.0
    discount: float = 0.0
    tax_reported_by_source: float = 0.0
    total: float = 0.0
