"""Stage-one review invariants: image learning, RBAC and status lookup."""

from datetime import datetime, timezone

from order_item_engine.models import (
    OrderItemIdentityDTO,
    OrderItemOptionDTO,
    OrderItemSourceDTO,
)
from order_review_routes import (
    _can_review,
    _merchant_user_id,
    _reviewed_status_id,
    build_image_preference_identity,
)


def item(*, color="فضي", personal_name="أحمد", product_id="p-1"):
    return OrderItemIdentityDTO(
        order_item_id="item-1",
        order_id="order-1",
        order_number="300",
        order_created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        line_index=0,
        source=OrderItemSourceDTO(source_order_id="order-1"),
        product_id=product_id,
        name="سلسال",
        quantity=1,
        color=color,
        options=[
            OrderItemOptionDTO(name="اللون", value=color),
            OrderItemOptionDTO(name="الاسم المنقوش", value=personal_name),
        ],
    )


def test_image_preference_reuses_visual_options_not_personal_text():
    first = build_image_preference_identity(item(personal_name="أحمد"))
    second = build_image_preference_identity(item(personal_name="سارة"))

    assert first[0] == "product:p-1"
    assert first[1] == second[1]
    assert "الاسم المنقوش" not in first[2]


def test_image_preference_separates_color_and_product():
    silver = build_image_preference_identity(item(color="فضي"))
    gold = build_image_preference_identity(item(color="ذهبي"))
    other_product = build_image_preference_identity(item(product_id="p-2"))

    assert silver[1] != gold[1]
    assert silver[1] != other_product[1]


def test_only_order_managers_can_review():
    assert _can_review({"id": "1", "role": "owner"})
    assert _can_review({"id": "2", "role": "operations", "created_by": "1"})
    assert _can_review({"id": "3", "role": "viewer", "extra_permissions": ["orders.manage"]})
    assert not _can_review({"id": "4", "role": "viewer"})
    assert not _can_review({"id": "5", "role": "operations", "denied_permissions": ["orders.manage"]})


def test_employee_reads_store_owner_data_but_keeps_separate_actor():
    assert _merchant_user_id({"id": "employee-1", "role": "operations", "created_by": "owner-1"}) == "owner-1"
    assert _merchant_user_id({"id": "owner-1", "role": "owner"}) == "owner-1"


def test_salla_reviewed_status_id_accepts_both_arabic_names():
    response = {
        "data": [
            {"id": 10, "name": "بانتظار المراجعة"},
            {"children": [{"status_id": "22", "name": "تمت المراجعة"}]},
        ]
    }
    assert _reviewed_status_id(response) == 22
