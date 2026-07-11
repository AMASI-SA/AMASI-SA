from pathlib import Path


def test_salla_orders_sync_does_not_use_deprecated_expanded():
    source = Path(
        "salla_integration/sync.py"
    ).read_text(encoding="utf-8")

    assert '"expanded"' not in source
    assert source.count('"format": "light"') >= 2
