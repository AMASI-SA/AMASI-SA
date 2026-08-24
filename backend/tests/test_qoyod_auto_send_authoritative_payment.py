"""Regression coverage for the unified-order Qoyod automatic-send source."""
from __future__ import annotations

import asyncio
from copy import deepcopy

from qoyod_auto_payment_freshness import (
    _canonical_from_unified,
    _invoice_financials,
    _oldest_key,
    sync_authoritative_payment_to_inbox,
)


class _Result:
    def __init__(self, *, matched_count=1, modified_count=1, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
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
    def __init__(self, row):
        self.row = deepcopy(row) if row else None
        self.updates = []

    async def find_one(self, query, projection=None):
        if not self.row:
            return None
        if query.get("user_id") != self.row.get("user_id"):
            return None
        if query.get("order_number") != self.row.get("order_number"):
            return None
        return deepcopy(self.row)

    def find(self, query, projection=None):
        if not self.row:
            return _Cursor([])
        if query.get("user_id") != self.row.get("user_id"):
            return _Cursor([])
        order_query = query.get("order_number")
        if isinstance(order_query, dict):
            allowed = {str(value) for value in order_query.get("$in", [])}
            if str(self.row.get("order_number")) not in allowed:
                return _Cursor([])
        elif order_query != self.row.get("order_number"):
            return _Cursor([])
        return _Cursor([self.row])

    async def update_one(self, query, update):
        self.updates.append((deepcopy(query), deepcopy(update)))
        if self.row:
            self.row.update(deepcopy(update.get("$set") or {}))
            return _Result()
        return _Result(matched_count=0, modified_count=0)


class _IntegrationInbox:
    def __init__(self, *, snapshot=None, markers=None):
        self.snapshot = deepcopy(snapshot) if snapshot else None
        self.markers = list(markers or [])
        self.upserts = []

    async def find_one(self, query, projection=None, sort=None):
        if query.get("connector_key") == "salla_direct_status_resync":
            return deepcopy(self.snapshot)
        return None

    def find(self, query, projection=None):
        return _Cursor(self.markers)

    async def update_one(self, query, update, upsert=False):
        self.upserts.append((deepcopy(query), deepcopy(update), upsert))
        return _Result(matched_count=0, modified_count=0, upserted_id="new")


class _DB:
    def __init__(self, unified, *, snapshot=None, markers=None):
        self.unified_orders = _UnifiedOrders(unified)
        self.integration_inbox = _IntegrationInbox(
            snapshot=snapshot,
            markers=markers,
        )


def _paid_order():
    return {
        "user_id": "owner-1",
        "order_id": "salla-1",
        "order_number": "279460595",
        "order_date": "2026-08-23",
        "order_status": "تم التنفيذ",
        "order_status_slug": "completed",
        "payment_method": "mada",
        "payment_status": "paid",
        "payment_collection_status": "paid",
        "paid_amount": 187.92,
        "remaining_amount": 0.0,
        "has_remaining_amount": False,
        "total_amount": 187.92,
        "currency": "SAR",
        "customer_name": "عزيزة الجهني",
        "customer_mobile": "0500000000",
        "items": [{
            "order_item_id": "line-1",
            "sku": "AMS-1",
            "name": "منتج تجريبي",
            "quantity": 1,
            "unit_price": 163.41,
            "tax_amount": 24.51,
            "total": 187.92,
        }],
        "raw_by_source": {
            "salla_direct": {
                "id": "salla-1",
                "reference_id": "279460595",
                "date": {"date": "2026-08-23T10:00:00+03:00"},
            }
        },
    }


def test_paid_order_without_legacy_inbox_row_creates_sender_projection():
    db = _DB(_paid_order())

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="279460595",
    ))

    assert result["ok"] is True
    assert result["source_authority"] == "unified_orders"
    assert result["item_count"] == 1
    selector, update, upsert = db.integration_inbox.upserts[-1]
    assert selector == {
        "user_id": "main",
        "connector_key": "qoyod_unified_auto_sender",
        "salla_order_number": "279460595",
    }
    assert upsert is True
    canonical = update["$set"]["canonical_payload"]
    assert canonical["total_amount"] == 187.92
    assert canonical["payment_method"] == "mada"
    assert canonical["payment_status"] == "paid"
    assert canonical["items"][0]["sku"] == "AMS-1"
    assert canonical["customer"]["name"] == "عزيزة الجهني"


