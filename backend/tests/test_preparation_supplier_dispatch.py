from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from preparation_supplier_dispatch import (
    ASSIGNMENT_STATUS_UNASSIGNED,
    BATCHES,
    CreateSupplierDispatchRequest,
    DISPATCHES,
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_READY,
    DISPATCH_STATUS_SENT,
    MEZAN_SUPPLIERS_V2,
    PIECES,
    PIECE_EVENTS,
    REGISTRY,
    RejectPreparationPiecesRequest,
    WORKFLOWS,
    _actor_id,
    _actor_name,
    _employee_workspace,
    _group_piece_products,
    _hydrate_piece_images_from_batches,
    _hydrate_legacy_piece_identity,
    _hydrate_piece_print_facts_from_batches,
    _mark_orders_started_if_fully_dispatched,
    _piece_products,
    _require_preparation_worker,
    employee_workspace_summary,
    file_is_fully_dispatched,
    make_preparation_supplier_dispatch_router,
    piece_is_available_for_supplier_dispatch,
    plan_piece_selections,
    supplier_dispatch_blocker,
    supplier_dispatch_cards,
    supplier_dispatch_lines,
    supplier_receiving_dispatch_blocker,
)


def test_mobile_merchant_principal_preserves_real_employee_identity():
    worker = {
        "id": "owner-1",
        "name": "Store Owner",
        "email": "owner@example.com",
        "_mobile_actor_id": "employee-1",
        "_mobile_actor_email": "employee@example.com",
        "_mobile_owner_id": "owner-1",
    }

    assert _actor_id(worker) == "employee-1"
    assert _actor_name(worker) == "employee@example.com"


def test_browser_principal_keeps_normal_actor_identity():
    user = {"id": "owner-1", "name": "Store Owner"}

    assert _actor_id(user) == "owner-1"
    assert _actor_name(user) == "Store Owner"


@pytest.mark.asyncio
async def test_preparation_operator_role_can_open_and_work_only_its_assignment():
    assignment_collection = MagicMock()
    assignment_collection.find_one = AsyncMock(return_value={
        "owner_user_id": "owner-1",
        "user_id": "employee-1",
        "created_by": "owner-1",
        "role_key": "preparation_operator",
        "enabled": True,
    })
    db = MagicMock()
    db.__getitem__.return_value = assignment_collection
    worker = {"id": "employee-1", "role": "viewer", "created_by": "owner-1"}

    assert await _require_preparation_worker(
        db,
        worker,
        permission="preparation.assigned.read",
    ) == worker
    assignment_collection.find_one.assert_awaited_once_with(
        {"owner_user_id": "owner-1", "user_id": "employee-1"},
        {"_id": 0},
    )
    assert await _require_preparation_worker(
        db,
        worker,
        permission="preparation.assigned.work",
    ) == worker


@pytest.mark.asyncio
async def test_unrelated_operational_role_cannot_open_employee_preparation():
    assignment_collection = MagicMock()
    assignment_collection.find_one = AsyncMock(return_value={
        "user_id": "employee-1",
        "role_key": "product_operator",
        "enabled": True,
    })
    db = MagicMock()
    db.__getitem__.return_value = assignment_collection

    with pytest.raises(HTTPException) as error:
        await _require_preparation_worker(
            db,
            {"id": "employee-1", "role": "viewer", "created_by": "owner-1"},
            permission="preparation.assigned.read",
        )

    assert error.value.status_code == 403
    assert error.value.detail["permission"] == "preparation.assigned.read"


def _piece(piece_id, group_key="product:1", unit_index=1, **patch):
    return {
        "piece_id": piece_id,
        "group_key": group_key,
        "order_number": "3001",
        "order_item_id": "item-1",
        "unit_index": unit_index,
        "status": "assigned",
        "services": [{"service_id": "engrave", "service_name": "نحت", "status": "pending"}],
        **patch,
    }


def test_product_quantity_selection_chooses_stable_physical_units():
    pieces = [
        _piece("piece-3", unit_index=3),
        _piece("piece-1", unit_index=1),
        _piece("piece-2", unit_index=2),
        _piece("other-1", group_key="product:2", unit_index=1),
    ]

    planned = plan_piece_selections(
        pieces,
        [
            {"group_key": "product:1", "quantity": 2},
            {"group_key": "product:2", "quantity": 1},
        ],
    )

    assert [row["piece_id"] for row in planned] == [
        "piece-1",
        "piece-2",
        "other-1",
    ]


