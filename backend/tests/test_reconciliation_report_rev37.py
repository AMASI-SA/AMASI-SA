"""rev37 — تقرير المطابقة ميزان ↔ قيود. READ-ONLY contract tests.

Pins:
1. Match by qoyod_invoice_id + total within 0.01 → مطابق.
2. Total difference > 0.01 → فرق مبلغ with the signed difference.
3. MEZAN row with real invoice id absent from قيود → في ميزان فقط.
4. قيود invoice (issue_date >= 2026-07-01) unclaimed → في قيود فقط.
5. DRY:/PREVIEW: rows and pre-2026-07-01 Salla orders are OUT of scope.
6. قيود invoices with issue_date < 2026-07-01 are OUT of scope.
7. NO write method is ever called on the api client.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.reconciliation_report import (  # noqa: E402
    AMOUNT_MISMATCH, MATCHED, MEZAN_ONLY, QOYOD_ONLY,
    run_reconciliation_report,
)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def sort(self, *_a, **_k):
        return self
    def limit(self, *_a, **_k):
        return self
    def __aiter__(self):
        self._it = iter(self._rows)
        return self
    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self):
        self.rows = []
        self.inserted = []
    def find(self, q, projection=None):
        out = []
        for r in self.rows:
            if r.get("user_id") != q.get("user_id"):
                continue
            qid = r.get("qoyod_invoice_id")
            if qid in (None, ""):
                continue
            out.append(dict(r))
        return _Cursor(out)
    async def insert_one(self, doc):
        self.inserted.append(doc)


class _DB:
    def __init__(self):
        self.integration_inbox = _Coll()
        self.qoyod_reconciliation_reports = _Coll()


class _ReadOnlyClient:
    """list_invoices only. Any write attribute access is a hard fail."""
    def __init__(self, invoices):
        self._invoices = invoices
    async def list_invoices(self, *, page=1, limit=50):
        return {"invoices": self._invoices} if page == 1 else {"invoices": []}
    def __getattr__(self, name):
        if name.startswith(("create", "post", "put", "delete", "update")):
            raise AssertionError(f"WRITE method {name} touched — "
                                 "reconciliation must be READ-ONLY")
        raise AttributeError(name)


def _mezan_row(order, invoice_id, total, order_date="2026-07-05"):
    return {
        "user_id": "main",
        "salla_order_number": order,
        "qoyod_invoice_id": invoice_id,
        "pipeline_stage": "COMPLETED",
        "canonical_payload": {"order_date": order_date,
                              "total_amount": total},
    }


def _qoyod_inv(inv_id, ref, total, issue_date="2026-07-05"):
    return {"id": inv_id, "reference": ref, "invoice_number": f"INV-{inv_id}",
            "issue_date": issue_date, "total": total, "status": "Approved"}


@pytest.mark.asyncio
async def test_exact_match_and_verdict():
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("100", "501", 213.78))
    client = _ReadOnlyClient([_qoyod_inv("501", "100", 213.78)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["counts"][MATCHED] == 1
    assert r["all_matched"] is True
    assert r["sync_start_date"] == "2026-07-01"
    assert len(db.qoyod_reconciliation_reports.inserted) == 1


@pytest.mark.asyncio
async def test_amount_mismatch_shows_difference():
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("100", "501", 213.78))
    client = _ReadOnlyClient([_qoyod_inv("501", "100", 200.00)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["counts"][AMOUNT_MISMATCH] == 1
    assert r["all_matched"] is False
    row = next(x for x in r["rows"] if x["status"] == AMOUNT_MISMATCH)
    assert row["difference"] == pytest.approx(13.78)


@pytest.mark.asyncio
async def test_tolerance_001_still_matches():
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("100", "501", 213.78))
    client = _ReadOnlyClient([_qoyod_inv("501", "100", 213.77)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["counts"][MATCHED] == 1
    assert r["all_matched"] is True


@pytest.mark.asyncio
async def test_mezan_only_and_qoyod_only():
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("100", "501", 100.0))
    # قيود has a DIFFERENT invoice (e.g. zombie leak / manual invoice).
    client = _ReadOnlyClient([_qoyod_inv("999", "270054904", 55.0)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["counts"][MEZAN_ONLY] == 1
    assert r["counts"][QOYOD_ONLY] == 1
    assert r["all_matched"] is False


@pytest.mark.asyncio
async def test_scope_dry_rows_old_orders_old_invoices_excluded():
    db = _DB()
    db.integration_inbox.rows.append(
        _mezan_row("100", "DRY:invoice:abc", 100.0))          # dry → out
    db.integration_inbox.rows.append(
        _mezan_row("200", "600", 50.0, order_date="2026-06-15"))  # pre-floor → out
    client = _ReadOnlyClient([
        _qoyod_inv("700", "300", 20.0, issue_date="2026-06-01"),  # old → out
    ])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["mezan_sent_total"] == 0
    assert r["qoyod_invoices_total"] == 0
    assert sum(r["counts"].values()) == 0
    assert r["all_matched"] is True


@pytest.mark.asyncio
async def test_fallback_match_by_reference_when_id_differs():
    """MEZAN stored id may drift (string form) — reference (Salla order
    number) is the fallback join key."""
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("269571122", "501", 186.0))
    client = _ReadOnlyClient([_qoyod_inv("999501", "269571122", 186.0)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["counts"][MATCHED] == 1
    assert r["counts"][QOYOD_ONLY] == 0


@pytest.mark.asyncio
async def test_multiple_inbox_rows_same_order_counted_once():
    """rev37.1 — inbox stores a row per status transition. Two rows
    for the same order+invoice must yield ONE reconciliation entry."""
    db = _DB()
    db.integration_inbox.rows.append(_mezan_row("100", "501", 213.78))
    db.integration_inbox.rows.append(_mezan_row("100", "501", 213.78))
    client = _ReadOnlyClient([_qoyod_inv("501", "100", 213.78)])
    r = await run_reconciliation_report(db, user_id="main", api_client=client)
    assert r["mezan_sent_total"] == 1
    assert r["counts"][MATCHED] == 1
    assert r["counts"][MEZAN_ONLY] == 0
    assert r["all_matched"] is True