def test_live_snapshot_payment_is_promoted_before_projection():
    order = _paid_order()
    order.update({
        "payment_method": "pending_payment",
        "payment_status": "unpaid",
        "payment_collection_status": "unpaid",
        "paid_amount": 0.0,
        "remaining_amount": 187.92,
        "has_remaining_amount": True,
    })
    db = _DB(order, snapshot={
        "canonical_payload": {
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "mada",
            "payment_status": "paid",
            "payment_collection_status": "paid",
            "paid_amount": 187.92,
            "remaining_amount": 0.0,
            "has_remaining_amount": False,
        }
    })

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="279460595",
    ))

    assert result["ok"] is True
    assert db.unified_orders.row["payment_method"] == "mada"
    assert db.unified_orders.row["paid_amount"] == 187.92
    assert db.unified_orders.row["remaining_amount"] == 0.0


def test_unknown_payment_needs_verification_and_does_not_upsert_sender():
    order = _paid_order()
    order.update({
        "payment_method": "mada",
        "payment_status": "",
        "payment_collection_status": "",
        "paid_amount": 0.0,
        "remaining_amount": 0.0,
        "has_remaining_amount": False,
    })
    db = _DB(order)

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="279460595",
    ))

    assert result["ok"] is False
    assert result["code"] == "authoritative_payment_needs_verification"
    assert db.integration_inbox.upserts == []


def test_unpaid_order_fails_closed_and_does_not_upsert_sender():
    order = _paid_order()
    order.update({
        "payment_status": "unpaid",
        "payment_collection_status": "unpaid",
        "paid_amount": 0.0,
        "remaining_amount": 187.92,
        "has_remaining_amount": True,
    })
    db = _DB(order)

    result = asyncio.run(sync_authoritative_payment_to_inbox(
        db,
        orders_user_id="owner-1",
        legacy_user_id="main",
        order_number="279460595",
    ))

    assert result["ok"] is False
    assert result["code"] == "authoritative_payment_not_eligible"
    assert db.integration_inbox.upserts == []


def test_canonical_projection_preserves_order_amount_items_and_customer():
    canonical = _canonical_from_unified(_paid_order())
    assert canonical["order_number"] == "279460595"
    assert canonical["order_date"].startswith("2026-08-23")
    assert canonical["total_amount"] == 187.92
    assert canonical["items"][0]["total"] == 187.92
    assert canonical["customer"]["phone"] == "0500000000"


def test_invoice_paid_status_resolves_zero_remaining():
    result = _invoice_financials({
        "total": 187.92,
        "paid_amount": 187.92,
        "status": "paid",
    })
    assert result["resolved_paid"] is True
    assert result["remaining"] == 0.0
    assert result["status"] == "paid"


def test_worker_sort_key_is_oldest_first():
    rows = [
        {"order_number": "3", "order_date": "2026-08-23"},
        {"order_number": "1", "order_date": "2026-08-20"},
        {"order_number": "2", "order_date": "2026-08-20"},
    ]
    assert [row["order_number"] for row in sorted(rows, key=_oldest_key)] == [
        "1", "2", "3",
    ]


def test_paid_exact_reference_overrides_stale_failure_classification():
    from qoyod_auto_unified.queue_counts import _proof_classification

    classification = _proof_classification(
        object(),
        {
            "qoyod_invoice_count_for_reference": 1,
            "payment_method": "mada",
            "qoyod_invoice": {
                "qoyod_invoice_id": "2116",
                "total": 187.92,
                "paid_amount": 187.92,
                "remaining": 0.0,
                "status": "paid",
            },
        },
        {
            "source": "auto_quarantine",
            "code": "legacy_sender_inbox_row_missing",
            "message": "تعذر اعتماد أحدث حالة دفع من سلة",
        },
    )

    assert classification["status"] == "أُرسل"
    assert classification["retry_allowed"] is False
    assert classification["failure_code"] is None
    assert "تمت المصالحة" in classification["reason"]


