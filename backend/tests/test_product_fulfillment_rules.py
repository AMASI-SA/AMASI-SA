from types import SimpleNamespace as Obj

from fulfillment_batch_pdf import generate_shipping_batch_pdf
from product_fulfillment_rules import (
    FULFILLMENT_TYPE_INSTANT,
    FULFILLMENT_TYPE_PREPARATION,
    classify_line_fulfillment,
    evaluate_order_fulfillment,
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
        "warehouse_id": "wh-1",
        "inventory_available": True,
        "supplier_export_eligible": False,
    }
    row.update(overrides)
    return row


def test_explicit_preparation_service_overrides_instant_product():
    result = classify_line_fulfillment(
        profile={"fulfillment_type": "instant", "warehouse_id": "wh-1"},
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
    assert result["forcing_services"][0]["name"] == "قص"
    assert result["supplier_export_eligible"] is True


def test_stock_component_alone_does_not_override_instant_product():
    result = classify_line_fulfillment(
        profile={"fulfillment_type": "instant", "warehouse_id": "wh-1"},
        product_resources=[{
            "id": "component-box",
            "kind": "stock_component",
            "requires_preparation": True,
        }],
    )

    assert result["resolved_type"] == FULFILLMENT_TYPE_INSTANT
    assert result["forcing_services"] == []


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
                "warehouse_id": "wh-1",
                "inventory_available": None,
                "supplier_export_eligible": True,
            },
        ],
    )

    assert decision["order_type"] == "mixed"
    assert decision["ready_to_ship"] is False
    assert decision["supplier_export_order_required"] is True
    assert decision["instant_items_excluded_from_supplier_export"] is True


def test_missing_warehouse_or_inventory_fails_closed():
    decision = evaluate_order_fulfillment(
        order=_order(),
        lines=[_instant_line(warehouse_id=None, inventory_available=False)],
    )

    assert decision["ready_to_ship"] is False
    assert "warehouse_not_assigned" in decision["blockers"]
    assert "operational_inventory_not_available" in decision["blockers"]


def test_shipping_batch_pdf_is_generated_without_provider_calls():
    order = _order()
    order.items = [Obj(name="منتج اختبار", quantity=1.0, sku="AMS10001")]
    pdf = generate_shipping_batch_pdf(
        batch={"id": "ship_test"},
        orders=[order],
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000
