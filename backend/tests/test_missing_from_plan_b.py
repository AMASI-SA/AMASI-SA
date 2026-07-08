"""Tests for Plan-B `missing-from-plan-b` diagnostic endpoint.

Scope contract (user directive 2026-07-09):
    Main universe = `unified_orders` filtered STRICTLY by:
      • parseable Salla `order_date` >= 2026-07-01
      • order_status maps to one of the 3 Plan-B statuses
    `integration_inbox` is a diagnostic aid only. Orphan inbox rows
    (in inbox but not in unified_orders) go to a SEPARATE bucket.

Invariant:
    eligible_salla_orders == sent_to_qoyod
                           + visible_in_plan_b
                           + hidden_with_reason
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


# ── Minimal in-memory fakes ────────────────────────────────────────
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


def _inbox_row(order_number, *, status="completed",
               status_native="تم التنفيذ",
               order_date="2026-08-01",
               received_at=None,
               manual_id=None, legacy_id=None,
               total=100.0, payment="mada"):
    if received_at is None:
        received_at = datetime.now(timezone.utc)
    return {
        "user_id": "main",
        "id": f"ib-{order_number}",
        "trace_id": f"trace-{order_number}",
        "salla_order_number": order_number,
        "salla_order_id": f"salla-{order_number}",
        "received_at": received_at,
        "manual_qoyod_invoice_id": manual_id,
        "qoyod_invoice_id": legacy_id,
        "raw_payload": {"data": {"date": {"date": order_date}}},
        "canonical_payload": {
            "order_date": order_date,
            "created_at": order_date,
            "order_status": status,
            "order_status_native": status_native,
            "payment_method": payment,
            "payment_method_native": payment,
            "total_amount": total,
            "currency": "SAR",
            "customer": {"name": "عميل", "phone": "+966500000000"},
        },
    }


def _unified_row(order_number, *, status="completed",
                 status_slug="completed",
                 order_date="2026-08-01",
                 payment="mada", total=100.0):
    return {
        "user_id": "main",
        "order_number": order_number,
        "order_id": f"salla-{order_number}",
        "order_status": status,
        "order_status_slug": status_slug,
        "order_date": order_date,
        "created_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "payment_method": payment,
        "total_amount": total,
        "currency": "SAR",
        "customer_name": "عميل",
        "customer_mobile": "+966500000000",
    }


def _qoyod_invoice(order_number, invoice_id="9999"):
    return {
        "user_id": "main",
        "salla_order_number": order_number,
        "salla_order_id": f"salla-{order_number}",
        "qoyod_invoice_id": invoice_id,
        "invoice_number": f"INV-{invoice_id}",
        "created_at": datetime.now(timezone.utc),
    }


# ── Actual tests ───────────────────────────────────────────────────
def test_endpoint_shape_and_empty_universe():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB()
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["ok"] is True
    assert res["floor_date"] == "2026-07-01"
    assert res["supported_statuses"] == ["completed", "delivered", "in_delivery"]
    c = res["counts"]
    assert c["eligible_salla_orders"] == 0
    assert c["sent_to_qoyod"] == 0
    assert c["visible_in_plan_b"] == 0
    assert c["hidden_with_reason"] == 0
    assert res["orders"] == []
    assert res["webhooks_without_unified"] == []
    assert res["invariant_holds"] is True


def test_invariant_eligible_equals_sum_of_buckets():
    """The core contract: eligible == sent + visible + hidden."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[
            _unified_row("O-VISIBLE"),        # → visible_in_plan_b
            _unified_row("O-SENT"),           # → sent (marker below)
            _unified_row("O-HIDDEN"),         # → hidden (no inbox)
        ],
        inbox=[
            _inbox_row("O-VISIBLE"),
            _inbox_row("O-SENT", manual_id="11111"),
            # no inbox for O-HIDDEN
        ],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    c = res["counts"]
    assert c["eligible_salla_orders"] == 3
    assert c["sent_to_qoyod"] == 1
    assert c["visible_in_plan_b"] == 1
    assert c["hidden_with_reason"] == 1
    assert res["invariant_holds"] is True
    assert (c["eligible_salla_orders"]
            == c["sent_to_qoyod"] + c["visible_in_plan_b"]
            + c["hidden_with_reason"])


