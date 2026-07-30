from types import SimpleNamespace as Obj

from product_inventory_rules import (
    build_inventory_configuration_key,
    canonical_specifications,
    choose_inventory_rows,
    order_item_specifications,
)


def _row(
    *,
    state,
    specs,
    quantity=3,
    warehouse="wh-1",
    key="config-1",
):
    return {
        "warehouse_id": warehouse,
        "identifiers": {"AMS-FLOWER"},
        "remaining": float(quantity),
        "preparation_state": state,
        "specifications": specs,
        "configuration_key": key,
    }


def test_configuration_key_is_stable_across_spacing_and_pair_order():
    first = build_inventory_configuration_key(
        sku="ams-flower",
        preparation_state="ready_complete",
        specifications={"الاسم": " عبير ", "اللون": "ذهبي"},
    )
    second = build_inventory_configuration_key(
        sku="AMS-FLOWER",
        preparation_state="ready_complete",
        specifications=[
            {"name": " اللون ", "value": " ذهبي "},
            {"name": "الاسم", "value": "عبير"},
        ],
    )

    assert first == second
    assert canonical_specifications({
        "الاسم": " عبير ",
        "اللون": "ذهبي",
    }) == {"الاسم": "عبير", "اللون": "ذهبي"}


def test_order_item_signature_includes_options_and_custom_name():
    item = Obj(
        options_normalized={"اللون": "ذهبي"},
        custom_fields=[{"name": "الاسم", "value": " عبير "}],
        color="ذهبي",
        size=None,
        material=None,
    )

    assert order_item_specifications(item) == {
        "الاسم": "عبير",
        "اللون": "ذهبي",
    }


def test_semantic_specification_names_and_nested_option_values_are_normalized():
    item = Obj(
        options_normalized={
            "لون السلسال": {"value": "ذهبي"},
            "إضافة الاسم": "نعم",
        },
        custom_fields=[
            {"name": "الاسم المراد كتابته", "value": "عبير"},
        ],
        color="ذهبي",
        size=None,
        material=None,
    )

    assert order_item_specifications(item) == {
        "الاسم": "عبير",
        "اللون": "ذهبي",
    }


def test_exact_ready_stock_skips_preparation_for_matching_name_and_color():
    rows = [
        _row(
            state="ready_complete",
            specs={"الاسم": "عبير", "اللون": "ذهبي"},
            quantity=100,
            key="abeer-gold",
        ),
        _row(
            state="requires_preparation",
            specs={"اللون": "ذهبي"},
            quantity=50,
            key="base-gold",
        ),
    ]

    result = choose_inventory_rows(
        rows=rows,
        identifiers={"AMS-FLOWER"},
        quantity=1,
        order_specifications={"الاسم": "عبير", "اللون": "ذهبي"},
        preparation_required=True,
    )

    assert result["available"] is True
    assert result["match_type"] == "ready_complete"
    assert result["preparation_satisfied_by_ready_stock"] is True
    assert result["configuration_keys"] == ["abeer-gold"]
    assert result["allocations"][0]["quantity"] == 1
    assert rows[0]["remaining"] == 99
    assert rows[1]["remaining"] == 50


def test_nonmatching_name_uses_base_stock_and_keeps_preparation():
    rows = [
        _row(
            state="ready_complete",
            specs={"الاسم": "عبير", "اللون": "ذهبي"},
            quantity=100,
            key="abeer-gold",
        ),
        _row(
            state="requires_preparation",
            specs={"اللون": "ذهبي"},
            quantity=50,
            key="base-gold",
        ),
    ]

    result = choose_inventory_rows(
        rows=rows,
        identifiers={"AMS-FLOWER"},
        quantity=1,
        order_specifications={"الاسم": "نورة", "اللون": "ذهبي"},
        preparation_required=True,
    )

    assert result["match_type"] == "requires_preparation"
    assert result["preparation_satisfied_by_ready_stock"] is False
    assert rows[0]["remaining"] == 100
    assert rows[1]["remaining"] == 49


def test_ready_stock_never_matches_different_color():
    rows = [_row(
        state="ready_complete",
        specs={"الاسم": "عبير", "اللون": "ذهبي"},
        quantity=100,
    )]

    result = choose_inventory_rows(
        rows=rows,
        identifiers={"AMS-FLOWER"},
        quantity=1,
        order_specifications={"الاسم": "عبير", "اللون": "فضي"},
        preparation_required=True,
    )

    assert result["available"] is False
    assert result["available_quantity"] == 0
    assert result["total_product_quantity"] == 100
    assert rows[0]["remaining"] == 100


def test_ready_and_base_stock_can_cover_one_multi_unit_order_together():
    rows = [
        _row(
            state="ready_complete",
            specs={"الاسم": "عبير", "اللون": "ذهبي"},
            quantity=1,
            key="abeer-gold",
        ),
        _row(
            state="requires_preparation",
            specs={"اللون": "ذهبي"},
            quantity=1,
            key="base-gold",
        ),
    ]

    result = choose_inventory_rows(
        rows=rows,
        identifiers={"AMS-FLOWER"},
        quantity=2,
        order_specifications={"الاسم": "عبير", "اللون": "ذهبي"},
        preparation_required=True,
    )

    assert result["available"] is True
    assert result["match_type"] == "mixed"
    assert result["preparation_satisfied_by_ready_stock"] is False
    assert result["configuration_keys"] == ["abeer-gold", "base-gold"]
    assert [row["quantity"] for row in result["allocations"]] == [1, 1]
    assert rows[0]["remaining"] == 0
    assert rows[1]["remaining"] == 0
