"""Iter-291 — Idempotent invoice short-circuit for retry scenarios.

Scenario
────────
Production order 268756329 reached PARTIAL_FAILURE: the invoice was
successfully created in Qoyod (id=51) but the receipt failed because of
a missing `contact_id`. Iter-290d fixed the receipt, but if we naively
retry the row, the pipeline would POST /invoices again → DUPLICATE
invoice in Qoyod.

Fix (`pipeline.py::process_customer_resolved_row`):
    When `row["qoyod_invoice_id"]` is already populated AND the row is
    NOT in dry-run mode, skip the POST entirely. Reuse the stored id
    and advance straight to the receipt step.

Coverage
────────
1. Reused id: `api_client.create_invoice` is NOT called when row
   already has qoyod_invoice_id.
2. Receipt step still runs with the reused id.
3. Diagnostic flags are stamped on the row so an auditor can tell
   the invoice was reused.
4. Fresh row (no qoyod_invoice_id) still POSTs normally.
5. Dry-run mode: even with a stored id, the dry-run path still
   produces a fresh DRY:* stub (don't reuse production ids in dry).
"""
from __future__ import annotations

import asyncio
import os
import uuid
import hashlib

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from integrations.qoyod.pipeline import process_customer_resolved_row
from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.invoice_builder import DryRunQoyodClient


# A LiveLike client that records every create_invoice / create_receipt
# call. Tests assert the counts.
class _CountingClient(DryRunQoyodClient):
    def __init__(self) -> None:
        super().__init__()
        self.invoice_calls = 0
        self.receipt_calls = 0          # legacy /receipts path
        self.invoice_payment_calls = 0  # Iter-290h /invoice_payments path

    def _fake(self, kind: str, payload: dict) -> str:
        h = hashlib.sha1(repr(sorted(payload.items())).encode()).hexdigest()
        return str(int(h[:6], 16))

    async def create_invoice(self, payload, *, idem=None):
        self.invoice_calls += 1
        inv_id = self._fake("invoice", payload.get("invoice", {}))
        return {"invoice": {"id": inv_id, "number": f"INV-{inv_id}"}}

    async def create_receipt(self, payload, *, idem=None):
        self.receipt_calls += 1
        rcp_id = self._fake("receipt", payload.get("receipt", {}))
        return {"receipt": {"id": rcp_id}}

    async def create_invoice_payment(self, payload, *, idem=None):
        self.invoice_payment_calls += 1
        ip_id = self._fake("ip", payload.get("invoice_payment", {}))
        return {"invoice_payment": {"id": ip_id}}


# ── Fixtures shared with test_qoyod_day5_invoice_receipt ─────────────
from datetime import datetime, timezone


def _make_canonical(order_id: str) -> dict:
    return {
        "order_id":       order_id,
        "order_number":   order_id,
        "order_status":   "completed",
        "order_status_native": "completed",
        "currency":       "SAR",
        "total_amount":   115.0,
        "subtotal":       100.0,
        "tax_amount":     15.0,
        "shipping_amount": 0.0,
        "discount_amount": 0.0,
        "items_count":    1,
        "payment_method": "mada",
        "items": [{"sku": "SKU-1", "name": "Item",
                   "quantity": 1, "unit_price": 100,
                   "tax_amount": 15, "discount_amount": 0, "total": 115}],
    }


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _cleanup(db):
    await db.integration_inbox.delete_many({"salla_order_id": {"$regex": "^ITER291-"}})
    await db.qoyod_invoices.delete_many({"salla_order_id": {"$regex": "^ITER291-"}})
    await db.qoyod_settings.delete_many({"user_id": "iter291"})


async def _seed_settings(db, user_id: str) -> None:
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {
            "schema_version": 1, "user_id": user_id,
            "enabled": True, "auto_send": True, "auto_receipt": True,
            "trigger_once_only": True,
            "dry_run_mode": False,
            # Iter-293.4 hardening — missing field now defaults to LOCKED.
            # Existing tests exercise the happy path, so explicitly unlock.
            "production_writes_locked": False,
            "invoice_trigger_statuses": ["completed"],
            "default_tax_id": "1",
            "default_branch_id": "1",
            "default_inventory_id": "1",
            "default_product_category_id":  "1",
            "default_product_tax_id":       "1",
            "default_product_unit_type_id": "1",
            "default_sales_account_id":     "1",
            "tax_mode": "mezan_fixed_15",
            "payment_method_mapping": [
                {"salla_method": "mada", "qoyod_account_id": "94"},
            ],
        }}, upsert=True)


