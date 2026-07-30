import pytest
from fastapi import HTTPException

from salla_inventory_sync_routes import (
    SALLA_BRANCH_SYNC_FEATURE_FLAG,
    _require_branch_sync_enabled,
    salla_branch_inventory_sync_enabled,
)
from salla_inventory_sync_rules import (
    build_branch_sync_plan,
    build_mezan_branch_targets,
    executable_payload_row,
    plan_signature,
    validate_branch_mappings,
)


def _profile(product_id="p-1", *, stockout="close_when_out_of_stock"):
    return {
        "salla_product_id": product_id,
        "inventory_policy": "branch_stock_required",
        "stockout_policy": stockout,
        "low_stock_threshold": 3,
    }


def _product(**patch):
    return {
        "mezan_product_id": "mp-1",
        "salla_product_id": "p-1",
        "name": "سلسال",
        "sku": "NECKLACE",
        "variants": [],
        "variants_count": 0,
        **patch,
    }


def _stock(*, warehouse="wh-1", remaining=5, **patch):
    return {
        "key": f"row-{warehouse}-{patch.get('salla_variant_id') or 'base'}",
        "warehouse_id": warehouse,
        "identifiers": {"mp-1", "p-1", "NECKLACE"},
        "remaining": remaining,
        "salla_variant_id": None,
        **patch,
    }


def test_branch_mapping_is_one_to_one():
    assert validate_branch_mappings([
        {"salla_branch_id": "b-2", "mezan_warehouse_id": "w-2"},
        {"salla_branch_id": "b-1", "mezan_warehouse_id": "w-1"},
    ])[0] == {
        "salla_branch_id": "b-1",
        "mezan_warehouse_id": "w-1",
    }

    with pytest.raises(ValueError, match="inventory_salla_branch_mapped_twice"):
        validate_branch_mappings([
            {"salla_branch_id": "b-1", "mezan_warehouse_id": "w-1"},
            {"salla_branch_id": "b-1", "mezan_warehouse_id": "w-2"},
        ])

    with pytest.raises(ValueError, match="inventory_mezan_warehouse_mapped_twice"):
        validate_branch_mappings([
            {"salla_branch_id": "b-1", "mezan_warehouse_id": "w-1"},
            {"salla_branch_id": "b-2", "mezan_warehouse_id": "w-1"},
        ])


def test_salla_branch_sync_is_frozen_by_default(monkeypatch):
    monkeypatch.delenv(SALLA_BRANCH_SYNC_FEATURE_FLAG, raising=False)

    assert salla_branch_inventory_sync_enabled() is False
    with pytest.raises(HTTPException) as error:
        _require_branch_sync_enabled()

    assert error.value.status_code == 409
    assert error.value.detail["code"] == (
        "salla_branch_inventory_sync_frozen"
    )


def test_salla_branch_sync_can_be_activated_after_approval(monkeypatch):
    monkeypatch.setenv(SALLA_BRANCH_SYNC_FEATURE_FLAG, "true")

    assert salla_branch_inventory_sync_enabled() is True
    assert _require_branch_sync_enabled() is None


def test_product_is_global_but_target_quantity_is_warehouse_scoped():
    rows = [
        _stock(warehouse="wh-1", remaining=4),
        _stock(warehouse="wh-2", remaining=11),
    ]

    first = build_mezan_branch_targets(
        products=[_product()],
        profiles=[_profile()],
        stock_rows=rows,
        warehouse_id="wh-1",
    )
    second = build_mezan_branch_targets(
        products=[_product()],
        profiles=[_profile()],
        stock_rows=rows,
        warehouse_id="wh-2",
    )

    assert first["targets"][0]["desired_quantity"] == 4
    assert second["targets"][0]["desired_quantity"] == 11
    assert first["targets"][0]["salla_product_id"] == "p-1"


def test_preorder_at_zero_is_sellable_without_inventing_mezan_stock():
    result = build_mezan_branch_targets(
        products=[_product()],
        profiles=[_profile(stockout="allow_preorder")],
        stock_rows=[],
        warehouse_id="wh-1",
    )

    assert result["targets"][0]["desired_quantity"] == 0
    assert result["targets"][0]["desired_unlimited"] is True


def test_variant_stock_never_falls_into_parent_or_other_variant():
    product = _product(
        variants_count=2,
        variants=[
            {"id": "v-gold", "sku": "GOLD", "display_name": "ذهبي"},
            {"id": "v-silver", "sku": "SILVER", "display_name": "فضي"},
        ],
    )
    stock = [
        _stock(
            remaining=30,
            salla_variant_id="v-gold",
            identifiers={"mp-1", "p-1", "GOLD"},
        ),
        _stock(
            remaining=7,
            salla_variant_id="v-silver",
            identifiers={"mp-1", "p-1", "SILVER"},
        ),
    ]

    result = build_mezan_branch_targets(
        products=[product],
        profiles=[_profile()],
        stock_rows=stock,
        warehouse_id="wh-1",
    )
    quantities = {
        row["salla_variant_id"]: row["desired_quantity"]
        for row in result["targets"]
    }

    assert quantities == {"v-gold": 30, "v-silver": 7}
    assert result["issues"] == []


def test_unlinked_variant_stock_is_blocked_from_publication():
    product = _product(
        variants_count=1,
        variants=[{"id": "v-gold", "sku": "GOLD"}],
    )
    result = build_mezan_branch_targets(
        products=[product],
        profiles=[_profile()],
        stock_rows=[_stock(remaining=9)],
        warehouse_id="wh-1",
    )

    assert result["targets"][0]["desired_quantity"] == 0
    assert result["issues"][0]["code"] == "variant_stock_not_linked"
    assert result["issues"][0]["quantity"] == 9


def test_plan_uses_atomic_delta_and_overwrite_only_for_policy_change():
    targets = build_mezan_branch_targets(
        products=[_product()],
        profiles=[_profile()],
        stock_rows=[_stock(remaining=8)],
        warehouse_id="wh-1",
    )["targets"]
    plan = build_branch_sync_plan(
        salla_branch_id="branch-1",
        warehouse_id="wh-1",
        targets=targets,
        remote_quantities=[{
            "id": "p-1",
            "quantity": 3,
            "unlimited_quantity": False,
        }],
    )
    row = plan["rows"][0]

    assert row["operation"] == "increment"
    assert row["operation_quantity"] == 5
    assert executable_payload_row(row, reason_id="123") == {
        "identifer_type": "id",
        "identifer": "p-1",
        "quantity": 5,
        "mode": "increment",
        "branch": "branch-1",
        "reason_id": 123,
        "unlimited_quantity": False,
    }

    policy_change = build_branch_sync_plan(
        salla_branch_id="branch-1",
        warehouse_id="wh-1",
        targets=targets,
        remote_quantities=[{
            "id": "p-1",
            "quantity": 99,
            "unlimited_quantity": True,
        }],
    )["rows"][0]
    assert policy_change["operation"] == "overwrite"
    assert policy_change["operation_quantity"] == 8


def test_plan_signature_changes_when_either_side_changes():
    row = {
        "target_key": "product:p-1",
        "salla_branch_id": "b-1",
        "warehouse_id": "w-1",
        "desired_quantity": 5,
        "desired_unlimited": False,
        "remote_quantity": 3,
        "remote_unlimited": False,
        "operation": "increment",
        "operation_quantity": 2,
    }
    assert plan_signature([row]) == plan_signature([dict(row)])
    assert plan_signature([row]) != plan_signature([
        {**row, "remote_quantity": 4, "operation_quantity": 1}
    ])
