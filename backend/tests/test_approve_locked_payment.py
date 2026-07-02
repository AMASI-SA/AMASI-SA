"""Iter-2026-02.rev21 — Approve locked payment tests."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

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
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                for k in (u.get("$unset") or {}):
                    r.pop(k, None)
                for k, v in (u.get("$push") or {}).items():
                    arr = r.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else:
                        arr.append(v)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            new = {**q, **(u.get("$set") or {}),
                   **(u.get("$setOnInsert") or {})}
            self.rows.append(new)
            return MagicMock(matched_count=0, modified_count=0,
                             upserted_id="new")
        return MagicMock(matched_count=0, modified_count=0)
    async def insert_one(self, d):
        self.rows.append(d)
        return MagicMock(inserted_id="new")


class _DB:
    def __init__(self):
        self.qoyod_write_lock_attempts = _Col()
        self.integration_inbox         = _Col()
        self.qoyod_settings            = _Col()
        self.qoyod_invoice_payments    = _Col()


ATTEMPT_ID = "7027285a-df11-4972-94d4-2545aeb535a5"
ORDER      = "270075325"


def _seed(db, action="create_invoice_payment", payload=None,
          row_payment_id=None, ledger_paid=False):
    db.qoyod_write_lock_attempts.rows.append({
        "attempt_id":       ATTEMPT_ID,
        "user_id":          "main",
        "action":           action,
        "method":           "POST",
        "path":             "/invoice_payments",
        "order_number":     ORDER,
        "trace_id":         "tr-1",
        "locked_payload":   payload if payload else {
            "invoice_payment": {
                "invoice_id":     187,
                "amount":         260.98,
                "date":           "2026-07-03",
                "account_id":     91,
                "reference":      ORDER,
                "description":    f"Mezan · Salla order {ORDER}",
                "payment_method": "mada",
            }},
        "idempotency_key": "mzn-tr-1-invoice-payment-187",
    })
    db.integration_inbox.rows.append({
        "id":                        "row-270075325",
        "user_id":                   "main",
        "salla_order_number":        ORDER,
        "trace_id":                  "tr-1",
        "pipeline_stage":            "LOCKED_AWAITING_APPROVAL",
        "qoyod_invoice_id":          "187",
        "qoyod_customer_id":         "230",
        "qoyod_invoice_payment_id":  row_payment_id,
        "stage_history":             [],
        "canonical_payload": {
            "order_id":     f"MZN-{ORDER}",
            "order_number": ORDER,
            "currency":     "SAR",
        },
    })
    db.qoyod_settings.rows.append({"user_id": "main"})
    if ledger_paid:
        db.qoyod_invoice_payments.rows.append({
            "user_id":                  "main",
            "salla_order_id":           f"MZN-{ORDER}",
            "qoyod_invoice_id":         187,
            "qoyod_invoice_payment_id": "PMT-EXISTING",
        })


class _StubQoyod:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []
    async def create_invoice_payment(self, payload, *, idem):
        self.calls.append({"payload": payload, "idem": idem})
        if not self.ok:
            raise RuntimeError("قيود outage")
        return {"invoice_payment": {"id": "PYT-ABC-99"}}
    async def create_invoice(self, *a, **kw):
        raise AssertionError("MUST NOT create invoice")
    async def create_customer(self, *a, **kw):
        raise AssertionError("MUST NOT create customer")
    async def create_product(self, *a, **kw):
        raise AssertionError("MUST NOT create product")


# ─── 1. Happy path: parked payload replayed once ───────────────────
@pytest.mark.asyncio
async def test_approve_replays_only_invoice_payment_post(monkeypatch):
    from integrations.qoyod import approve_locked_payment as mod
    db = _DB()
    _seed(db)
    qoyod = _StubQoyod()

    monkeypatch.setattr("integrations.qoyod.api_client.QoyodAPIClient", lambda k, **kw: qoyod)
    monkeypatch.setattr("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="live-key"))

    out = await mod.approve_locked_payment(
        db, user_id="main",
        lock_attempt_id=ATTEMPT_ID,
        confirm_token=f"APPROVE-PAYMENT-{ORDER}",
        actor="ops")

    assert out["ok"] is True
    assert out["outcome"] == "COMPLETED"
    assert out["qoyod_invoice_payment_id"] == "PYT-ABC-99"
    # Exactly ONE POST — invoice_payment only.
    assert len(qoyod.calls) == 1
    body = qoyod.calls[0]["payload"]["invoice_payment"]
    assert body["invoice_id"] == 187
    assert body["amount"]     == 260.98
    assert body["account_id"] == 91
    # Row transitioned.
    r = await db.integration_inbox.find_one(
        {"id": "row-270075325"})
    assert r["pipeline_stage"]           == "COMPLETED"
    assert r["qoyod_invoice_payment_id"] == "PYT-ABC-99"
    # Ledger written for idempotency.
    led = await db.qoyod_invoice_payments.find_one(
        {"user_id": "main", "qoyod_invoice_id": 187})
    assert led["qoyod_invoice_payment_id"] == "PYT-ABC-99"
    assert led["source"] == "approve_locked_payment"
    # Attempt marked approved.
    att = await db.qoyod_write_lock_attempts.find_one(
        {"attempt_id": ATTEMPT_ID})
    assert att["approved"] is True
    assert att["qoyod_invoice_payment_id"] == "PYT-ABC-99"


# ─── 2. Refuse — bad confirm_token ────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_bad_confirm_token():
    from integrations.qoyod import approve_locked_payment as mod
    from integrations.qoyod.approve_locked_payment import (
        ApproveLockedPaymentRefused,
    )
    db = _DB()
    _seed(db)
    with pytest.raises(ApproveLockedPaymentRefused) as exc:
        await mod.approve_locked_payment(
            db, user_id="main",
            lock_attempt_id=ATTEMPT_ID,
            confirm_token="WRONG",
            actor="ops")
    assert exc.value.code == "confirm_token_mismatch"


# ─── 3. Refuse — attempt not found ────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_missing_attempt():
    from integrations.qoyod import approve_locked_payment as mod
    from integrations.qoyod.approve_locked_payment import (
        ApproveLockedPaymentRefused,
    )
    db = _DB()
    with pytest.raises(ApproveLockedPaymentRefused) as exc:
        await mod.approve_locked_payment(
            db, user_id="main",
            lock_attempt_id="does-not-exist",
            confirm_token=f"APPROVE-PAYMENT-{ORDER}",
            actor="ops")
    assert exc.value.code == "lock_attempt_not_found"


# ─── 4. Refuse — wrong action (e.g. invoice creation) ─────────────
@pytest.mark.asyncio
async def test_refuse_wrong_action():
    from integrations.qoyod import approve_locked_payment as mod
    from integrations.qoyod.approve_locked_payment import (
        ApproveLockedPaymentRefused,
    )
    db = _DB()
    _seed(db, action="create_invoice")
    with pytest.raises(ApproveLockedPaymentRefused) as exc:
        await mod.approve_locked_payment(
            db, user_id="main",
            lock_attempt_id=ATTEMPT_ID,
            confirm_token=f"APPROVE-PAYMENT-{ORDER}",
            actor="ops")
    assert exc.value.code == "wrong_action"


# ─── 5. ALREADY_PAID — row already has payment id ─────────────────
@pytest.mark.asyncio
async def test_already_paid_by_row_field(monkeypatch):
    from integrations.qoyod import approve_locked_payment as mod
    db = _DB()
    _seed(db, row_payment_id="PMT-EARLIER")
    qoyod = _StubQoyod()
    monkeypatch.setattr("integrations.qoyod.api_client.QoyodAPIClient", lambda k, **kw: qoyod)
    monkeypatch.setattr("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="live-key"))

    out = await mod.approve_locked_payment(
        db, user_id="main",
        lock_attempt_id=ATTEMPT_ID,
        confirm_token=f"APPROVE-PAYMENT-{ORDER}",
        actor="ops")
    assert out["outcome"] == "ALREADY_PAID"
    assert out["qoyod_invoice_payment_id"] == "PMT-EARLIER"
    assert out["no_qoyod_api_calls"] is True
    assert len(qoyod.calls) == 0


# ─── 6. ALREADY_PAID — ledger already has payment id ──────────────
@pytest.mark.asyncio
async def test_already_paid_by_ledger(monkeypatch):
    from integrations.qoyod import approve_locked_payment as mod
    db = _DB()
    _seed(db, ledger_paid=True)
    qoyod = _StubQoyod()
    monkeypatch.setattr("integrations.qoyod.api_client.QoyodAPIClient", lambda k, **kw: qoyod)
    monkeypatch.setattr("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="live-key"))

    out = await mod.approve_locked_payment(
        db, user_id="main",
        lock_attempt_id=ATTEMPT_ID,
        confirm_token=f"APPROVE-PAYMENT-{ORDER}",
        actor="ops")
    assert out["outcome"] == "ALREADY_PAID"
    assert out["qoyod_invoice_payment_id"] == "PMT-EXISTING"
    assert len(qoyod.calls) == 0


# ─── 7. POST_FAILED — قيود outage returns structured error ────────
@pytest.mark.asyncio
async def test_post_failed_surfaces_cleanly(monkeypatch):
    from integrations.qoyod import approve_locked_payment as mod
    db = _DB()
    _seed(db)
    qoyod = _StubQoyod(ok=False)
    monkeypatch.setattr("integrations.qoyod.api_client.QoyodAPIClient", lambda k, **kw: qoyod)
    monkeypatch.setattr("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="live-key"))

    out = await mod.approve_locked_payment(
        db, user_id="main",
        lock_attempt_id=ATTEMPT_ID,
        confirm_token=f"APPROVE-PAYMENT-{ORDER}",
        actor="ops")
    assert out["ok"] is False
    assert out["outcome"] == "POST_FAILED"
    assert "قيود outage" in out["detail"]
    # Row NOT transitioned.
    r = await db.integration_inbox.find_one(
        {"id": "row-270075325"})
    assert r["pipeline_stage"] == "LOCKED_AWAITING_APPROVAL"
    assert r.get("qoyod_invoice_payment_id") is None


# ─── 8. Malformed payload refused (no invoice_id) ─────────────────
@pytest.mark.asyncio
async def test_refuse_malformed_payload():
    from integrations.qoyod import approve_locked_payment as mod
    from integrations.qoyod.approve_locked_payment import (
        ApproveLockedPaymentRefused,
    )
    db = _DB()
    _seed(db, payload={"invoice_payment": {"amount": 100}})  # no invoice_id
    with pytest.raises(ApproveLockedPaymentRefused) as exc:
        await mod.approve_locked_payment(
            db, user_id="main",
            lock_attempt_id=ATTEMPT_ID,
            confirm_token=f"APPROVE-PAYMENT-{ORDER}",
            actor="ops")
    assert exc.value.code == "malformed_payload"


# ─── 9. Tenant mismatch refused ───────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_tenant_mismatch():
    from integrations.qoyod import approve_locked_payment as mod
    from integrations.qoyod.approve_locked_payment import (
        ApproveLockedPaymentRefused,
    )
    db = _DB()
    _seed(db)
    with pytest.raises(ApproveLockedPaymentRefused) as exc:
        await mod.approve_locked_payment(
            db, user_id="different_tenant",
            lock_attempt_id=ATTEMPT_ID,
            confirm_token=f"APPROVE-PAYMENT-{ORDER}",
            actor="ops")
    assert exc.value.code == "tenant_mismatch"
