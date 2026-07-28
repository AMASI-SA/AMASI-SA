"""One-shot patch: make Order Engine V2 the only review read model.

The patch is intentionally limited to V2 Order Engine/read-only review code.
It removes page-specific Order Details resync and reconstructs missing DTO facts
from the durable unified_orders root snapshot without database writes or Salla
calls.
"""
from pathlib import Path


REPOSITORY = Path("backend/order_engine/repository.py")
MAPPER = Path("backend/order_engine/mapper.py")
REVIEW_ROUTES = Path("backend/order_review_routes.py")
REVIEW_TESTS = Path("backend/tests/test_order_review_stage_one.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# 1) Order Engine V2 repository: build one canonical read payload from the
#    provider raw snapshot plus durable root fields preserved by merge rules.
# ---------------------------------------------------------------------------
repository = REPOSITORY.read_text(encoding="utf-8")
helper_marker = "\n\n\ndef _customized_status_expression()"
if repository.count(helper_marker) != 1:
    raise SystemExit("repository helper marker not found exactly once")

helper_code = r'''

_ROOT_FALLBACK_FIELDS = (
    "order_id",
    "order_date_raw",
    "order_status",
    "order_status_slug",
    "payment_status",
    "paid_amount",
    "remaining_amount",
    "has_remaining_amount",
    "payment_collection_status",
    "payment_checkout_url",
    "receiving_bank_name",
    "payment_receipt_url",
    "customer_name",
    "customer_mobile",
    "customer_email",
    "payment_method",
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
    "products",
    "subtotal",
    "shipping_cost",
    "discount",
    "tax",
    "total_amount",
    "currency",
)


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def _set_missing(target: dict[str, Any], key: str, value: Any) -> None:
    if not _has_value(target.get(key)) and _has_value(value):
        target[key] = deepcopy(value)


def _merge_missing(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(target or {})
    for key, value in (source or {}).items():
        _set_missing(merged, str(key), value)
    return merged


def _root_shipping_address(row: dict[str, Any]) -> dict[str, Any]:
    address: dict[str, Any] = {}
    for candidate in (row.get("shipping_address_raw"), row.get("shipping_address")):
        if isinstance(candidate, dict):
            address = _merge_missing(address, candidate)
        elif _has_value(candidate):
            _set_missing(address, "formatted", candidate)

    values = {
        "city": row.get("shipping_city") or row.get("customer_city"),
        "district": row.get("shipping_district"),
        "street": row.get("shipping_street"),
        "short_address": row.get("shipping_short_address") or row.get("shipping_national_address"),
        "postal_code": row.get("shipping_postal_code"),
        "building_number": row.get("shipping_building_number"),
        "additional_number": row.get("shipping_additional_number"),
        "country": row.get("shipping_country"),
        "latitude": row.get("shipping_latitude"),
        "longitude": row.get("shipping_longitude"),
    }
    for key, value in values.items():
        _set_missing(address, key, value)
    return address


def _build_v2_read_payload(
    raw_provider: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Return the V2 canonical read payload without I/O or mutation.

    ``raw_by_source.salla_direct`` can be a reduced API snapshot after a manual
    diagnostic, while ``unified_orders`` root fields retain richer verified
    webhook facts under first-writer/fill-empty merge rules.  V2 consumers read
    one reconstructed payload so Orders V2 and Fulfillment V2 cannot disagree.
    """
    payload = deepcopy(raw_provider or {})

    for field in _ATTRIBUTION_FIELDS:
        _set_missing(payload, field, row.get(field))

    _set_missing(payload, "id", row.get("order_id"))
    _set_missing(payload, "reference_id", row.get("order_number"))
    _set_missing(payload, "date", row.get("order_date_raw") or row.get("order_date"))
    _set_missing(payload, "status_slug", row.get("order_status_slug"))
    if not _has_value(payload.get("status")) and _has_value(row.get("order_status")):
        payload["status"] = {
            "slug": row.get("order_status_slug") or row.get("order_status"),
            "name": row.get("order_status"),
        }

    customer = deepcopy(payload.get("customer")) if isinstance(payload.get("customer"), dict) else {}
    _set_missing(customer, "full_name", row.get("customer_name"))
    _set_missing(customer, "mobile", row.get("customer_mobile"))
    _set_missing(customer, "email", row.get("customer_email"))

    address = _root_shipping_address(row)
    provider_address = payload.get("shipping_address")
    if isinstance(provider_address, dict):
        address = _merge_missing(provider_address, address)
    if address:
        payload["shipping_address"] = deepcopy(address)
        _set_missing(customer, "shipping_address", address)
    if customer:
        payload["customer"] = customer

    _set_missing(payload, "payment_method", row.get("payment_method"))
    for field in (
        "paid_amount",
        "remaining_amount",
        "has_remaining_amount",
        "payment_collection_status",
        "payment_checkout_url",
        "receiving_bank_name",
        "payment_receipt_url",
        "shipping_label_url",
    ):
        _set_missing(payload, field, row.get(field))

    payment = deepcopy(payload.get("payment")) if isinstance(payload.get("payment"), dict) else {}
    _set_missing(payment, "status", row.get("payment_status"))
    _set_missing(payment, "paid_amount", row.get("paid_amount"))
    _set_missing(payment, "remaining_amount", row.get("remaining_amount"))
    _set_missing(payment, "has_remaining_amount", row.get("has_remaining_amount"))
    _set_missing(payment, "collection_status", row.get("payment_collection_status"))
    _set_missing(payment, "checkout_url", row.get("payment_checkout_url"))
    _set_missing(payment, "receiving_bank_name", row.get("receiving_bank_name"))
    _set_missing(payment, "receipt_url", row.get("payment_receipt_url"))
    if payment:
        payload["payment"] = payment

    shipping = deepcopy(payload.get("shipping")) if isinstance(payload.get("shipping"), dict) else {}
    _set_missing(shipping, "company_name", row.get("shipping_company"))
    _set_missing(shipping, "company_code", row.get("shipping_company_code"))
    _set_missing(shipping, "method", row.get("shipping_method"))
    _set_missing(shipping, "status", row.get("shipping_status") or row.get("shipment_status"))
    _set_missing(shipping, "tracking_number", row.get("tracking_number"))
    _set_missing(shipping, "tracking_url", row.get("tracking_url"))
    _set_missing(shipping, "label_url", row.get("shipping_label_url"))
    if address:
        current_address = shipping.get("address") if isinstance(shipping.get("address"), dict) else {}
        shipping["address"] = _merge_missing(current_address, address)
    if shipping:
        payload["shipping"] = shipping

    if not _has_value(payload.get("items")) and isinstance(row.get("products"), list):
        payload["items"] = deepcopy(row.get("products"))

    amounts = deepcopy(payload.get("amounts")) if isinstance(payload.get("amounts"), dict) else {}
    _set_missing(amounts, "sub_total", row.get("subtotal"))
    _set_missing(amounts, "shipping_cost", row.get("shipping_cost"))
    _set_missing(amounts, "discount", row.get("discount"))
    _set_missing(amounts, "tax", row.get("tax"))
    _set_missing(amounts, "total", row.get("total_amount"))
    _set_missing(amounts, "currency", row.get("currency"))
    if amounts:
        payload["amounts"] = amounts
    _set_missing(payload, "total_amount", row.get("total_amount"))
    _set_missing(payload, "currency", row.get("currency"))
    return payload
'''
repository = repository.replace(helper_marker, helper_code + helper_marker, 1)

projection_old = "            **{field: 1 for field in _ATTRIBUTION_FIELDS},"
projection_new = "            **{field: 1 for field in (*_ATTRIBUTION_FIELDS, *_ROOT_FALLBACK_FIELDS)},"
if repository.count(projection_old) != 2:
    raise SystemExit(f"repository projection: expected 2 matches, found {repository.count(projection_old)}")
repository = repository.replace(projection_old, projection_new)

repository = replace_once(
    repository,
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
    '''        # V2 reads one canonical payload. The provider raw snapshot remains
        # first authority, while missing facts are filled from durable root fields
        # preserved by unified_orders merge rules. This is read-only and performs
        # no Salla or database write.
        salla_raw = _build_v2_read_payload(raw_provider, row)
''',
    "repository canonical payload",
)
REPOSITORY.write_text(repository, encoding="utf-8")


# ---------------------------------------------------------------------------
# 2) Mapper: empty Salla containers must not block richer V2 root fallbacks.
# ---------------------------------------------------------------------------
mapper = MAPPER.read_text(encoding="utf-8")
mapper = replace_once(
    mapper,
    '''def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _nested(data: dict[str, Any], *path: str) -> Any:
''',
    '''def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _nested(data: dict[str, Any], *path: str) -> Any:
''',
    "mapper first-nonempty helper",
)
mapper = replace_once(
    mapper,
    '''        country=_text(_first(country.get("name"), data.get("country"))),
''',
    '''        country=_text(_first(country.get("name"), data.get("country_name"), data.get("country"))),
''',
    "country fallback",
)
mapper = replace_once(
    mapper,
    '''        city=_text(_first(city.get("name"), data.get("city"))),
''',
    '''        city=_text(_first(city.get("name"), data.get("city_name"), data.get("locality"), data.get("city"))),
''',
    "city fallback",
)
mapper = replace_once(
    mapper,
    '''                district.get("name"),
                data.get("district_name"),
''',
    '''                district.get("name"),
                data.get("district"),
                data.get("district_name"),
''',
    "scalar district fallback",
)
mapper = replace_once(
    mapper,
    '''    shipping_raw = _dict(
        _first(
            raw_order.get("shipping"),
            raw_order.get("shipping_address"),
        )
    )
''',
    '''    shipping_raw = _dict(
        _first_nonempty(
            raw_order.get("shipping"),
            raw_order.get("shipping_address"),
        )
    )
''',
    "shipping container fallback",
)
mapper = replace_once(
    mapper,
    '''    courier = _dict(
        _first(
            first_shipment.get("courier"),
            shipping_raw.get("company"),
        )
    )

    shipping_address_raw = _first(
''',
    '''    courier = _dict(
        _first_nonempty(
            first_shipment.get("courier"),
            shipping_raw.get("company"),
        )
    )

    shipping_address_raw = _first_nonempty(
''',
    "shipping address nonempty selection",
)
MAPPER.write_text(mapper, encoding="utf-8")


# ---------------------------------------------------------------------------
# 3) Fulfillment V2 review: consume Order Engine V2 only; never resync Order
#    Details from a page-specific route.
# ---------------------------------------------------------------------------
routes = REVIEW_ROUTES.read_text(encoding="utf-8")
routes = replace_once(
    routes,
    "from salla_integration.sync import resync_single_order\n",
    "",
    "remove review resync import",
)
routes = replace_once(
    routes,
    'REVIEW_SOURCE_REFRESH_VERSION = 2\n',
    '',
    "remove review refresh version",
)
refresh_start = "async def _refresh_review_source_once("
refresh_end = "\n\nasync def _sync_salla_reviewed("
if routes.count(refresh_start) != 1 or routes.count(refresh_end) != 1:
    raise SystemExit("review refresh function markers not found exactly once")
start = routes.index(refresh_start)
end = routes.index(refresh_end, start)
routes = routes[:start] + routes[end + 2:]
routes = replace_once(
    routes,
    '''
        if await _refresh_review_source_once(db, merchant_id, order_number):
            try:
                order = await get_order(repository, user_id=merchant_id, order_number=order_number)
            except OrderNotFoundError:
                pass
        return await _detail(db, merchant_id, order)
''',
    '''
        # Fulfillment V2 consumes the same canonical read model as Orders V2.
        # Opening review is read-only: no Order Details resync and no source
        # snapshot replacement from this page.
        return await _detail(db, merchant_id, order)
''',
    "review detail V2 read path",
)
REVIEW_ROUTES.write_text(routes, encoding="utf-8")


# ---------------------------------------------------------------------------
# 4) Regression tests: root shipping facts repair reduced raw snapshots, and
#    review detail never invokes external order resync.
# ---------------------------------------------------------------------------
tests = REVIEW_TESTS.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    "from datetime import datetime, timezone\n",
    "from datetime import datetime, timezone\nimport inspect\n",
    "inspect import",
)
tests = replace_once(
    tests,
    "from order_engine.mapper import map_salla_order\n",
    "from order_engine.mapper import map_salla_order\nfrom order_engine.repository import MongoOrderRepository\n",
    "repository import",
)
tests = replace_once(
    tests,
    '''from order_review_routes import (
    REVIEW_SOURCE_REFRESH_VERSION,
    _can_review,
    _merchant_user_id,
    _refresh_review_source_once,
    _review_item_identities,
    _reviewed_status_id,
    build_image_preference_identity,
)
''',
    '''from order_review_routes import (
    _can_review,
    _merchant_user_id,
    _review_item_identities,
    _reviewed_status_id,
    build_image_preference_identity,
    make_order_review_router,
)
''',
    "review imports",
)
refresh_test_start = "class _ReviewRefreshCollection:"
refresh_test_end = "\n\n@pytest.mark.asyncio\nasync def test_empty_current_shipments_preserve_embedded_delivery_context():"
if tests.count(refresh_test_start) != 1 or tests.count(refresh_test_end) != 1:
    raise SystemExit("legacy review refresh test block not found exactly once")
