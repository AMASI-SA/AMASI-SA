"""One-shot V2 fix: rebuild canonical order address from durable root fields.

The Salla raw snapshot may be replaced by a modern/light Order Details response.
The durable normalized fields in unified_orders are intentionally preserved by
orders_db.  Order Engine V2 must consume those fields as a fallback instead of
showing an empty address.
"""
from pathlib import Path


REPOSITORY = Path("backend/order_engine/repository.py")
TESTS = Path("backend/tests/test_order_engine_repository.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


source = REPOSITORY.read_text(encoding="utf-8")

source = replace_once(
    source,
    '''_ATTRIBUTION_FIELDS = (
    "source",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "device",
    "is_gift",
    "gift",
    "gift_order",
    "order_type",
    "type",
    "mezan_read_at",
)


def _customized_status_expression() -> dict[str, Any]:''',
    '''_ATTRIBUTION_FIELDS = (
    "source",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "device",
    "is_gift",
    "gift",
    "gift_order",
    "order_type",
    "type",
    "mezan_read_at",
)

# Durable normalized fields owned by the V2 Order Engine read model.  A modern
# Salla Order Details response can be intentionally light and omit shipping
# objects.  `orders_db` preserves these root fields from richer webhooks, so V2
# rehydrates the provider-shaped read payload from them without calling legacy
# pages or mutating storage.
_V2_CANONICAL_ROOT_FIELDS = tuple(dict.fromkeys((
    *_ATTRIBUTION_FIELDS,
    "customer_name",
    "customer_mobile",
    "payment_method",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "shipping_company",
    "shipping_company_code",
    "shipping_method",
    "shipping_status",
    "shipment_status",
    "tracking_number",
    "tracking_url",
    "shipping_label_url",
    "shipping_address",
    "shipping_address_raw",
    "shipping_city",
    "customer_city",
    "shipping_district",
    "shipping_street",
    "shipping_national_address",
    "shipping_short_address",
    "shipping_postal_code",
    "shipping_building_number",
    "shipping_additional_number",
    "shipping_country",
    "shipping_latitude",
    "shipping_longitude",
)))


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _fill_missing(target: dict[str, Any], key: str, value: Any) -> None:
    if not _present(target.get(key)) and _present(value):
        target[key] = deepcopy(value)


def _v2_address_fallback(raw: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    durable = row.get("shipping_address_raw")
    if isinstance(durable, dict):
        address = deepcopy(durable)
    elif isinstance(row.get("shipping_address"), dict):
        address = deepcopy(row["shipping_address"])
    else:
        address = {}
        if _present(row.get("shipping_address")):
            address["address_line"] = row.get("shipping_address")

    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    _fill_missing(address, "country", row.get("shipping_country") or customer.get("country"))
    _fill_missing(address, "country_code", customer.get("country_code"))
    _fill_missing(address, "city", row.get("shipping_city") or row.get("customer_city") or customer.get("city"))

    district = row.get("shipping_district")
    if _present(district):
        if not _present(address.get("district")):
            address["district"] = {"name": district}
        _fill_missing(address, "block", district)

    _fill_missing(address, "street", row.get("shipping_street"))
    _fill_missing(address, "short_address", row.get("shipping_short_address") or row.get("shipping_national_address"))
    _fill_missing(address, "postal_code", row.get("shipping_postal_code"))
    _fill_missing(address, "building_number", row.get("shipping_building_number"))
    _fill_missing(address, "additional_number", row.get("shipping_additional_number"))
    _fill_missing(address, "latitude", row.get("shipping_latitude"))
    _fill_missing(address, "longitude", row.get("shipping_longitude"))
    _fill_missing(address, "address_line", customer.get("location"))

    return address if any(_present(value) for value in address.values()) else {}


def _apply_v2_root_fallbacks(raw: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    hydrated = deepcopy(raw)

    for field in _ATTRIBUTION_FIELDS:
        _fill_missing(hydrated, field, row.get(field))

    customer = deepcopy(hydrated.get("customer")) if isinstance(hydrated.get("customer"), dict) else {}
    _fill_missing(customer, "full_name", row.get("customer_name"))
    _fill_missing(customer, "mobile", row.get("customer_mobile"))
    _fill_missing(customer, "city", row.get("shipping_city") or row.get("customer_city"))
    _fill_missing(customer, "country", row.get("shipping_country"))
    if customer:
        hydrated["customer"] = customer

    address = _v2_address_fallback(hydrated, row)
    if address:
        _fill_missing(hydrated, "shipping_address", address)

    shipping = deepcopy(hydrated.get("shipping")) if isinstance(hydrated.get("shipping"), dict) else {}
    _fill_missing(shipping, "company_name", row.get("shipping_company"))
    _fill_missing(shipping, "company_code", row.get("shipping_company_code"))
    _fill_missing(shipping, "method", row.get("shipping_method"))
    _fill_missing(shipping, "status", row.get("shipping_status") or row.get("shipment_status"))
    _fill_missing(shipping, "tracking_number", row.get("tracking_number"))
    _fill_missing(shipping, "tracking_url", row.get("tracking_url"))
    _fill_missing(shipping, "label_url", row.get("shipping_label_url"))
    if address:
        _fill_missing(shipping, "address", address)
    if shipping:
        hydrated["shipping"] = shipping

    for field in (
        "payment_method",
        "paid_amount",
        "remaining_amount",
        "has_remaining_amount",
        "payment_collection_status",
        "payment_checkout_url",
        "receiving_bank_name",
        "payment_receipt_url",
        "shipping_label_url",
    ):
        _fill_missing(hydrated, field, row.get(field))

    return hydrated


def _customized_status_expression() -> dict[str, Any]:''',
    "V2 root fallback helpers",
)

source = source.replace(
    '**{field: 1 for field in _ATTRIBUTION_FIELDS},',
    '**{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},',
)
if source.count('**{field: 1 for field in _V2_CANONICAL_ROOT_FIELDS},') != 2:
    raise SystemExit("V2 projection replacement did not affect exactly two projections")

source = replace_once(
    source,
    '''        # unified_orders already stores normalized attribution fields at the
        # document root. Preserve the provider payload as the first authority,
        # then fill only missing fields from that durable normalized snapshot.
        salla_raw = deepcopy(raw_provider)
        for field in _ATTRIBUTION_FIELDS:
            if salla_raw.get(field) in (None, "", [], {}):
                value = row.get(field)
                if value not in (None, "", [], {}):
                    salla_raw[field] = deepcopy(value)
''',
    '''        # Provider payload stays authoritative.  Missing V2 operational
        # fields are rehydrated from the durable normalized root snapshot so a
        # light Salla response cannot erase address, courier or payment evidence.
        salla_raw = _apply_v2_root_fallbacks(raw_provider, row)
''',
    "repository raw hydration",
)
REPOSITORY.write_text(source, encoding="utf-8")


tests = TESTS.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    'from order_engine.repository import MongoOrderRepository\n',
    'from order_engine.mapper import map_salla_order\nfrom order_engine.repository import MongoOrderRepository\n',
    "repository test mapper import",
)

