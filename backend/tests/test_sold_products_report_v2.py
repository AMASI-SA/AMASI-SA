from sold_products_report_v2 import aggregate_sold_products, order_matches_status, summarize
from sold_product_reversals import (
    collect_archived_removals, collect_reversals,
    collect_review_snapshot_removals, merge_reversal_evidence,
)


def test_registered_mezan_cost_and_missing_cost_sort_before_popular_priced_product():
    products = [
        {"salla_product_id": "p1", "mezan_product_id": "mpv2_p1", "sku": "SKU1", "name": "الأول", "main_image": "https://example.com/p1.jpg", "cost_price_from_salla": 99},
        {"salla_product_id": "p2", "mezan_product_id": "mpv2_p2", "sku": "SKU2", "name": "الثاني"},
    ]
    orders = [{"products": [
        {"product_id": "p1", "sku": "SKU1", "quantity": 30},
        {"product_id": "p2", "sku": "SKU2", "quantity": 1},
    ]}]
    rows = aggregate_sold_products(orders, products, [{"salla_product_id": "p1", "base_cost": 15}])
    assert [row["sku"] for row in rows] == ["SKU2", "SKU1"]
    assert rows[0]["total_cost"] is None
    assert rows[0]["mezan_product_id"] == "mpv2_p2"
    assert rows[1]["image_url"] == "https://example.com/p1.jpg"
    assert rows[1]["unit_cost"] == 15
    assert rows[1]["cost_source"] == "mezan"
    assert rows[1]["total_cost"] == 450
    assert summarize(rows) == {
        "units_sold": 31.0, "average_unit_cost": None, "total_cost": None,
        "known_cost_total": 450.0, "known_cost_units": 30.0,
        "units_costed": 31.0, "uncertain_cost_total": 0.0,
    }


def test_salla_cost_is_used_only_when_mezan_cost_is_not_registered():
    products = [{"salla_product_id": "p1", "sku": "SKU1", "cost_price_from_salla": 25}]
    orders = [{"products": [{"product_id": "p1", "sku": "SKU1", "quantity": 3}]}]
    fallback = aggregate_sold_products(orders, products, [])
    assert fallback[0]["cost_source"] == "salla"
    assert fallback[0]["unit_cost"] == 25
    assert fallback[0]["total_cost"] == 75
    mezan = aggregate_sold_products(orders, products, [{"salla_product_id": "p1", "base_cost": 10}])
    assert mezan[0]["cost_source"] == "mezan"
    assert mezan[0]["total_cost"] == 30


def test_selected_order_options_do_not_change_registered_unit_cost():
    rows = aggregate_sold_products([{"products": [
        {"product_id": "p1", "sku": "SKU1", "quantity": 2},
        {"product_id": "p1", "sku": "SKU1", "quantity": 1,
         "options": [{"name": "التغليف", "value": "فاخر"}]},
    ]}], [{"salla_product_id": "p1", "sku": "SKU1"}], [{"salla_product_id": "p1", "base_cost": 15}])
    assert rows[0]["units_sold"] == 3
    assert rows[0]["total_cost"] == 45
    assert summarize(rows)["average_unit_cost"] == 15


def test_statuses_default_exclude_cancelled_and_custom_selection_can_include_multiple():
    pending = {"order_status": "بإنتظار المراجعة"}
    reviewed = {"order_status": "تم المراجعة"}
    cancelled = {"order_status": "ملغي"}
    assert order_matches_status(pending, ["default"], [])
    assert order_matches_status(reviewed, ["pending_review", "reviewed"], [])
    assert not order_matches_status(cancelled, ["default"], [])
    assert order_matches_status(cancelled, ["default"], ["ملغي"])
    assert not order_matches_status(reviewed, ["default"], ["ملغي"])
    assert not order_matches_status(cancelled, ["pending_review", "reviewed"], [])


