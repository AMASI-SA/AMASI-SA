"""Regression tests for Plan-B automatic-send payment freshness."""
from pathlib import Path


def test_order_engine_bootstrap_marks_payment_method_latest_wins():
    source = Path("order_engine/__init__.py").read_text(encoding="utf-8")
    assert '_orders_db.CRITICAL_FIELDS.add("payment_method")' in source


def test_orders_db_tracks_latest_payment_facts():
    source = Path("orders_db.py").read_text(encoding="utf-8")
    for field in (
        '"payment_method"',
        '"payment_status"',
        '"paid_amount"',
        '"remaining_amount"',
        '"payment_collection_status"',
    ):
        assert field in source
