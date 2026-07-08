"""Read-only audit: Plan-B markers vs diagnostic sent counter.

User directive 2026-07-09: prove that the diagnostic under-counts
`already_sent_plan_b` compared to the authoritative marker set in
`integration_inbox.manual_qoyod_invoice_id`, and expose per-order
exclusion reasons.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


# ── Fake DB — same shape as the other missing-from-plan-b tests ──
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:int(n)]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as e:
            raise StopAsyncIteration from e


class _FakeColl:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query=None, projection=None):
        matched = [d for d in self.docs if _matches(d, query or {})]
        return _FakeCursor(matched)

    async def find_one(self, query, projection=None, sort=None):
        for d in self.docs:
            if _matches(d, query):
                return d
        return None


def _matches(doc, q):
    for k, v in q.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        if isinstance(v, dict):
            if "$gte" in v:
                dv = doc.get(k)
                if dv is None or not (dv >= v["$gte"]):
                    return False
            if "$nin" in v and doc.get(k) in v["$nin"]:
                return False
            if "$regex" in v:
                pass
            continue
        if doc.get(k) != v:
            return False
    return True


class _FakeDB:
    def __init__(self, unified=None, inbox=None, invoices=None):
        self.unified_orders = _FakeColl(unified or [])
        self.integration_inbox = _FakeColl(inbox or [])
        self.qoyod_invoices = _FakeColl(invoices or [])


def _run(coro):
    return asyncio.run(coro)


def _inbox(order_number, *, manual_id, status="completed",
           status_native="تم التنفيذ", order_date="2026-08-01"):
    return {
        "user_id": "main",
        "id": f"ib-{order_number}",
        "trace_id": f"tr-{order_number}",
        "salla_order_number": order_number,
        "salla_order_id": f"salla-{order_number}",
        "received_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "manual_qoyod_invoice_id": manual_id,
        "qoyod_invoice_id": None,
        "raw_payload": {"data": {"date": {"date": order_date}}},
        "canonical_payload": {
            "order_date": order_date,
            "created_at": order_date,
            "order_status": status,
            "order_status_native": status_native,
            "payment_method": "mada",
            "payment_method_native": "مدى",
            "total_amount": 100.0,
            "currency": "SAR",
            "customer": {"name": "عميل", "phone": "+966500000000"},
        },
    }


def _unified(order_number, *, user_id="u-42",
             status="completed", status_slug="completed",
             order_date="2026-08-01"):
    return {
        "user_id": user_id,
        "order_number": order_number,
        "order_id": f"salla-{order_number}",
        "order_status": status,
        "order_status_slug": status_slug,
        "order_date": order_date,
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "payment_method": "mada",
        "total_amount": 100.0,
        "currency": "SAR",
        "customer_name": "عميل",
        "customer_mobile": "+966500000000",
    }


# ── Tests ──────────────────────────────────────────────────────────
def test_all_synced_gives_empty_diff():
    """Every marker-bearing inbox row has a matching eligible
    unified row → the audit reports zero missing."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-1", manual_id="INV-1"),
             _inbox("O-2", manual_id="INV-2")]
    unified = [_unified("O-1"), _unified("O-2")]
    db = _FakeDB(unified=unified, inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 2
    assert res["diagnostic_sent_plan_b_count"] == 2
    assert res["missing_from_diagnostic_count"] == 0
    assert res["orders"] == []


def test_marker_but_no_unified_row_flagged():
    """Marker exists in inbox but the order isn't synced to
    unified_orders under the JWT tenant."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-GAP", manual_id="INV-9")]
    db = _FakeDB(unified=[], inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 1
    assert res["diagnostic_sent_plan_b_count"] == 0
    assert res["missing_from_diagnostic_count"] == 1
    hit = res["orders"][0]
    assert hit["order_number"] == "O-GAP"
    assert hit["exclusion_reason"] == "not_in_unified_orders_for_tenant"
    assert res["reason_histogram"]["not_in_unified_orders_for_tenant"] == 1


def test_marker_but_unified_status_out_of_scope():
    """Order was sent, then customer status transitioned to
    `cancelled` in unified → diagnostic drops it (status filter),
    marker still counts."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-CANCEL", manual_id="INV-C",
                    status="completed", status_native="تم التنفيذ")]
    unified = [_unified("O-CANCEL",
                        status="cancelled", status_slug="cancelled")]
    db = _FakeDB(unified=unified, inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["missing_from_diagnostic_count"] == 1
    hit = res["orders"][0]
    assert hit["exclusion_reason"] == "unified_status_not_in_plan_b_scope"
    assert hit["detail"]["unified_order_status"] == "cancelled"


def test_marker_but_unified_before_floor():
    """Marker sent, but unified order_date is before 2026-07-01."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-OLD", manual_id="INV-O")]
    unified = [_unified("O-OLD", order_date="2026-06-15")]
    db = _FakeDB(unified=unified, inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["missing_from_diagnostic_count"] == 1
    assert res["orders"][0]["exclusion_reason"] == "unified_before_floor_date"


def test_marker_but_unified_no_date():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-NODATE", manual_id="INV-N")]
    u = _unified("O-NODATE")
    u["order_date"] = None
    db = _FakeDB(unified=[u], inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["missing_from_diagnostic_count"] == 1
    assert res["orders"][0]["exclusion_reason"] == "unified_missing_order_date"


def test_dry_markers_are_ignored():
    """DRY:/PREVIEW: markers do NOT count as real Plan-B sends."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [
        _inbox("O-DRY", manual_id="DRY:preview-abc"),
        _inbox("O-REAL", manual_id="INV-R"),
    ]
    unified = [_unified("O-REAL")]
    db = _FakeDB(unified=unified, inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    # O-DRY must NOT be counted as sent, O-REAL must be.
    assert res["plan_b_sent_count"] == 1
    assert res["missing_from_diagnostic_count"] == 0


def test_reason_histogram_aggregates():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    # Mix: 2 not-in-unified, 1 status-out-of-scope, 1 clean.
    inbox = [
        _inbox("A", manual_id="1"),
        _inbox("B", manual_id="2"),
        _inbox("C", manual_id="3"),
        _inbox("D", manual_id="4"),
    ]
    unified = [
        _unified("C", status="cancelled", status_slug="cancelled"),
        _unified("D"),  # clean — in both sets
    ]
    db = _FakeDB(unified=unified, inbox=inbox)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 4
    assert res["diagnostic_sent_plan_b_count"] == 1
    assert res["missing_from_diagnostic_count"] == 3
    hist = res["reason_histogram"]
    assert hist.get("not_in_unified_orders_for_tenant") == 2
    assert hist.get("unified_status_not_in_plan_b_scope") == 1
