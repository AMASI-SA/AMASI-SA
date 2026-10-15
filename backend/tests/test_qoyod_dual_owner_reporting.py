"""Regression coverage for Qoyod markers split across owner scopes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re

from integrations.qoyod.reconciliation_v2 import (
    MATCHED,
    NEEDS_REPAIR_MARKER,
    _has_marker_in_inbox,
    run_reconciliation_v2,
)
from integrations.qoyod.unsent_orders import SENT, UNSENT, list_unsent_orders


def _dotted(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _matches(doc, query):
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in expected):
                return False
            continue
        actual = _dotted(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            if "$gte" in expected and (
                actual is None or actual < expected["$gte"]
            ):
                return False
            if "$regex" in expected and not re.search(
                expected["$regex"], str(actual or "")
            ):
                return False
            continue
        if actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.limit_value = None

    def sort(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.limit_value = int(value)
        return self

    def __aiter__(self):
        rows = self.rows
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        self.iterator = iter(rows)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def find(self, query, projection=None):
        self.queries.append(query)
        return _Cursor([
            dict(row) for row in self.rows if _matches(row, query)
        ])


class _DB:
    def __init__(self, *, inbox=(), invoices=(), orders=()):
        self.integration_inbox = _Collection(inbox)
        self.qoyod_invoices = _Collection(invoices)
        self.unified_orders = _Collection(orders)


def _run(awaitable):
    return asyncio.run(awaitable)


def _inbox(owner, order_number, *, marker=None, received_at=None):
    return {
        "user_id": owner,
        "id": f"{owner}-{order_number}",
        "trace_id": f"trace-{owner}-{order_number}",
        "salla_order_number": order_number,
        "received_at": received_at or datetime.now(timezone.utc),
        "pipeline_stage": "RECEIVED",
        "manual_qoyod_invoice_id": marker,
        "canonical_payload": {
            "order_date": "2026-08-10",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "mada",
            "total_amount": 100.0,
        },
    }


def _unified(owner, order_number, *, order_date="2026-08-10"):
    return {
        "user_id": owner,
        "order_number": order_number,
        "order_date": order_date,
        "order_status": "completed",
        "order_status_slug": "completed",
        "payment_method": "mada",
        "total_amount": 100.0,
    }


def test_unsent_report_reads_only_main_and_current_orders_owner():
    now = datetime.now(timezone.utc)
    inbox = [
        _inbox("merchant-1", "277274465", marker="1363",
               received_at=now),
        _inbox("merchant-1", "277300001", received_at=now),
        _inbox("main", "277300001",
               received_at=now - timedelta(seconds=1)),
        _inbox("main", "277300002", received_at=now),
        _inbox("merchant-2", "277300002", marker="9999",
               received_at=now),
        _inbox("merchant-2", "277300003", marker="9998",
               received_at=now),
    ]
    db = _DB(
        inbox=inbox,
        orders=[
            _unified("merchant-1", "277274465"),
            _unified("merchant-1", "277300001"),
            _unified("merchant-1", "277300002"),
            _unified("merchant-2", "277300003"),
        ],
        invoices=[{
            "user_id": "main",
            "qoyod_invoice_id": "1363",
            "reference": "277274465",
            "qoyod_official_reference": "277274465",
            "reference_provenance": "qoyod.reference",
        }],
    )

    result = _run(list_unsent_orders(
        db,
        user_id="main",
        orders_user_id="merchant-1",
        days=30,
        limit=1000,
    ))

    assert result["counts"][SENT] == 1
    assert result["counts"][UNSENT] == 2
    assert result["total"] == 3
    assert {row["order_number"] for row in result["orders"]} == {
        "277274465",
        "277300001",
        "277300002",
    }
    sent = next(
        row for row in result["orders"]
        if row["order_number"] == "277274465"
    )
    assert sent["status"] == SENT
    assert sent["debug"]["invoice_id"] == "1363"
    assert sent["debug"]["match_source"] == "qoyod_invoices.reference"
    duplicated = next(
        row for row in result["orders"]
        if row["order_number"] == "277300001"
    )
    assert duplicated["events_count"] == 2

    inbox_query = db.integration_inbox.queries[0]
    assert set(inbox_query["user_id"]["$in"]) == {"main", "merchant-1"}
    assert "merchant-2" not in inbox_query["user_id"]["$in"]


def test_unsent_report_deduplicates_when_main_is_the_orders_owner():
    db = _DB(
        inbox=[_inbox("main", "277274465", marker="1363")],
        orders=[_unified("main", "277274465")],
        invoices=[{
            "user_id": "main",
            "qoyod_invoice_id": "1363",
            "reference": "277274465",
            "qoyod_official_reference": "277274465",
            "reference_provenance": "qoyod.reference",
        }],
    )

    result = _run(list_unsent_orders(
        db,
        user_id="main",
        orders_user_id="main",
        days=30,
        limit=1000,
    ))

    assert result["total"] == 1
    assert result["counts"][SENT] == 1
    assert db.integration_inbox.queries[0]["user_id"] == "main"


def test_unsent_report_applies_real_requested_window_without_hidden_floor():
    now = datetime.now(timezone.utc)
    before_floor = _inbox(
        "main", "269999991", received_at=now,
    )
    before_floor["canonical_payload"]["order_date"] = "2026-06-30"

    missing_date = _inbox(
        "main", "269999992", received_at=now,
    )
    missing_date["canonical_payload"].pop("order_date")

    on_floor = _inbox(
        "main", "269999993", received_at=now,
    )
    on_floor["canonical_payload"]["order_date"] = "2026-07-01"

    result = _run(list_unsent_orders(
        _DB(
            inbox=[before_floor, missing_date, on_floor],
            orders=[
                _unified("main", "269999991", order_date="2026-06-30"),
                {
                    **_unified("main", "269999992"),
                    "order_date": None,
                },
                _unified("main", "269999993", order_date="2026-07-01"),
            ],
        ),
        user_id="main",
        orders_user_id="main",
        days=365,
        limit=1000,
    ))

    assert result["counts"][UNSENT] == 2
    assert [row["order_number"] for row in result["orders"]] == [
        "269999993",
        "269999991",
    ]
    assert result["sync_start_date"] == "2026-07-01"
    assert result["excluded_outside_requested_period"] == 0


def test_reconciliation_accepts_marker_from_current_orders_owner():
    order_number = "277274465"
    db = _DB(
        inbox=[_inbox("merchant-1", order_number, marker="1363")],
        invoices=[{
            "user_id": "main",
            "qoyod_invoice_id": "1363",
            "invoice_number": "277274465",
            "reference": order_number,
            "qoyod_official_reference": order_number,
            "reference_provenance": "qoyod.reference",
            "issue_date": "2026-08-12",
            "total": 170.83,
            "paid_amount": 170.83,
            "remaining": 0.0,
            "status": "paid",
        }],
        orders=[{
            "user_id": "merchant-1",
            "order_number": order_number,
            "order_date": "2026-08-10",
            "order_status": "completed",
            "total_amount": 170.83,
            "customer_name": "Customer",
        }],
    )

    result = _run(run_reconciliation_v2(
        db,
        orders_user_id="merchant-1",
        markers_user_id="main",
    ))

    assert result["counts"][MATCHED] == 1
    assert result["counts"][NEEDS_REPAIR_MARKER] == 0
    assert result["rows"][0]["order_number"] == order_number
    assert result["rows"][0]["match"] == MATCHED


def test_reconciliation_bulk_loads_markers_once_with_exact_id_and_tenant_scope():
    order_numbers = [str(277400000 + index) for index in range(24)]
    invoice_ids = [str(2000 + index) for index in range(24)]

    orders = [{
        "user_id": "merchant-1",
        "order_number": order_number,
        "order_date": "2026-08-10",
        "order_status": "completed",
        "payment_method": "cash" if index == 1 else "mada",
        "total_amount": 100.0 + index,
        "customer_name": f"Customer {index}",
    } for index, order_number in enumerate(order_numbers)]
    orders.append({
        "user_id": "merchant-1",
        "order_number": "277499999",
        "order_date": "2026-08-10",
        "order_date_inferred": True,
        "order_status": "completed",
        "total_amount": 999.0,
        "customer_name": "Inferred date must be excluded",
    })
    invoices = [{
        "user_id": "main",
        "qoyod_invoice_id": invoice_ids[index],
        "invoice_number": invoice_ids[index],
        "reference": order_number,
        "qoyod_official_reference": order_number,
        "reference_provenance": "qoyod.reference",
        "issue_date": "2026-08-12",
        "total": 100.0 + index,
        "paid_amount": 100.0 + index,
        "remaining": 0.0,
        "status": "paid",
    } for index, order_number in enumerate(order_numbers)]

    inbox = []
    for index, order_number in enumerate(order_numbers):
        marker = invoice_ids[index]
        if index == 0:
            # A stale manual marker must not hide the matching canonical id.
            row = _inbox("merchant-1", order_number, marker="999999")
            row["qoyod_invoice_id"] = marker
            inbox.append(row)
        elif index == 1:
            # COD is valid with an invoice marker and no payment marker.
            inbox.append(_inbox("merchant-1", order_number, marker=marker))
        elif index == 2:
            # An allowed owner has a stale marker while an unrelated tenant
            # has the expected one.  This order must still need repair.
            inbox.append(_inbox(
                "merchant-1", order_number, marker="999998"))
            inbox.append(_inbox("merchant-2", order_number, marker=marker))
        else:
            inbox.append(_inbox("main", order_number, marker=marker))

    db = _DB(inbox=inbox, invoices=invoices, orders=orders)

    result = _run(run_reconciliation_v2(
        db,
        orders_user_id="merchant-1",
        markers_user_id="main",
    ))

    assert result["counts"][MATCHED] == 23
    assert result["counts"][NEEDS_REPAIR_MARKER] == 1
    by_order = {row["order_number"]: row for row in result["rows"]}
    assert by_order[order_numbers[0]]["match"] == MATCHED
    assert by_order[order_numbers[0]]["debug"]["invoice_id"] == invoice_ids[0]
    assert by_order[order_numbers[1]]["match"] == MATCHED
    assert by_order[order_numbers[1]]["debug"]["payment_id"] is None
    assert by_order[order_numbers[2]]["match"] == NEEDS_REPAIR_MARKER

    # Regression guard: this used to be one integration_inbox query per
    # matched invoice, which timed out behind Cloudflare at production scale.
    assert len(db.integration_inbox.queries) == 1
    inbox_query = db.integration_inbox.queries[0]
    assert set(inbox_query["user_id"]["$in"]) == {"main", "merchant-1"}
    assert "merchant-2" not in inbox_query["user_id"]["$in"]
    assert set(inbox_query["salla_order_number"]["$in"]) == set(order_numbers)


def test_marker_lookup_does_not_cross_into_an_unrelated_owner():
    db = _DB(inbox=[
        _inbox("merchant-2", "277274465", marker="9999"),
    ])

    found = _run(_has_marker_in_inbox(
        db,
        marker_user_ids=["main", "merchant-1"],
        order_number="277274465",
        expected_invoice_id="1363",
    ))

    assert found == (False, None, None)
    query = db.integration_inbox.queries[0]
    assert set(query["user_id"]["$in"]) == {"main", "merchant-1"}


def test_marker_for_a_different_invoice_does_not_satisfy_reconciliation():
    db = _DB(inbox=[
        _inbox("merchant-1", "277274465", marker="9999"),
    ])

    found = _run(_has_marker_in_inbox(
        db,
        marker_user_ids=["main", "merchant-1"],
        order_number="277274465",
        expected_invoice_id="1363",
    ))

    assert found == (False, None, None)


def test_matching_qoyod_id_wins_over_stale_manual_id_on_same_row():
    row = _inbox("merchant-1", "277274465", marker="9999")
    row["qoyod_invoice_id"] = "1363"
    db = _DB(inbox=[row])

    found = _run(_has_marker_in_inbox(
        db,
        marker_user_ids=["main", "merchant-1"],
        order_number="277274465",
        expected_invoice_id="1363",
    ))

    assert found == (True, "1363", None)


def test_cod_invoice_marker_counts_without_a_payment_marker():
    db = _DB(inbox=[
        _inbox("merchant-1", "277369908", marker="1364"),
    ])

    found = _run(_has_marker_in_inbox(
        db,
        marker_user_ids=["main", "merchant-1"],
        order_number="277369908",
        expected_invoice_id="1364",
    ))

    assert found == (True, "1364", None)