if "test_repository_rehydrates_shipping_from_v2_root_snapshot" not in tests:
    tests += r'''


@pytest.mark.asyncio
async def test_repository_rehydrates_shipping_from_v2_root_snapshot():
    rows = [{
        "user_id": "u1",
        "order_number": "274682897",
        "order_date": "2026-07-28",
        "customer_name": "عبدالله جمعي",
        "customer_mobile": "561752841",
        "shipping_company": "iMile",
        "shipping_company_code": "imile",
        "shipping_method": "shipping",
        "shipping_city": "الرياض",
        "customer_city": "الرياض",
        "shipping_district": "العليا",
        "shipping_street": "طريق الملك فهد",
        "shipping_country": "السعودية",
        "shipping_postal_code": "12262",
        "shipping_short_address": "RRRD2929",
        "shipping_address_raw": {
            "city": "الرياض",
            "block": "العليا",
            "street_number": "طريق الملك فهد",
            "country": "السعودية",
        },
        "payment_receipt_url": "https://cdn.salla.sa/receipt.jpg",
        # Simulates the modern/light Order Details snapshot that replaced the
        # richer provider payload after an explicit review refresh.
        "raw_by_source": {
            "salla_direct": {
                "id": "901",
                "reference_id": "274682897",
                "date": "2026-07-28T09:00:00+03:00",
                "customer": {
                    "full_name": "عبدالله جمعي",
                    "mobile": "561752841",
                },
                "payment_method": "credit_card",
                "amounts": {"total": {"amount": 127.60, "currency": "SAR"}},
            },
        },
    }]
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.get_salla_order(
        user_id="u1",
        order_number="274682897",
    )

    assert result is not None
    dto = map_salla_order(result.salla_raw)
    assert dto.shipping.company == "iMile"
    assert dto.shipping.company_code == "imile"
    assert dto.shipping.method == "shipping"
    assert dto.shipping.address is not None
    assert dto.shipping.address.country == "السعودية"
    assert dto.shipping.address.city == "الرياض"
    assert dto.shipping.address.district == "العليا"
    assert dto.shipping.address.street == "طريق الملك فهد"
    assert dto.shipping.address.postal_code == "12262"
    assert dto.shipping.address.short_address == "RRRD2929"
    assert dto.customer.shipping_address.city == "الرياض"
    assert dto.payment.receipt_url == "https://cdn.salla.sa/receipt.jpg"

    projection = db.unified_orders.last_projection
    assert projection["shipping_address_raw"] == 1
    assert projection["shipping_company"] == 1
    assert projection["shipping_city"] == 1


@pytest.mark.asyncio
async def test_repository_keeps_provider_address_over_root_fallback():
    rows = [{
        "user_id": "u1",
        "order_number": "274724433",
        "order_date": "2026-07-28",
        "shipping_city": "الرياض",
        "shipping_district": "العليا",
        "raw_by_source": {
            "salla_direct": {
                "id": "902",
                "reference_id": "274724433",
                "date": "2026-07-28T10:00:00+03:00",
                "shipping_address": {
                    "city": "جدة",
                    "block": "الروضة",
                    "street_number": "شارع الأمير",
                },
                "amounts": {"total": {"amount": 134, "currency": "SAR"}},
            },
        },
    }]
    repository = MongoOrderRepository(FakeDB(rows))

    result = await repository.get_salla_order(user_id="u1", order_number="274724433")
    dto = map_salla_order(result.salla_raw)

    assert dto.shipping.address.city == "جدة"
    assert dto.shipping.address.district == "الروضة"
    assert dto.shipping.address.street == "شارع الأمير"
'''

TESTS.write_text(tests, encoding="utf-8")
print("Orders V2 canonical root address fallback applied.")