def test_deleted_item_is_visible_even_when_absent_from_current_eligible_order():
    orders = [{"order_number": "100", "order_status": "قيد التنفيذ", "products": []}]
    adjustments = [{"order_number": "100", "removal_stage": "after", "items_diff": {
        "removed": [{"sku": "SKU1", "product_id": "p1", "name": "قطعة تالفة", "quantity": 2}]}}]
    reversals = collect_reversals(orders, adjustments, [])
    rows = aggregate_sold_products(orders, [{"salla_product_id": "p1", "sku": "SKU1", "cost_price_from_salla": 20}], [], reversals)
    assert len(rows) == 1
    assert rows[0]["units_sold"] == 0
    assert rows[0]["units_returned_after"] == 2
    assert rows[0]["total_cost"] == 40
    assert rows[0]["unit_cost"] == 20
    assert summarize(rows)["total_cost"] == 40


def test_net_cost_excludes_only_cancellations_proven_before_execution():
    products = [{"salla_product_id": "p1", "sku": "SKU1", "cost_price_from_salla": 12}]
    orders = [{"products": [{"product_id": "p1", "sku": "SKU1", "quantity": 4}]}]
    reversals = [
        {"item": {"product_id": "p1", "sku": "SKU1"}, "quantity": 2, "stage": "before"},
        {"item": {"product_id": "p1", "sku": "SKU1"}, "quantity": 3, "stage": "after"},
        {"item": {"product_id": "p1", "sku": "SKU1"}, "quantity": 1, "stage": "unknown"},
    ]
    row = aggregate_sold_products(orders, products, [], reversals)[0]
    assert (row["units_sold"], row["units_cancelled_before"], row["units_returned_after"], row["units_unclassified"]) == (4, 2, 3, 1)
    assert row["total_cost"] == 96  # (4 sold + 3 after + 1 unclassified) * 12
    assert row["uncertain_cost"] == 12
    assert summarize([row])["units_costed"] == 8
    assert summarize([row])["average_unit_cost"] == 12


def test_cancelled_before_execution_never_requires_cost_and_later_missing_cost_is_incomplete():
    reversals = [{"item": {"sku": "UNKNOWN"}, "quantity": 2, "stage": "before"}]
    before = aggregate_sold_products([], [], [], reversals)[0]
    assert before["total_cost"] == 0
    assert summarize([before])["total_cost"] == 0
    reversals[0]["stage"] = "after"
    after = aggregate_sold_products([], [], [], reversals)[0]
    assert after["total_cost"] is None
    assert summarize([after])["total_cost"] is None


def test_historic_adjustment_without_status_evidence_stays_unclassified():
    orders = [{"order_number": "200", "order_status": "ملغي", "products": [{"sku": "A", "quantity": 3}]}]
    adjustments = [{"order_number": "200", "items_diff": {
        "removed": [{"sku": "A", "quantity": 3}]}}]
    reversals = collect_reversals(orders, adjustments, [{"order_number": "200", "title": "تم التنفيذ", "occurred_at": "2025-02-01"}])
    assert [(row["stage"], row["quantity"]) for row in reversals] == [("unknown", 3)]


def test_terminal_order_keeps_other_lines_while_avoiding_stale_deleted_line():
    orders = [{"order_number": "201", "order_status": "ملغي", "sold_products_completed_at": "2025-02-01",
               "products": [{"sku": "A", "quantity": 3}, {"sku": "B", "quantity": 2}]}]
    adjustments = [{"order_number": "201", "removal_stage": "after", "items_diff": {
        "removed": [{"sku": "A", "quantity": 3}]}}]
    rows = collect_reversals(orders, adjustments, [])
    assert sorted((row["item"]["sku"], row["quantity"]) for row in rows) == [("A", 3), ("B", 2)]


