from datetime import datetime, timezone

from order_review_mezan_image_unlink import (
    build_workflow_clear_plans,
    clear_image_from_workflow_items,
    mezan_image_url,
    workflow_uses_image,
)


def test_workflow_usage_detects_only_the_requested_image():
    workflow = {
        "items": [
            {"order_item_id": "item-1", "selected_image_url": mezan_image_url("abc")},
            {"order_item_id": "item-2", "selected_image_url": mezan_image_url("def")},
        ]
    }

    assert workflow_uses_image(workflow, mezan_image_url("abc")) is True
    assert workflow_uses_image(workflow, mezan_image_url("missing")) is False
    assert workflow_uses_image(None, mezan_image_url("abc")) is False


def test_clear_image_removes_only_matching_links_and_preserves_other_state():
    timestamp = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    items = [
        {
            "order_item_id": "item-1",
            "selected_image_url": mezan_image_url("abc"),
            "selected_image_source": "manual",
            "revision": 4,
            "preparation_note": "احتفظ بهذه الملاحظة",
        },
        {
            "order_item_id": "item-2",
            "selected_image_url": mezan_image_url("def"),
            "revision": 2,
        },
        "legacy-row",
    ]

    updated, cleared = clear_image_from_workflow_items(
        items,
        mezan_image_url("abc"),
        actor_id="owner-1",
        updated_at=timestamp,
    )

    assert cleared == 1
    assert "selected_image_url" not in updated[0]
    assert "selected_image_source" not in updated[0]
    assert updated[0]["revision"] == 5
    assert updated[0]["preparation_note"] == "احتفظ بهذه الملاحظة"
    assert updated[0]["updated_by"] == "owner-1"
    assert updated[0]["updated_at"] == timestamp
    assert updated[1] == items[1]
    assert updated[2] == "legacy-row"


def test_clear_image_with_no_match_is_a_noop():
    items = [{"order_item_id": "item-1", "selected_image_url": mezan_image_url("abc")}]
    updated, cleared = clear_image_from_workflow_items(
        items,
        mezan_image_url("def"),
        actor_id="owner-1",
        updated_at="now",
    )

    assert cleared == 0
    assert updated == items


def test_global_clear_plan_includes_all_orders_and_completed_stages():
    timestamp = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    image_url = mezan_image_url("shared")
    workflows = [
        {
            "order_number": "1001",
            "stage": "pending_review",
            "revision": 3,
            "items": [
                {
                    "order_item_id": "a",
                    "selected_image_url": image_url,
                    "selected_image_source": "default",
                    "revision": 1,
                    "preparation_note": "يبقى",
                },
            ],
        },
        {
            "order_number": "1002",
            "stage": "reviewed",
            "revision": 7,
            "items": [
                {
                    "order_item_id": "b",
                    "selected_image_url": image_url,
                    "selected_image_source": "options",
                    "revision": 4,
                },
                {
                    "order_item_id": "c",
                    "selected_image_url": mezan_image_url("other"),
                    "revision": 2,
                },
            ],
        },
        {
            "order_number": "1003",
            "stage": "completed",
            "items": [
                {
                    "order_item_id": "d",
                    "selected_image_url": image_url,
                    "revision": 0,
                },
            ],
        },
        {
            "order_number": "1004",
            "stage": "pending_review",
            "revision": 1,
            "items": [
                {
                    "order_item_id": "e",
                    "selected_image_url": mezan_image_url("other"),
                },
            ],
        },
    ]

    plans, cleared = build_workflow_clear_plans(
        workflows,
        image_url,
        actor_id="owner-1",
        updated_at=timestamp,
    )

    assert cleared == 3
    assert [plan["order_number"] for plan in plans] == ["1001", "1002", "1003"]
    assert [plan["stage"] for plan in plans] == [
        "pending_review",
        "reviewed",
        "completed",
    ]
    assert plans[0]["expected_revision"] == 3
    assert plans[0]["next_revision"] == 4
    assert plans[2]["revision_present"] is False
    assert plans[2]["next_revision"] == 1

    first_item = plans[0]["items"][0]
    assert "selected_image_url" not in first_item
    assert "selected_image_source" not in first_item
    assert first_item["preparation_note"] == "يبقى"

    second_unrelated = plans[1]["items"][1]
    assert second_unrelated["selected_image_url"] == mezan_image_url("other")
