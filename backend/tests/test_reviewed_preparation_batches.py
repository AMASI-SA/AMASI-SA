import io
from datetime import datetime, timezone
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

import reviewed_preparation_batches as batch_module
from order_review_forward_stage_guard import (
    FORWARD_FULFILLMENT_STAGES,
    install_order_review_forward_stage_guard,
)
from order_review_routes import EVENTS, REVIEW_COMPLETED_STAGES, WORKFLOWS
from preparation_pdf import ProductLine, _register_font
from preparation_pdf_reference_layout import (
    REFERENCE_CARDS_PER_PAGE,
    REFERENCE_COLUMNS,
    REFERENCE_ROWS,
    generate_reference_preparation_pdf,
    image_candidate_urls,
    reference_card_rows,
)
from preparation_pdf_wrapped_text import (
    build_wrapped_specification_plan,
    generate_wrapped_reference_preparation_pdf,
    wrap_reference_text,
)
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from reviewed_preparation_v3 import stable_ready_item_id
from reviewed_preparation_batches import (
    _batch_response,
    _card_field_projection,
    _finalize_batch_assignment,
    _review_snapshot_identity,
    _reviewed_ready_identity,
    _reconcile_order_stage,
    make_reviewed_preparation_batches_router,
    plan_preparation_allocations,
    repair_batch_line_customer_options,
    render_preparation_batch_pdf,
)


# Production installs this through Order Engine startup. Install it explicitly
# in the focused contract suite so PDF regeneration uses the same renderer.
install_order_review_forward_stage_guard()


def _product(group_key, quantity, source_lines):
    return {
        "group_key": group_key,
        "name": f"منتج {group_key}",
        "quantity": quantity,
        "remaining_quantity": quantity,
        "source_lines": source_lines,
    }


def _line(order_number, order_item_id, quantity, start=1):
    return {
        "order_number": order_number,
        "order_item_id": order_item_id,
        "quantity": quantity,
        "available_unit_indices": list(range(start, start + quantity)),
    }


