"""Iter-2026-02.rev14 — HTTP binding tests for
`POST /api/integrations/qoyod/admin/retry-payment-only`.

Regression: production returned 422 with
`{"loc": ["query", "body"], "type": "missing"}` even though the
caller sent a valid JSON body. Root cause: `_RetryPaymentBody` is
defined INSIDE `make_qoyod_router` (function scope) — FastAPI's
type introspection then mis-classifies it as a query parameter
unless the handler declares `= Body(...)` explicitly.

These tests hit the real route via ASGITransport so a regression
(dropping `Body(...)`) fails loudly and immediately.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI


@pytest.fixture
def fake_user():
    class U:
        id    = "main"
        email = "ops@mezan.example"
    return U()


class _Col:
    def __init__(self):
        self.rows: list[dict] = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None
    def find(self, q, projection=None):
        class _C:
            def __aiter__(self_inner): return self_inner
            async def __anext__(self_inner):
                raise StopAsyncIteration
        return _C()
    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                for k, v in (u.get("$push") or {}).items():
                    arr = r.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else:
                        arr.append(v)
                return
        if upsert:
            new = {**q, **(u.get("$set") or {}),
                   **(u.get("$setOnInsert") or {})}
            self.rows.append(new)


class _DB:
    def __init__(self):
        self.integration_inbox      = _Col()
        self.qoyod_settings         = _Col()
        self.qoyod_invoices         = _Col()
        self.qoyod_invoice_payments = _Col()


class _OK:
    """قيود stub — succeeds; refuses any endpoint but
    /invoice_payments so a regression is caught."""
    def __init__(self):
        self.calls = []
    async def create_invoice_payment(self, payload, *, idem):
        self.calls.append({"payload": payload, "idem": idem})
        return {"invoice_payment": {"id": "PMT-HTTP-OK"}}
    async def create_invoice(self, *a, **kw):
        raise AssertionError(
            "retry-payment-only MUST NOT call /invoices")
    async def create_customer(self, *a, **kw):
        raise AssertionError(
            "retry-payment-only MUST NOT call /customers")
    async def create_product(self, *a, **kw):
        raise AssertionError(
            "retry-payment-only MUST NOT call /products")
    async def create_receipt(self, *a, **kw):
        raise AssertionError(
            "retry-payment-only MUST NOT call /receipts")


def _build_test_app(db, fake_user, monkeypatch, qoyod=None):
    """Build a fresh FastAPI app with the qoyod router bound to
    an in-memory DB — no live DB / no live قيود reached."""
    from integrations.qoyod import (
        routes as qroutes, retry_payment_only as retry_mod,
    )
    from server import current_user as real_current_user

    async def _key(*a, **kw): return "test-key"
    monkeypatch.setattr(retry_mod, "get_api_key", _key)
    if qoyod is not None:
        monkeypatch.setattr(
            retry_mod, "QoyodAPIClient",
            lambda key, **kw: qoyod)

    router = qroutes.make_qoyod_router(db, real_current_user)
    app = FastAPI()

    async def _override_user():
        return fake_user
    app.dependency_overrides[real_current_user] = _override_user
    app.include_router(router, prefix="/api")
    return app


def _seed_269629400_row(db):
    db.qoyod_settings.rows.append({
        "user_id": "main",
        "payment_method_mapping": [
            {"salla_method": "tabby", "qoyod_account_id": "92"},
        ],
        "credentials": {"api_key_encrypted": "test-key"},
    })
    db.integration_inbox.rows.append({
        "id":                 "row-269629400",
        "user_id":            "main",
        "salla_order_number": "269629400",
        "trace_id":           "162b59c818114b259850f2e2d35449f7",
        "qoyod_invoice_id":   "186",
        "qoyod_customer_id":  "228",
        "pipeline_stage":     "INVOICE_CREATED",
        "stage_history":      [],
        "canonical_payload": {
            "order_id":       "MZN-269629400",
            "order_number":   "269629400",
            "total_amount":   178.87,
            "currency":       "SAR",
            "payment_method": "tabby_installment",
        },
        "business_rules_decision": {
            "invoice_date": "2026-07-02T00:00:00+00:00",
        },
    })


# ─── 1. Valid JSON body — 200 OK, POST /invoice_payments only ────
@pytest.mark.asyncio
async def test_valid_json_body_accepted_and_only_invoice_payments_posted(
        monkeypatch, fake_user):
    db = _DB()
    _seed_269629400_row(db)
    qoyod = _OK()
    app = _build_test_app(db, fake_user, monkeypatch, qoyod=qoyod)

    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        resp = await ac.post(
            "/api/integrations/qoyod/admin/retry-payment-only",
            json={
                "salla_order_number": "269629400",
                "confirm_token":      "RETRY-PAYMENT-269629400",
            })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["outcome"] == "COMPLETED"
    assert body["qoyod_invoice_payment_id"] == "PMT-HTTP-OK"
    # Exactly ONE قيود call — POST /invoice_payments — no /invoices.
    assert len(qoyod.calls) == 1
    inv_pay = qoyod.calls[0]["payload"]["invoice_payment"]
    assert inv_pay["invoice_id"] == 186
    assert inv_pay["amount"]     == 178.87
    assert inv_pay["date"]       == "2026-07-02"
    assert inv_pay["account_id"] == 92          # Tabby clearing
    assert inv_pay["reference"]  == "269629400"


# ─── 2. Missing JSON body → 422 with proper `body` (not `query`) ─
@pytest.mark.asyncio
async def test_missing_json_returns_422_body_validation(
        monkeypatch, fake_user):
    db = _DB()
    app = _build_test_app(db, fake_user, monkeypatch)
    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        resp = await ac.post(
            "/api/integrations/qoyod/admin/retry-payment-only")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # Iter-2026-02.rev14 — the WRONG classification would report
    # `loc: ["query", "body"]`. The correct one is `loc: ["body"]`
    # or `loc: ["body", "<field>"]` — never `["query", ...]`.
    for err in detail:
        assert "query" not in err.get("loc", ()), (
            "regression: `_RetryPaymentBody` was mis-bound as a "
            "query parameter — restore `Body(...)` on the handler")


# ─── 3. Partial body → 422 with `body`-scoped field errors ───────
@pytest.mark.asyncio
async def test_partial_body_returns_body_scoped_422(
        monkeypatch, fake_user):
    db = _DB()
    app = _build_test_app(db, fake_user, monkeypatch)
    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        resp = await ac.post(
            "/api/integrations/qoyod/admin/retry-payment-only",
            json={"salla_order_number": "269629400"})    # no token
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # The MISSING field is `confirm_token` and it MUST be reported
    # under the body (not query) scope.
    saw_body_field_error = any(
        err.get("loc", [None])[0] == "body" for err in detail)
    assert saw_body_field_error, detail


# ─── 4. No invoice POST attempted even on refusal paths ──────────
@pytest.mark.asyncio
async def test_no_invoice_call_ever_from_this_endpoint(
        monkeypatch, fake_user):
    """The قيود stub raises if `/invoices` is touched — proves the
    handler NEVER routes to invoice creation, regardless of the
    body's contents."""
    db = _DB()
    _seed_269629400_row(db)
    qoyod = _OK()
    app = _build_test_app(db, fake_user, monkeypatch, qoyod=qoyod)
    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        await ac.post(
            "/api/integrations/qoyod/admin/retry-payment-only",
            json={
                "salla_order_number": "269629400",
                "confirm_token":      "RETRY-PAYMENT-269629400",
            })
    # Only /invoice_payments touched — the _OK stub would have
    # raised AssertionError from any other endpoint.
    assert all("invoice_payment" in str(c) for c in qoyod.calls)


