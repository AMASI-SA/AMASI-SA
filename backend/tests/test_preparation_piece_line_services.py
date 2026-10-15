from preparation_piece_line_services import (
    build_line_service_plans,
    preparation_line_service_key,
)
from preparation_piece_operations import inherit_required_services


def _resources_and_bindings():
    resources = {
        "gold-paint": {
            "id": "gold-paint",
            "name": "طلاء ذهبي",
            "kind": "service",
        },
        "silver-paint": {
            "id": "silver-paint",
            "name": "طلاء فضي",
            "kind": "service",
        },
    }
    bindings = [
        {
            "salla_product_id": "44",
            "mode": "resource",
            "resource_id": "gold-paint",
            "option_name": "اللون",
            "value_name": "ذهبي",
        },
        {
            "salla_product_id": "44",
            "mode": "resource",
            "resource_id": "silver-paint",
            "option_name": "اللون",
            "value_name": "فضي",
        },
    ]
    return resources, bindings


def test_same_product_keeps_different_option_services_per_order_line():
    batch = {
        "lines": [
            {
                "order_number": "3001",
                "order_item_id": "item-gold",
                "product_id": "44",
                "file_spec_fields": [
                    {"name": "اللون", "value": "ذهبي"},
                ],
            },
            {
                "order_number": "3002",
                "order_item_id": "item-silver",
                "product_id": "44",
                "file_spec_fields": [
                    {"name": "اللون", "value": "فضي"},
                ],
            },
        ],
    }
    resources, bindings = _resources_and_bindings()

    plans = build_line_service_plans(
        batch=batch,
        product_links=[],
        option_bindings=bindings,
        resources_by_id=resources,
        inherit_services=inherit_required_services,
    )

    gold_key = preparation_line_service_key(batch["lines"][0])
    silver_key = preparation_line_service_key(batch["lines"][1])
    assert [row["service_id"] for row in plans[gold_key]["services"]] == [
        "gold-paint",
    ]
    assert [row["service_id"] for row in plans[silver_key]["services"]] == [
        "silver-paint",
    ]
    assert gold_key != silver_key


def test_pdf_replacement_does_not_change_original_option_service():
    batch = {
        "lines": [{
            "order_number": "3001",
            "order_item_id": "item-gold",
            "product_id": "44",
            # Merchant-facing PDF wording was changed.
            "file_spec_fields": [
                {"spec_key": "color", "name": "لون الطلاء", "value": "ذهبي فاخر"},
            ],
            # Operational service rules retain the original Salla option.
            "service_spec_fields": [
                {"spec_key": "color", "name": "اللون", "value": "ذهبي"},
            ],
        }],
    }
    resources, bindings = _resources_and_bindings()

    plans = build_line_service_plans(
        batch=batch,
        product_links=[],
        option_bindings=bindings,
        resources_by_id=resources,
        inherit_services=inherit_required_services,
    )

    key = preparation_line_service_key(batch["lines"][0])
    assert [row["service_id"] for row in plans[key]["services"]] == [
        "gold-paint",
    ]
