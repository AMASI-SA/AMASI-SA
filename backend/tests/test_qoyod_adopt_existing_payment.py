"""Iter-2026-02.rev15 — Adopt existing قيود payment tests.

Locks in the contract for `adopt_existing_payment`:

  • ZERO قيود API calls (booking-only operation).
  • Writes an idempotency record so `retry_payment_only` returns
    ALREADY_PAID on the next attempt.
  • Sets row.pipeline_stage=COMPLETED, qoyod_invoice_payment_id, etc.
  • Refuses on confirm_token mismatch / missing invoice id.
  • ALREADY_ADOPTED path is idempotent (safe to call twice).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, "/app/backend")


class _Col:
    def __init__(self):
        self.rows: list[dict] = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None
    async def update_one(self, q, u, upsert=False):
        matched = 0
        modified = 0
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                matched = 1
                for k, v in (u.get("$set") or {}).items():
                    if r.get(k) != v:
                        modified = 1
                        r[k] = v
                for k, v in (u.get("$push") or {}).items():
                    arr = r.setdefault(k, [])
                    arr.append(v)
                # Return simulated result matching Motor's shape
                res = MagicMock()
                res.matched_count = matched
                res.modified_count = modified
                res.upserted_id = None
                return res
        if upsert:
            new = {**q, **(u.get("$set") or {}),
                   **(u.get("$setOnInsert") or {})}
            self.rows.append(new)
            res = MagicMock()
            res.matched_count = 0
            res.modified_count = 0
            res.upserted_id = "upserted"
            return res
        res = MagicMock()
        res.matched_count = 0
        res.modified_count = 0
        res.upserted_id = None
        return res


class _DB:
    def __init__(self):
        self.integration_inbox      = _Col()
        self.qoyod_settings         = _Col()
        self.qoyod_invoices         = _Col()
        self.qoyod_invoice_payments = _Col()


def _seed_269629400(db):
    """Simulate PRODUCTION state after invoice was bound manually."""
    db.qoyod_settings.rows.append({
        "user_id": "main",
        "payment_method_mapping": [
            {"salla_method": "tabby", "qoyod_account_id": "110702"},
        ],
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


# ─── 1. Happy path — adopts and writes idempotency record ──────────
@pytest.mark.asyncio
async def test_adopt_pyt2_writes_idempotency_and_row_transition():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment,
    )
    db = _DB()
    _seed_269629400(db)

    out = await adopt_existing_payment(
        db, user_id="main",
        salla_order_number="269629400",
        qoyod_invoice_payment_id="PYT2",
        confirm_token="ADOPT-PAYMENT-269629400",
        qoyod_invoice_id="186",
        qoyod_customer_id="228",
        actor="ops",
    )

    assert out["ok"] is True
    assert out["outcome"] == "ADOPTED"
    assert out["qoyod_invoice_payment_id"] == "PYT2"
    assert out["qoyod_invoice_id"]         == "186"
    assert out["qoyod_customer_id"]        == "228"
    assert out["no_qoyod_api_calls"]       is True

    # Row transitioned to COMPLETED with all bindings.
    r = await db.integration_inbox.find_one({"id": "row-269629400"})
    assert r["pipeline_stage"]           == "COMPLETED"
    assert r["qoyod_invoice_payment_id"] == "PYT2"
    assert r["qoyod_invoice_id"]         == "186"
    assert r["qoyod_customer_id"]        == "228"
    assert r["adopted_payment"]          is True
    assert any(h.get("to_stage") == "COMPLETED"
               for h in r["stage_history"])

    # Idempotency record written.
    led = await db.qoyod_invoice_payments.find_one(
        {"salla_order_id": "MZN-269629400"})
    assert led is not None
    assert led["qoyod_invoice_payment_id"] == "PYT2"
    assert led["source"] == "adopt_existing_payment"
    assert led["adopted_by"] == "ops"
    assert led["amount"] == 178.87

    # قيود invoices projection updated.
    inv = await db.qoyod_invoices.find_one(
        {"user_id": "main", "salla_order_number": "269629400"})
    assert inv["qoyod_invoice_payment_id"] == "PYT2"
    assert inv["status"] == "sent"
    assert inv["source"] == "adopt_existing_payment"


# ─── 2. Post-adopt: retry_payment_only returns ALREADY_PAID ────────
@pytest.mark.asyncio
async def test_post_adopt_retry_payment_only_returns_already_paid(
        monkeypatch):
    """The most important invariant: after adopt, any further
    `retry_payment_only` for this order MUST hit the idempotency
    guard and refuse to POST — never a duplicate PYT2."""
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment,
    )
    from integrations.qoyod.retry_payment_only import (
        retry_payment_only,
    )
    db = _DB()
    _seed_269629400(db)

    # Adopt first.
    await adopt_existing_payment(
        db, user_id="main",
        salla_order_number="269629400",
        qoyod_invoice_payment_id="PYT2",
        confirm_token="ADOPT-PAYMENT-269629400",
        actor="ops",
    )

    # قيود stub that raises if touched — retry MUST NOT reach it.
    class _NoQoyod:
        async def create_invoice_payment(self, *a, **kw):
            raise AssertionError(
                "retry_payment_only MUST NOT POST after adopt — "
                "idempotency guard failed")

    from integrations.qoyod import retry_payment_only as retry_mod
    monkeypatch.setattr(retry_mod, "get_api_key",
                        AsyncMock(return_value="test-key"))
    monkeypatch.setattr(retry_mod, "QoyodAPIClient",
                        lambda key, **kw: _NoQoyod())

    retry_out = await retry_payment_only(
        db, user_id="main",
        salla_order_number="269629400",
        confirm_token="RETRY-PAYMENT-269629400",
        actor="ops",
    )
    assert retry_out["ok"] is True
    assert retry_out["outcome"] == "ALREADY_PAID"
    assert retry_out["skip_reason"] == "idempotency_success_record_exists"
    assert retry_out["payment_post_attempted"] is False
    assert retry_out["request_sent_to_qoyod"] is False
    assert retry_out["qoyod_invoice_payment_id"] == "PYT2"


# ─── 3. Idempotent adopt — safe to call twice ─────────────────────
@pytest.mark.asyncio
async def test_adopt_is_idempotent():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment,
    )
    db = _DB()
    _seed_269629400(db)

    out1 = await adopt_existing_payment(
        db, user_id="main",
        salla_order_number="269629400",
        qoyod_invoice_payment_id="PYT2",
        confirm_token="ADOPT-PAYMENT-269629400",
        actor="ops")
    out2 = await adopt_existing_payment(
        db, user_id="main",
        salla_order_number="269629400",
        qoyod_invoice_payment_id="PYT2",
        confirm_token="ADOPT-PAYMENT-269629400",
        actor="ops")
    assert out1["outcome"] == "ADOPTED"
    assert out2["outcome"] == "ALREADY_ADOPTED"
    # Ledger unchanged — no duplicate rows.
    leds = [r for r in db.qoyod_invoice_payments.rows
            if r.get("salla_order_id") == "MZN-269629400"]
    assert len(leds) == 1


# ─── 4. Refuse — bad confirm_token ────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_confirm_token_mismatch():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment, AdoptPaymentRefused,
    )
    db = _DB()
    _seed_269629400(db)
    with pytest.raises(AdoptPaymentRefused) as excinfo:
        await adopt_existing_payment(
            db, user_id="main",
            salla_order_number="269629400",
            qoyod_invoice_payment_id="PYT2",
            confirm_token="WRONG",
            actor="ops")
    assert excinfo.value.code == "confirm_token_mismatch"
    # No writes.
    assert not db.qoyod_invoice_payments.rows
    r = await db.integration_inbox.find_one({"id": "row-269629400"})
    assert r["pipeline_stage"] == "INVOICE_CREATED"  # unchanged


# ─── 5. Refuse — missing qoyod_invoice_id (row + arg both empty) ──
@pytest.mark.asyncio
async def test_refuse_missing_invoice_id():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment, AdoptPaymentRefused,
    )
    db = _DB()
    _seed_269629400(db)
    # Clear the row's invoice binding to simulate no target.
    db.integration_inbox.rows[0]["qoyod_invoice_id"] = None

    with pytest.raises(AdoptPaymentRefused) as excinfo:
        await adopt_existing_payment(
            db, user_id="main",
            salla_order_number="269629400",
            qoyod_invoice_payment_id="PYT2",
            confirm_token="ADOPT-PAYMENT-269629400",
            actor="ops")
    assert excinfo.value.code == "missing_qoyod_invoice_id"
    assert not db.qoyod_invoice_payments.rows


# ─── 6. Refuse — missing qoyod_invoice_payment_id ─────────────────
@pytest.mark.asyncio
async def test_refuse_missing_payment_id():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment, AdoptPaymentRefused,
    )
    db = _DB()
    _seed_269629400(db)
    with pytest.raises(AdoptPaymentRefused) as excinfo:
        await adopt_existing_payment(
            db, user_id="main",
            salla_order_number="269629400",
            qoyod_invoice_payment_id="",
            confirm_token="ADOPT-PAYMENT-269629400",
            actor="ops")
    assert excinfo.value.code == "missing_qoyod_invoice_payment_id"


# ─── 7. Refuse — row not found ────────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_row_not_found():
    from integrations.qoyod.adopt_existing_payment import (
        adopt_existing_payment, AdoptPaymentRefused,
    )
    db = _DB()
    with pytest.raises(AdoptPaymentRefused) as excinfo:
        await adopt_existing_payment(
            db, user_id="main",
            salla_order_number="999999",
            qoyod_invoice_payment_id="PYT2",
            confirm_token="ADOPT-PAYMENT-999999",
            actor="ops")
    assert excinfo.value.code == "row_not_found"