# ─── 5. Adopt-existing-payment — same body-binding guarantee ──────
@pytest.mark.asyncio
async def test_adopt_existing_payment_json_body_accepted(
        monkeypatch, fake_user):
    """The adopt endpoint MUST also accept its JSON body — same
    module-scope model pattern applies (`AdoptExistingPaymentBody`).
    Regression check: `loc` must never be `["query", ...]`."""
    db = _DB()
    _seed_269629400_row(db)
    app = _build_test_app(db, fake_user, monkeypatch)

    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        resp = await ac.post(
            "/api/integrations/qoyod/admin/adopt-existing-payment",
            json={
                "salla_order_number":       "269629400",
                "qoyod_invoice_payment_id": "PYT2",
                "qoyod_invoice_id":         "186",
                "qoyod_customer_id":        "228",
                "confirm_token": "ADOPT-PAYMENT-269629400",
            })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["outcome"] == "ADOPTED"
    assert body["qoyod_invoice_payment_id"] == "PYT2"
    assert body["no_qoyod_api_calls"] is True


@pytest.mark.asyncio
async def test_adopt_missing_json_returns_body_scoped_422(
        monkeypatch, fake_user):
    db = _DB()
    app = _build_test_app(db, fake_user, monkeypatch)
    async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test") as ac:
        resp = await ac.post(
            "/api/integrations/qoyod/admin/adopt-existing-payment")
    assert resp.status_code == 422
    for err in resp.json()["detail"]:
        assert "query" not in err.get("loc", ()), (
            "regression: AdoptExistingPaymentBody bound as query "
            "parameter — restore module scope + Body(...)")