def _image_bytes(index=0):
    image = Image.new(
        "RGB",
        (260, 260),
        ((index * 37) % 255, (index * 71 + 50) % 255, (index * 19 + 90) % 255),
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _reference_line(index=1):
    return ProductLine(
        order_number=str(275800000 + index),
        order_date="2026-08-02T10:00:00+03:00",
        product_name=f"اسم المنتج التجريبي {index}",
        customer_name="سارة",
        note="رسالة هدية",
        quantity=1,
        total_products_in_order=2,
        item_index=index - 1,
        image_bytes=_image_bytes(index),
        image_mime="image/jpeg",
        shipping_company="iMile",
        size="18",
        color="ذهبي",
        product_id=f"product-{index}",
        sku=f"SKU-{index}",
        product_options={"الخامة": "ستانلس"},
    )



def test_normal_reviewed_ready_identity_does_not_remap_live_salla_line():
    order = SimpleNamespace(
        order_id="order-200",
        order_number="200",
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        source=SimpleNamespace(source_order_id="salla-order-200"),
    )
    workflow = {"items": [{
        "order_item_id": "reviewed-line-200",
        "product_id": "p-ready",
        "sku": "READY-1",
        "product_name": "منتج جاهز",
        "quantity": 3,
        "specifications_snapshot": {"المقاس": "كبير", "اللون": "ذهبي"},
    }]}
    line = {
        "identity_source": "reviewed_ready",
        "order_number": "200",
        "order_item_id": "reviewed-line-200",
        "line_index": 0,
        "quantity": 3,
        "product_id": "p-ready",
        "product_name": "منتج جاهز",
        "sku": "READY-1",
        "ready_item_identity": {
            "order_item_id": "reviewed-line-200",
            "product_id": "p-ready",
            "sku": "READY-1",
            "quantity": 3,
            "product_name": "منتج جاهز",
            "options": {"المقاس": "كبير", "اللون": "ذهبي"},
        },
    }
    line["ready_item_id"] = stable_ready_item_id(line)
    allocation = {
        "order_item_id": "reviewed-line-200",
        "ready_item_id": line["ready_item_id"],
        "quantity": 3,
        "line": line,
    }

    resolved = _reviewed_ready_identity(order, workflow, allocation)

    assert resolved is not None
    identity, state = resolved
    assert identity.order_item_id == "reviewed-line-200"
    assert identity.quantity == 3
    assert {row.name: row.value for row in identity.options} == {
        "المقاس": "كبير",
        "اللون": "ذهبي",
    }
    assert state is workflow["items"][0]

    changed = {
        **allocation,
        "line": {**line, "line_index": 1},
    }
    assert _reviewed_ready_identity(order, workflow, changed) is None


def test_full_selected_products_allocate_every_available_piece_into_one_plan():
    first = _product(
        "product:p-1",
        5,
        [
            {**_line("100", "line-a", 2), "ready_item_id": "ready-a"},
            {**_line("101", "line-b", 3), "ready_item_id": "ready-b"},
        ],
    )
    second = _product(
        "product:p-2",
        2,
        [{**_line("102", "line-c", 2), "ready_item_id": "ready-c"}],
    )

    result = plan_preparation_allocations(
        [first, second],
        [
            {"group_key": "product:p-1", "quantity": 5},
            {"group_key": "product:p-2", "quantity": 2},
        ],
    )

    assert sum(row["quantity"] for row in result) == 7
    assert [
        (row["order_item_id"], row["quantity"], row["ready_item_id"])
        for row in result
    ] == [
        ("line-a", 2, "ready-a"),
        ("line-b", 3, "ready-b"),
        ("line-c", 2, "ready-c"),
    ]


def test_review_snapshot_recovery_identity_keeps_stale_guard_strict():
    order = SimpleNamespace(
        order_id="order-279803951",
        order_number="279803951",
        created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        source=SimpleNamespace(source_order_id="salla-order-1"),
    )
    workflow = {"items": [{
        "product_id": "p-dress",
        "sku": "AMS11353",
        "product_name": "فستان بناتي أخضر",
        "quantity": 2,
        "options": [{"name": "المقاس", "value": "10 سنوات"}],
    }]}
    allocation = {
        "order_item_id": "review-snapshot:279803951:0",
        "quantity": 2,
        "line": {
            "identity_source": "review_snapshot",
            "review_snapshot_index": 0,
            "line_index": 0,
            "product_id": "p-dress",
            "sku": "AMS11353",
            "product_name": "فستان بناتي أخضر",
        },
    }

    recovered = _review_snapshot_identity(order, workflow, allocation)

    assert recovered is not None
    identity, state = recovered
    assert identity.order_item_id == "review-snapshot:279803951:0"
    assert identity.product_id == "p-dress"
    assert identity.sku == "AMS11353"
    assert identity.options[0].value == "10 سنوات"
    assert state is workflow["items"][0]

    changed = {**allocation, "line": {**allocation["line"], "sku": "OTHER"}}
    assert _review_snapshot_identity(order, workflow, changed) is None

    workflow["items"][0]["order_item_id"] = "historical-line-1"
    historical = {
        **allocation,
        "order_item_id": "historical-line-1",
    }
    assert _review_snapshot_identity(order, workflow, historical) is not None


def test_review_snapshot_allows_proven_canonical_sku_enrichment():
    order = SimpleNamespace(
        order_id="order-13062",
        order_number="13062",
        created_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        source=SimpleNamespace(source_order_id="salla-order-13062"),
    )
    workflow = {"items": [{
        "order_item_id": "historical-line-13062",
        "source_item_id": "source-line-13062",
        "product_id": "variant-product-13062",
        "product_name": "فستان بناتي أنيق",
        "quantity": 7,
        # Historical review snapshot was sparse before Product V2 enrichment.
        "sku": None,
    }]}
    allocation = {
        "order_item_id": "historical-line-13062",
        "quantity": 7,
        "line": {
            "identity_source": "review_snapshot",
            "review_snapshot_index": 0,
            "product_id": "canonical-product-13062",
            "sku": "AMS13062",
            "product_name": "فستان بناتي أنيق",
            "review_snapshot_identity": {
                "order_item_id": "historical-line-13062",
                "source_item_id": "source-line-13062",
                "product_id": "variant-product-13062",
                "sku": "",
                "quantity": 7,
            },
        },
    }

    recovered = _review_snapshot_identity(order, workflow, allocation)

    assert recovered is not None
    identity, _state = recovered
    assert identity.order_item_id == "historical-line-13062"
    assert identity.product_id == "canonical-product-13062"
    assert identity.sku == "AMS13062"

    changed_snapshot = {
        **allocation,
        "line": {
            **allocation["line"],
            "review_snapshot_identity": {
                **allocation["line"]["review_snapshot_identity"],
                "product_id": "unrelated-product",
            },
        },
    }
    assert _review_snapshot_identity(order, workflow, changed_snapshot) is None


@pytest.mark.asyncio
async def test_batch_success_requires_employee_piece_registry_ready(monkeypatch):
    import preparation_file_registry as registry_module

    responses = iter([
        {
            "status": "ready",
            "piece_registry_status": "recovery_required",
            "responsible_employee_id": "employee-1",
        },
        {
            "status": "ready",
            "piece_registry_status": "ready",
            "responsible_employee_id": "employee-1",
        },
    ])
    calls = []

    async def finalize(*_args, **_kwargs):
        calls.append(True)
        return next(responses)

    monkeypatch.setattr(registry_module, "_finalize_registry_row", finalize)

    result = await _finalize_batch_assignment(
        object(),
        user_id="owner-1",
        client_request_id="request-123",
        actor={"id": "reviewer-1"},
    )

    assert result["piece_registry_status"] == "ready"
    assert len(calls) == 2


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return list(self.rows[:length] if length else self.rows)


class _Collection:
    def __init__(self, *, one=None, rows=None):
        self.one = one
        self.rows = list(rows or [])
        self.last_update = None
        self.inserted = []

    async def find_one(self, *_args, **_kwargs):
        return dict(self.one) if isinstance(self.one, dict) else self.one

    def find(self, *_args, **_kwargs):
        return _Cursor(self.rows)

    async def update_one(self, _selector, update, **_kwargs):
        self.last_update = update
        if isinstance(self.one, dict):
            self.one.update(update.get("$set") or {})
            self.one["revision"] = int(self.one.get("revision") or 0) + int((update.get("$inc") or {}).get("revision") or 0)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def insert_one(self, row):
        self.inserted.append(dict(row))
        return SimpleNamespace(inserted_id="event-1")


class _DB:
    def __init__(self, workflow, allocations):
        self.collections = {
            WORKFLOWS: _Collection(one=workflow),
            PREPARATION_UNIT_ALLOCATIONS: _Collection(rows=allocations),
            EVENTS: _Collection(),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_select_thirty_of_fifty_across_deterministic_order_lines():
    products = [
        _product("product:p-1", 50, [
            _line("100", "line-a", 20),
            _line("101", "line-b", 30),
        ]),
    ]

    result = plan_preparation_allocations(
        products,
        [{"group_key": "product:p-1", "quantity": 30}],
    )

    assert [(row["order_number"], row["quantity"]) for row in result] == [
        ("100", 20),
        ("101", 10),
    ]
    assert result[1]["unit_indices"] == list(range(1, 11))


def test_full_second_product_can_share_same_file():
    products = [
        _product("product:p-1", 50, [_line("100", "line-a", 50)]),
        _product("product:p-2", 10, [_line("200", "line-b", 10)]),
    ]

    result = plan_preparation_allocations(products, [
        {"group_key": "product:p-1", "quantity": 30},
        {"group_key": "product:p-2", "quantity": 10},
    ])

    assert sum(row["quantity"] for row in result) == 40
    assert {row["group_key"] for row in result} == {
        "product:p-1",
        "product:p-2",
    }


def test_allocation_uses_only_free_unit_indices():
    product = _product(
        "product:p-1",
        2,
        [{
            "order_number": "100",
            "order_item_id": "line-a",
            "quantity": 5,
            "allocated_unit_indices": [1, 2, 3],
            "available_unit_indices": [4, 5],
        }],
    )

    result = plan_preparation_allocations(
        [product],
        [{"group_key": "product:p-1", "quantity": 2}],
    )

    assert result[0]["unit_indices"] == [4, 5]


def test_quantity_above_remaining_is_rejected():
    with pytest.raises(ValueError, match="preparation_quantity_exceeds_remaining"):
        plan_preparation_allocations(
            [_product("product:p-1", 20, [_line("100", "line-a", 20)])],
            [{"group_key": "product:p-1", "quantity": 21}],
        )


def test_duplicate_group_is_rejected():
    with pytest.raises(ValueError, match="duplicate_product_group"):
        plan_preparation_allocations(
            [_product("product:p-1", 20, [_line("100", "line-a", 20)])],
            [
                {"group_key": "product:p-1", "quantity": 10},
                {"group_key": "product:p-1", "quantity": 5},
            ],
        )


def test_file_fields_preserve_name_color_size_and_notes():
    projected = _card_field_projection([
        {"spec_key": "name", "name": "الاسم", "value": "سارة"},
        {"spec_key": "color", "name": "اللون", "value": "ذهبي"},
        {"spec_key": "size", "name": "المقاس", "value": "18"},
        {"spec_key": "message", "name": "رسالة الإهداء", "value": "مبروك"},
        {"spec_key": "material", "name": "الخامة", "value": "ستانلس"},
    ], "انتبه للتغليف")

    assert projected["customer_name"] == "سارة"
    assert projected["color"] == "ذهبي"
    assert projected["size"] == "18"
    assert "مبروك" in projected["note"]
    assert "انتبه للتغليف" in projected["note"]
    assert projected["product_options"] == {"الخامة": "ستانلس"}


def test_reference_card_uses_full_labels_and_confirmed_field_order():
    rows = reference_card_rows(_reference_line())
    labels = [label for label, _value in rows]

    assert labels[:5] == ["الاسم", "المقاس", "اللون", "الخامة", "ملاحظة"]
    assert labels[-4:] == ["ط", "تاريخ", "الكمية", "للتوصيل"]
    assert "ك" not in labels
    assert rows[-3] == ("تاريخ", "2026-08-02")
    assert rows[-2] == ("الكمية", "1")
    assert rows[-1] == ("للتوصيل", "2 - iMile")


def test_long_note_wraps_without_ellipsis_or_word_loss():
    note = "تجربة ملف تجهيز طويلة للتأكد من ظهور الملاحظة كاملة دون اختفاء أي كلمة داخل البطاقة"
    font_name, font_bold = _register_font()
    wrapped = wrap_reference_text(
        note,
        font_name=font_name,
        font_size=6.6,
        first_width=42,
        continuation_width=66,
    )
    plan = build_wrapped_specification_plan(
        [("ملاحظة", note)],
        font_name=font_name,
        font_bold=font_bold,
        width=66,
        available_height=58,
    )

    assert len(wrapped) > 1
    assert " ".join(wrapped) == note
    assert "…" not in " ".join(wrapped)
    assert " ".join(plan.fields[0].lines) == note
    assert plan.physical_line_count > 1


def test_wrapped_note_keeps_fifteen_card_page_density():
    lines = [_reference_line(index) for index in range(1, 16)]
    for line in lines:
        line.note = "اكتب العبارة كاملة داخل الكرت مع تغليف المنتج بعناية وعدم حذف أي جزء من الملاحظة"
    pdf = generate_wrapped_reference_preparation_pdf(lines)
    document = fitz.open(stream=pdf, filetype="pdf")

    assert document.page_count == 1
    assert len(document[0].get_images(full=True)) >= 24


def test_reference_pdf_is_three_columns_by_five_rows_with_images_and_qr():
    assert REFERENCE_COLUMNS == 3
    assert REFERENCE_ROWS == 5
    assert REFERENCE_CARDS_PER_PAGE == 15

    pdf = generate_reference_preparation_pdf([
        _reference_line(index) for index in range(1, 15)
    ])
    document = fitz.open(stream=pdf, filetype="pdf")

    assert document.page_count == 1
    # Fourteen unique product images plus fourteen unique QR images. PyMuPDF
    # may consolidate a small number of resources, so require at least 24.
    assert len(document[0].get_images(full=True)) >= 24
    pixmap = document[0].get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    assert pixmap.width > 500
    assert pixmap.height > 800


def test_sixteenth_reference_card_starts_a_second_page():
    pdf = generate_reference_preparation_pdf([
        _reference_line(index) for index in range(1, 17)
    ])
    document = fitz.open(stream=pdf, filetype="pdf")
    assert document.page_count == 2


def test_image_candidates_skip_relative_mezan_then_fall_back_to_salla():
    identity = SimpleNamespace(
        image_url="https://cdn.salla.sa/main.jpg",
        image_urls=[
            "/api/order-reviews-v1/mezan-images/abc",
            "https://cdn.salla.sa/gallery.jpg",
        ],
    )
    candidates = image_candidate_urls(
        {"selected_image_url": "/api/order-reviews-v1/mezan-images/abc"},
        identity,
        {"image_url": "https://cdn.salla.sa/source.jpg"},
    )

    assert candidates[0].startswith("/api/order-reviews-v1/mezan-images/")
    assert "https://cdn.salla.sa/main.jpg" in candidates
    assert "https://cdn.salla.sa/source.jpg" in candidates


def test_batch_snapshot_can_regenerate_pdf():
    pdf = render_preparation_batch_pdf({
        "id": "batch-1",
        "title": "تجهيز المنتجات",
        "lines": [{
            "order_number": "275678403",
            "order_date": "2026-08-02",
            "product_name": "سلسال بالاسم",
            "customer_name": "سارة",
            "note": "هدية",
            "quantity": 2,
            "total_products_in_order": 3,
            "line_index": 0,
            "image_b64": __import__("base64").b64encode(_image_bytes(9)).decode("ascii"),
            "image_mime": "image/jpeg",
            "shipping_company": "iMile",
            "size": "18",
            "color": "ذهبي",
            "product_options": {"الخامة": "ستانلس"},
        }],
    })

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
    document = fitz.open(stream=pdf, filetype="pdf")
    assert document.page_count == 1
    assert len(document[0].get_images(full=True)) >= 2


@pytest.mark.asyncio
async def test_partial_file_keeps_order_in_reviewed(monkeypatch):
    workflow = {
        "user_id": "owner-1",
        "order_number": "100",
        "stage": "reviewed",
        "revision": 1,
        "items": [],
    }
    order = SimpleNamespace(
        order_number="100",
        items=[SimpleNamespace(order_item_id="line-a", quantity=2)],
    )
    db = _DB(workflow, [
        {"status": "committed", "order_item_id": "line-a", "unit_index": 1},
    ])

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, workflow)]}

    monkeypatch.setattr(batch_module, "load_reviewed_product_context", context)
    complete, remaining = await _reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="100",
        batch_id="batch-1",
        actor={"id": "employee-1", "name": "موظف"},
    )

    assert complete is False
    assert remaining == 1
    update = db[WORKFLOWS].last_update
    assert update["$set"]["preparation_progress"]["remaining_quantity"] == 1
    assert "stage" not in update["$set"]
    assert db[EVENTS].inserted == []


