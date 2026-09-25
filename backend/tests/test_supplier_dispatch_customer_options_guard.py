from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import unicodedata

import fitz
import pytest
from fastapi import HTTPException

from supplier_dispatch_pdf import (
    _assert_order_customer_options_preserved,
    _assert_saved_customer_options_preserved,
    build_supplier_dispatch_pdf,
)
from preparation_piece_operations import PIECES
from reviewed_preparation_batches import BATCHES


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
        note="دام عزك",
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
    assert "note" in exc.value.detail["missing_fields"]


def test_supplier_guard_rejects_blank_snapshot_for_bag_with_three_order_choices():
    identity = SimpleNamespace(
        options=[
            SimpleNamespace(name="اختر", value="اسود"),
            SimpleNamespace(name="هل ترغب بإضافة كرت اهداء برسالة مخصصة؟", value="نعم"),
            SimpleNamespace(name="الكلام على الكرت", value="لانك تستحقين لولو"),
        ],
        options_raw=[], options_normalized={}, custom_fields=[],
        color=None, size=None, material=None,
    )
    source = {
        "order_number": "288457267", "order_item_id": "item-1", "file_spec_fields": [],
    }
    with pytest.raises(HTTPException) as exc:
        _assert_order_customer_options_preserved(source, identity, {}, piece=_piece())
    assert exc.value.status_code == 409
    assert len(exc.value.detail["mismatched_spec_keys"]) == 3


def test_supplier_guard_accepts_genuinely_optionless_order_line():
    identity = SimpleNamespace(
        options=[], options_raw=[], options_normalized={}, custom_fields=[],
        color=None, size=None, material=None,
    )
    _assert_order_customer_options_preserved(
        {"order_number": "100", "order_item_id": "item-1", "file_spec_fields": []},
        identity, {}, piece=_piece(),
    )


def test_supplier_guard_accepts_all_three_saved_bag_choices():
    options = [
        SimpleNamespace(name="اختر", value="اسود"),
        SimpleNamespace(name="هل ترغب بإضافة كرت اهداء برسالة مخصصة؟", value="نعم"),
        SimpleNamespace(name="الكلام على الكرت", value="لانك تستحقين لولو"),
    ]
    identity = SimpleNamespace(
        options=options, options_raw=[], options_normalized={}, custom_fields=[],
        color=None, size=None, material=None,
    )
    _assert_order_customer_options_preserved(
        {"order_number": "288457267", "order_item_id": "item-1", "file_spec_fields": [
            {"name": option.name, "value": option.value} for option in options
        ]},
        identity, {}, piece=_piece(),
    )


@pytest.mark.asyncio
async def test_supplier_pdf_prints_all_saved_fields_in_salla_order_without_ellipsis(monkeypatch):
    fields = [
        {"name": "العبارة خارج الفنجان", "value": "Samaher"},
        {"name": "العبارة داخل الفنجان على الجانب", "value":
         "ألا ياغزال في عيونك سهوم الموت وفي مبسمك جنة وفي شوفتك راحه"},
        {"name": "العبارة داخل الفنجان في الأسفل", "value": "من بعدك ياغزال"},
    ]
    import supplier_dispatch_pdf as supplier_pdf_module
    monkeypatch.setattr(supplier_pdf_module, "MongoOrderRepository", lambda _db: object())
    async def canonical_order(*_args, **_kwargs):
        return SimpleNamespace(order_number="288180853")
    monkeypatch.setattr(supplier_pdf_module, "get_order", canonical_order)
    monkeypatch.setattr(supplier_pdf_module, "map_order_item_identities", lambda _order: [
        SimpleNamespace(
            order_item_id="item-one", options=[
                SimpleNamespace(name=field["name"], value=field["value"])
                for field in fields
            ], options_raw=[], options_normalized={}, custom_fields=[],
            color=None, size=None, material=None,
        ),
    ])
    from order_review_routes import WORKFLOWS
    db = {PIECES: MagicMock(), BATCHES: MagicMock(), WORKFLOWS: MagicMock()}
    db[WORKFLOWS].find_one = AsyncMock(return_value={"items": []})
    db[PIECES].find.return_value.to_list = AsyncMock(return_value=[{
        "user_id": "merchant", "piece_id": "piece-one", "batch_id": "batch-one",
        "order_item_id": "item-one", "unit_index": 1,
    }])
    db[BATCHES].find.return_value.to_list = AsyncMock(return_value=[{
        "user_id": "merchant", "id": "batch-one", "lines": [{
            "order_number": "288180853", "order_item_id": "item-one", "unit_index": 1,
            "file_spec_fields": fields, "order_date": "2026-09-23",
        }],
    }])
    pdf = await build_supplier_dispatch_pdf(db, user_id="merchant", dispatch={
        "piece_ids": ["piece-one"], "supplier_name": "المورد",
        "sent_by_name": "الموظف", "file_number": "SUP-96",
    })
    with fitz.open(stream=pdf, filetype="pdf") as document:
        page = document[0]
        printed = unicodedata.normalize("NFKC", page.get_text())
        assert [printed.index(field["name"]) for field in fields] == sorted(
            printed.index(field["name"]) for field in fields
        )
        assert "Samaher" in printed
        assert "ألا ياغزال في عيونك سهوم الموت" in printed.replace("\n", " ")
        assert "وفي مبسمك جنة وفي شوفتك راحه" in printed.replace("\n", " ")
        assert "من بعدك ياغزال" in printed.replace("\n", " ")
        assert "…" not in printed and "..." not in printed
        assert len(page.get_images(full=True)) >= 1
        assert max(block[3] for block in page.get_text("blocks")) < page.rect.height / 3
