from pathlib import Path


route_path = Path("backend/supplier_receiving_routes.py")
route_source = route_path.read_text(encoding="utf-8")
required_route_markers = (
    "def supplier_mezan_product_reference_price(",
    '"product_price_authority": "mezan_v2"',
    '"salla_price_fallback_allowed": False',
    '"supplier_receiving_mezan_product_price_required"',
)
missing_markers = [
    marker for marker in required_route_markers if marker not in route_source
]
if missing_markers:
    raise SystemExit(
        "Mezan-only supplier-price implementation is incomplete: "
        + ", ".join(missing_markers)
    )


def replace_in_function(
    source: str,
    function_name: str,
    old: str,
    new: str,
) -> str:
    start_marker = f"def {function_name}("
    start = source.find(start_marker)
    if start < 0:
        raise SystemExit(f"test function not found: {function_name}")
    next_sync = source.find("\ndef ", start + len(start_marker))
    next_async = source.find("\nasync def ", start + len(start_marker))
    candidates = [index for index in (next_sync, next_async) if index >= 0]
    end = min(candidates) if candidates else len(source)
    block = source[start:end]
    if new in block:
        return source
    if block.count(old) != 1:
        raise SystemExit(
            f"fixture anchor for {function_name} expected once, "
            f"found {block.count(old)}"
        )
    block = block.replace(old, new, 1)
    return source[:start] + block + source[end:]


test_path = Path("backend/tests/test_supplier_receiving.py")
tests = test_path.read_text(encoding="utf-8")

fixture_patches = (
    (
        "test_invoice_draft_separates_product_and_service_prices",
        '        "reference_product_unit_price_halalas": 500,\n',
        '        "reference_product_unit_price_halalas": 500,\n'
        '        "reference_product_price_complete": True,\n'
        '        "reference_product_price_source": "mezan_v2_base",\n',
    ),
    (
        "test_invoice_draft_allows_normally_priced_product_without_services",
        '            "reference_product_unit_price_halalas": 1500,\n',
        '            "reference_product_unit_price_halalas": 1500,\n'
        '            "reference_product_price_complete": True,\n'
        '            "reference_product_price_source": "mezan_v2_base",\n',
    ),
    (
        "test_invoice_rejects_grouping_pieces_with_different_pending_services",
        '        "reference_product_unit_price_halalas": 500,\n',
        '        "reference_product_unit_price_halalas": 500,\n'
        '        "reference_product_price_complete": True,\n'
        '        "reference_product_price_source": "mezan_v2_base",\n',
    ),
    (
        "test_price_overrides_require_permissions_and_additions_use_dedicated_route",
        '        "reference_product_unit_price_halalas": 500,\n',
        '        "reference_product_unit_price_halalas": 500,\n'
        '        "reference_product_price_complete": True,\n'
        '        "reference_product_price_source": "mezan_v2_base",\n',
    ),
)

for function_name, old, new in fixture_patches:
    tests = replace_in_function(tests, function_name, old, new)

test_path.write_text(tests, encoding="utf-8")