@pytest.mark.asyncio
async def test_final_unit_moves_order_to_in_progress(monkeypatch):
    workflow = {
        "user_id": "owner-1",
        "order_number": "100",
        "stage": "reviewed",
        "revision": 1,
        "items": [],
    }
    order = SimpleNamespace(
        order_number="100",
        items=[SimpleNamespace(order_item_id="line-a", quantity=2)],
    )
    db = _DB(workflow, [
        {"status": "committed", "order_item_id": "line-a", "unit_index": 1},
        {"status": "committed", "order_item_id": "line-a", "unit_index": 2},
    ])

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, workflow)]}

    monkeypatch.setattr(batch_module, "load_reviewed_product_context", context)
    complete, remaining = await _reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="100",
        batch_id="batch-2",
        actor={"id": "employee-1", "name": "موظف"},
    )

    assert complete is True
    assert remaining == 0
    update = db[WORKFLOWS].last_update
    assert update["$set"]["stage"] == "in_progress"
    assert update["$set"]["preparation_progress"]["remaining_quantity"] == 0
    assert db[EVENTS].inserted[0]["event_type"] == "order_moved_to_in_progress"
    assert db[EVENTS].inserted[0]["salla_updated"] is False


@pytest.mark.asyncio
async def test_failed_batch_release_restores_in_progress_order_to_reviewed(monkeypatch):
    workflow = {
        "user_id": "owner-1",
        "order_number": "100",
        "stage": "in_progress",
        "reviewed_at": "2026-08-25T10:00:00+00:00",
        "revision": 2,
        "items": [],
    }
    order = SimpleNamespace(
        order_number="100",
        items=[SimpleNamespace(order_item_id="line-a", quantity=2)],
    )
    db = _DB(workflow, [
        {"status": "committed", "order_item_id": "line-a", "unit_index": 1},
    ])

    async def reviewed_context(*_args, **_kwargs):
        return {"pairs": []}

    async def canonical_order(*_args, **_kwargs):
        return order

    monkeypatch.setattr(batch_module, "load_reviewed_product_context", reviewed_context)
    monkeypatch.setattr(batch_module, "MongoOrderRepository", lambda _db: object())
    monkeypatch.setattr(batch_module, "get_order", canonical_order)

    complete, remaining = await _reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="100",
        batch_id="",
        actor={"id": "owner-1", "name": "المالك"},
    )

    assert complete is False
    assert remaining == 1
    update = db[WORKFLOWS].last_update
    assert update["$set"]["stage"] == "reviewed"
    assert update["$set"]["preparation_progress"]["remaining_quantity"] == 1
    assert "preparation_batch_ids" not in update.get("$addToSet", {})
    assert "in_progress_at" in update["$unset"]
    assert db[EVENTS].inserted[0]["event_type"] == (
        "order_restored_to_reviewed_after_failed_preparation_file"
    )


