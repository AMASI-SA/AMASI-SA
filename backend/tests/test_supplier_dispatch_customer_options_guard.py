from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from supplier_dispatch_pdf import _assert_saved_customer_options_preserved


def _source():
    return {
        "order_number": "279700001",
        "order_item_id": "item-1",
        "file_spec_fields": [
            {"spec_key": "size", "name": "المقاس", "value": "42"},
            {"spec_key": "color", "name": "اللون", "value": "أخضر"},
            {"spec_key": "name", "name": "الاسم", "value": "محمد"},
            {"spec_key": "text", "name": "العبارة", "value": "دام عزك"},
        ],
    }


def _piece():
    return {"piece_id": "piece-1", "order_item_id": "item-1"}


def test_supplier_guard_accepts_complete_saved_customer_options():
    line = SimpleNamespace(
        customer_name="محمد",
        size="42",
        color="أخضر",
        note=None,
        product_options={"العبارة": "دام عزك"},
    )
    _assert_saved_customer_options_preserved(_source(), line, piece=_piece())


def test_supplier_guard_allows_products_without_saved_customer_options():
    source = {"order_number": "279700002", "order_item_id": "item-2", "file_spec_fields": []}
    line = SimpleNamespace(customer_name=None, size=None, color=None, note=None, product_options={})
    _assert_saved_customer_options_preserved(source, line, piece={"piece_id": "piece-2"})


def test_supplier_guard_blocks_partial_supplier_pdf():
    line = SimpleNamespace(
        customer_name="محمد",
        size=None,
        color="أخضر",
        note=None,
        product_options={},
    )
    with pytest.raises(HTTPException) as exc:
        _assert_saved_customer_options_preserved(_source(), line, piece=_piece())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "supplier_dispatch_customer_options_incomplete"
    assert "size" in exc.value.detail["missing_fields"]
    assert "product_options.العبارة" in exc.value.detail["missing_fields"]
