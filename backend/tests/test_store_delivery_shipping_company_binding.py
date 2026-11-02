"""Carrier-bound NEW delivery assignment rules. Never rewrite legacy assignments."""
import pytest
from store_delivery_domain import (
    StoreDeliveryRuleError, carrier_assignment_snapshot,
    assert_driver_can_take_shipping_company, normalize_shipping_carrier,
)
from store_delivery_handover_routes import _order_shipping_company


def talal(**kwargs):
    driver = {"id": "talal", "name": "طلال", "status": "active",
              "city": "الرياض", "shipping_company": "مندوب الرياض", "delivery_fee": 20}
    driver.update(kwargs)
    return driver


def test_shipping_company_overrides_destination_city_without_modifying_legacy():
    current_order = {"shipping_company": "مندوب الرياض", "shipping_city": "جدة"}
    company = _order_shipping_company(current_order)
    result = carrier_assignment_snapshot(driver=talal(), shipping_company=company, shipping_city="جدة")
    assert result["coverage_mode_snapshot"] == "shipping_company"
    assert result["shipping_city_snapshot"] == "جدة"
    assert result["shipping_company_snapshot"] == "مندوب الرياض"
    assert result["delivery_fee_snapshot"] == 20
    assert current_order["shipping_city"] == "جدة"


@pytest.mark.parametrize("carrier", ["مندوب جدة", "مندوب", "سمسا", "mandoob jeddah"])
def test_other_shipping_companies_cannot_be_assigned_to_talal(carrier):
    with pytest.raises(StoreDeliveryRuleError, match="driver_shipping_company_mismatch"):
        assert_driver_can_take_shipping_company(driver=talal(), shipping_company=carrier)


def test_legacy_unlinked_driver_must_be_configured_before_a_new_assignment():
    with pytest.raises(StoreDeliveryRuleError, match="driver_shipping_company_unconfigured"):
        carrier_assignment_snapshot(driver=talal(shipping_company="", coverage_mode="city"),
                                    shipping_company="مندوب الرياض")


def test_inactive_driver_and_missing_shipping_identity_fail_closed():
    with pytest.raises(StoreDeliveryRuleError, match="driver_inactive"):
        assert_driver_can_take_shipping_company(driver=talal(status="inactive"),
                                                shipping_company="مندوب الرياض")
    with pytest.raises(StoreDeliveryRuleError, match="shipment_shipping_company_required"):
        assert_driver_can_take_shipping_company(driver=talal(), shipping_company="")


def test_strict_company_labels_do_not_collapse_different_local_couriers():
    assert normalize_shipping_carrier("مندوب جدة") != normalize_shipping_carrier("مندوب الرياض")
    assert normalize_shipping_carrier("مندوب جدة") != normalize_shipping_carrier("مندوب")
    assert normalize_shipping_carrier("Mandoob Riyadh") == normalize_shipping_carrier("مندوب الرياض")
    assert _order_shipping_company({
        "shipping_company": "مندوب الرياض",
        "salla_shipping_current": {"company_name": "مندوب جدة", "company_code": "jeddah"},
    }) == "مندوب جدة"
    assert _order_shipping_company({
        "shipping_company": "مندوب الرياض",
        "salla_shipping_current": {"company_code": "unknown"},
    }) == ""
