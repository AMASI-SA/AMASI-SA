from pathlib import Path


def test_recent_sync_is_first_page_only_and_never_archives():
    source = Path("product_v2_recent_sync_routes.py").read_text(encoding="utf-8")
    assert '"/products"' in source
    assert '"page": 1' in source
    assert "RECENT_SYNC_LIMIT" in source
    assert "update_many" not in source
    assert "archived_at" not in source
    assert '@router.post("/sync-recent")' in source
