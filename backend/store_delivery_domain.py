"""Core domain rules for Amasi store-driver delivery.

Pure helpers live here so route/UI implementations cannot silently bypass
city coverage, fee snapshots, COD custody, or immutable assignment history.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

COVERAGE_MODE_CITY = "city"
DRIVER_STATUS_ACTIVE = "active"
DRIVER_STATUS_INACTIVE = "inactive"

DELIVERY_STATUS_ASSIGNED = "assigned"
DELIVERY_STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERY_STATUS_DELIVERED = "delivered"

PAYMENT_METHOD_CASH = "cash"
PAYMENT_METHOD_CARD_TERMINAL = "card_terminal"
PAYMENT_METHOD_BANK_TRANSFER = "bank_transfer"

PAYMENT_REVIEW_NOT_REQUIRED = "not_required"
PAYMENT_REVIEW_PENDING = "pending_accountant_review"


class StoreDeliveryRuleError(ValueError):
    """Raised when a store-driver operation violates a binding rule."""


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_city(value: Any) -> str:
    """Normalize city text for V1 exact city coverage matching."""
    return normalize_text(value).casefold()


def money(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StoreDeliveryRuleError("invalid_money") from exc
    if parsed < 0:
        raise StoreDeliveryRuleError("negative_money")
    return parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DriverCoverage:
    city: str
    region: str = ""
    district: str = ""
    street: str = ""
    mode: str = COVERAGE_MODE_CITY

    def validate(self) -> None:
        if not normalize_text(self.city):
            raise StoreDeliveryRuleError("driver_city_required")
        if self.mode != COVERAGE_MODE_CITY:
            raise StoreDeliveryRuleError("coverage_mode_not_enabled_in_v1")


def assert_driver_can_take_shipment(*, driver: dict[str, Any], shipping_city: Any) -> None:
    """Fail closed unless active driver and shipment share the same city.

    Region/district/street are intentionally stored but ignored in V1.
    """
    if normalize_text(driver.get("status") or DRIVER_STATUS_ACTIVE) != DRIVER_STATUS_ACTIVE:
        raise StoreDeliveryRuleError("driver_inactive")
    coverage = DriverCoverage(
        city=normalize_text(driver.get("city")),
        region=normalize_text(driver.get("region")),
        district=normalize_text(driver.get("district")),
        street=normalize_text(driver.get("street")),
        mode=normalize_text(driver.get("coverage_mode") or COVERAGE_MODE_CITY),
    )
    coverage.validate()
    shipment_city = normalize_city(shipping_city)
    if not shipment_city:
        raise StoreDeliveryRuleError("shipping_city_required")
    if normalize_city(coverage.city) != shipment_city:
        raise StoreDeliveryRuleError("driver_city_mismatch")


def assignment_snapshot(*, driver: dict[str, Any], shipping_city: Any) -> dict[str, Any]:
    """Return immutable values captured at assignment time."""
    assert_driver_can_take_shipment(driver=driver, shipping_city=shipping_city)
    return {
        "driver_id": normalize_text(driver.get("id")),
        "driver_name_snapshot": normalize_text(driver.get("name")),
        "driver_city_snapshot": normalize_text(driver.get("city")),
        "shipping_city_snapshot": normalize_text(shipping_city),
        "delivery_fee_snapshot": float(money(driver.get("delivery_fee"))),
        "coverage_mode_snapshot": COVERAGE_MODE_CITY,
    }


def collection_requirements(*, outstanding_amount: Any, payment_method: str | None) -> dict[str, Any]:
    """Describe evidence/custody rules when a driver completes delivery."""
    outstanding = money(outstanding_amount)
    if outstanding == Decimal("0.00"):
        return {
            "amount": 0.0,
            "payment_method": None,
            "receipt_required": False,
            "bank_account_required": False,
            "review_status": PAYMENT_REVIEW_NOT_REQUIRED,
            "cod_custody_amount": 0.0,
        }

    method = normalize_text(payment_method)
    if method not in {
        PAYMENT_METHOD_CASH,
        PAYMENT_METHOD_CARD_TERMINAL,
        PAYMENT_METHOD_BANK_TRANSFER,
    }:
        raise StoreDeliveryRuleError("collection_method_required")

    if method == PAYMENT_METHOD_CASH:
        return {
            "amount": float(outstanding),
            "payment_method": method,
            "receipt_required": False,
            "bank_account_required": False,
            "review_status": PAYMENT_REVIEW_NOT_REQUIRED,
            "cod_custody_amount": float(outstanding),
        }

    return {
        "amount": float(outstanding),
        "payment_method": method,
        "receipt_required": True,
        "bank_account_required": method == PAYMENT_METHOD_BANK_TRANSFER,
        "review_status": PAYMENT_REVIEW_PENDING,
        "cod_custody_amount": 0.0,
    }


def driver_earning(*, assignment: dict[str, Any], delivered: bool) -> float:
    """Earning becomes due only after successful delivery."""
    if not delivered:
        return 0.0
    return float(money(assignment.get("delivery_fee_snapshot")))


__all__ = [
    "COVERAGE_MODE_CITY",
    "DELIVERY_STATUS_ASSIGNED",
    "DELIVERY_STATUS_OUT_FOR_DELIVERY",
    "DELIVERY_STATUS_DELIVERED",
    "DRIVER_STATUS_ACTIVE",
    "DRIVER_STATUS_INACTIVE",
    "PAYMENT_METHOD_CASH",
    "PAYMENT_METHOD_CARD_TERMINAL",
    "PAYMENT_METHOD_BANK_TRANSFER",
    "StoreDeliveryRuleError",
    "assert_driver_can_take_shipment",
    "assignment_snapshot",
    "collection_requirements",
    "driver_earning",
    "money",
    "normalize_city",
]
