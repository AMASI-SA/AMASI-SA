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

    def aggregate(self, pipeline):
        """Minimal aggregation runner — supports the subset used by
        `list_pending_orders` (as of 2026-07-09). Stages handled:
        $match, $sort, $group($first), $replaceRoot, $limit, $project.
        """
        return _FakeAggCursor(_run_pipeline(self.docs, pipeline))


def _run_pipeline(docs, pipeline):
    out = list(docs)
    for stage in pipeline:
        if "$match" in stage:
            q = stage["$match"]
            out = [d for d in out if _matches(d, q)]
        elif "$sort" in stage:
            spec = stage["$sort"]
            for key, direction in reversed(list(spec.items())):
                out.sort(key=lambda d, k=key: _dotted(d, k) or 0,
                         reverse=(direction == -1))
        elif "$group" in stage:
            spec = stage["$group"]
            gid  = spec["_id"]
            def _resolve(doc, expr):
                if isinstance(expr, str) and expr.startswith("$"):
                    return _dotted(doc, expr[1:])
                return expr
            groups: dict = {}
            for d in out:
                key = _resolve(d, gid)
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


def _dotted(doc, path):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


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


def _qoyod_invoice_row(order_number, invoice_id="INV-QID"):
    """A `qoyod_invoices` row that confirms a real قيود invoice
    for the strict Plan-B definition."""
    return {
        "user_id": "main",
        "salla_order_number": order_number,
        "salla_order_id": f"salla-{order_number}",
        "qoyod_invoice_id": invoice_id,
        "invoice_number": f"NUM-{invoice_id}",
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
    }


# ── Tests ──────────────────────────────────────────────────────────
def test_all_synced_gives_empty_diff():
    """Every marker-bearing inbox row has a matching eligible
    unified row AND a قيود invoice row → the audit reports zero
    missing (strict definition)."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-1", manual_id="INV-1"),
             _inbox("O-2", manual_id="INV-2")]
    unified = [_unified("O-1"), _unified("O-2")]
    invoices = [_qoyod_invoice_row("O-1", "10001"),
                _qoyod_invoice_row("O-2", "10002")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 2
    assert res["diagnostic_sent_plan_b_count"] == 2
    assert res["missing_from_diagnostic_count"] == 0
    assert res["orders"] == []
    assert res["plan_b_sent_dropped_by_strict_filter"] == 0


def test_marker_without_qoyod_invoice_falls_into_strict_extras():
    """User directive 2026-07-09: a marker WITHOUT a matching
    qoyod_invoices entry MUST be dropped from Plan-B Sent and
    reported in `strict_filter_extras`."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-ORPHAN", manual_id="ORPH-999")]
    unified = [_unified("O-ORPHAN")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=[])
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count_loose"] == 1
    assert res["plan_b_sent_count"] == 0  # strict drops it
    assert res["plan_b_sent_dropped_by_strict_filter"] == 1
    extras = res["strict_filter_extras"]
    assert len(extras) == 1
    assert extras[0]["order_number"] == "O-ORPHAN"
    assert extras[0]["exclusion_reason"] == "no_qoyod_invoice_confirmation"


def test_marker_before_floor_falls_into_strict_extras():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-EARLY", manual_id="X",
                     order_date="2026-06-15")]
    unified = [_unified("O-EARLY", order_date="2026-06-15")]
    invoices = [_qoyod_invoice_row("O-EARLY")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 0
    assert res["plan_b_sent_dropped_by_strict_filter"] == 1
    extras = res["strict_filter_extras"]
    assert extras[0]["exclusion_reason"] == "inbox_date_before_floor"


def test_marker_but_no_unified_row_flagged():
    """Strict marker set includes O-GAP (has qoyod invoice), but
    diagnostic can't find matching unified row → shown in
    per-order missing breakdown."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-GAP", manual_id="INV-9")]
    invoices = [_qoyod_invoice_row("O-GAP", "9999")]
    db = _FakeDB(unified=[], inbox=inbox, invoices=invoices)
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
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [_inbox("O-CANCEL", manual_id="INV-C")]
    unified = [_unified("O-CANCEL",
                        status="cancelled", status_slug="cancelled")]
    invoices = [_qoyod_invoice_row("O-CANCEL")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["missing_from_diagnostic_count"] == 1
    hit = res["orders"][0]
    assert hit["exclusion_reason"] == "unified_status_not_in_plan_b_scope"


def test_cross_trace_marker_makes_diagnostic_count_correctly():
    """User directive 2026-07-09 (Diagnostic fix): if ANY trace of
    the order carries a real Plan-B marker, the diagnostic MUST
    classify the order as already_sent_plan_b — even if the newest
    trace has no marker."""
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    older = _inbox("O-CROSS", manual_id="OLD-777")
    older["received_at"] = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    newer = _inbox("O-CROSS", manual_id=None,
                   status="in_delivery",
                   status_native="جاري التوصيل")
    newer["received_at"] = datetime(2026, 7, 5, 12, tzinfo=timezone.utc)
    unified = [_unified("O-CROSS")]
    invoices = [_qoyod_invoice_row("O-CROSS", "12345")]
    db = _FakeDB(unified=unified, inbox=[older, newer], invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 1
    # Post-fix: diagnostic must also count it as already_sent_plan_b.
    assert res["diagnostic_sent_plan_b_count"] == 1
    assert res["missing_from_diagnostic_count"] == 0


def test_marker_but_unified_before_floor():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    # Inbox says 2026-08 (strict passes) but unified says 2026-06 →
    # diagnostic drops unified (out of scope), missing reason =
    # unified_before_floor_date.
    inbox = [_inbox("O-OLD", manual_id="INV-O",
                     order_date="2026-08-01")]
    unified = [_unified("O-OLD", order_date="2026-06-15")]
    invoices = [_qoyod_invoice_row("O-OLD")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
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
    invoices = [_qoyod_invoice_row("O-NODATE")]
    db = _FakeDB(unified=[u], inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["missing_from_diagnostic_count"] == 1
    assert res["orders"][0]["exclusion_reason"] == "unified_missing_order_date"


def test_dry_markers_are_ignored():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [
        _inbox("O-DRY", manual_id="DRY:preview-abc"),
        _inbox("O-REAL", manual_id="INV-R"),
    ]
    unified = [_unified("O-REAL")]
    invoices = [_qoyod_invoice_row("O-REAL")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 1
    assert res["missing_from_diagnostic_count"] == 0


def test_reason_histogram_aggregates():
    from integrations.qoyod_manual.audit_sent_count import (
        audit_plan_b_vs_diagnostic,
    )
    inbox = [
        _inbox("A", manual_id="1"),
        _inbox("B", manual_id="2"),
        _inbox("C", manual_id="3"),
        _inbox("D", manual_id="4"),
    ]
    unified = [
        _unified("C", status="cancelled", status_slug="cancelled"),
        _unified("D"),
    ]
    invoices = [_qoyod_invoice_row(k) for k in ("A", "B", "C", "D")]
    db = _FakeDB(unified=unified, inbox=inbox, invoices=invoices)
    res = _run(audit_plan_b_vs_diagnostic(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["plan_b_sent_count"] == 4
    assert res["diagnostic_sent_plan_b_count"] == 1
    assert res["missing_from_diagnostic_count"] == 3
    hist = res["reason_histogram"]
    assert hist.get("not_in_unified_orders_for_tenant") == 2
    assert hist.get("unified_status_not_in_plan_b_scope") == 1