def test_unpaid_exact_reference_remains_true_exception():
    from qoyod_auto_unified.queue_counts import _proof_classification

    classification = _proof_classification(
        object(),
        {
            "qoyod_invoice_count_for_reference": 1,
            "payment_method": "mada",
            "qoyod_invoice": {
                "qoyod_invoice_id": "2117",
                "total": 187.92,
                "paid_amount": 0.0,
                "remaining": 187.92,
                "status": "unpaid",
            },
        },
        {
            "source": "manual_send_lock",
            "code": "invoice_created_payment_failed",
            "message": "تم إنشاء الفاتورة لكن فشل تسجيل السداد",
        },
    )

    assert classification["status"] == "فشل"
    assert classification["retry_allowed"] is False
    assert classification["failure_code"] == "invoice_created_payment_failed"


def test_duplicate_exact_reference_is_never_auto_reconciled():
    from qoyod_auto_unified.queue_counts import _proof_classification

    classification = _proof_classification(
        object(),
        {
            "qoyod_invoice_count_for_reference": 2,
            "payment_method": "mada",
            "qoyod_invoice": {
                "qoyod_invoice_id": "2118",
                "total": 187.92,
                "paid_amount": 187.92,
                "status": "paid",
            },
        },
        None,
    )

    assert classification["status"] == "مكرر"
    assert classification["retry_allowed"] is False


class _UpdateManyCollection:
    def __init__(self):
        self.calls = []

    async def update_many(self, query, update, **kwargs):
        self.calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        return _Result(modified_count=1)


class _InvoiceCollection:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]
        self.updates = []

    def find(self, query, projection=None):
        return _Cursor(self.rows)

    async def update_one(self, query, update, **kwargs):
        self.updates.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        invoice_id = str(query.get("qoyod_invoice_id") or "")
        for row in self.rows:
            if str(row.get("qoyod_invoice_id") or "") == invoice_id:
                row.update(deepcopy(update.get("$set") or {}))
                return _Result()
        return _Result(matched_count=0, modified_count=0)


class _ReconcileInbox(_UpdateManyCollection):
    pass


class _ReconcileDB:
    def __init__(self, invoices):
        self.unified_orders = _UnifiedOrders({
            "user_id": "owner-1",
            "order_number": "279460595",
            "payment_method": "mada",
        })
        self.qoyod_invoices = _InvoiceCollection(invoices)
        self.integration_inbox = _ReconcileInbox()
        self.qoyod_manual_auto_quarantines = _UpdateManyCollection()
        self.qoyod_manual_send_locks = _UpdateManyCollection()


def test_qoyod_sync_reconciliation_rejects_unproven_local_reference():
    from qoyod_auto_unified.reconcile import _reconcile_local_mirror_after_sync

    db = _ReconcileDB([{
        "qoyod_invoice_id": "2116",
        "invoice_number": "2116",
        "reference": "279460595",
        "source": "synced_from_qoyod",
        "total": 187.92,
        "paid_amount": 187.92,
        "remaining": 0.0,
        "status": "paid",
        "raw_response": {},
    }])

    result = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
    ))

    assert result["strict_invoice_count"] == 0
    assert result["resolved_exception_count"] == 0
    assert db.integration_inbox.calls == []
    assert db.qoyod_manual_auto_quarantines.calls == []


def test_qoyod_sync_reconciliation_closes_unique_paid_official_reference():
    from qoyod_auto_unified.reconcile import _reconcile_local_mirror_after_sync

    db = _ReconcileDB([{
        "qoyod_invoice_id": "2116",
        "invoice_number": "2116",
        "reference": "279460595",
        "source": "synced_from_qoyod",
        "total": 187.92,
        "paid_amount": 187.92,
        "remaining": 0.0,
        "status": "paid",
        "raw_response": {"reference": "279460595"},
    }])

    result = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
    ))

    assert result["strict_invoice_count"] == 1
    assert result["resolved_exception_count"] == 1
    assert result["inbox_markers_updated"] == 1
    assert db.unified_orders.row["qoyod_invoice_id"] == "2116"
    assert db.qoyod_manual_auto_quarantines.calls
    assert db.qoyod_manual_send_locks.calls
    assert db.qoyod_invoices.updates
    assert result["processed_invoice_count"] == 1
    assert result["repair_backlog_drained"] is True


