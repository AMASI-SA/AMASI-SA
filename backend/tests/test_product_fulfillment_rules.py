from types import SimpleNamespace as Obj

from fulfillment_batch_pdf import generate_shipping_batch_pdf
from fulfillment_v2_routes import (
    _apply_inventory_reservations,
    _inventory_rows,
    _inventory_reservation_blockers,
    _inventory_consumption_targets,
    _reserve_inventory_for_line,
    _ready_order_allowed,
    _satisfy_preparation_with_ready_stock,
    _warehouse_allowed,
)
from order_review_export_controls import (
    ReviewExportControlPatch,
    apply_export_control_patch,
    item_export_control_view,
    partition_review_items_for_preparation,
)
from product_fulfillment_rules import (
    FULFILLMENT_TYPE_INSTANT,
    FULFILLMENT_TYPE_PREPARATION,
    classify_line_fulfillment,
    evaluate_order_fulfillment,
    normalize_low_stock_threshold,
    normalize_stockout_policy,
)


def _order(*, payment_method="mada", payment_status="paid", order_status="processing"):
    return Obj(
        order_number="1001",
        status=order_status,
        status_native=order_status,
        payment=Obj(
            method=payment_method,
            method_native=payment_method,
            status=payment_status,
            collection_status="paid" if payment_status == "paid" else "unpaid",
            paid_amount=100 if payment_status == "paid" else 0,
            remaining_amount=0 if payment_status == "paid" else 100,
            has_remaining_amount=payment_status != "paid",
        ),
        shipping=Obj(
            company="ناقل",
            address=Obj(
                city="الرياض",
                district="العليا",
                street="شارع الاختبار",
                formatted=None,
                short_address=None,
                building_number=None,
                postal_code=None,
            ),
        ),
    )


def _instant_line(**overrides):
    row = {
        "configured": True,
        "configured_type": FULFILLMENT_TYPE_INSTANT,
        "resolved_type": FULFILLMENT_TYPE_INSTANT,
        "requires_preparation": False,
        "inventory_policy": "branch_stock_required",
        "requires_branch_inventory": True,
        "warehouse_ids": ["wh-1"],
        "inventory_available": True,
        "supplier_export_eligible": False,
    }
    row.update(overrides)
    return row


def test_explicit_preparation_service_overrides_instant_product():
    result = classify_line_fulfillment(
        profile={
            "fulfillment_type": "instant",
            "inventory_policy": "branch_stock_required",
        },
        product_resources=[],
        selected_option_resources=[{
            "id": "svc-cut",
            "name": "قص",
            "kind": "service",
            "requires_preparation": True,
            "_link_source": "option",
        }],
    )

    assert result["resolved_type"] == FULFILLMENT_TYPE_PREPARATION
    assert result["configured_type"] == FULFILLMENT_TYPE_PREPARATION
    assert result["requires_branch_inventory"] is False
    assert result["operation_choices_frozen"] is True
    assert result["forcing_services"][0]["name"] == "قص"
    assert result["supplier_export_eligible"] is True


def test_stock_component_keeps_frozen_preparation_without_forcing_service():
    result = classify_line_fulfillment(
        profile={"fulfillment_type": "instant"},
        product_resources=[{
            "id": "component-box",
            "kind": "stock_component",
            "requires_preparation": True,
        }],
    )

    assert result["resolved_type"] == FULFILLMENT_TYPE_PREPARATION
    assert result["requires_preparation"] is True
    assert result["forcing_services"] == []


def test_product_service_metadata_is_preserved_during_operation_freeze():
    classification = classify_line_fulfillment(
        profile={
            "fulfillment_type": "instant",
            "inventory_policy": "branch_stock_required",
        },
        product_resources=[{
            "id": "svc-name",
            "name": "كتابة الاسم",
            "kind": "service",
            "requires_preparation": True,
            "_link_source": "product",
        }],
        selected_option_resources=[],
    )

    assert classification["requires_preparation"] is True
    assert classification["requires_branch_inventory"] is False
    assert classification["forcing_services"] == [{
        "id": "svc-name",
        "name": "كتابة الاسم",
        "source": "product",
    }]

    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[{
            **classification,
            "warehouse_ids": ["wh-riyadh"],
            "inventory_available": True,
        }],
    )

    assert decision["ready_to_ship"] is False
    assert decision["preparation_stages_required"] is True
    assert decision["warehouse_ids"] == ["wh-riyadh"]
    assert "operational_inventory_not_available" not in decision["blockers"]


