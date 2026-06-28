"""Tests for the First-Sync Monitor shaper + Branch ID optional behaviour."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.qoyod.first_sync_monitor import (
    shape_inbox_row_for_monitor, _status_for_stage,
)
from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.setup_validation import validate_settings_for_setup


# ─── Status calculation ────────────────────────────────────────────
def test_status_success_when_pipeline_passed_stage():
    row = {"last_success_stage": "INVOICE_CREATED",
           "pipeline_stage": "INVOICE_CREATED"}
    assert _status_for_stage("CUSTOMER_RESOLVED", row, "FAILED_CUSTOMER") == "success"
    assert _status_for_stage("INVOICE_CREATED",   row, "FAILED_INVOICE")  == "success"


def test_status_failed_when_pipeline_in_fail_stage():
    row = {"last_failed_stage": "FAILED_INVOICE",
           "pipeline_stage":    "FAILED_INVOICE",
           "last_success_stage": "PRODUCT_RESOLVED"}
    assert _status_for_stage("INVOICE_CREATED", row, "FAILED_INVOICE") == "failed"


def test_status_pending_when_not_yet_reached():
    row = {"last_success_stage": "CUSTOMER_RESOLVED",
           "pipeline_stage":     "CUSTOMER_RESOLVED"}
    assert _status_for_stage("INVOICE_CREATED", row, "FAILED_INVOICE") == "pending"


def test_status_skipped_when_pipeline_skipped():
    row = {"pipeline_stage": "SKIPPED"}
    assert _status_for_stage("INVOICE_CREATED", row, "FAILED_INVOICE") == "skipped"


# ─── Shaper ────────────────────────────────────────────────────────
def test_shape_inbox_row_extracts_four_qoyod_steps():
    now = datetime.now(timezone.utc)
    row = {
        "id":       "abc",
        "trace_id": "trace-1",
        "user_id":  "main",
        "received_at": now,
        "pipeline_stage": "COMPLETED",
        "pipeline_outcome": "COMPLETED",
        "pipeline_started_at": now,
        "pipeline_finished_at": now,
        "pipeline_duration_ms": 1234,
        "last_success_stage": "COMPLETED",
        "qoyod_customer_id": "C-9",
        "qoyod_invoice_payment_id": "P-1",        # Iter-290h
        "customer_resolution": {
            "created_new": True,
            "lookup_keys": ["phone:+966500000000"],
        },
        "product_resolution": {
            "items": [
                {"sku": "ABC", "qoyod_product_id": "P-1", "created_new": True},
            ],
        },
        "qoyod_payloads": {
            "invoice": {"invoice": {"contact_id": "C-9"}},
            "invoice_payment": {"invoice_payment": {"invoice_id": "I-1",
                                                    "amount": 100}},
        },
        "qoyod_responses": {
            "invoice": {"qoyod_id": "I-1", "duration_ms": 250,
                        "body": {"invoice": {"id": "I-1", "number": "INV-001"}}},
            "invoice_payment": {"qoyod_id": "P-1", "duration_ms": 180,
                                "body": {"invoice_payment": {"id": "P-1"}}},
        },
        "stage_history": [
            {"from_stage": "NEW", "to_stage": "RECEIVED", "at": now,
             "actor": "system"},
            {"from_stage": "INVOICE_CREATED",
             "to_stage": "INVOICE_PAYMENT_CREATED", "at": now,
             "actor": "worker"},
        ],
        "canonical_payload": {
            "order_id": "12345", "order_number": "S-12345",
            "total_amount": 100, "currency": "SAR",
            "items": [{"sku": "ABC"}],
            "customer": {"name": "Test User"},
        },
        "raw_payload": {"source": "make", "id": "12345"},
    }
    out = shape_inbox_row_for_monitor(row)
    assert out["trace_id"] == "trace-1"
    assert len(out["qoyod_steps"]) == 4
    keys = [s["key"] for s in out["qoyod_steps"]]
    assert keys == ["customer", "product", "invoice", "invoice_payment"]
    # All steps should report "success"
    assert all(s["status"] == "success" for s in out["qoyod_steps"])
    # Order summary populated
    assert out["order_summary"]["order_id"] == "12345"
    assert out["order_summary"]["customer_name"] == "Test User"
    # Raw + canonical present
    assert out["make_raw_payload"]["source"] == "make"
    assert out["canonical_dto"]["order_id"] == "12345"


def test_shape_inbox_row_marks_failed_step():
    row = {
        "id": "x", "trace_id": "t-2",
        "pipeline_stage": "DEAD_LETTER",
        "last_success_stage": "PRODUCT_RESOLVED",
        "last_failed_stage": "FAILED_INVOICE",
        "qoyod_payloads": {
            "invoice": {"invoice": {"contact_id": "C"}},
        },
        "qoyod_responses": {
            "invoice": {"error": {"code": "qoyod_unauthorized"},
                        "duration_ms": 50},
        },
        "stage_history": [],
        "canonical_payload": {},
    }
    out = shape_inbox_row_for_monitor(row)
    invoice_step = next(s for s in out["qoyod_steps"] if s["key"] == "invoice")
    assert invoice_step["status"] == "failed"
    # Earlier successful steps still report success
    cust_step = next(s for s in out["qoyod_steps"] if s["key"] == "customer")
    assert cust_step["status"] == "success"
    # Later steps stay pending
    rcpt_step = next(s for s in out["qoyod_steps"] if s["key"] == "invoice_payment")
    assert rcpt_step["status"] == "pending"


# ─── Branch ID optional in invoice builder ─────────────────────────
def test_invoice_payload_omits_branch_id_when_settings_blank():
    settings = {"default_tax_id": "1", "tax_mode": "mezan_fixed_15"}  # no branch_id at all
    body = build_invoice_payload(
        dto_dict={"order_id": "9", "items": [], "currency": "SAR"},
        qoyod_customer_id="C-1",
        product_resolutions=[],
        invoice_date=None,
        settings=settings,
    )
    assert "branch_id" not in body["invoice"]


def test_invoice_payload_includes_branch_id_when_set():
    body = build_invoice_payload(
        dto_dict={"order_id": "9", "items": [], "currency": "SAR"},
        qoyod_customer_id="1",
        product_resolutions=[],
        invoice_date=None,
        settings={"default_branch_id": "5", "default_tax_id": "1",
                   "tax_mode": "mezan_fixed_15"},
    )
    # Iter-290c — ids on the invoice payload are integers.
    assert body["invoice"]["branch_id"] == 5


def test_invoice_line_uses_tax_id_not_rate():
    """Iter-290c — Qoyod docs use `tax_percent` per line (not tax_id).
    This test was originally a regression guard against tax_rate leaks;
    now it pins the new contract: tax_percent on every line, no tax_id."""
    body = build_invoice_payload(
        dto_dict={
            "order_id": "9", "currency": "SAR",
            "items": [{"sku": "S1", "name": "Item",
                       "quantity": 1, "unit_price": 100}],
        },
        qoyod_customer_id="1",
        product_resolutions=[{"sku": "S1", "qoyod_product_id": "1"}],
        invoice_date=None,
        settings={"default_tax_id": "1", "tax_mode": "mezan_fixed_15"},
    )
    line = body["invoice"]["line_items"][0]
    assert line["tax_percent"] == 15
    assert "tax_id" not in line
    assert "tax_rate" not in line
    assert "rate" not in line


def test_invoice_line_omits_tax_id_when_settings_blank():
    body = build_invoice_payload(
        dto_dict={
            "order_id": "9", "currency": "SAR",
            "items": [{"sku": "S1", "name": "Item",
                       "quantity": 1, "unit_price": 100}],
        },
        qoyod_customer_id="C-1",
        product_resolutions=[{"sku": "S1", "qoyod_product_id": "P-1"}],
        invoice_date=None,
        settings={},
    )
    assert "tax_id" not in body["invoice"]["line_items"][0]


# ─── Setup validation: branch_id is WARNING not BLOCKER ───────────
class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def __aiter__(self):
        async def gen():
            for r in self._rows: yield r
        return gen()


class _FakeColl:
    def __init__(self, rows=None): self.rows = rows or []
    def find(self, *_a, **_kw): return _FakeCursor(self.rows)
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return r
        return None


class _FakeDB:
    def __init__(self):
        self.qoyod_settings    = _FakeColl()
        self.unified_orders    = _FakeColl()
        self.integration_inbox = _FakeColl()


@pytest.mark.asyncio
async def test_validate_treats_missing_branch_as_warning_not_blocker():
    db = _FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_tax_id": "1",
        "tax_mode": "mezan_fixed_15",
        "default_product_type": "service",
        "payment_method_mapping": [],
    }]
    res = await validate_settings_for_setup(db, user_id="main")
    branch_issue = next(
        (i for i in res["issues"] if i["code"] == "missing_branch_id"), None)
    assert branch_issue is not None
    assert branch_issue["severity"] == "warning"
    # And the setup can still be saved.
    assert res["ok"] is True
