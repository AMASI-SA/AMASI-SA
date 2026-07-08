"""Reconciliation v2 — Salla orders (unified_orders) ↔ local
qoyod_invoices — with the 5 outcome labels per user directive
2026-07-09.

Focus: prove the ARITHMETIC of the reconciliation is correct;
sync is exercised by a separate test module.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


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


def _unified(order_number, *, user_id="u-42",
             status="completed", status_slug="completed",
             order_date="2026-08-01", total=100.0,
             customer="عميل"):
    return {
        "user_id": user_id,
        "order_number": order_number,
        "order_id": f"salla-{order_number}",
        "order_status": status, "order_status_slug": status_slug,
        "order_date": order_date,
        "total_amount": total, "currency": "SAR",
        "customer_name": customer,
    }


def _invoice(reference, *, qid, total, paid=None, remaining=None,
             issue_date="2026-08-01", status="paid",
             invoice_number=None, customer="عميل",
             user_id="main"):
    if paid is None:
        paid = total
    if remaining is None:
        remaining = round(total - paid, 2)
    return {
        "user_id": user_id,
        "qoyod_invoice_id": str(qid),
        "invoice_number": invoice_number or f"NUM-{qid}",
        "reference": reference,
        "salla_order_number": reference,
        "customer_name": customer,
        "issue_date": issue_date,
        "total": total, "paid_amount": paid, "remaining": remaining,
        "status": status,
        "source": "synced_from_qoyod",
        "last_sync_at": datetime.now(timezone.utc),
    }


def _inbox_marker(order_number, *, manual_id, user_id="main"):
    return {
        "user_id": user_id,
        "salla_order_number": order_number,
        "manual_qoyod_invoice_id": manual_id,
        "qoyod_invoice_id": None,
    }


# ── Tests ──────────────────────────────────────────────────────────
def test_matched_exact_total():
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[_unified("O-1", total=150.0)],
        inbox=[_inbox_marker("O-1", manual_id="INV-1")],
        invoices=[_invoice("O-1", qid="INV-1", total=150.0)],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["مطابق"] == 1
    assert res["all_matched"] is True
    row = res["rows"][0]
    assert row["match"] == "مطابق"
    assert row["salla_total"] == 150.0
    assert row["qoyod_total"] == 150.0
    assert row["paid_amount"] == 150.0


def test_needs_plan_b_send():
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[_unified("O-2", total=100.0)],
        inbox=[], invoices=[],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["يحتاج إرسال Plan B"] == 1
    assert res["rows"][0]["match"] == "يحتاج إرسال Plan B"


def test_needs_repair_marker():
    """Invoice exists in قيود but no marker in inbox."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[_unified("O-3", total=200.0)],
        inbox=[],
        invoices=[_invoice("O-3", qid="INV-3", total=200.0)],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["يحتاج Repair Marker"] == 1
    row = res["rows"][0]
    assert row["match"] == "يحتاج Repair Marker"
    assert row["qoyod_invoice_id"] == "INV-3"


def test_amount_mismatch():
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[_unified("O-4", total=100.0)],
        inbox=[_inbox_marker("O-4", manual_id="INV-4")],
        invoices=[_invoice("O-4", qid="INV-4", total=110.0)],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["فرق مبلغ"] == 1
    row = res["rows"][0]
    assert row["match"] == "فرق مبلغ"
    assert row["difference"] == -10.0


def test_qoyod_only():
    """Invoice in قيود with no matching Salla order eligible."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[],
        inbox=[],
        invoices=[_invoice("O-99", qid="INV-99", total=300.0)],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["موجود في قيود فقط"] == 1
    row = res["rows"][0]
    assert row["match"] == "موجود في قيود فقط"
    assert row["qoyod_total"] == 300.0


def test_orphan_invoice_no_reference():
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("", qid="INV-ORPHAN", total=50.0)
    inv["reference"] = ""
    inv["salla_order_number"] = ""
    db = _FakeDB(unified=[], inbox=[], invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["موجود في قيود فقط"] == 1
    assert res["rows"][0]["order_number"] is None


def test_before_floor_orders_are_excluded():
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[_unified("O-OLD", order_date="2026-06-15")],
        inbox=[], invoices=[],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["يحتاج إرسال Plan B"] == 0
    assert res["salla_orders_total"] == 0


def test_all_five_outcomes_together():
    """Mixed dataset — every outcome represented once."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    db = _FakeDB(
        unified=[
            _unified("A", total=100.0),   # matched
            _unified("B", total=100.0),   # needs Plan-B send
            _unified("C", total=100.0),   # needs Repair Marker
            _unified("D", total=100.0),   # amount mismatch
        ],
        inbox=[
            _inbox_marker("A", manual_id="1"),
            _inbox_marker("D", manual_id="4"),
            # C has no marker → Repair Marker
            # B has no marker → needs Plan-B send
        ],
        invoices=[
            _invoice("A", qid="1", total=100.0),
            _invoice("C", qid="3", total=100.0),
            _invoice("D", qid="4", total=125.0),  # mismatch
            _invoice("E", qid="99", total=50.0),  # qoyod-only
        ],
    )
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    c = res["counts"]
    assert c["مطابق"] == 1
    assert c["يحتاج إرسال Plan B"] == 1
    assert c["يحتاج Repair Marker"] == 1
    assert c["فرق مبلغ"] == 1
    assert c["موجود في قيود فقط"] == 1
    assert res["all_matched"] is False
