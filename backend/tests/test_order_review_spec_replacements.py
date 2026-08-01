from pathlib import Path
from types import SimpleNamespace

from order_review_spec_replacements import (
    ORDER_OVERRIDE_FIELD,
    canonical_spec_key,
    effective_spec_rows,
    extract_item_specs,
    materialize_defaults_into_state,
    replacement_override_map,
    supplier_file_spec_lines,
)


ROOT = Path(__file__).resolve().parents[2]


def item(*, order_item_id="order-1:item-1", size="54 انش"):
    return SimpleNamespace(
        order_item_id=order_item_id,
        order_id="order-1",
        order_number="1001",
        line_index=0,
        product_id="product-88",
        parent_product_id=None,
        variant_id=None,
        sku="AMS13031",
        name="عباية صيفية",
        color=None,
        size=size,
        material=None,
        options=[],
        custom_fields=[],
        source=SimpleNamespace(source_product_id="product-88"),
    )


def test_canonical_spec_key_matches_review_ui_aliases():
    assert canonical_spec_key("المقاس:") == "size"
    assert canonical_spec_key(" اللون ") == "color"
    assert canonical_spec_key("ملاحظات") == "ملاحظات"


def test_extract_item_specs_preserves_original_salla_value():
    rows = extract_item_specs(item())
    assert rows == [{
        "spec_key": "size",
        "name": "المقاس",
        "value": "54 انش",
    }]


def test_replacement_changes_file_line_only_not_original_spec():
    product = item()
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_text": "المقاس 54 انش",
        }],
    }

    rows = effective_spec_rows(product, state, {})
    assert rows[0]["original_text"] == "المقاس: 54 انش"
    assert rows[0]["replacement_text"] == "المقاس 54 انش"
    assert rows[0]["file_text"] == "المقاس 54 انش"
    assert product.size == "54 انش"
    assert supplier_file_spec_lines(product, state) == ["المقاس 54 انش"]


def test_hidden_spec_stays_out_of_supplier_file_even_with_replacement():
    state = {
        "supplier_export_excluded_spec_keys": ["المقاس"],
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_text": "المقاس 54 انش",
        }],
    }
    assert supplier_file_spec_lines(item(), state) == []


def test_order_override_has_priority_over_later_product_default():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_text": "النص المحفوظ للطلب القديم",
        }],
    }
    rows = effective_spec_rows(
        item(),
        state,
        {"size": "نص افتراضي جديد للطلبات القادمة"},
    )
    assert rows[0]["replacement_text"] == "النص المحفوظ للطلب القديم"
    assert rows[0]["replacement_source"] == "order"


def test_explicit_current_order_clear_suppresses_product_default():
    state = {
        ORDER_OVERRIDE_FIELD: [{
            "spec_key": "size",
            "replacement_text": None,
        }],
    }
    rows = effective_spec_rows(
        item(),
        state,
        {"size": "المقاس 54 انش"},
    )
    assert rows[0]["replacement_text"] is None
    assert rows[0]["replacement_source"] == "order_clear"
    assert rows[0]["file_text"] == "المقاس: 54 انش"


def test_future_default_is_snapshotted_once_into_each_order():
    product = item()
    first, changed = materialize_defaults_into_state(
        product,
        {"order_item_id": product.order_item_id},
        {"size": "المقاس 54 انش"},
    )
    assert changed is True
    assert replacement_override_map(first) == {"size": "المقاس 54 انش"}

    second, changed_again = materialize_defaults_into_state(
        product,
        first,
        {"size": "نص افتراضي تم تعديله لاحقًا"},
    )
    assert changed_again is False
    assert replacement_override_map(second) == {"size": "المقاس 54 انش"}


def test_router_is_registered_in_order_engine():
    source = (ROOT / "backend/order_engine/__init__.py").read_text(encoding="utf-8")
    assert "make_order_review_spec_replacements_router" in source
    assert "make_order_review_spec_replacements_router(db, current_user)" in source