def test_old_batch_line_options_are_rebuilt_from_canonical_order_item():
    identity = SimpleNamespace(
        order_item_id="line-a",
        line_index=0,
        sku="SKU-1",
        options=[
            SimpleNamespace(name="المقاس", value="من 3 إلى 6 أشهر"),
            SimpleNamespace(name="الاسم", value="سلمان"),
        ],
        custom_fields=[],
        color=None,
        size=None,
        material=None,
    )
    repaired, count, unresolved = repair_batch_line_customer_options(
        [{
            "order_number": "100",
            "order_item_id": "line-a",
            "line_index": 0,
            "sku": "SKU-1",
            "file_spec_fields": [],
            "product_options": {},
        }],
        identities_by_order={"100": [identity]},
        workflows_by_order={"100": {"items": []}},
    )

    assert count == 1
    assert unresolved == []
    assert repaired[0]["size"] == "من 3 إلى 6 أشهر"
    assert repaired[0]["customer_name"] == "سلمان"
    assert len(repaired[0]["file_spec_fields"]) == 2


def test_forward_stages_freeze_review_mutations():
    previous = set(REVIEW_COMPLETED_STAGES)
    try:
        install_order_review_forward_stage_guard()
        assert FORWARD_FULFILLMENT_STAGES.issubset(REVIEW_COMPLETED_STAGES)
    finally:
        REVIEW_COMPLETED_STAGES.clear()
        REVIEW_COMPLETED_STAGES.update(previous)


def test_batch_response_is_mezan_only_and_exposes_transitions():
    response = _batch_response({
        "id": "batch-1",
        "status": "ready",
        "file_name": "ملف.pdf",
        "allocated_quantity": 40,
        "selected_product_count": 2,
        "card_count": 2,
        "order_count": 2,
        "transitioned_order_numbers": ["100"],
        "remaining_review_order_numbers": ["101"],
    })

    assert response["ok"] is True
    assert response["allocated_quantity"] == 40
    assert response["transitioned_order_numbers"] == ["100"]
    assert response["salla_updated"] is False
    assert response["qoyod_updated"] is False


def test_router_registers_create_list_and_download_routes():
    router = make_reviewed_preparation_batches_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/reviewed-preparation-batches-v1/batches", "POST") in routes
    assert ("/reviewed-preparation-batches-v1/batches", "GET") in routes
    assert (
        "/reviewed-preparation-batches-v1/batches/{batch_id}/repair-customer-options",
        "POST",
    ) in routes
    assert (
        "/reviewed-preparation-batches-v1/batches/{batch_id}/pdf",
        "GET",
    ) in routes
