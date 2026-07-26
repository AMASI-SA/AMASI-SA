"""Regression tests for current-status authority in Plan-B manual list."""
import asyncio

from qoyod_manual_current_status import filter_pending_by_current_status


class _UnifiedOrders:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, projection=None):
        key = (query["user_id"], query["order_number"])
        row = self.rows.get(key)
        return dict(row) if row else None


class _DB:
    def __init__(self, rows):
        self.unified_orders = _UnifiedOrders(rows)


def test_processing_order_is_removed_from_completed_manual_list():
    db = _DB({
        ("owner-1", "274071833"): {
            "order_status": "قيد التنفيذ",
            "order_status_slug": "processing",
        }
    })
    result = asyncio.run(filter_pending_by_current_status(
        db,
        {"orders": [{"order_number": "274071833", "salla_status": "تم التنفيذ"}], "counts": {"returned": 1}},
        user_id="main",
        orders_user_id="owner-1",
        status="completed",
    ))
    assert result["orders"] == []
    assert result["counts"]["returned"] == 0
    assert result["counts"]["excluded_current_status_mismatch"] == 1


def test_current_completed_order_remains_visible():
    db = _DB({
        ("owner-1", "274071833"): {
            "order_status": "تم التنفيذ",
            "order_status_slug": "completed",
        }
    })
    result = asyncio.run(filter_pending_by_current_status(
        db,
        {"orders": [{"order_number": "274071833", "salla_status": "تم التنفيذ"}], "counts": {"returned": 1}},
        user_id="main",
        orders_user_id="owner-1",
        status="completed",
    ))
    assert len(result["orders"]) == 1
    assert result["orders"][0]["status_source"] == "unified_orders_current"


def test_legacy_order_without_unified_row_keeps_existing_decision():
    db = _DB({})
    result = asyncio.run(filter_pending_by_current_status(
        db,
        {"orders": [{"order_number": "legacy-1", "salla_status": "تم التنفيذ"}], "counts": {"returned": 1}},
        user_id="main",
        orders_user_id="owner-1",
        status="completed",
    ))
    assert len(result["orders"]) == 1