async def _seed_customer_resolved_row(
    db, *, user_id: str, order_id: str, qoyod_invoice_id=None,
) -> dict:
    rid = uuid.uuid4().hex
    row = {
        "id": rid, "schema_version": 1, "user_id": user_id,
        "connector_key": "qoyod_v1",
        "salla_order_id": order_id,
        "trace_id": f"trace-{rid[:8]}",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "received_at": datetime.now(timezone.utc),
        "raw_payload": {"event": "order.completed"},
        "canonical_payload": _make_canonical(order_id),
        "qoyod_customer_id": "109",
        "product_resolution": {"items": [{"sku": "SKU-1",
                                          "qoyod_product_id": "39",
                                          "created_new": False}]},
        "business_rules_decision": {
            "should_create_invoice": True,
            "invoice_date": datetime.now(timezone.utc).isoformat(),
        },
    }
    if qoyod_invoice_id is not None:
        row["qoyod_invoice_id"] = qoyod_invoice_id
        row["qoyod_invoice_number"] = f"INV-{qoyod_invoice_id}"
    await db.integration_inbox.insert_one(row)
    return row


# ─── Tests ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invoice_create_NOT_called_when_qoyod_invoice_id_exists(db):
    await ensure_qoyod_indexes(db); await _cleanup(db)
    user_id = "iter291"
    order_id = f"ITER291-REUSE-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    row = await _seed_customer_resolved_row(
        db, user_id=user_id, order_id=order_id,
        qoyod_invoice_id="51")
    client = _CountingClient()
    out = await process_customer_resolved_row(db, row, api_client=client)
    assert client.invoice_calls == 0, (
        f"create_invoice was called {client.invoice_calls}x; expected 0 "
        f"because row.qoyod_invoice_id was pre-populated. outcome={out}")


@pytest.mark.asyncio
async def test_invoice_payment_still_runs_after_invoice_reuse(db):
    """Iter-290h — When the invoice was reused from a previous run, the
    pipeline must still post `/invoice_payments` so the invoice balance
    actually closes. Replaces the Iter-291 receipt assertion."""
    await ensure_qoyod_indexes(db); await _cleanup(db)
    user_id = "iter291"
    order_id = f"ITER291-RCP-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    await _seed_customer_resolved_row(
        db, user_id=user_id, order_id=order_id,
        qoyod_invoice_id="51")
    row = await db.integration_inbox.find_one({"salla_order_id": order_id})
    client = _CountingClient()
    out = await process_customer_resolved_row(db, row, api_client=client)
    # New flow — invoice_payment fires; legacy receipt path does NOT.
    assert client.invoice_payment_calls == 1, (
        f"create_invoice_payment should fire exactly once; got "
        f"{client.invoice_payment_calls}. outcome={out}")
    assert client.receipt_calls == 0, (
        f"Iter-290h — /receipts path must be dormant in new flow; "
        f"got {client.receipt_calls} calls. outcome={out}")


@pytest.mark.asyncio
async def test_invoice_create_IS_called_when_no_stored_id(db):
    await ensure_qoyod_indexes(db); await _cleanup(db)
    user_id = "iter291"
    order_id = f"ITER291-FRESH-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    await _seed_customer_resolved_row(
        db, user_id=user_id, order_id=order_id,
        qoyod_invoice_id=None)
    row = await db.integration_inbox.find_one({"salla_order_id": order_id})
    client = _CountingClient()
    await process_customer_resolved_row(db, row, api_client=client)
    assert client.invoice_calls == 1


@pytest.mark.asyncio
async def test_reuse_writes_diagnostic_flag_on_row(db):
    await ensure_qoyod_indexes(db); await _cleanup(db)
    user_id = "iter291"
    order_id = f"ITER291-FLAG-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    await _seed_customer_resolved_row(
        db, user_id=user_id, order_id=order_id,
        qoyod_invoice_id="51")
    row = await db.integration_inbox.find_one({"salla_order_id": order_id})
    await process_customer_resolved_row(db, row, api_client=_CountingClient())
    updated = await db.integration_inbox.find_one({"id": row["id"]})
    qr = (updated.get("qoyod_responses") or {}).get("invoice") or {}
    assert qr.get("reused_from_previous_run") is True
    assert qr.get("reused_qoyod_id") == "51"
