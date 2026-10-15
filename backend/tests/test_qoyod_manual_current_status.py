"""Regression tests for current-status authority in Plan-B manual list."""
import asyncio

from qoyod_manual_current_status import filter_pending_by_current_status
from integrations.qoyod_manual.missing_diagnostics import (
    _status_key_from_unified,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _UnifiedOrders:
    def __init__(self, rows):
        self.rows = rows
        self.find_calls = 0

    def find(self, query, projection=None):
        self.find_calls += 1
        owner_id = query["user_id"]
        order_numbers = set(query["order_number"]["$in"])
        matches = []
        for (row_owner_id, order_number), row in self.rows.items():
            if row_owner_id == owner_id and order_number in order_numbers:
                matches.append({"order_number": order_number, **dict(row)})
        return _Cursor(matches)


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


def test_current_status_lookup_is_batched_for_all_orders():
    db = _DB({
        ("owner-1", "275957683"): {
            "order_status": "جاري التوصيل",
            "order_status_slug": "in_delivery",
        },
        ("owner-1", "275899999"): {
            "order_status": "جاري التوصيل",
            "order_status_slug": "in_delivery",
        },
    })
    result = asyncio.run(filter_pending_by_current_status(
        db,
        {
            "orders": [
                {"order_number": "275957683"},
                {"order_number": "275899999"},
            ],
            "counts": {"returned": 2},
        },
        user_id="main",
        orders_user_id="owner-1",
        status="in_delivery",
    ))
    assert len(result["orders"]) == 2
    assert db.unified_orders.find_calls == 1


def test_shipping_and_delivering_slugs_are_in_delivery_aliases():
    db = _DB({
        ("owner-1", "275957683"): {
            "order_status": "جاري التوصيل",
            "order_status_slug": "shipping",
        },
        ("owner-1", "275957684"): {
            "order_status": "تم الشحن",
            "order_status_slug": "shipped",
        },
        ("owner-1", "275957685"): {
            "order_status": "جاري التوصيل",
            "order_status_slug": "delivering",
        },
    })
    result = asyncio.run(filter_pending_by_current_status(
        db,
        {
            "orders": [
                {"order_number": "275957683"},
                {"order_number": "275957684"},
                {"order_number": "275957685"},
            ],
            "counts": {"returned": 3},
        },
        user_id="main",
        orders_user_id="owner-1",
        status="in_delivery",
    ))
    assert [row["order_number"] for row in result["orders"]] == [
        "275957683", "275957685",
    ]


def test_diagnostic_classifier_maps_delivering_to_in_delivery():
    assert _status_key_from_unified({
        "order_status_slug": "delivering",
        "order_status": "جاري التوصيل",
    }) == "in_delivery"