def test_frozen_preparation_does_not_require_finished_goods_stock():
    classification = classify_line_fulfillment(
        profile={
            "fulfillment_type": "instant",
            "inventory_policy": "branch_stock_required",
        },
        product_resources=[{
            "id": "svc-name",
            "kind": "service",
            "requires_preparation": True,
            "_link_source": "product",
        }],
    )

    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[{
            **classification,
            "warehouse_ids": [],
            "inventory_available": False,
        }],
    )

    assert decision["ready_to_ship"] is False
    assert "operational_inventory_not_available" not in decision["blockers"]
    assert decision["warehouse_resolution_source"] == "not_required"


def test_frozen_preparation_ignores_preorder_and_stock_threshold_choices():
    classification = classify_line_fulfillment(
        profile={
            "fulfillment_type": "instant",
            "inventory_policy": "branch_stock_required",
            "stockout_policy": "allow_preorder",
            "low_stock_threshold": 5,
        },
    )
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[{
            **classification,
            "order_item_id": "item-1",
            "warehouse_ids": [],
            "inventory_available": False,
        }],
    )

    assert classification["preorder_when_out_of_stock"] is False
    assert classification["low_stock_threshold"] == 3
    assert decision["ready_to_ship"] is False
    assert decision["preorder_required"] is False
    assert decision["preorder_line_ids"] == []
    assert "preorder_waiting_for_stock" not in decision["blockers"]
    assert "operational_inventory_not_available" not in decision["blockers"]


def test_pure_instant_order_routes_to_shipping_queue_when_all_guards_pass():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[_instant_line()],
    )

    assert decision["ready_to_ship"] is True
    assert decision["route_stage"] == "ready_to_ship"
    assert decision["preparation_stages_required"] is False
    assert decision["supplier_export_order_required"] is False


def test_cod_is_payment_eligible_but_awaiting_card_payment_is_not():
    cod = evaluate_order_fulfillment(
        order=_order(
            payment_method="الدفع عند الاستلام",
            payment_status="unpaid",
        ),
        lines=[_instant_line()],
    )
    awaiting = evaluate_order_fulfillment(
        order=_order(payment_method="mada", payment_status="pending"),
        lines=[_instant_line()],
    )

    assert cod["ready_to_ship"] is True
    assert awaiting["ready_to_ship"] is False
    assert "payment_not_eligible" in awaiting["blockers"]


def test_unknown_non_cod_payment_fails_closed():
    order = _order(payment_method="mada", payment_status="pending")
    order.payment.status = None
    order.payment.collection_status = "unknown"
    order.payment.remaining_amount = 0
    order.payment.has_remaining_amount = False

    decision = evaluate_order_fulfillment(
        order=order,
        lines=[_instant_line()],
    )

    assert decision["ready_to_ship"] is False
    assert "payment_not_eligible" in decision["blockers"]


def test_mixed_order_waits_and_excludes_instant_line_from_supplier_export():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[
            _instant_line(),
            {
                "configured": True,
                "configured_type": FULFILLMENT_TYPE_PREPARATION,
                "resolved_type": FULFILLMENT_TYPE_PREPARATION,
                "requires_preparation": True,
                "requires_branch_inventory": False,
                "warehouse_ids": [],
                "inventory_available": None,
                "supplier_export_eligible": True,
            },
        ],
    )

    assert decision["order_type"] == "mixed"
    assert decision["ready_to_ship"] is False
    assert decision["supplier_export_order_required"] is True
    assert decision["instant_items_excluded_from_supplier_export"] is True