def test_legacy_sku_less_pieces_join_the_single_canonical_sibling_in_memory():
    canonical = _piece(
        "canonical",
        group_key="product:ams13067",
        batch_id="batch-pf0042",
        product_name="أفرول مواليد اليوم الوطني السعودي",
        product_id="salla-product-13067",
        sku="AMS13067",
        product_options_snapshot={"الاسم": "محمد"},
    )
    legacy = _piece(
        "legacy",
        group_key="title:legacy-afrol",
        batch_id="batch-pf0042",
        product_name="  أفرول مواليد اليوم الوطني السعودي  ",
        product_id=None,
        sku=None,
        product_options_snapshot={"الاسم": "خالد"},
    )

    _hydrate_legacy_piece_identity([canonical, legacy])
    products = _group_piece_products([canonical, legacy])

    assert len(products) == 1
    assert products[0]["sku"] == "AMS13067"
    assert products[0]["available_quantity"] == 2
    assert legacy["group_key"] == canonical["group_key"]
    assert legacy["product_id"] == canonical["product_id"]
    assert legacy["product_options_snapshot"] == {"الاسم": "خالد"}


def test_legacy_identity_is_not_guessed_when_named_siblings_disagree():
    pieces = [
        _piece("one", group_key="product:1", batch_id="b1", product_name="فستان", sku="AMS1"),
        _piece("two", group_key="product:2", batch_id="b1", product_name="فستان", sku="AMS2"),
        _piece("legacy", group_key="title:dress", batch_id="b1", product_name="فستان", sku=None),
    ]

    _hydrate_legacy_piece_identity(pieces)

    assert pieces[-1]["group_key"] == "title:dress"
    assert pieces[-1].get("sku") is None


def test_same_product_with_and_without_service_uses_two_independent_cards():
    without_service = _piece(
        "plain-piece",
        unit_index=1,
        services=[],
        service_specifications_snapshot=[],
    )
    with_name_service = _piece(
        "named-piece",
        unit_index=2,
        services=[{
            "service_id": "embroider-name",
            "service_name": "تطريز الاسم",
            "status": "pending",
        }],
        service_specifications_snapshot=[{
            "spec_key": "name",
            "name": "الاسم",
            "value": "محمد",
        }],
    )

    products = _group_piece_products([without_service, with_name_service])

    assert len(products) == 2
    assert {row["available_quantity"] for row in products} == {1}
    assert {tuple(service["service_name"] for service in row["services"]) for row in products} == {
        (),
        ("تطريز الاسم",),
    }
    assert len({row["group_key"] for row in products}) == 2

    named_group = next(row for row in products if row["services"])["group_key"]
    plain_group = next(row for row in products if not row["services"])["group_key"]
    assert [row["piece_id"] for row in plan_piece_selections(
        [without_service, with_name_service],
        [{"group_key": named_group, "quantity": 1}],
    )] == ["named-piece"]
    assert [row["piece_id"] for row in plan_piece_selections(
        [without_service, with_name_service],
        [{"group_key": plain_group, "quantity": 1}],
    )] == ["plain-piece"]


def test_piece_grain_keeps_physical_units_independent_when_grouping_is_wrong():
    named = _piece(
        "named-piece",
        order_item_id="item-named",
        specifications_snapshot=[
            {"name": "هل تريد تطريز الاسم", "value": "نعم"},
            {"name": "الاسم", "value": "محمد"},
        ],
    )
    plain = _piece(
        "plain-piece",
        order_item_id="item-plain",
        specifications_snapshot=[
            {"name": "هل تريد تطريز الاسم", "value": "لا"},
        ],
    )

    products = _piece_products([named, plain])

    assert [row["group_key"] for row in products] == [
        "piece:named-piece",
        "piece:plain-piece",
    ]
    assert [row["available_quantity"] for row in products] == [1, 1]
    assert products[0]["specifications"][0]["value"] == "نعم"
    assert products[1]["specifications"][0]["value"] == "لا"
    assert [row["piece_id"] for row in plan_piece_selections(
        [named, plain],
        [{"group_key": "piece:named-piece", "quantity": 1}],
    )] == ["named-piece"]
    assert [row["piece_id"] for row in plan_piece_selections(
        [named, plain],
        [{"group_key": "piece:plain-piece", "quantity": 1}],
    )] == ["plain-piece"]


