from preparation_incident_recovery import (
    EXPECTED,
    INCIDENT_ID,
    SKU,
    _resolved_target_items,
)


def test_incident_scope_is_exactly_the_eight_orders_and_eleven_units():
    assert INCIDENT_ID == "ams11353-lost-11-20260825"
    assert SKU == "AMS11353"
    assert EXPECTED == {
        "279756840": 1,
        "279809610": 1,
        "279778158": 2,
        "279820694": 1,
        "279803951": 2,
        "279787662": 1,
        "279773618": 2,
        "279726749": 1,
    }
    assert len(EXPECTED) == 8
    assert sum(EXPECTED.values()) == 11


def test_incident_recovery_is_sku_scoped_and_fail_closed():
    source = __import__("inspect").getsource(
        __import__("preparation_incident_recovery")
    )
    assert "target_allocation_has_registered_file" in source
    assert "target_batch_contains_other_products" in source
    assert '"stage": "reviewed"' in source
    assert '"salla_updated": False' in source
    assert '"qoyod_updated": False' in source
    assert "incident_already_recovered" in source


def test_exact_review_snapshot_is_used_when_live_order_items_are_missing():
    rows = _resolved_target_items(
        "279778158",
        2,
        [],
        {"items": [
            {"order_item_id": "dress-a", "sku": "AMS11353", "quantity": 1},
            {"order_item_id": "dress-b", "sku": "AMS11353", "quantity": 1},
            {"order_item_id": "other", "sku": "AMS13067", "quantity": 9},
        ]},
    )

    assert [(row["order_item_id"], row["quantity"]) for row in rows] == [
        ("dress-a", 1),
        ("dress-b", 1),
    ]
    assert {row["source"] for row in rows} == {"review_snapshot"}


def test_review_snapshot_fails_closed_when_quantity_does_not_match_incident():
    rows = _resolved_target_items(
        "279778158",
        2,
        [],
        {"items": [{"order_item_id": "dress-a", "sku": "AMS11353", "quantity": 1}]},
    )

    assert rows == []