def test_preparation_order_does_not_require_finished_goods_inventory():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[{
            "configured": True,
            "configured_type": FULFILLMENT_TYPE_PREPARATION,
            "resolved_type": FULFILLMENT_TYPE_PREPARATION,
            "requires_preparation": True,
            "requires_branch_inventory": False,
            "warehouse_ids": [],
            "inventory_available": None,
            "supplier_export_eligible": True,
        }],
    )

    assert "operational_inventory_not_available" not in decision["blockers"]
    assert decision["warehouse_resolution_source"] == "not_required"


def test_missing_inventory_fails_closed_without_product_warehouse_blocker():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[_instant_line(warehouse_ids=[], inventory_available=False)],
    )

    assert decision["ready_to_ship"] is False
    assert "warehouse_not_assigned" not in decision["blockers"]
    assert "operational_inventory_not_available" in decision["blockers"]


def test_instant_inventory_without_location_is_not_ready():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[_instant_line(warehouse_ids=[])],
    )

    assert decision["ready_to_ship"] is False
    assert decision["warehouse_ids"] == []
    assert "operational_inventory_not_available" in decision["blockers"]
    assert (
        decision["warehouse_resolution_source"]
        == "inventory_location_missing"
    )


def test_inventory_reservation_sets_warehouse_from_stock_location():
    stock_rows = [
        {
            "warehouse_id": "wh-2",
            "identifiers": {"SKU-1"},
            "remaining": 2.0,
        },
        {
            "warehouse_id": "wh-1",
            "identifiers": {"SKU-1"},
            "remaining": 4.0,
        },
    ]

    available, quantity, warehouse_ids = _reserve_inventory_for_line(
        stock_rows=stock_rows,
        identifiers={"SKU-1"},
        quantity=3,
    )

    assert available is True
    assert quantity == 6
    assert warehouse_ids == ["wh-1", "wh-2"]


def test_unassigned_inventory_location_is_not_operational_stock():
    rows = _inventory_rows([
        {
            "id": "loc-unassigned",
            "warehouse_id": None,
            "occupancy": {
                "items": [{"sku": "SKU-1", "quantity": 5}],
            },
        },
        {
            "id": "loc-assigned",
            "warehouse_id": "wh-stock",
            "occupancy": {
                "items": [{"sku": "SKU-1", "quantity": 2}],
            },
        },
    ])

    assert len(rows) == 1
    assert rows[0]["warehouse_id"] == "wh-stock"


def test_inventory_rows_preserve_ready_configuration_metadata():
    rows = _inventory_rows([{
        "id": "loc-ready",
        "warehouse_id": "wh-stock",
        "occupancy": {
            "items": [{
                "sku": "SKU-1",
                "quantity": 2,
                "preparation_state": "ready_complete",
                "specifications": {
                    "الاسم": "عبير",
                    "اللون": "ذهبي",
                },
                "configuration_key": "abeer-gold",
                "lot_id": "purchase:1:1:receipt-1",
            }],
        },
    }])

    assert rows[0]["preparation_state"] == "ready_complete"
    assert rows[0]["specifications"]["الاسم"] == "عبير"
    assert rows[0]["configuration_key"] == "abeer-gold"
    assert rows[0]["lot_id"] == "purchase:1:1:receipt-1"


def test_only_active_reservations_reduce_current_physical_availability():
    rows = [{
        "key": "receipt:r-1",
        "on_hand": 10.0,
        "remaining": 10.0,
    }]
    _apply_inventory_reservations(
        rows,
        [
            {
                "order_number": "1001",
                "status": "active",
                "allocations": [{
                    "inventory_row_key": "receipt:r-1",
                    "quantity": 2,
                }],
            },
            {
                "order_number": "999",
                "status": "active",
                "allocations": [{
                    "inventory_row_key": "receipt:r-1",
                    "quantity": 3,
                }],
            },
        ],
        current_order_number="1001",
    )

    assert rows[0]["remaining"] == 7
    assert rows[0]["reserved_quantity"] == 3


def test_stockout_policy_and_threshold_are_bounded():
    assert normalize_stockout_policy("حجز مسبق") == "allow_preorder"
    assert normalize_low_stock_threshold("5") == 5