def test_full_cancelled_order_uses_timestamped_status_history_for_stage():
    orders = [{"order_number": "300", "order_status": "مسترجع", "products": [{"sku": "B", "quantity": 2}]}]
    history = [{"order_number": "300", "title": "تم التنفيذ", "occurred_at": "2025-02-01T12:00:00+03:00"},
               {"order_number": "300", "title": "مسترجع", "occurred_at": "2025-02-02T12:00:00+03:00"}]
    assert collect_reversals(orders, [], history)[0]["stage"] == "after"
    history[0]["occurred_at"] = "2025-02-03T12:00:00+03:00"
    assert collect_reversals(orders, [], history)[0]["stage"] == "before"


def test_cancelled_before_execution_requires_creation_and_cancellation_history():
    orders = [{"order_number": "400", "order_status": "ملغي", "products": [{"sku": "C", "quantity": 1}]}]
    history = [{"order_number": "400", "event_type": "order_created", "title": "تم إنشاء الطلب", "occurred_at": "2025-02-01T09:00:00+03:00"},
               {"order_number": "400", "title": "ملغي", "occurred_at": "2025-02-01T10:00:00+03:00"}]
    assert collect_reversals(orders, [], history)[0]["stage"] == "before"
    assert collect_reversals(orders, [], history[1:])[0]["stage"] == "unknown"


def test_webhook_snapshots_recover_deleted_product_and_ignore_sparse_status_event():
    orders = [{"order_number": "500", "order_status": "قيد التنفيذ", "products": []}]
    captures = [
        {"event": "order.created", "first_received_at": "2025-02-01T10:00:00Z", "payload": {"data": {"reference_id": 500,
            "items": [{"quantity": 3, "product": {"id": "p1", "sku": "SKU1", "name": "منتج"}}]}}},
        {"first_received_at": "2025-02-01T10:15:00Z", "payload": {"data": {"reference_id": 500, "status": "تم التنفيذ"}}},
        {"event": "order.products.updated", "first_received_at": "2025-02-01T10:30:00Z", "payload": {"data": {"reference_id": 500, "items": []}}},
    ]
    history = [{"order_number": "500", "title": "تم التنفيذ", "occurred_at": "2025-02-01 13:15:00"}]
    result = collect_archived_removals(captures, history, orders)
    assert [(row["item"]["sku"], row["quantity"], row["stage"]) for row in result] == [("SKU1", 3, "after")]


def test_review_snapshot_recovers_missing_lines_without_guessing_removal_time():
    orders = [{"order_number": "600", "order_status": "مسترجع", "products": [{"sku": "SKU1", "quantity": 1}]}]
    workflow = [{"order_number": "600", "reviewed_at": "2025-01-01", "items": [
        {"sku": "SKU1", "quantity": 3, "product_name": "منتج"}]}]
    reviewed = collect_review_snapshot_removals(orders, workflow)
    assert len(reviewed) == 1
    assert reviewed[0]["quantity"] == 2
    assert reviewed[0]["stage"] == "unknown"
    saved = [{"order_number": "600", "item": {"sku": "SKU1"}, "quantity": 2, "stage": "after"}]
    assert merge_reversal_evidence(saved, [], reviewed) == saved


def test_sparse_current_active_order_does_not_turn_review_snapshot_into_cancellation():
    orders = [{"order_number": "601", "order_status": "قيد التنفيذ", "products": []}]
    workflows = [{"order_number": "601", "reviewed_at": "2025-01-01", "items": [
        {"sku": "SKU1", "quantity": 2}]}]
    assert collect_review_snapshot_removals(orders, workflows) == []
    captures = [
        {"event": "order.created", "first_received_at": "2025-01-01", "payload": {"data": {
            "reference_id": 601, "items": [{"sku": "SKU1", "quantity": 2}]}}},
        {"event": "order.status.updated", "first_received_at": "2025-01-02", "payload": {"data": {
            "reference_id": 601, "items": []}}},
    ]
    assert collect_archived_removals(captures, [], orders) == []
