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

    def aggregate(self, pipeline):
        """Minimal aggregation shim — see the twin in
        test_missing_from_plan_b.py / test_audit_sent_count.py."""
        return _FakeAggCursor(_run_pipeline_static(self.docs, pipeline))


def _dotted(doc, path):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _run_pipeline_static(docs, pipeline):
    out = list(docs)
    for stage in pipeline:
        if "$match" in stage:
            out = [d for d in out if _matches(d, stage["$match"])]
        elif "$sort" in stage:
            for key, direction in reversed(list(stage["$sort"].items())):
                out.sort(key=lambda d, k=key: _dotted(d, k) or 0,
                         reverse=(direction == -1))
        elif "$group" in stage:
            spec = stage["$group"]
            def _resolve(doc, expr):
                if isinstance(expr, str) and expr.startswith("$"):
                    return _dotted(doc, expr[1:])
                return expr
            groups: dict = {}
            for d in out:
                key = _resolve(d, spec["_id"])
                if key not in groups:
                    groups[key] = {"_id": key}
                    for out_field, sub in spec.items():
                        if out_field == "_id":
                            continue
                        if isinstance(sub, dict) and "$first" in sub:
                            groups[key][out_field] = (
                                d if sub["$first"] == "$$ROOT"
                                else _resolve(d, sub["$first"]))
            out = list(groups.values())
        elif "$replaceRoot" in stage:
            new_root = stage["$replaceRoot"]["newRoot"]
            if isinstance(new_root, str) and new_root.startswith("$"):
                out = [d.get(new_root[1:], {}) for d in out]
        elif "$limit" in stage:
            out = out[:int(stage["$limit"])]
        elif "$project" in stage:
            proj = stage["$project"]
            keep = {k for k, v in proj.items() if v == 1}
            drop = {k for k, v in proj.items() if v == 0}
            new_out = []
            for d in out:
                nd = {k: v for k, v in d.items()
                       if (not keep or k in keep) and k not in drop}
                new_out.append(nd)
            out = new_out
    return out


class _FakeAggCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


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


# ── Fallback match-key chain (user directive 2026-07-09) ─────────
def test_match_key_falls_back_to_salla_order_number():
    """Invoice has empty `reference` but a valid `salla_order_number`
    — the reconciliation MUST use it as the join key."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("O-A", qid="INV-A", total=100.0)
    inv["reference"] = ""          # blank the standard field
    inv["salla_order_number"] = "270883333"
    unified = [_unified("270883333", total=100.0)]
    inbox = [_inbox_marker("270883333", manual_id="INV-A")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["مطابق"] == 1
    assert res["counts"]["يحتاج إرسال Plan B"] == 0
    assert res["counts"]["موجود في قيود فقط"] == 0


def test_notes_field_no_longer_used_for_primary_matching():
    """User directive 2026-07-09 (final): `notes` is NEVER a primary
    match source. When both `reference` and `salla_order_number` are
    empty, the invoice becomes a `qoyod_only` orphan even if `notes`
    contains a valid-looking order number. The Salla order (which
    has NO Qoyod invoice with matching reference) stays as
    `needs_plan_b_send`.
    """
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("dummy", qid="INV-B", total=200.0)
    inv["reference"] = ""
    inv["salla_order_number"] = ""
    inv["notes"] = "طلب سلة رقم 270884444 - عميل تجريبي"
    unified = [_unified("270884444", total=200.0)]
    inbox = [_inbox_marker("270884444", manual_id="INV-B")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["مطابق"] == 0
    assert res["counts"]["يحتاج إرسال Plan B"] == 1
    assert res["counts"]["موجود في قيود فقط"] == 1


def test_description_field_no_longer_used_for_primary_matching():
    """Same rule as `notes`: `description` is Debug ONLY."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("", qid="INV-C", total=50.0)
    inv["reference"] = ""
    inv["salla_order_number"] = ""
    inv["description"] = "Order ref: 270885555"
    unified = [_unified("270885555", total=50.0)]
    inbox = [_inbox_marker("270885555", manual_id="INV-C")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["مطابق"] == 0
    assert res["counts"]["يحتاج إرسال Plan B"] == 1
    assert res["counts"]["موجود في قيود فقط"] == 1


def test_qoyod_only_row_carries_debug_fields():
    """When we can't resolve a Salla order, the qoyod_only row
    MUST include the debug bag so the operator can inspect why."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("", qid="INV-D", total=75.0)
    inv["reference"] = ""
    inv["salla_order_number"] = ""
    inv["notes"] = "no digits here"
    inv["description"] = "also no order number"
    db = _FakeDB(unified=[], inbox=[], invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["موجود في قيود فقط"] == 1
    row = res["rows"][0]
    assert row["debug"]["match_source"] == "orphan"
    assert "notes_snippet" in row["debug"]
    assert row["debug"]["notes_snippet"] == "no digits here"


def test_short_ref_is_not_matched_as_order_number():
    """`reference='X-1'` is not 8+ digits → shouldn't be treated
    as a Salla order_number join key (would produce false matches)."""
    from integrations.qoyod.reconciliation_v2 import run_reconciliation_v2
    inv = _invoice("X-1", qid="INV-E", total=100.0)
    inv["reference"] = "X-1"
    inv["salla_order_number"] = ""
    db = _FakeDB(unified=[], inbox=[], invoices=[inv])
    res = _run(run_reconciliation_v2(
        db, orders_user_id="u-42", markers_user_id="main"))
    # Loose match → surfaces as qoyod_only with match_source=reference_loose
    assert res["counts"]["موجود في قيود فقط"] == 1
    assert res["rows"][0]["debug"]["match_source"] == "reference_loose"