def test_direct_assembly_route_skips_supplier_and_employee_custody():
    state = apply_export_control_patch(
        {},
        ReviewExportControlPatch(
            preparation_route="direct_assembly",
            save_assignment_as_default=True,
        ),
        operational_items=[],
        order_item_id="item-1",
        actor_id="owner-1",
    )

    assert state["preparation_route"] == "direct_assembly"
    assert state["supplier_export"] is False
    assert state["assigned_employee_id"] is None
    assert state["preparation_status"] == "awaiting_assembly"

    partition = partition_review_items_for_preparation([state])
    assert partition["supplier_file_items"] == []
    assert partition["internal_preparation_items"] == []
    assert partition["direct_assembly_items"] == [state]


def test_product_default_exposes_direct_assembly_on_future_order():
    view = item_export_control_view(
        {},
        operational_items=[],
        order_item_id="future-item",
        product_key="product:100",
        default_assignment={"preparation_route": "direct_assembly"},
    )

    assert view["preparation_route"] == "direct_assembly"
    assert view["direct_assembly"] is True
    assert view["route_source"] == "default"
    assert view["supplier_export"] is False
    assert view["preparation_status"] == "awaiting_assembly"


def test_unready_preparation_work_keeps_stock_reserved():
    assert _inventory_reservation_blockers([
        "operational_items_not_ready",
    ]) == []
    assert _inventory_reservation_blockers([
        "operational_items_not_ready",
        "payment_not_eligible",
    ]) == ["payment_not_eligible"]


def test_inventory_consumption_groups_same_receipt_across_orders():
    targets = _inventory_consumption_targets([
        {
            "id": "reservation-1",
            "allocations": [{
                "location_id": "loc-1",
                "receipt_id": "receipt-1",
                "quantity": 1,
            }],
        },
        {
            "id": "reservation-2",
            "allocations": [{
                "location_id": "loc-1",
                "receipt_id": "receipt-1",
                "quantity": 2,
            }],
        },
    ])

    assert list(targets.values()) == [{
        "location_id": "loc-1",
        "receipt_id": "receipt-1",
        "item_index": None,
        "quantity": 3.0,
    }]


def test_employee_assignment_filters_inventory_but_never_replaces_it():
    context = {
        "is_owner": False,
        "warehouse_ids": {"wh-employee"},
    }
    owner = {"is_owner": True, "warehouse_ids": None}

    assert _warehouse_allowed(context, []) is False
    assert _warehouse_allowed(owner, []) is False
    assert _warehouse_allowed(context, ["wh-employee"]) is True
    assert _warehouse_allowed(context, ["wh-other"]) is False
    assert _warehouse_allowed(owner, ["wh-stock"]) is True


def test_received_preparation_order_enters_assembly_without_inventory_location():
    employee = {
        "is_owner": False,
        "warehouse_ids": set(),
    }

    assert _ready_order_allowed(employee, {
        "ready_to_ship_source": "preparation_receipt",
        "fulfillment_decision": {"warehouse_ids": []},
    }) is True
    assert _ready_order_allowed(employee, {
        "ready_to_ship_source": "instant_inventory",
        "fulfillment_decision": {"warehouse_ids": []},
    }) is False


def test_exact_ready_stock_marks_linked_services_as_already_completed():
    result = _satisfy_preparation_with_ready_stock({
        "configured": True,
        "configured_type": FULFILLMENT_TYPE_PREPARATION,
        "resolved_type": FULFILLMENT_TYPE_PREPARATION,
        "requires_preparation": True,
        "requires_branch_inventory": True,
        "forcing_services": [
            {"id": "engraving", "name": "كتابة الاسم"},
        ],
        "supplier_export_eligible": True,
    })

    assert result["resolved_type"] == FULFILLMENT_TYPE_INSTANT
    assert result["requires_preparation"] is False
    assert result["forcing_services"] == []
    assert result["forcing_services_satisfied_by_inventory"] == [
        {"id": "engraving", "name": "كتابة الاسم"},
    ]
    assert result["supplier_export_eligible"] is False


def test_shipping_batch_pdf_is_generated_without_provider_calls():
    order = _order()
    order.items = [Obj(name="منتج اختبار", quantity=1.0, sku="AMS10001")]
    pdf = generate_shipping_batch_pdf(
        batch={"id": "ship_test"},
        orders=[order],
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
