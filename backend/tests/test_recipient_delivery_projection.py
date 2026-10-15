from order_engine.mapper import map_salla_order
from order_engine.recipient_enrichment import (
    recipient_is_independent,
    resolve_salla_recipient,
)
from order_engine.shipping_label_service import _store_courier_print_data


def _base_order():
    return {
        "id": "salla-1",
        "reference_id": "10001",
        "date": "2026-10-01T01:00:00+03:00",
        "customer": {
            "full_name": "عرفات",
            "mobile": "0500000001",
            "email": "buyer@example.com",
            "shipping_address": {
                "city": "جدة",
                "street": "شارع المشتري",
            },
        },
        "amounts": {
            "total": {"amount": 250, "currency": "SAR"},
        },
        "payment": {
            "method": "cod",
            "paid_amount": 0,
        },
        "payment_actions": {
            "remaining_action": {
                "remaining_amount": {"amount": 250, "currency": "SAR"},
            }
        },
        "shipping": {},
        "shipments": [],
        "items": [],
    }


def test_same_buyer_and_recipient_does_not_become_independent():
    order = _base_order()
    order["recipient"] = {
        "name": "عرفات",
        "mobile": "0500000001",
        "address": {"city": "جدة"},
    }
    recipient = resolve_salla_recipient(order)
    assert recipient["name"] == "عرفات"
    assert recipient_is_independent(order, recipient) is False


def test_independent_recipient_wins_over_buyer_identity_and_address():
    order = _base_order()
    order["recipient"] = {
        "name": "موضي",
        "mobile": "0500000002",
        "address": {
            "country": "السعودية",
            "city": "الرياض",
            "district": "العليا",
            "street": "شارع المستلم",
            "short_address": "RRRD1234",
        },
    }
    recipient = resolve_salla_recipient(order)
    assert recipient["name"] == "موضي"
    assert recipient["mobile"] == "0500000002"
    assert recipient["address"]["city"] == "الرياض"
    assert recipient_is_independent(order, recipient) is True


def test_recipient_can_fill_missing_fields_from_shipment_ship_to_without_buyer_mix():
    order = _base_order()
    order["recipient"] = {"name": "موضي"}
    shipment = {
        "ship_to": {
            "name": "موضي",
            "phone": "0500000002",
            "city": "الرياض",
            "street": "شارع الشحنة",
        }
    }
    recipient = resolve_salla_recipient(order, shipment=shipment)
    assert recipient["name"] == "موضي"
    assert recipient["mobile"] == "0500000002"
    assert recipient["address"]["city"] == "الرياض"


def test_shipping_address_is_preserved_when_no_independent_recipient():
    order = _base_order()
    order["shipping"] = {
        "address": {
            "country": "السعودية",
            "city": "الرياض",
            "district": "الملقا",
            "street": "شارع أنس",
        }
    }
    mapped = map_salla_order(order)
    assert mapped.shipping.address.city == "الرياض"
    assert mapped.shipping.address.district == "الملقا"


def test_courier_object_name_maps_to_shipping_company():
    order = _base_order()
    order["shipments"] = [{
        "id": "sh-1",
        "courier": {"name": "iMile"},
        "ship_to": {"name": "عرفات", "phone": "0500000001"},
    }]
    mapped = map_salla_order(order)
    assert mapped.shipping.company == "iMile"


def test_shipping_company_string_maps_without_becoming_unspecified():
    order = _base_order()
    order["shipping"] = {
        "company": "SMSA",
        "method": "standard",
    }
    mapped = map_salla_order(order)
    assert mapped.shipping.company == "SMSA"


def test_store_courier_label_uses_independent_recipient_and_never_buyer_phone():
    order = _base_order()
    order["recipient"] = {
        "name": "موضي",
        "address": {
            "city": "الرياض",
            "district": "العليا",
            "street": "شارع المستلم",
            "short_address": "RRRD1234",
        },
    }
    shipment = {
        "courier_name": "مندوب المتجر",
        "ship_to": {
            "name": "موضي",
            # Deliberately no phone: buyer phone must not be substituted.
            "city": "الرياض",
            "district": "العليا",
            "street": "شارع المستلم",
            "short_address": "RRRD1234",
        },
        "total": {"amount": 250, "currency": "SAR"},
        "packages": [{"name": "منتج سري", "quantity": 1}],
    }
    data = _store_courier_print_data(
        "10001",
        order,
        shipment,
        {"name": "AMASI"},
    )
    assert data["recipient_name"] == "موضي"
    assert data["customer_name"] == "موضي"
    assert data["recipient_independent"] is True
    assert data["customer_phone"] is None
    assert data["buyer_phone"] == "0500000001"
    assert data["address"]["city"] == "الرياض"
    assert data["remaining_amount"]["amount"] == 250
    assert data["qr_code"].startswith("data:image/")


def test_store_courier_label_uses_ship_to_when_recipient_container_is_shipment_only():
    order = _base_order()
    shipment = {
        "courier_name": "مندوب المتجر",
        "ship_to": {
            "name": "موضي",
            "phone": "0500000002",
            "city": "الرياض",
            "street": "شارع الشحنة",
        },
        "total": {"amount": 250, "currency": "SAR"},
    }
    data = _store_courier_print_data(
        "10001",
        order,
        shipment,
        {"name": "AMASI"},
    )
    assert data["customer_name"] == "موضي"
    assert data["customer_phone"] == "0500000002"
    assert data["address"]["city"] == "الرياض"
