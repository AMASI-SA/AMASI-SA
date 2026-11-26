"""Deterministic, strict Salla pending-order payload builder.

This module NEVER creates an order. Authentication, quoted carrier and
catalog/options eligibility, consent, replay protection and Salla POST must be
enforced by the Mezan Action Gateway before this pure builder is called.

Reference: https://docs.salla.dev/5394145e0 (2026-09-21)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Customer(_Strict):
    id: PositiveInt | None = None
    name: str | None = Field(default=None, min_length=2, max_length=120)
    mobile: str | None = Field(default=None, min_length=8, max_length=20)
    email: str | None = Field(default=None, min_length=5, max_length=254)

    @model_validator(mode="after")
    def known_customer_or_complete_identity(self) -> "Customer":
        # Salla's published API requires name/mobile/email if no customer ID.
        # Never fabricate an address to satisfy API validation.
        if self.id is None and not (self.name and self.mobile and self.email):
            raise ValueError("Salla requires a customer id or name, mobile and email")
        return self


class Recipient(_Strict):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=20)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")


class ShippingAddress(_Strict):
    country: PositiveInt
    city: PositiveInt
    district: PositiveInt
    address: str = Field(min_length=3, max_length=400)
    street_number: str = Field(min_length=1, max_length=40)
    postal_code: str = Field(min_length=3, max_length=20)
    address_line: str | None = Field(default=None, max_length=200)
    block: str | None = Field(default=None, max_length=100)


class Option(_Strict):
    id: PositiveInt
    value: list[str] = Field(min_length=1, max_length=15)

    @field_validator("value")
    @classmethod
    def nonempty_values(cls, values: list[str]) -> list[str]:
        if any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in values):
            raise ValueError("product option values must be non-empty strings")
        return [v.strip() for v in values]


class Product(_Strict):
    identifier_type: Literal["id", "sku"]
    identifier: int | str
    quantity: int = Field(ge=1, le=100)
    options: list[Option] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def valid_identifier(self) -> "Product":
        if self.identifier_type == "id":
            if type(self.identifier) is not int or self.identifier <= 0:
                raise ValueError("id identifier must be positive integer")
        elif not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("sku identifier must be nonempty")
        if len({opt.id for opt in self.options}) != len(self.options):
            raise ValueError("duplicate option ids for one product")
        return self


PaymentMethod = Literal["credit_card", "mada", "bank", "cod"]


class PendingOrder(_Strict):
    customer: Customer
    receiver: Recipient
    branch_id: PositiveInt
    courier_id: PositiveInt
    ship_to: ShippingAddress
    accepted_methods: list[PaymentMethod] = Field(min_length=1, max_length=4)
    products: list[Product] = Field(min_length=1, max_length=50)

    @field_validator("accepted_methods")
    @classmethod
    def no_duplicate_methods(cls, methods: list[PaymentMethod]) -> list[PaymentMethod]:
        if len(set(methods)) != len(methods):
            raise ValueError("duplicate accepted payment methods")
        return methods


def build_pending_order_payload(order: PendingOrder) -> dict:
    """Produce only Salla's allowlisted fields, always unpaid.

    Call only after trusted shipping/product price checks and customer consent.
    The payment status is immutable here: no AI can label a new order paid.
    Payment-link creation is intentionally absent (Salla owns checkout).
    """
    customer = order.customer.model_dump(exclude_none=True)
    return {
        "customer": customer,
        "receiver": {
            **order.receiver.model_dump(),
            "notify": False,
        },
        "delivery_method": "shipping",
        "branch_id": order.branch_id,
        "courier_id": order.courier_id,
        "ship_to": order.ship_to.model_dump(exclude_none=True),
        "payment": {
            "status": "pending_payment",
            "accepted_methods": list(order.accepted_methods),
        },
        "products": [line.model_dump(exclude={"options"}) |
                     ({"options": [opt.model_dump() for opt in line.options]} if line.options else {})
                     for line in order.products],
    }
