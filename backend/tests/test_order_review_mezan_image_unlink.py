from datetime import datetime, timezone

from order_review_mezan_image_unlink import (
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
