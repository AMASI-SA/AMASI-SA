"""Tests for Qoyod → Orders V2 local accounting projection."""
import asyncio

from qoyod_order_accounting_sync import (
    repair_qoyod_order_accounting,
    sync_unified_order_accounting_from_result,
)


class _Result:
    def __init__(self, matched_count=1, modified_count=1):
        self.matched_count = matched_count
        self.modified_count = modified_count


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *args, **kwargs):
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _UnifiedOrders:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))
        return _Result()


class _QoyodInvoices:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        return _Cursor([dict(row) for row in self.rows])


class _IntegrationInbox:
    def __init__(self):
        self.calls = []

    async def update_many(self, query, update):
        self.calls.append((query, update))
        return _Result(modified_count=1)


class _DB:
    def __init__(self, invoices=None):
        self.unified_orders = _UnifiedOrders()
        self.qoyod_invoices = _QoyodInvoices(invoices or [])
        self.integration_inbox = _IntegrationInbox()


def test_paid_manual_result_updates_order_accounting():
    db = _DB()
    result = asyncio.run(sync_unified_order_accounting_from_result(
        db,
        orders_user_id="owner-1",
        order_number="275957683",
        result={
            "invoice_id": 1110,
            "invoice_number": "1110",
            "payment_id": 1050,
            "salla_total": 239.41,
            "payment_amount": 239.41,
            "difference": 0.0,
        },
    ))

    assert result["updated"] is True
    query, update = db.unified_orders.calls[0]
    assert query == {
        "user_id": "owner-1",
        "order_number": "275957683",
    }
    assert update["$set"]["accounting.status"] == "paid"
    assert update["$set"]["accounting.invoice_number"] == "1110"
    assert update["$set"]["accounting.payment_id"] == "1050"
    assert update["$set"]["accounting.remaining"] == 0.0


def test_cod_result_stays_unpaid_without_payment_marker():
    db = _DB()
    result = asyncio.run(sync_unified_order_accounting_from_result(
        db,
        orders_user_id="owner-1",
        order_number="276000001",
        result={
            "invoice_id": 1111,
            "invoice_number": "1111",
            "payment_id": None,
            "salla_total": 174.91,
            "expected_total": 174.91,
            "payment_amount": 0.0,
            "difference": 0.0,
            "invoice_only": True,
        },
    ))

    assert result["status"] == "unpaid"
    assert result["remaining"] == 174.91
    _, update = db.unified_orders.calls[0]
    assert "accounting.payment_id" in update["$unset"]
    assert update["$set"]["accounting.paid_amount"] == 0.0


def test_repair_ignores_dry_markers_and_projects_real_invoice():
    db = _DB([
        {
            "qoyod_invoice_id": "DRY:invoice:test",
            "reference": "273310114",
        },
        {
            "qoyod_invoice_id": "1110",
            "invoice_number": "1110",
            "reference": "275957683",
            "total": 239.41,
            "paid_amount": 239.41,
            "remaining": 0.0,
            "status": "paid",
        },
    ])

    result = asyncio.run(repair_qoyod_order_accounting(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
        actor="test",
    ))

    assert result["counts"]["skipped_not_real"] == 1
    assert result["counts"]["unified_orders_updated"] == 1
    assert result["counts"]["inbox_markers_updated"] == 1
    assert len(db.unified_orders.calls) == 1
    query, _ = db.unified_orders.calls[0]
    assert query["order_number"] == "275957683"

