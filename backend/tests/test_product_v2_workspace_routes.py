from product_v2_workspace_routes import _sku_number


def test_sku_number_extracts_sequence():
    assert _sku_number("AMS12047", "AMS") == 12047
    assert _sku_number("ams00001", "AMS") == 1


def test_sku_number_rejects_other_formats():
    assert _sku_number("SKU-12047", "AMS") is None
    assert _sku_number("AMS12A", "AMS") is None
    assert _sku_number("", "AMS") is None
