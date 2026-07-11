from pathlib import Path


def test_orders_sync_does_not_stop_on_short_capped_page():
    source = Path(
        "salla_integration/sync.py"
    ).read_text(encoding="utf-8")

    assert "if len(data) < ORDERS_PER_PAGE:" not in source
    assert 'pagination.get("totalPages")' in source
    assert 'pagination.get("last_page")' in source