start = tests.index(refresh_test_start)
end = tests.index(refresh_test_end, start)
tests = tests[:start] + tests[end + 2:]

new_tests = r'''


def test_v2_read_model_restores_durable_shipping_receipt_and_items():
    row = {
        "user_id": "owner-1",
        "order_number": "274682897",
        "order_date": "2026-07-28",
        "order_id": "salla-internal-1",
        "order_status": "بانتظار المراجعة",
        "order_status_slug": "under_review",
        "customer_name": "عميل اختبار",
        "customer_mobile": "0500000000",
        "payment_method": "bank",
        "payment_receipt_url": "https://cdn.salla.sa/receipt.jpg",
        "shipping_company": "iMile",
        "shipping_city": "الرياض",
        "shipping_district": "العليا",
        "shipping_street": "شارع الاختبار",
        "shipping_country": "السعودية",
        "total_amount": 134.0,
        "currency": "SAR",
        "products": [{
            "id": "line-1",
            "product_id": "product-1",
            "sku": "AMS12095",
            "name": "قلادة",
            "quantity": 1,
            "custom_fields": [{"name": "هل تريد إضافة كرت اهداء", "value": "لا"}],
        }],
        # Simulates the reduced provider raw snapshot that previously replaced
        # the richer webhook payload.
        "raw_by_source": {
            "salla_direct": {
                "id": "salla-internal-1",
                "reference_id": "274682897",
                "date": "2026-07-28T12:00:00+03:00",
                "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
                "shipping": {},
                "shipments": [{"shipping_address": {}}],
                "items": [],
            }
        },
    }

    discovery = MongoOrderRepository._to_discovery_row(row)
    assert discovery is not None
    order = map_salla_order(discovery.salla_raw)

    assert order.shipping.company == "iMile"
    assert order.shipping.address.city == "الرياض"
    assert order.shipping.address.district == "العليا"
    assert order.shipping.address.street == "شارع الاختبار"
    assert order.shipping.address.country == "السعودية"
    assert order.payment.receipt_url == "https://cdn.salla.sa/receipt.jpg"
    assert order.items[0].custom_fields[0]["value"] == "لا"


def test_fulfillment_review_uses_orders_v2_read_model_without_order_resync():
    source = inspect.getsource(make_order_review_router)

    assert "resync_single_order" not in source
    assert "_refresh_review_source_once" not in source
    assert "return await _detail(db, merchant_id, order)" in source
'''
if "test_v2_read_model_restores_durable_shipping_receipt_and_items" not in tests:
    tests += new_tests
REVIEW_TESTS.write_text(tests, encoding="utf-8")

print("Orders V2 canonical shipping read-model patch applied.")