def test_stale_product_only_selection_is_rejected_when_services_are_ambiguous():
    pieces = [
        _piece("plain-piece", services=[]),
        _piece("named-piece"),
    ]

    with pytest.raises(ValueError, match="ambiguous_piece_group"):
        plan_piece_selections(
            pieces,
            [{"group_key": "product:1", "quantity": 1}],
        )


def test_rejected_or_already_sent_piece_is_not_available_for_another_dispatch():
    assert piece_is_available_for_supplier_dispatch(_piece("available")) is True
    assert piece_is_available_for_supplier_dispatch(
        _piece("archived", experiment_archived_at="2026-08-20T16:00:00Z")
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece("rejected", assignment_status=ASSIGNMENT_STATUS_UNASSIGNED)
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT)
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece(
            "partial",
            supplier_dispatch_status=DISPATCH_STATUS_PARTIAL,
            supplier_receiving_history=[{"invoice_id": "invoice-1"}],
        )
    ) is False
    assert piece_is_available_for_supplier_dispatch(
        _piece("assembly", status="ready_for_assembly")
    ) is False


def test_file_enters_execution_only_after_every_active_piece_is_dispatched():
    partial = [
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece("waiting", group_key="product:2"),
    ]
    complete = [
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece(
            "ready",
            group_key="product:2",
            supplier_dispatch_status=DISPATCH_STATUS_READY,
        ),
        _piece(
            "received",
            group_key="product:3",
            supplier_dispatch_status="received",
        ),
    ]

    assert file_is_fully_dispatched(partial) is False
    assert file_is_fully_dispatched(complete) is True


def test_cancelled_piece_does_not_block_file_dispatch_completion():
    pieces = [
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece("cancelled", group_key="product:2", status="cancelled"),
    ]

    assert file_is_fully_dispatched(pieces) is True


def test_archived_piece_does_not_block_file_dispatch_completion():
    pieces = [
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece(
            "archived",
            group_key="product:2",
            experiment_archived_at="2026-08-20T16:00:00Z",
        ),
    ]

    assert file_is_fully_dispatched(pieces) is True


@pytest.mark.asyncio
async def test_file_status_changes_only_after_the_last_piece_is_dispatched():
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _length):
            return [dict(row) for row in self.rows]

    registry = {
        "user_id": "merchant-1",
        "file_number": "PF-100",
        "batch_id": "batch-1",
        "status": "ready",
        "execution_status": "assigned",
    }
    pieces = [
        {
            **_piece("piece-1", supplier_dispatch_status=DISPATCH_STATUS_SENT),
            "user_id": "merchant-1",
            "file_number": "PF-100",
            "batch_id": "batch-1",
        },
        {
            **_piece("piece-2", group_key="product:2", order_number="3002"),
            "user_id": "merchant-1",
            "file_number": "PF-100",
            "batch_id": "batch-1",
        },
    ]
    collections = {
        PIECES: MagicMock(find=MagicMock(side_effect=lambda *_args, **_kwargs: Cursor(pieces))),
        REGISTRY: MagicMock(update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1))),
        BATCHES: MagicMock(update_one=AsyncMock()),
        WORKFLOWS: MagicMock(update_many=AsyncMock()),
        PIECE_EVENTS: MagicMock(insert_one=AsyncMock()),
    }
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__

    changed = await _mark_orders_started_if_fully_dispatched(
        db,
        user_id="merchant-1",
        registry=registry,
        actor={"id": "employee-1", "name": "أحمد"},
    )

    assert changed is False
    collections[REGISTRY].update_one.assert_not_awaited()

    pieces[1]["supplier_dispatch_status"] = DISPATCH_STATUS_SENT
    changed = await _mark_orders_started_if_fully_dispatched(
        db,
        user_id="merchant-1",
        registry=registry,
        actor={"id": "employee-1", "name": "أحمد"},
    )

    assert changed is True
    registry_update = collections[REGISTRY].update_one.await_args.args[1]["$set"]
    assert registry_update["execution_status"] == "in_progress"
    collections[BATCHES].update_one.assert_awaited_once()
    workflow_update = collections[WORKFLOWS].update_many.await_args.args[1]["$set"]
    assert workflow_update["stage"] == "in_progress"
    event = collections[PIECE_EVENTS].insert_one.await_args.args[0]
    assert event["event_type"] == "preparation_file_fully_dispatched"
    assert event["piece_count"] == 2


