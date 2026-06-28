"""Iter-290h.6 — GET /integrations/qoyod/invoices/{order_id} must
return the payloads + responses + invoice_payment id so the
Timeline drawer can prove the /invoice_payments step actually ran.

User report (production order 269077005, 2026-06-28):
The drawer showed "لا يوجد سجل تحوّلات" and never displayed the
payment-link payload or قيود's response — even though قيود showed
the invoice as "دفعت" with balance=0. The previous projection on
`integration_inbox` was narrow on purpose (stage_history + a few
status fields) and never carried the قيود bodies. This file pins
the expanded projection so the UI has everything it needs.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from server import app


@pytest.fixture
def fake_user():
    class U:
        id = "tenant-a"
        email = "ops@mezan.example"
    return U()


# ─── 1. The projection on inbox must include قيود bodies ─────────────
@pytest.mark.asyncio
async def test_get_invoice_returns_inbox_payloads_responses_and_payment_id(
    monkeypatch, fake_user,
):
    """Iter-290h.6 — the Timeline drawer needs `qoyod_payloads.invoice`,
    `qoyod_payloads.invoice_payment`, `qoyod_responses.invoice.body`,
    `qoyod_responses.invoice_payment.body`, and
    `qoyod_invoice_payment_id`. Without these the drawer cannot prove
    the payment step ran."""
    inbox_doc = {
        "trace_id":          "trace-abc",
        "stage_history": [
            {"to_stage": "NORMALIZED",              "at": "t0", "actor": "system"},
            {"to_stage": "RULES_APPLIED",           "at": "t1", "actor": "system"},
            {"to_stage": "CUSTOMER_RESOLVED",       "at": "t2", "actor": "system"},
            {"to_stage": "PRODUCT_RESOLVED",        "at": "t3", "actor": "system"},
            {"to_stage": "INVOICE_CREATED",         "at": "t4", "actor": "system"},
            {"to_stage": "INVOICE_PAYMENT_CREATED", "at": "t5", "actor": "system"},
            {"to_stage": "COMPLETED",               "at": "t6", "actor": "system"},
        ],
        "pipeline_stage":         "COMPLETED",
        "pipeline_outcome":       "COMPLETED",
        "last_success_stage":     "COMPLETED",
        "last_failed_stage":      None,
        "attempts":               1,
        "received_at":            "2026-06-28T10:00:00Z",
        "qoyod_invoice_id":         "63",
        "qoyod_invoice_payment_id": "888",
        "qoyod_customer_id":        "109",
        "qoyod_payloads": {
            "invoice":         {"invoice": {"contact_id": 109}},
            "invoice_payment": {"invoice_payment": {
                "invoice_id": 63, "amount": 131.92,
                "date": "2026-06-28", "account_id": 94,
                "reference": "269077005",
            }},
        },
        "qoyod_responses": {
            "invoice":         {"body": {"invoice": {"id": 63}}, "qoyod_id": "63"},
            "invoice_payment": {"body": {"invoice_payment": {"id": 888}}, "qoyod_id": "888"},
        },
    }
    invoice_doc = {
        "salla_order_id":           "269077005",
        "qoyod_invoice_id":         "63",
        "qoyod_invoice_number":     "269077005",
        "qoyod_invoice_payment_id": "888",
        "qoyod_customer_id":        "109",
        "status":                   "sent",
        "pipeline_stage":           "COMPLETED",
        "attempts":                 1,
        "updated_at":               "2026-06-28T10:01:00Z",
    }

    class _FakeCol:
        def __init__(self, doc):
            self._doc = doc

        async def find_one(self, query, projection=None, sort=None):
            # Capture the projection the route requested so the test
            # can assert it carries the new keys.
            self.last_projection = projection
            self.last_query = query
            return dict(self._doc) if self._doc else None

    invoices_col = _FakeCol(invoice_doc)
    inbox_col    = _FakeCol(inbox_doc)

    from integrations.qoyod import routes as qroutes
    from server import api, current_user as real_current_user

    class _DB:
        qoyod_invoices    = invoices_col
        integration_inbox = inbox_col

    # The router was already attached with the live db. Build a fresh
    # router pointing at our fakes and mount it on a sub-app for the
    # test so we don't touch the live app's routing.
    test_router = qroutes.make_qoyod_router(_DB(), real_current_user)
    # FastAPI doesn't expose remove_router; mount onto a private path.
    from fastapi import FastAPI
    test_app = FastAPI()

    async def _override_user():
        return fake_user
    test_app.dependency_overrides[real_current_user] = _override_user
    test_app.include_router(test_router, prefix="/api")

    async with AsyncClient(transport=ASGITransport(app=test_app),
                           base_url="http://test") as ac:
        resp = await ac.get("/api/integrations/qoyod/invoices/269077005")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True

    # The invoice document is returned unchanged.
    assert data["invoice"]["qoyod_invoice_payment_id"] == "888"

    # ── The crucial part: the inbox carries the new qoyod payloads ──
    inbox = data["inbox"]
    assert inbox["qoyod_invoice_payment_id"] == "888"
    assert inbox["qoyod_payloads"]["invoice"]["invoice"]["contact_id"] == 109
    ip_body = inbox["qoyod_payloads"]["invoice_payment"]["invoice_payment"]
    assert ip_body["invoice_id"] == 63
    assert ip_body["account_id"] == 94
    assert ip_body["amount"]     == 131.92
    assert inbox["qoyod_responses"]["invoice"]["body"]["invoice"]["id"] == 63
    assert inbox["qoyod_responses"]["invoice_payment"]["body"]["invoice_payment"]["id"] == 888
    # Stage history STILL comes through (regression on the original spec).
    stages = [e["to_stage"] for e in inbox["stage_history"]]
    assert "INVOICE_PAYMENT_CREATED" in stages
    assert stages[-1] == "COMPLETED"


# ─── 2. The projection itself carries the new keys ───────────────────
def test_get_invoice_projection_includes_qoyod_bodies_and_payment_id():
    """A static guardrail: the inbox-side `find_one` MUST request the
    payloads/responses/payment_id fields. Without this the live UI
    silently strips them even when MongoDB has the data."""
    import inspect
    from integrations.qoyod import routes as qroutes
    src = inspect.getsource(qroutes.make_qoyod_router)
    # The projection passed to the inbox find_one must include the
    # new keys.
    required_in_projection = [
        '"qoyod_invoice_payment_id"',
        '"qoyod_payloads.invoice"',
        '"qoyod_payloads.invoice_payment"',
        '"qoyod_responses.invoice.body"',
        '"qoyod_responses.invoice_payment.body"',
        '"stage_history"',  # regression: still present after expansion
    ]
    for key in required_in_projection:
        assert key in src, (
            f"projection for `get_invoice` is missing required key: {key!r} — "
            "the Timeline drawer needs this to render the قيود bodies "
            "without re-running the pipeline.")
