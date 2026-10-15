from datetime import datetime, timezone

import campaign_ai_product_change_history_v3 as history


def test_product_state_preserves_zero_inventory():
    state = history._state({
        "name": "منتج",
        "status": "active",
        "quantity": 0,
        "price": 99,
        "sale_price": None,
        "images": [],
        "options": [],
        "variants": [],
    })
    assert state["quantity"] == 0
    assert state["visibility"] == "public_status_expected"


def test_changes_are_recorded_only_when_field_actually_changes():
    observed = datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)
    previous = {
        "title": "منتج",
        "price": 99.0,
        "sale_price": None,
        "visibility": "public_status_expected",
        "status": "active",
        "quantity": 10.0,
        "description_hash": "a",
        "hero_image": "hero-a",
        "gallery_hash": "g1",
        "options_hash": "o1",
        "variants_hash": "v1",
    }
    current = dict(previous)
    current["price"] = 119.0
    current["visibility"] = "hidden_or_inactive"
    changes = history._changes(previous, current, observed)
    assert set(changes) == {"price", "visibility"}
    assert changes["price"] == observed.isoformat()
    assert changes["visibility"] == observed.isoformat()


def test_first_snapshot_never_invents_historical_change_time():
    observed = datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)
    current = {field: None for field in history.TRACKED_FIELDS}
    assert history._changes(None, current, observed) == {}
