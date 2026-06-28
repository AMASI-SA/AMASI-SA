"""Iter-290h.5 — Surgical retry_payment_only endpoint tests.

Locks in the user's hard requirements for the production 269048975
recovery case:

  1. Does not touch /customers, /products, /invoices, /receipts.
  2. Refuses without an existing qoyod_invoice_id.
  3. Refuses with stale confirm_token.
  4. Surfaces LIVE قيود verdict on failure (not stale pipeline_error).
  5. Idempotency skips only on REAL prior success (not on prior failure).
  6. Persists qoyod_invoice_payments ledger row on success.
  7. Transitions row → COMPLETED + clears pipeline_error on success.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from integrations.qoyod.retry_payment_only import (
    retry_payment_only, RetryPaymentRefused,
)
from integrations.qoyod.api_client import QoyodAPIError


# ─── In-memory DB stub ───────────────────────────────────────────────
class _Cur:
    def __init__(self, items): self._items = list(items)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._items: raise StopAsyncIteration
        return self._items.pop(0)


class _Col:
    def __init__(self): self.rows: list[dict] = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None
    def find(self, q, projection=None):
        return _Cur([dict(r) for r in self.rows
                     if all(r.get(k) == v for k, v in q.items())])
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


# ─── Stub Qoyod client ───────────────────────────────────────────────
class _OK:
    """Succeeds with deterministic invoice_payment id."""
    def __init__(self): self.calls = []
    async def create_invoice_payment(self, payload, *, idem):
        self.calls.append({"payload": payload, "idem": idem})
        return {"invoice_payment": {"id": "PMT-OK-1"}}
    async def create_invoice(self, *_a, **_k):
        raise AssertionError("retry_payment_only must NOT call /invoices")
    async def create_customer(self, *_a, **_k):
        raise AssertionError("retry_payment_only must NOT call /customers")
    async def create_product(self, *_a, **_k):
        raise AssertionError("retry_payment_only must NOT call /products")
    async def create_receipt(self, *_a, **_k):
        raise AssertionError("retry_payment_only must NOT call /receipts")


class _FlakyQoyod(_OK):
    async def create_invoice_payment(self, payload, *, idem):
        self.calls.append({"payload": payload, "idem": idem})
        raise QoyodAPIError(
            status_code=422, code="qoyod_validation_error",
            message="Invalid resource",
            response_excerpt='{"error":"Invalid resource"}',
            endpoint="POST /invoice_payments",
        )


# ─── Common fixture ──────────────────────────────────────────────────
async def _seed(db, *, salla_order_number="269048975", invoice_id="63",
                pipeline_stage="PARTIAL_FAILURE", with_mapping=True,
                stale_error=True):
    db.qoyod_settings.rows.append({
        "user_id": "main",
        "payment_method_mapping": (
            [{"salla_method": "mada", "qoyod_account_id": "94"}]
            if with_mapping else []),
        "credentials": {"api_key_encrypted": "test-key"},
    })
    row = {
        "id": "row1", "user_id": "main",
        "salla_order_number": salla_order_number,
        "trace_id": "t1",
        "qoyod_invoice_id": invoice_id,
        "pipeline_stage": pipeline_stage,
        "last_failed_stage": "PAYMENT_LINK_FAILED" if stale_error else None,
        "stage_history": [],
        "canonical_payload": {
            "order_id":      f"MZN-{salla_order_number}",
            "order_number":  salla_order_number,
            "total_amount":  131.92,
            "currency":      "SAR",
            "payment_method": "mada",
        },
        "business_rules_decision": {
            "invoice_date": "2026-06-28T00:00:00+00:00",
        },
    }
    if stale_error:
        # Stale pipeline_error from previous failed attempt — proves
        # the retry surfaces a FRESH verdict, not this old one.
        row["pipeline_error"] = {
            "code": "qoyod_validation_error",
            "message": "Invalid resource",
            "status_code": 422,
            "qoyod_response_excerpt": "STALE error from previous run",
            "request_body_json": {"OLD": "stale"},
        }
        row["qoyod_responses"] = {"invoice_payment": {"error": row["pipeline_error"]}}
    db.integration_inbox.rows.append(row)


@pytest.fixture
def patch_deps(monkeypatch):
    from integrations.qoyod import retry_payment_only as mod
    async def _key(*a, **kw): return "test-key"
    monkeypatch.setattr(mod, "get_api_key", _key)
    # Patch QoyodAPIClient at module scope to a placeholder; tests
    # override per-case via monkeypatch below.
    return monkeypatch


# ─── 1. Refuses missing confirm token ────────────────────────────────
@pytest.mark.asyncio
async def test_refuses_invalid_confirm_token(patch_deps):
    db = _DB()
    await _seed(db)
    with pytest.raises(RetryPaymentRefused) as exc:
        await retry_payment_only(
            db, user_id="main",
            salla_order_number="269048975",
            confirm_token="WRONG", actor="ops")
    assert exc.value.code == "confirm_token_mismatch"


# ─── 2. Refuses when no existing invoice ─────────────────────────────
@pytest.mark.asyncio
async def test_refuses_without_existing_invoice_id(patch_deps):
    db = _DB()
    await _seed(db, invoice_id=None)
    with pytest.raises(RetryPaymentRefused) as exc:
        await retry_payment_only(
            db, user_id="main",
            salla_order_number="269048975",
            confirm_token="RETRY-PAYMENT-269048975", actor="ops")
    assert exc.value.code == "missing_existing_invoice_id"


# ─── 3. Refuses with structured response when method unmapped ────────
@pytest.mark.asyncio
async def test_payment_method_mapping_missing_returns_structured_refusal(patch_deps):
    db = _DB()
    await _seed(db, with_mapping=False)
    from integrations.qoyod import retry_payment_only as mod
    patch_deps.setattr(mod, "QoyodAPIClient", lambda key: _OK())
    out = await retry_payment_only(
        db, user_id="main", salla_order_number="269048975",
        confirm_token="RETRY-PAYMENT-269048975", actor="ops")
    assert out["ok"] is False
    assert out["outcome"] == "REFUSED"
    assert out["skip_reason"] == "payment_method_mapping_missing"
    assert out["payment_post_attempted"] is False
    assert out["request_sent_to_qoyod"]  is False


# ─── 4. Live قيود failure — surfaces fresh response, not stale ───────
@pytest.mark.asyncio
async def test_qoyod_failure_surfaces_fresh_verdict_not_stale(patch_deps):
    db = _DB()
    await _seed(db, stale_error=True)
    qoyod = _FlakyQoyod()
    from integrations.qoyod import retry_payment_only as mod
    patch_deps.setattr(mod, "QoyodAPIClient", lambda key: qoyod)

    out = await retry_payment_only(
        db, user_id="main", salla_order_number="269048975",
        confirm_token="RETRY-PAYMENT-269048975", actor="ops")

    # The CALL did happen (this is the critical fix).
    assert len(qoyod.calls) == 1
    assert out["payment_post_attempted"] is True
    assert out["request_sent_to_qoyod"]  is True
    assert out["outcome"] == "PAYMENT_LINK_FAILED"
    assert out["qoyod_status_code"] == 422
    # FRESH excerpt — NOT the stale value seeded.
    assert "STALE" not in (out["qoyod_response"] or "")
    assert "Invalid resource" in (out["qoyod_response"] or "")
    # The new payload — date + account, no payment_date/payment_method_id.
    body = out["request_body_json"]["invoice_payment"]
    assert body["date"]    == "2026-06-28"
    assert body["account"] == 94
    # The wire-level fingerprint is correctly attached.
    assert out["existing_qoyod_invoice_id"] == "63"


# ─── 5. Idempotency — skips only on REAL prior success ───────────────
@pytest.mark.asyncio
async def test_idempotency_skips_only_on_real_success(patch_deps):
    db = _DB()
    await _seed(db)
    db.qoyod_invoice_payments.rows.append({
        "user_id":          "main",
        "salla_order_id":   "MZN-269048975",
        "qoyod_invoice_id": 63,
        "payment_method":   "mada",
        "amount":           131.92,
        "qoyod_invoice_payment_id": "PMT-PRIOR",   # REAL success record
    })
    qoyod = _OK()
    from integrations.qoyod import retry_payment_only as mod
    patch_deps.setattr(mod, "QoyodAPIClient", lambda key: qoyod)
    out = await retry_payment_only(
        db, user_id="main", salla_order_number="269048975",
        confirm_token="RETRY-PAYMENT-269048975", actor="ops")
    # POST is NOT re-attempted — short-circuit is correct.
    assert qoyod.calls == []
    assert out["ok"] is True
    assert out["outcome"] == "ALREADY_PAID"
    assert out["qoyod_invoice_payment_id"] == "PMT-PRIOR"
    assert out["skip_reason"] == "idempotency_success_record_exists"


# ─── 6. Success path — full state transition + ledger write ──────────
@pytest.mark.asyncio
async def test_success_transitions_row_to_completed_and_writes_ledger(patch_deps):
    db = _DB()
    await _seed(db)
    qoyod = _OK()
    from integrations.qoyod import retry_payment_only as mod
    patch_deps.setattr(mod, "QoyodAPIClient", lambda key: qoyod)
    out = await retry_payment_only(
        db, user_id="main", salla_order_number="269048975",
        confirm_token="RETRY-PAYMENT-269048975", actor="ops")
    assert out["ok"] is True
    assert out["outcome"] == "COMPLETED"
    assert out["qoyod_invoice_payment_id"] == "PMT-OK-1"
    assert out["payment_post_attempted"] is True
    # Row state — COMPLETED + cleared stale error.
    r = await db.integration_inbox.find_one({"id": "row1"})
    assert r["pipeline_stage"]   == "COMPLETED"
    assert r["last_failed_stage"] is None
    assert r["pipeline_error"]    is None
    assert r["qoyod_invoice_payment_id"] == "PMT-OK-1"
    history_to = [h.get("to_stage") for h in r.get("stage_history", [])]
    assert "INVOICE_PAYMENT_CREATED" in history_to
    assert "COMPLETED" in history_to
    # Ledger row written.
    led = await db.qoyod_invoice_payments.find_one(
        {"salla_order_id": "MZN-269048975"})
    assert led["qoyod_invoice_payment_id"] == "PMT-OK-1"
    assert led["source"] == "retry_payment_only"
