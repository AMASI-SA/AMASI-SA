"""Cross-trace guard for Plan-B pending (user directive 2026-07-08).

Bug repro: order X has two integration_inbox traces —
    • trace-A (older): status=completed, qoyod_invoice_id="12345" (real)
    • trace-B (newer): status=in_delivery, NO invoice ids
Before the fix, `list_pending_orders(status="in_delivery")` de-duped
to trace-B (newest per order_number) and — because trace-B has no
markers — surfaced the order in Plan B even though قيود already has
an invoice.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


# ── Minimal fake DB ────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_key = None
        self._sort_dir = 1
        self._limit = None

    def sort(self, *args, **kwargs):
        # Accepts either sort("field", -1) or sort([("field", -1)]).
        if args and isinstance(args[0], str):
            self._sort_key, self._sort_dir = args[0], (
                args[1] if len(args) > 1 else 1)
        elif args and isinstance(args[0], list):
            self._sort_key, self._sort_dir = args[0][0]
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def _materialise(self):
        docs = list(self._docs)
        if self._sort_key:
            docs.sort(
                key=lambda d: d.get(self._sort_key) or datetime.min.replace(
                    tzinfo=timezone.utc),
                reverse=self._sort_dir < 0,
            )
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    def __aiter__(self):
        self._it = iter(self._materialise())
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
            nr = stage["$replaceRoot"]["newRoot"]
            if isinstance(nr, str) and nr.startswith("$"):
                out = [d.get(nr[1:], {}) for d in out]
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
            if "$regex" in v:
                pass  # no test uses it
            continue
        if doc.get(k) != v:
            return False
    return True


class _FakeDB:
    def __init__(self, inbox=None, invoices=None):
        self.integration_inbox = _FakeColl(inbox or [])
        self.qoyod_invoices = _FakeColl(invoices or [])
        # The production repository checks the canonical order store
        # before falling back to integration_inbox. These fixtures
        # intentionally exercise the fallback path.
        self.unified_orders = _FakeColl([])


def _run(coro):
    return asyncio.run(coro)


def _inbox(order_number, *, status_native, status_slug, received_at,
           order_date="2026-08-01",
           manual_id=None, legacy_id=None,
           trace_id=None, salla_order_id=None):
    return {
        "user_id": "main",
        "id":               f"ib-{trace_id or order_number}",
        "trace_id":         trace_id or f"tr-{order_number}",
        "salla_order_number": order_number,
        "salla_order_id":   salla_order_id or f"salla-{order_number}",
        "received_at":      received_at,
        "manual_qoyod_invoice_id": manual_id,
        "qoyod_invoice_id":        legacy_id,
        "raw_payload": {"data": {"date": {"date": order_date}}},
        "canonical_payload": {
            "order_date":            order_date,
            "created_at":            order_date,
            "order_status":          status_slug,
            "order_status_native":   status_native,
            "payment_method":        "mada",
            "payment_method_native": "مدى",
            "total_amount":          100.0,
            "currency":              "SAR",
            "customer": {"name": "عميل", "phone": "+966500000000"},
        },
    }


# ── The regression test ────────────────────────────────────────────
def test_older_completed_trace_hides_newer_in_delivery_trace():
    """Same order_number: older `completed` trace was sent (has real
    qoyod_invoice_id); newer `in_delivery` trace has no markers.
    Plan-B `in_delivery` tab MUST NOT surface this order."""
    from integrations.qoyod_manual.pending import list_pending_orders
    older = _inbox(
        "ORD-XT",
        trace_id="tr-old",
        status_native="تم التنفيذ",
        status_slug="completed",
        received_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        legacy_id="12345",  # a real invoice id
    )
    newer = _inbox(
        "ORD-XT",
        trace_id="tr-new",
        status_native="جاري التوصيل",
        status_slug="in_delivery",
        received_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
        # no marker on this newer row
    )
    db = _FakeDB(inbox=[older, newer])

    res = _run(list_pending_orders(
        db, user_id="main", days=90, status="in_delivery"))

    order_numbers = [o["order_number"] for o in res["orders"]]
    assert "ORD-XT" not in order_numbers, (
        "Expected the order to be HIDDEN because an older trace "
        "already carries a real قيود invoice id. Actual list: "
        + repr(res["orders"])
    )
    assert res["counts"]["excluded_already_sent"] >= 1


def test_qoyod_invoices_collection_also_hides_the_row():
    """Even if NO inbox trace has a marker, an entry in
    `qoyod_invoices` for the same salla_order_number must hide the
    order from Plan B."""
    from integrations.qoyod_manual.pending import list_pending_orders
    row = _inbox(
        "ORD-Q",
        status_native="جاري التوصيل",
        status_slug="in_delivery",
        received_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
    )
    db = _FakeDB(
        inbox=[row],
        invoices=[{
            "user_id": "main",
            "salla_order_number": "ORD-Q",
            "salla_order_id":     "salla-ORD-Q",
            "qoyod_invoice_id":   "77777",
        }],
    )
    res = _run(list_pending_orders(
        db, user_id="main", days=90, status="in_delivery"))
    order_numbers = [o["order_number"] for o in res["orders"]]
    assert "ORD-Q" not in order_numbers, res["orders"]


def test_dry_ids_do_not_hide_the_row():
    """DRY:/PREVIEW: ids MUST NOT count as sent."""
    from integrations.qoyod_manual.pending import list_pending_orders
    older = _inbox(
        "ORD-DRY",
        trace_id="tr-old",
        status_native="تم التنفيذ",
        status_slug="completed",
        received_at=datetime(2026, 8, 1, 10, tzinfo=timezone.utc),
        legacy_id="DRY:preview-xyz",
    )
    newer = _inbox(
        "ORD-DRY",
        trace_id="tr-new",
        status_native="جاري التوصيل",
        status_slug="in_delivery",
        received_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
    )
    db = _FakeDB(inbox=[older, newer])
    res = _run(list_pending_orders(
        db, user_id="main", days=90, status="in_delivery"))
    order_numbers = [o["order_number"] for o in res["orders"]]
    assert "ORD-DRY" in order_numbers, (
        "DRY marker must NOT count as sent. Got: "
        + repr(res["orders"])
    )


def test_no_cross_trace_history_still_surfaces_order():
    """Sanity: order with a single un-sent trace still appears."""
    from integrations.qoyod_manual.pending import list_pending_orders
    row = _inbox(
        "ORD-FRESH",
        status_native="جاري التوصيل",
        status_slug="in_delivery",
        received_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
    )
    db = _FakeDB(inbox=[row])
    res = _run(list_pending_orders(
        db, user_id="main", days=90, status="in_delivery"))
    order_numbers = [o["order_number"] for o in res["orders"]]
    assert order_numbers == ["ORD-FRESH"], res["orders"]
