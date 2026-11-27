"""Strict Salla Merchant API pending-order payload, without payment-link creation.

This is a pure formatter: it does not verify stock, discounts, quotes, identity,
customer consent, tenant permissions or the shipping service. Only a trusted
Mezan action gateway may call Salla after checking those independent facts.

Official create-order contract: https://docs.salla.dev/5394145e0 (2026-09-21)
National address update: https://docs.salla.dev/orders/update (2026-10-08)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Customer(_Strict):
    id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    mobile: str | None = Field(default=None, min_length=8, max_length=25)
    email: str | None = Field(default=None, min_length=5, max_length=254)

    @model_validator(mode="after")
    def valid_identity(self) -> "Customer":
        if self.id is None and not (self.name and self.mobile and self.email):
            raise ValueError("new Salla customer requires name, mobile and email")
        return self


class Recipient(_Strict):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=25)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")


class GeoCoordinates(_Strict):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class ShippingAddress(_Strict):
    country: int = Field(gt=0)
    city: int = Field(gt=0)
    district: int | None
    block: str | None = Field(max_length=120)
    street_number: str = Field(min_length=1, max_length=80)
    address_line: str = Field(min_length=3, max_length=400)
    postal_code: str | None = Field(max_length=20)
    short_address: str | None = Field(default=None, max_length=20)
    building_number: str | None = Field(default=None, max_length=20)
    additional_number: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=400)
    geo_coordinates: GeoCoordinates | None = None


class Option(_Strict):
    id: int = Field(gt=0)
    value: list[str] = Field(min_length=1, max_length=15)

    @field_validator("value")
    @classmethod
    def validate_values(cls, values: list[str]) -> list[str]:
        if any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in values):
            raise ValueError("option values must be non-empty strings")
        return [v.strip() for v in values]


class Product(_Strict):
    identifier_type: Literal["id", "sku"]
    identifier: int | str
    quantity: int = Field(ge=1, le=100)
    options: list[Option] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_identifier_and_options(self) -> "Product":
        if self.identifier_type == "id":
            if type(self.identifier) is not int or self.identifier <= 0:
                raise ValueError("product id must be a positive integer")
        elif not isinstance(self.identifier, str) or not self.identifier.strip():
            raise ValueError("SKU must be a non-empty string")
        if len({opt.id for opt in self.options}) != len(self.options):
            raise ValueError("duplicate option IDs")
        return self


PaymentMethod = Literal["credit_card", "mada", "bank", "cod"]


class PendingOrder(_Strict):
    customer: Customer
    receiver: Recipient
    courier_id: int | str
    ship_to: ShippingAddress
    accepted_methods: list[PaymentMethod] = Field(min_length=1, max_length=4)
    products: list[Product] = Field(min_length=1, max_length=50)
    branch_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_shipping_and_payment(self) -> "PendingOrder":
        if isinstance(self.courier_id, bool) or not str(self.courier_id).isdigit() or int(self.courier_id) <= 0:
            raise ValueError("courier_id must be a positive Salla carrier ID")
        if len(set(self.accepted_methods)) != len(self.accepted_methods):
            raise ValueError("duplicate accepted payment methods")
        if self.receiver.country_code == "SA":
            address = self.ship_to
            # Salla's Saudi national-address requirements became mandatory in 2026.
            if not all((address.short_address, address.building_number,
                        address.additional_number, address.postal_code,
                        address.geo_coordinates)):
                raise ValueError("complete Saudi national address required")
        return self


def build_pending_order_payload(order: PendingOrder) -> dict:
    """Emit only Salla create-order fields. No price, paid flag or checkout URL.

    Server must ensure every field comes from a verified quote and that the
    payment method is permitted for the verified carrier/branch before calling.
    """
    shipping = order.ship_to.model_dump(exclude_none=False)
    # Optional extension fields are only sent if available. Salla's nullable
    # district/block/postal_code fields remain explicit.
    for key in ("short_address", "building_number", "additional_number", "address",
                "geo_coordinates"):
        if shipping.get(key) is None:
            shipping.pop(key)

    payload = {
        "customer": order.customer.model_dump(exclude_none=True),
        "receiver": {**order.receiver.model_dump(), "notify": False},
        "delivery_method": "shipping",
        "courier_id": str(order.courier_id),
        "ship_to": shipping,
        "payment": {
            "status": "pending_payment",
            "accepted_methods": list(order.accepted_methods),
        },
        "products": [
            {
                "identifier_type": item.identifier_type,
                "identifier": item.identifier,
                "quantity": item.quantity,
                **({"options": [o.model_dump() for o in item.options]} if item.options else {}),
            }
            for item in order.products
        ],
    }
    if order.branch_id is not None:
        payload["branch_id"] = order.branch_id
    return payload
