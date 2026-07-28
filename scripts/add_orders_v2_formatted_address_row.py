"""Show the complete address string returned by Salla Order Details in V2."""
from pathlib import Path


PAGE = Path("frontend/src/pages/OrderDetailsV2.jsx")
CONTRACT = Path("backend/tests/test_fulfillment_v2_contract.py")

page = PAGE.read_text(encoding="utf-8")
needle = '''        ["الشارع", address.street || address.street_name || address.street_number],
        ["العنوان الوطني", address.national_address || address.short_address],
'''
replacement = '''        ["الشارع", address.street || address.street_name || address.street_number],
        ["العنوان", address.formatted || address.address_line || address.address_line1 || address.description || address.location],
        ["العنوان الوطني", address.national_address || address.short_address],
'''
if needle not in page:
    raise SystemExit("Shipping address rows marker not found")
page = page.replace(needle, replacement, 1)
PAGE.write_text(page, encoding="utf-8")

contract = CONTRACT.read_text(encoding="utf-8")
if "test_orders_v2_shipping_card_shows_complete_order_address" not in contract:
    contract += '''


def test_orders_v2_shipping_card_shows_complete_order_address():
    source = (ROOT / "frontend/src/pages/OrderDetailsV2.jsx").read_text(encoding="utf-8")

    assert '["العنوان", address.formatted || address.address_line' in source
    assert 'تفاصيل العنوان لم تصل من سلة' in source
'''
CONTRACT.write_text(contract, encoding="utf-8")

print("Orders V2 complete address row added.")