def test_supplier_must_offer_an_unfinished_service_on_the_piece():
    piece = _piece("piece-1")
    matching = {
        "id": "supplier-1",
        "service_links": [{"service_id": "engrave"}],
    }
    mismatch = {
        "id": "supplier-2",
        "service_links": [{"service_id": "paint"}],
    }

    assert supplier_dispatch_blocker(piece, matching) is None
    assert supplier_dispatch_blocker(piece, mismatch)["code"] == (
        "supplier_dispatch_service_mismatch"
    )


def test_supplier_file_contains_only_services_offered_by_selected_supplier():
    piece = _piece(
        "piece-1",
        services=[
            {"service_id": "engrave", "service_name": "نحت", "status": "pending"},
            {"service_id": "paint", "service_name": "طلاء", "status": "pending"},
        ],
    )

    lines = supplier_dispatch_lines(
        [piece],
        {"service_links": [{"service_id": "engrave"}]},
    )

    assert [row["service_name"] for row in lines[0]["services"]] == ["نحت"]


def test_existing_piece_uses_resolved_salla_image_when_manual_image_is_missing():
    pieces = [{
        **_piece("gift-card", group_key="product:AMS11542"),
        "batch_id": "batch-1",
        "product_name": "كرت إهداء حسب الطلب",
        "selected_image_url": None,
    }]
    batches = [{
        "id": "batch-1",
        "lines": [{
            "order_item_id": "item-1",
            "group_key": "product:AMS11542",
            "selected_image_url": None,
            "resolved_image_url": "https://cdn.salla.sa/AMS11542.jpg",
            "image_candidates": ["https://cdn.salla.sa/AMS11542.jpg"],
        }],
    }]

    _hydrate_piece_images_from_batches(pieces, batches)
    products = _group_piece_products(pieces)

    assert pieces[0]["selected_image_url"] is None
    assert pieces[0]["resolved_image_url"] == "https://cdn.salla.sa/AMS11542.jpg"
    assert products[0]["resolved_image_url"] == "https://cdn.salla.sa/AMS11542.jpg"
    assert products[0]["image_url"] == "https://cdn.salla.sa/AMS11542.jpg"


def test_supplier_print_cards_keep_physical_barcodes_and_customer_order_facts():
    pieces = [{
        **_piece("a" * 32, group_key="product:ring"),
        "batch_id": "batch-1",
        "product_name": "خاتم لا يظهر اسمه في الطباعة",
        "selected_image_url": None,
        "specifications_snapshot": [],
    }]
    batches = [{
        "id": "batch-1",
        "lines": [{
            "order_item_id": "item-1",
            "group_key": "product:ring",
            "resolved_image_url": "https://cdn.salla.sa/ring.jpg",
            "shipping_company": "سمسا",
            "total_products_in_order": 3,
            "file_spec_fields": [
                {"name": "اللون", "value": "ذهبي"},
                {"name": "المقاس", "value": "17"},
            ],
        }],
    }]

    _hydrate_piece_print_facts_from_batches(pieces, batches)
    cards = supplier_dispatch_cards(pieces)

    assert len(cards) == 1
    assert cards[0]["barcode_value"] == "a" * 32
    assert cards[0]["resolved_image_url"] == "https://cdn.salla.sa/ring.jpg"
    assert cards[0]["shipping_company"] == "سمسا"
    assert cards[0]["order_piece_count"] == 3
    assert cards[0]["specifications"] == [
        {"name": "اللون", "value": "ذهبي"},
        {"name": "المقاس", "value": "17"},
    ]


def test_new_dispatch_governance_requires_the_same_supplier_at_receiving():
    legacy_piece = _piece("legacy")
    governed_piece = _piece(
        "governed",
        supplier_dispatch_status=DISPATCH_STATUS_READY,
        supplier_id="supplier-1",
        supplier_name="مورد الحفر",
    )

    assert supplier_receiving_dispatch_blocker(legacy_piece, "supplier-2") is None
    assert supplier_receiving_dispatch_blocker(governed_piece, "supplier-1") is None
    mismatch = supplier_receiving_dispatch_blocker(governed_piece, "supplier-2")
    assert mismatch["code"] == "supplier_piece_dispatched_to_different_supplier"
    assert mismatch["expected_supplier_name"] == "مورد الحفر"


