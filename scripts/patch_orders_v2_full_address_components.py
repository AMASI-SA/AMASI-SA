"""One-shot Orders V2 patch for complete Salla delivery address components."""
from pathlib import Path


SERVICE = Path("backend/order_engine/salla_refresh.py")
TESTS = Path("backend/tests/test_order_engine_salla_refresh.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


source = SERVICE.read_text(encoding="utf-8")

source = replace_once(
    source,
    '''For modern Salla applications, Order Details is requested with ``format=light``.
That response still contains the order/customer/receiver facts, while order items
are retrieved through the dedicated ``/orders/items`` endpoint.
''',
    '''Order Details is requested without ``format=light`` so Salla can return the
complete delivery facts available on the order itself, including ``ship_to``,
``block`` and ``street_number``. Order items are retrieved separately through
``/orders/items``. Embedded shipment objects may be read from Order Details, but
this service never calls a Shipments API endpoint.
''',
    "service module contract",
)

source = replace_once(
    source,
    '''def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


''',
    '''def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_shipment(order: dict[str, Any]) -> dict[str, Any]:
    rows = order.get("shipments")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                return dict(row)
    if isinstance(rows, dict):
        return dict(rows)
    return _dict(order.get("shipment"))


''',
    "first shipment helper",
)

source = replace_once(
    source,
    '''def _named(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("name", "name_ar", "label", "title", "display_name", "value"):
            text = _text(value.get(key))
            if text and not text.replace(".", "", 1).isdigit():
                return text
        return None
    text = _text(value)
    if text and not text.replace(".", "", 1).isdigit():
        return text
    return None


''',
    '''def _named(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("name", "name_ar", "label", "title", "display_name", "value"):
            text = _text(value.get(key))
            if text and not text.replace(".", "", 1).isdigit():
                return text
        return None
    text = _text(value)
    if text and not text.replace(".", "", 1).isdigit():
        return text
    return None


def _first_named(*values: Any) -> Optional[str]:
    """Return the first human-readable label, skipping numeric Salla IDs."""
    for value in values:
        text = _named(value)
        if text:
            return text
    return None


''',
    "first named helper",
)

source = replace_once(
    source,
    '''        "description": 8,
        "location": 8,
        "short_address": 7,
''',
    '''        "description": 8,
        "location": 8,
        "address": 8,
        "house_desc": 8,
        "short_address": 7,
''',
    "address score aliases",
)

source = replace_once(
    source,
    '''def extract_order_details_address(order: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """Read the delivery address from the Order Details payload itself."""
    shipping = _dict(order.get("shipping"))
    receiver = _dict(order.get("receiver"))
    customer = _dict(order.get("customer"))

    preferred = [
        ("order.shipping.ship_to", _dict(shipping.get("ship_to"))),
''',
    '''def extract_order_details_address(order: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """Read the delivery address from the Order Details payload itself."""
    shipping = _dict(order.get("shipping"))
    receiver = _dict(order.get("receiver"))
    customer = _dict(order.get("customer"))
    first_shipment = _first_shipment(order)

    preferred = [
        ("order.shipments[0].ship_to", _dict(first_shipment.get("ship_to"))),
        ("order.shipments[0].shipping_address", _dict(first_shipment.get("shipping_address"))),
        ("order.shipments[0].address", _dict(first_shipment.get("address"))),
        ("order.shipping.ship_to", _dict(shipping.get("ship_to"))),
''',
    "order details address precedence",
)

source = replace_once(
    source,
    '''    city = _named(address.get("city") or address.get("city_name") or address.get("city_data"))
    district = _named(
        address.get("district")
        or address.get("district_name")
        or address.get("neighborhood")
        or address.get("neighbourhood")
        or address.get("block")
    )
    street = _named(
        address.get("street")
        or address.get("street_name")
        or address.get("street_number")
    )
    country = _named(address.get("country") or address.get("country_name") or address.get("country_data"))
''',
    '''    city = _first_named(
        address.get("city"),
        address.get("city_name"),
        address.get("city_data"),
        address.get("town"),
        address.get("locality"),
    )
    district = _first_named(
        address.get("district"),
        address.get("district_name"),
        address.get("district_data"),
        address.get("neighborhood"),
        address.get("neighbourhood"),
        address.get("block"),
        address.get("local"),
    )
    street = _first_named(
        address.get("street"),
        address.get("street_name"),
        address.get("street_number"),
    )
    country = _first_named(
        address.get("country"),
        address.get("country_name"),
        address.get("country_data"),
    )
''',
    "human-readable address components",
)

source = replace_once(
    source,
    '''        address.get("description"),
        address.get("location"),
    )
''',
    '''        address.get("description"),
        address.get("location"),
        address.get("address"),
        address.get("house_desc"),
    )
''',
    "formatted address aliases",
)

source = replace_once(
    source,
    '''            "GET",
            f"/orders/{internal_id}",
            params={"format": "light"},
        )
''',
    '''            "GET",
            f"/orders/{internal_id}",
        )
''',
    "full Order Details request",
)

source = replace_once(
    source,
    'REFRESH_ENDPOINT_FIELD: "GET /orders/{id}?format=light + GET /orders/items",',
    'REFRESH_ENDPOINT_FIELD: "GET /orders/{id} + GET /orders/items",',
    "refresh endpoint audit label",
)

SERVICE.write_text(source, encoding="utf-8")


tests = TESTS.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    '''    REFRESH_TIMESTAMP_FIELD,
    extract_order_details_address,
    refresh_order_from_salla,
''',
    '''    REFRESH_TIMESTAMP_FIELD,
    extract_order_details_address,
    extract_order_details_shipping_fields,
    refresh_order_from_salla,
''',
    "test imports",
)

tests = replace_once(
    tests,
    '            assert params == {"format": "light"}\n',
    '            assert params is None\n',
    "full Order Details request contract",
)

new_test = r'''


def test_full_order_details_uses_embedded_ship_to_and_skips_numeric_ids():
    fields, address, source = extract_order_details_shipping_fields({
        "customer": {
            "city": "جدة",
            "country": "السعودية",
        },
        "shipments": [{
            "pickup_address": {
                "country": "السعودية",
                "city": "الرياض",
                "block": "الملز",
                "street_number": "شارع المتجر",
            },
            "ship_to": {
                "country": "السعودية",
                "city": "جدة",
                "district": 1939592358,
                "district_name": "حي الصفا",
                "block": "حي الصفا",
                "street": 674989864,
                "street_number": "شارع الأربعين",
                "address_line": "شارع الأربعين، حي الصفا، جدة، السعودية",
                "postal_code": "23455",
            },
        }],
    })

    assert source == "order.shipments[0].ship_to"
    assert address["block"] == "حي الصفا"
    assert fields["shipping_city"] == "جدة"
    assert fields["shipping_district"] == "حي الصفا"
    assert fields["shipping_street"] == "شارع الأربعين"
    assert fields["shipping_address"] == "شارع الأربعين، حي الصفا، جدة، السعودية"
    assert fields["shipping_postal_code"] == "23455"
'''

if "test_full_order_details_uses_embedded_ship_to_and_skips_numeric_ids" not in tests:
    tests += new_test

TESTS.write_text(tests, encoding="utf-8")
print("Orders V2 full address component patch applied.")
