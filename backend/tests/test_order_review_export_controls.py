from order_review_export_controls import (
    INTERNAL_PREPARATION_ROUTE,
    SUPPLIER_FILE_ROUTE,
    ReviewExportControlPatch,
    apply_export_control_patch,
    item_export_control_view,
    normalize_spec_keys,
    partition_review_items_for_preparation,
)


def test_normalize_spec_keys_deduplicates_and_canonicalizes_spacing():
    assert normalize_spec_keys([
        " اسحب وافلت الصورة هنا ",
        "اسحب_وافلت_الصورة_هنا",
        "",
    ]) == ["اسحب وافلت الصورة هنا"]


def test_manual_hidden_fields_are_union_with_operational_fields():
    current = {
        "order_item_id": "item-1",
        "supplier_export_excluded_spec_keys": ["الاسم"],
    }
    operational_items = [{
        "source_order_item_id": "item-1",
        "linked_specs": [{"key": "الاسم", "name": "الاسم"}],
    }]
    next_state = apply_export_control_patch(
        current,
        ReviewExportControlPatch(
            manual_hidden_spec_keys=["اسحب وافلت الصورة هنا"],
        ),
        operational_items=operational_items,
        order_item_id="item-1",
        actor_id="owner-1",
    )

    assert next_state["manual_supplier_export_excluded_spec_keys"] == [
        "اسحب وافلت الصورة هنا"
    ]
    assert next_state["supplier_export_excluded_spec_keys"] == [
        "اسحب وافلت الصورة هنا",
        "الاسم",
    ]

    view = item_export_control_view(
        next_state,
        operational_items=operational_items,
        order_item_id="item-1",
    )
    assert view["manual_hidden_spec_keys"] == ["اسحب وافلت الصورة هنا"]
    assert view["operational_hidden_spec_keys"] == ["الاسم"]
    assert view["hidden_spec_keys"] == ["اسحب وافلت الصورة هنا", "الاسم"]


def test_internal_route_never_enters_supplier_file_partition():
    internal = apply_export_control_patch(
        {"order_item_id": "packaging"},
        ReviewExportControlPatch(
            preparation_route=INTERNAL_PREPARATION_ROUTE,
        ),
        operational_items=[],
        order_item_id="packaging",
        actor_id="owner-1",
    )
    supplier = apply_export_control_patch(
        {"order_item_id": "necklace"},
        ReviewExportControlPatch(preparation_route=SUPPLIER_FILE_ROUTE),
        operational_items=[],
        order_item_id="necklace",
        actor_id="owner-1",
    )

    assert internal["supplier_export"] is False
    assert internal["preparation_status"] == "in_progress"
    assert supplier["supplier_export"] is True
    assert supplier["preparation_status"] == "pending_file"

    partition = partition_review_items_for_preparation([supplier, internal])
    assert [row["order_item_id"] for row in partition["supplier_file_items"]] == [
        "necklace"
    ]
    assert [
        row["order_item_id"]
        for row in partition["internal_preparation_items"]
    ] == ["packaging"]