def test_partial_piece_is_assigned_by_receiving_without_second_dispatch():
    partial = _piece(
        "partial",
        status="in_progress",
        supplier_dispatch_status=DISPATCH_STATUS_PARTIAL,
        supplier_id="supplier-1",
        supplier_name="المورد الأول",
        supplier_receiving_history=[{
            "invoice_id": "invoice-1",
            "supplier_id": "supplier-1",
        }],
    )

    assert piece_is_available_for_supplier_dispatch(partial) is False
    assert supplier_receiving_dispatch_blocker(partial, "supplier-2") is None

    inconsistent = {
        **partial,
        "piece_id": "partial-without-history",
        "supplier_receiving_history": [],
    }
    blocker = supplier_receiving_dispatch_blocker(inconsistent, "supplier-2")
    assert blocker["code"] == "supplier_piece_partial_history_missing"


def test_dispatch_router_exposes_employee_supplier_and_manager_workflow():
    router = make_preparation_supplier_dispatch_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }

    assert ("/supplier-dispatch-v1/workspace", "GET") in routes
    assert ("/supplier-dispatch-v1/dispatches", "POST") in routes
    assert ("/supplier-dispatch-v1/rejections", "POST") in routes
    assert ("/supplier-dispatch-v1/manager/unassigned", "GET") in routes
    assert ("/supplier-dispatch-v1/manager/reassign", "POST") in routes
    assert (
        "/supplier-dispatch-v1/dispatches/{dispatch_id}/ready",
        "POST",
    ) in routes


def test_returning_assignment_requires_a_meaningful_employee_reason():
    with pytest.raises(ValidationError):
        RejectPreparationPiecesRequest(
            client_request_id="return-request-1",
            file_number="PF-100",
            selections=[{"group_key": "product:1", "quantity": 2}],
            reason=" ",
        )

    payload = RejectPreparationPiecesRequest(
        client_request_id="return-request-2",
        file_number="PF-100",
        selections=[{"group_key": "product:1", "quantity": 2}],
        reason="ليس من اختصاصي",
    )
    assert payload.reason == "ليس من اختصاصي"


def test_one_supplier_file_can_preserve_selections_from_multiple_source_files():
    payload = CreateSupplierDispatchRequest(
        client_request_id="supplier-dispatch-multi-1",
        supplier_id="supplier-1",
        files=[
            {
                "file_number": "PF-100",
                "selections": [{"group_key": "product:1", "quantity": 2}],
            },
            {
                "file_number": "PF-101",
                "selections": [{"group_key": "product:1", "quantity": 1}],
            },
        ],
    )

    assert [row.file_number for row in payload.file_requests()] == ["PF-100", "PF-101"]
    assert sum(
        selection.quantity
        for row in payload.file_requests()
        for selection in row.selections
    ) == 3


def test_supplier_file_rejects_duplicate_source_file_blocks():
    with pytest.raises(ValidationError):
        CreateSupplierDispatchRequest(
            client_request_id="supplier-dispatch-multi-2",
            supplier_id="supplier-1",
            files=[
                {
                    "file_number": "PF-100",
                    "selections": [{"group_key": "product:1", "quantity": 1}],
                },
                {
                    "file_number": "PF-100",
                    "selections": [{"group_key": "product:2", "quantity": 1}],
                },
            ],
        )


def test_legacy_single_file_supplier_payload_remains_supported():
    payload = CreateSupplierDispatchRequest(
        client_request_id="supplier-dispatch-legacy-1",
        supplier_id="supplier-1",
        file_number="PF-100",
        selections=[{"group_key": "product:1", "quantity": 1}],
    )

    assert payload.file_requests()[0].file_number == "PF-100"