def test_qoyod_sync_reconciliation_keeps_duplicate_reference_open():
    from qoyod_auto_unified.reconcile import _reconcile_local_mirror_after_sync

    rows = [
        {
            "qoyod_invoice_id": "2116",
            "invoice_number": "2116",
            "reference": "279460595",
            "source": "synced_from_qoyod",
            "total": 187.92,
            "paid_amount": 187.92,
            "remaining": 0.0,
            "status": "paid",
            "raw_response": {"reference": "279460595"},
        },
        {
            "qoyod_invoice_id": "2117",
            "invoice_number": "2117",
            "reference": "279460595",
            "source": "synced_from_qoyod",
            "total": 187.92,
            "paid_amount": 187.92,
            "remaining": 0.0,
            "status": "paid",
            "raw_response": {"reference": "279460595"},
        },
    ]
    db = _ReconcileDB(rows)

    result = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
    ))

    assert result["strict_invoice_count"] == 2
    assert result["duplicate_invoice_rows_not_auto_resolved"] == 2
    assert result["resolved_exception_count"] == 0
    assert db.qoyod_manual_auto_quarantines.calls == []
    assert db.qoyod_invoices.updates == []


def test_qoyod_sync_reconciliation_limits_and_resumes_repair_batches():
    from qoyod_auto_unified.reconcile import _reconcile_local_mirror_after_sync

    first = {
        "qoyod_invoice_id": "2116",
        "invoice_number": "2116",
        "reference": "279460595",
        "source": "synced_from_qoyod",
        "total": 187.92,
        "paid_amount": 187.92,
        "remaining": 0.0,
        "status": "paid",
        "raw_response": {"reference": "279460595"},
    }
    second = {
        **first,
        "qoyod_invoice_id": "2117",
        "invoice_number": "2117",
        "reference": "279460596",
        "raw_response": {"reference": "279460596"},
    }
    db = _ReconcileDB([first, second])
    original_row = deepcopy(db.unified_orders.row)

    def unified_find(query, projection=None):
        rows = [
            original_row,
            {**original_row, "order_number": "279460596"},
        ]
        return _Cursor(rows)

    db.unified_orders.find = unified_find

    first_run = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
        repair_limit=1,
    ))
    second_run = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
        repair_limit=1,
    ))

    assert first_run["processed_invoice_count"] == 1
    assert first_run["deferred_repair_count"] == 1
    assert first_run["repair_backlog_drained"] is False
    assert second_run["skipped_already_reconciled"] == 1
    assert second_run["processed_invoice_count"] == 1
    assert second_run["deferred_repair_count"] == 0
    assert second_run["repair_backlog_drained"] is True


def test_qoyod_sync_reconciliation_reopens_when_financial_state_changes():
    from qoyod_auto_unified.reconcile import _reconcile_local_mirror_after_sync

    db = _ReconcileDB([{
        "qoyod_invoice_id": "2116",
        "invoice_number": "2116",
        "reference": "279460595",
        "source": "synced_from_qoyod",
        "total": 187.92,
        "paid_amount": 0.0,
        "remaining": 187.92,
        "status": "unpaid",
        "raw_response": {"reference": "279460595"},
    }])

    unpaid_run = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
    ))
    db.qoyod_invoices.rows[0].update({
        "paid_amount": 187.92,
        "remaining": 0.0,
        "status": "paid",
    })
    paid_run = asyncio.run(_reconcile_local_mirror_after_sync(
        db,
        orders_user_id="owner-1",
        markers_user_id="main",
    ))

    assert unpaid_run["processed_invoice_count"] == 1
    assert unpaid_run["resolved_exception_count"] == 0
    assert paid_run["processed_invoice_count"] == 1
    assert paid_run["skipped_already_reconciled"] == 0
    assert paid_run["resolved_exception_count"] == 1
