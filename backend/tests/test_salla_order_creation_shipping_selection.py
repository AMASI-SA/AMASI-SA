"""Synthetic Salla shipping-rate selection and COD availability tests."""

from datetime import datetime,timedelta,timezone
import pytest

from salla_order_creation.payload import PendingOrder
from salla_order_creation.shipping_selection import (
    ShippingSelectionUnverified, validate_shipping_selection,
)

NOW=datetime.fromtimestamp(1800000000,tz=timezone.utc)


def order(methods=("mada",)):
    return PendingOrder.model_validate({
        "customer":{"id":123},
        "receiver":{"name":"Synthetic","phone":"+966500000000","country_code":"SA"},
        "courier_id":"200",
        "ship_to":{
            "country":1,"city":2,"district":3,"block":None,
            "street_number":"11","address_line":"Synthetic Street",
            "postal_code":"12345","short_address":"RHMA3184",
            "building_number":"11","additional_number":"22",
            "geo_coordinates":{"lat":24.7,"lng":46.7},
        },
        "accepted_methods":list(methods),
        "products":[{"identifier_type":"id","identifier":400,"quantity":1}],
    })


def rate(**changes):
    base={
        "rate_id":500,"shipping_company_id":200,
        "destination_country_id":1,"destination_city_id":2,
        "estimated_shipping_sar":"20.00",
        "cod_service_found":True,"cod_fee_estimate_sar":"3.00",
        "source":"salla_shipping_rate_estimate",
        "is_final_cart_quote":False,
    }
    base.update(changes)
    return base


def verify(order_value=None,rates=None,checked_at=NOW):
    return validate_shipping_selection(
        order_value or order(),selected_rate_id=500,
        rate_candidates=rates if rates is not None else [rate()],
        checked_at=checked_at,now=NOW,
    )


def test_selected_carrier_rate_belongs_to_order_destination():
    result=verify()
    assert result["shipping_company_id"]==200
    assert result["destination_country_id"]==1
    assert result["shipping_estimate_sar"]=="20.00"
    assert result["can_create_order"] is False
    assert result["is_final_cart_quote"] is False


def test_cod_fee_eligible_only_when_provider_lists_service():
    result=verify(order(("cod",)))
    assert result["cod_fee_estimate_sar"]=="3.00"
    with pytest.raises(ShippingSelectionUnverified,match="cod_not_available"):
        verify(order(("cod",)),rates=[rate(cod_service_found=False)])


@pytest.mark.parametrize("wrong",[
    {"shipping_company_id":999},
    {"destination_country_id":7},
    {"destination_city_id":8},
    {"source":"model_estimate"},
    {"is_final_cart_quote":True},
    {"estimated_shipping_sar":"-1"},
    {"estimated_shipping_sar":"not-a-number"},
])
def test_untrusted_carrier_or_rate_cannot_be_selected(wrong):
    with pytest.raises(ShippingSelectionUnverified):
        verify(rates=[rate(**wrong)])


def test_ambiguous_duplicate_rate_rejected():
    with pytest.raises(ShippingSelectionUnverified,match="unambiguous"):
        verify(rates=[rate(),rate()])


def test_wrong_rate_id_rejected():
    with pytest.raises(ShippingSelectionUnverified,match="unambiguous"):
        verify(rates=[rate(rate_id=501)])


def test_stale_rate_rejected_before_creation():
    with pytest.raises(ShippingSelectionUnverified,match="fresh"):
        verify(checked_at=NOW-timedelta(seconds=121))


def test_missing_cod_fee_denied_even_if_cod_service_flag_true():
    with pytest.raises(ShippingSelectionUnverified,match="invalid_shipping_price"):
        verify(order(("cod",)),rates=[rate(cod_fee_estimate_sar=None)])