def test_employee_summary_exposes_physical_piece_counts_for_overview_cards():
    files = [
        {
            "is_new": True,
            "available_quantity": 3,
            "sent_quantity": 2,
            "ready_quantity": 1,
            "received_quantity": 2,
            "products": [
                {"available_quantity": 3, "sent_quantity": 0, "ready_quantity": 0},
                {"available_quantity": 0, "sent_quantity": 2, "ready_quantity": 1},
            ],
        },
    ]
    pieces = [
        _piece("waiting"),
        _piece("sent", supplier_dispatch_status=DISPATCH_STATUS_SENT),
        _piece("received-1", order_number="3001", status="received"),
        _piece("received-2", order_number="3001", status="received"),
        _piece("handed-off", order_number="3002", status="received", branch_handoff_at="done"),
    ]

    summary = employee_workspace_summary(files, pieces)

    assert summary["waiting_review_pieces"] == 3
    assert summary["in_progress_pieces"] == 3
    assert summary["waiting_review_products"] == 1
    assert summary["in_progress_products"] == 1
    assert summary["received_orders_awaiting_branch_handoff"] == 1
    assert summary["received_pieces_awaiting_branch_handoff"] == 2
    assert summary["total_assigned_pieces"] == 4


def test_branch_handoff_removes_piece_from_received_awaiting_branch_cards():
    waiting = _piece(
        "received-waiting",
        status="received",
        supplier_dispatch_status="received",
    )
    handed_off = _piece(
        "received-handed-off",
        status="ready_for_assembly",
        supplier_dispatch_status="received",
        branch_handoff_at="2026-08-21T10:00:00+00:00",
    )

    grouped = _group_piece_products([waiting, handed_off])
    piece_rows = _piece_products([waiting, handed_off])

    assert grouped[0]["received_quantity"] == 1
    received_by_piece = {
        row["piece_id"]: row["received_quantity"] for row in piece_rows
    }
    assert received_by_piece == {
        "received-waiting": 1,
        "received-handed-off": 0,
    }


@pytest.mark.asyncio
async def test_employee_workspace_queries_only_pieces_assigned_to_that_employee():
    class EmptyCursor:
        def sort(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        async def to_list(self, _length):
            return []

    collection = MagicMock()
    collection.find.return_value = EmptyCursor()
    db = MagicMock()
    db.__getitem__.return_value = collection

    result = await _employee_workspace(
        db,
        user_id="merchant-1",
        employee_id="employee-1",
        limit=100,
    )

    pieces_query = collection.find.call_args_list[0].args[0]
    assert pieces_query["user_id"] == "merchant-1"
    assert pieces_query["responsible_employee_id"] == "employee-1"
    assert pieces_query["experiment_archived_at"] is None
    assert result["employee_id"] == "employee-1"
    assert result["files"] == []


@pytest.mark.asyncio
async def test_employee_workspace_hydrates_legacy_piece_image_from_reviewed_batch():
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def sort(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        async def to_list(self, _length):
            return [dict(row) for row in self.rows]

    piece = {
        **_piece("gift-card", group_key="product:AMS11542"),
        "user_id": "merchant-1",
        "batch_id": "batch-1",
        "file_number": "PF-11542",
        "product_name": "كرت إهداء حسب الطلب",
        "responsible_employee_id": "employee-1",
        "selected_image_url": None,
    }
    collections = {
        PIECES: MagicMock(find=MagicMock(return_value=Cursor([piece]))),
        BATCHES: MagicMock(find=MagicMock(return_value=Cursor([{
            "id": "batch-1",
            "lines": [{
                "order_item_id": "item-1",
                "group_key": "product:AMS11542",
                "resolved_image_url": "https://cdn.salla.sa/AMS11542.jpg",
            }],
        }]))),
        REGISTRY: MagicMock(find=MagicMock(return_value=Cursor([{
            "batch_id": "batch-1",
            "file_number": "PF-11542",
            "file_title": "ملف بطاقات الإهداء",
        }]))),
        DISPATCHES: MagicMock(find=MagicMock(return_value=Cursor([]))),
        MEZAN_SUPPLIERS_V2: MagicMock(find=MagicMock(return_value=Cursor([]))),
    }
    db = MagicMock()
    db.__getitem__.side_effect = collections.__getitem__

    result = await _employee_workspace(
        db,
        user_id="merchant-1",
        employee_id="employee-1",
        limit=100,
    )

    product = result["files"][0]["products"][0]
    assert product["selected_image_url"] is None
    assert product["resolved_image_url"] == "https://cdn.salla.sa/AMS11542.jpg"
    assert product["image_url"] == "https://cdn.salla.sa/AMS11542.jpg"
    batch_query = collections[BATCHES].find.call_args.args[0]
    assert batch_query == {"user_id": "merchant-1", "id": {"$in": ["batch-1"]}}
