"""Persist non-empty Order Details shipping fields in the V2 canonical record."""
from pathlib import Path


SERVICE = Path("backend/order_engine/salla_refresh.py")
TESTS = Path("backend/tests/test_order_engine_salla_refresh.py")

service = SERVICE.read_text(encoding="utf-8")
old = '''        now = datetime.now(timezone.utc)
        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": normalized},
            {"$set": {
                REFRESH_TIMESTAMP_FIELD: now.isoformat(),
                REFRESH_MODE_FIELD: "orders_v2_central_refresh",
                REFRESH_ENDPOINT_FIELD: "GET /orders/{id}?format=light + GET /orders/items",
                REFRESH_ITEMS_FIELD: len(items),
                "orders_v2_salla_address_source": address_source,
            }},
        )
'''
new = '''        now = datetime.now(timezone.utc)
        canonical_updates: dict[str, Any] = {
            REFRESH_TIMESTAMP_FIELD: now.isoformat(),
            REFRESH_MODE_FIELD: "orders_v2_central_refresh",
            REFRESH_ENDPOINT_FIELD: "GET /orders/{id}?format=light + GET /orders/items",
            REFRESH_ITEMS_FIELD: len(items),
            "orders_v2_salla_address_source": address_source,
        }
        # An explicit Orders V2 refresh is authoritative for non-empty delivery
        # facts returned by Order Details. Persist them at the canonical root so
        # a later light list sync cannot make the address disappear again.
        for key, value in shipping_fields.items():
            if key == "shipping_address_found" or _present(value):
                canonical_updates[key] = deepcopy(value)

        await db.unified_orders.update_one(
            {"user_id": str(user_id), "order_number": normalized},
            {"$set": canonical_updates},
        )
'''
if old not in service:
    raise SystemExit("central refresh metadata update block not found")
service = service.replace(old, new, 1)
SERVICE.write_text(service, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
marker = '''    assert raw["shipping"]["address"]["country"] == "السعودية"
    assert len(raw["items"]) == 1
'''
replacement = '''    assert raw["shipping"]["address"]["country"] == "السعودية"
    assert len(raw["items"]) == 1
    assert db.unified_orders.row["shipping_company"] == "iMile"
    assert db.unified_orders.row["shipping_city"] == "الرياض"
    assert db.unified_orders.row["shipping_country"] == "السعودية"
    assert db.unified_orders.row["shipping_address"] == "حي العليا، طريق الملك فهد"
'''
if marker not in tests:
    raise SystemExit("central refresh persistence test marker not found")
tests = tests.replace(marker, replacement, 1)
TESTS.write_text(tests, encoding="utf-8")

print("Authoritative Orders V2 shipping persistence applied.")
