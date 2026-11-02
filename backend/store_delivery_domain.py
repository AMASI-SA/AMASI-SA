"""Core domain rules for Amasi store-driver delivery.

Pure helpers live here so route/UI implementations cannot silently bypass
city coverage, fee snapshots, COD custody, or immutable assignment history.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
import unicodedata

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
    """Normalize known equivalent city names without expanding coverage."""
    city = normalize_text(value).casefold()
    if city in {"الرياض", "رياض", "riyadh"}:
        return "الرياض"
    return city


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



def normalize_shipping_carrier(value: Any) -> str:
    """Strict shipping-company identity; do not infer coverage from a city.

    Generic local courier names remain distinct from city-specific couriers.
    """
    if not isinstance(value, str):
        return ""
    cleaned = unicodedata.normalize("NFKC", value)
    for mark in ("\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufeff",
                 "\u202a", "\u202b", "\u202c", "\u202d", "\u202e"):
        cleaned = cleaned.replace(mark, "")
    name = normalize_text(cleaned.strip().strip(chr(39) + chr(34))).casefold()
    if name in {"مندوب الرياض", "mandoob riyadh"}:
        return "مندوب الرياض"
    return name


def is_city_courier_company(name: Any) -> bool:
    """Select only merchant-configured local city couriers, not generic 'مندوب'."""
    clean = normalize_shipping_carrier(name)
    return (
        (clean.startswith("مندوب ") and clean not in {"مندوب المتجر", "مندوب المحل"})
        or clean.startswith("مندوبي ")
        or clean.startswith("mandoob ")
    ) and len(clean.split()) > 1


def assert_driver_can_take_shipping_company(
    *, driver: dict[str, Any], shipping_company: Any,
) -> None:
    if normalize_text(driver.get("status") or DRIVER_STATUS_ACTIVE) != DRIVER_STATUS_ACTIVE:
        raise StoreDeliveryRuleError("driver_inactive")
    linked = normalize_shipping_carrier(driver.get("shipping_company"))
    if not linked:
        raise StoreDeliveryRuleError("driver_shipping_company_unconfigured")
    shipment = normalize_shipping_carrier(shipping_company)
    if not shipment:
        raise StoreDeliveryRuleError("shipment_shipping_company_required")
    if linked != shipment:
        raise StoreDeliveryRuleError("driver_shipping_company_mismatch")


def carrier_assignment_snapshot(
    *, driver: dict[str, Any], shipping_company: Any, shipping_city: Any = "",
) -> dict[str, Any]:
    """Only NEW assignments use carrier matching; historical rows are untouched."""
    assert_driver_can_take_shipping_company(driver=driver, shipping_company=shipping_company)
    return {
        "driver_id": normalize_text(driver.get("id")),
        "driver_name_snapshot": normalize_text(driver.get("name")),
        "driver_city_snapshot": normalize_text(driver.get("city")),
        "driver_shipping_company_snapshot": normalize_text(driver.get("shipping_company")),
        "shipping_company_snapshot": normalize_text(shipping_company),
        "shipping_city_snapshot": normalize_text(shipping_city),
        "delivery_fee_snapshot": float(money(driver.get("delivery_fee"))),
        "coverage_mode_snapshot": "shipping_company",
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
    "assert_driver_can_take_shipping_company",
    "carrier_assignment_snapshot",
    "normalize_shipping_carrier",
    "is_city_courier_company",
    "assignment_snapshot",
    "collection_requirements",
    "driver_earning",
    "money",
    "normalize_city",
]
