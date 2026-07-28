"""Repair the one-shot V2 address patch before it is applied.

This file is temporary and is removed before merge. It adjusts two things in the
branch bootstrap only:
1. Provider address must remain authoritative over root-field fallback data.
2. The repository test double must understand the current `$and` keyset query.
"""
from pathlib import Path


PATCH_SCRIPT = Path("scripts/patch_orders_v2_root_address_fallback.py")
TESTS = Path("backend/tests/test_order_engine_repository.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


patch_source = PATCH_SCRIPT.read_text(encoding="utf-8")
patch_source = replace_once(
    patch_source,
    '''    address = _v2_address_fallback(hydrated, row)
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
''',
    '''    shipping = deepcopy(hydrated.get("shipping")) if isinstance(hydrated.get("shipping"), dict) else {}
    provider_address = None
    if isinstance(shipping.get("address"), dict) and shipping.get("address"):
        provider_address = deepcopy(shipping["address"])
    elif isinstance(hydrated.get("shipping_address"), dict) and hydrated.get("shipping_address"):
        provider_address = deepcopy(hydrated["shipping_address"])

    address = provider_address or _v2_address_fallback(hydrated, row)
    if address and provider_address is None:
        _fill_missing(hydrated, "shipping_address", address)

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
''',
    "provider address precedence",
)
PATCH_SCRIPT.write_text(patch_source, encoding="utf-8")


test_source = TESTS.read_text(encoding="utf-8")
test_source = replace_once(
    test_source,
    '''        conditions = query.get("$or") or []

        if conditions:
            before_date = conditions[0]["order_date"]["$lt"]
            exact_date = conditions[1]["order_date"]
            before_number = conditions[1]["order_number"]["$lt"]
''',
    '''        conditions = query.get("$or") or []
        if not conditions:
            for clause in query.get("$and") or []:
                if isinstance(clause, dict) and isinstance(clause.get("$or"), list):
                    conditions = clause["$or"]
                    break

        if conditions:
            before_date = conditions[0]["order_date"]["$lt"]
            exact_date = conditions[1]["order_date"]
            before_number = conditions[1]["order_number"]["$lt"]
''',
    "repository keyset test double",
)
TESTS.write_text(test_source, encoding="utf-8")

print("Orders V2 address patch repair applied.")