def test_pre_floor_date_unified_does_NOT_enter_universe():
    """Directive #1/#2: an order with Salla date < 2026-07-01 must
    NOT count in eligible_salla_orders, even if webhooks arrived
    recently."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-OLD", order_date="2026-06-15")],
        inbox=[_inbox_row("O-OLD",
                          order_date="2026-06-15",
                          received_at=datetime(2026, 8, 1,
                                                tzinfo=timezone.utc))],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 0
    assert res["orders"] == []


def test_status_not_supported_unified_does_NOT_enter_universe():
    """A `pending` unified row must be excluded (only 3 statuses
    are eligible)."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-PENDING",
                              status="pending", status_slug="pending")],
        inbox=[_inbox_row("O-PENDING", status="pending",
                          status_native="بانتظار الدفع")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 0
    assert res["counts"]["status_out_of_scope_unified"] == 1


def test_visible_in_plan_b_counted_but_not_returned():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-VIS")],
        inbox=[_inbox_row("O-VIS")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 1
    assert res["counts"]["visible_in_plan_b"] == 1
    assert [o["order_number"] for o in res["orders"]] == []


def test_already_sent_plan_b_marker_appears_in_orders():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-SENT")],
        inbox=[_inbox_row("O-SENT", manual_id="12345")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    hits = [o for o in res["orders"] if o["order_number"] == "O-SENT"]
    assert len(hits) == 1
    assert hits[0]["missing_stage"] == "already_sent_plan_b"
    assert hits[0]["marker_source"] == "plan_b"
    assert res["counts"]["sent_to_qoyod"] == 1


def test_already_sent_legacy_marker():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-LEG")],
        inbox=[_inbox_row("O-LEG", legacy_id="55555")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    hits = [o for o in res["orders"] if o["order_number"] == "O-LEG"]
    assert len(hits) == 1
    assert hits[0]["missing_stage"] == "already_sent_legacy"


def test_missing_from_integration_inbox_is_hidden():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-NOWH")],
        inbox=[],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    hits = [o for o in res["orders"] if o["order_number"] == "O-NOWH"]
    assert len(hits) == 1
    assert hits[0]["missing_stage"] == "missing_from_integration_inbox"
    assert res["counts"]["hidden_with_reason"] == 1


def test_orphan_inbox_goes_to_separate_bucket():
    """Directive #3/#4: an inbox row without a unified match must NOT
    inflate eligible_salla_orders; it appears in
    webhooks_without_unified instead."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[],
        inbox=[_inbox_row("O-ORPHAN")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 0
    assert res["counts"]["webhooks_without_unified"] == 1
    orphans = res["webhooks_without_unified"]
    assert len(orphans) == 1
    assert orphans[0]["order_number"] == "O-ORPHAN"
    # And it must NOT appear in the main orders list.
    assert "O-ORPHAN" not in [o["order_number"] for o in res["orders"]]


def test_include_already_sent_toggle():
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-S1")],
        inbox=[_inbox_row("O-S1", manual_id="777")],
    )
    res_incl = _run(list_missing_from_plan_b(
        db, orders_user_id="main", include_already_sent=True))
    res_excl = _run(list_missing_from_plan_b(
        db, orders_user_id="main", include_already_sent=False))
    assert "O-S1" in [o["order_number"] for o in res_incl["orders"]]
    assert "O-S1" not in [o["order_number"] for o in res_excl["orders"]]
    # The bucket counter is unaffected by the display toggle.
    assert res_incl["counts"]["sent_to_qoyod"] == 1
    assert res_excl["counts"]["sent_to_qoyod"] == 1


def test_duplicate_invoice_in_qoyod_is_sent_bucket():
    """Eligible unified order with NO marker in inbox but قيود has
    an invoice → bucket=sent, stage=already_in_qoyod."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    db = _FakeDB(
        unified=[_unified_row("O-DUP")],
        inbox=[_inbox_row("O-DUP")],  # no marker in inbox
        invoices=[_qoyod_invoice("O-DUP", "88888")],
    )
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    hits = [o for o in res["orders"] if o["order_number"] == "O-DUP"]
    assert len(hits) == 1
    assert hits[0]["missing_stage"] == "already_in_qoyod"
    assert hits[0]["reason"] == "duplicate_invoice_in_qoyod"
    assert hits[0]["qoyod_invoice_id"] == "88888"
    assert res["counts"]["sent_to_qoyod"] == 1


def test_unified_without_order_date_excluded():
    """Directive #2: an eligible-status unified row with no
    order_date does NOT enter the main counter."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    row = _unified_row("O-NODATE")
    row["order_date"] = None
    db = _FakeDB(unified=[row], inbox=[])
    res = _run(list_missing_from_plan_b(db, orders_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 0


def test_tenant_axis_separation():
    """Directive 2026-07-09: unified_orders is queried under the
    JWT user_id (production tenant), while integration_inbox and
    qoyod_invoices stay under the webhook capture tenant
    (`_TENANT`). Both axes independently namespace their data."""
    from integrations.qoyod_manual.missing_diagnostics import (
        list_missing_from_plan_b,
    )
    # unified_orders is populated under real-user "u-42".
    unified_row = _unified_row("O-TENANT")
    unified_row["user_id"] = "u-42"

    # integration_inbox is populated under global "main".
    inbox_row = _inbox_row("O-TENANT", manual_id="INV-777")
    inbox_row["user_id"] = "main"

    db = _FakeDB(unified=[unified_row], inbox=[inbox_row])

    # Calling with orders_user_id="u-42" and markers_user_id="main"
    # must (a) find the unified row, (b) still detect the marker
    # from the inbox row → sent bucket.
    res = _run(list_missing_from_plan_b(
        db, orders_user_id="u-42", markers_user_id="main"))
    assert res["counts"]["eligible_salla_orders"] == 1
    assert res["counts"]["sent_to_qoyod"] == 1
    hits = [o for o in res["orders"] if o["order_number"] == "O-TENANT"]
    assert len(hits) == 1
    assert hits[0]["missing_stage"] == "already_sent_plan_b"

    # And with a WRONG orders_user_id, the diagnostic returns empty
    # (which is exactly what production was seeing before this fix).
    res_wrong = _run(list_missing_from_plan_b(
        db, orders_user_id="main", markers_user_id="main"))
    assert res_wrong["counts"]["eligible_salla_orders"] == 0
