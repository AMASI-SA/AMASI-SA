"""Iter-281 — Preview Reprocess (Safe Simulation, No Qoyod Calls).

User scenario (2026-02-27)
──────────────────────────
Operator wants to debug order `268632361` (DEAD_LETTER) but does NOT
want to flip Dry Run off (the existing `one_shot_reprocess` refuses
with `dry_run_mode_active` because it targets real Qoyod). Solution:
a SAFE preview endpoint that re-runs adapter → normalizer → builders
purely in memory and returns all diagnostics WITHOUT any network call.

These tests lock in the safety contract:
  • Never calls Qoyod (no api_client).
  • Never mutates the inbox row.
  • Returns `qoyod_request_sent=false` and `would_send_to_qoyod=*=false`.
  • Idempotency check surfaces existing real Qoyod invoices.
  • Returns structured `ok=false` (not raises) on every failure path.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from integrations.qoyod.preview_reprocess import (
    preview_reprocess_one_order, _drift, _shallow_preview,
)


pytestmark = pytest.mark.asyncio


# ─── Fakes ──────────────────────────────────────────────────────────
class _AsyncCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    async def to_list(self, *, length=None):
        if length is None:
            return list(self._rows)
        return self._rows[:length]


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_calls = []
        self.insert_calls = []
    def find(self, q):
        # Very small matcher — only `user_id`, `trace_id`,
        # `salla_order_number`, `salla_order_id`, `$or` and the
        # nested `canonical_payload.order_number` form supported.
        def match(r):
            for k, v in q.items():
                if k == "$or":
                    if not any(match_single(r, o) for o in v):
                        return False
                else:
                    if not match_single(r, {k: v}):
                        return False
            return True
        def match_single(r, sub):
            for k, v in sub.items():
                if "." in k:
                    parts = k.split(".")
                    cur = r
                    for p in parts:
                        if isinstance(cur, dict):
                            cur = cur.get(p)
                        else:
                            cur = None
                    if cur != v:
                        return False
                else:
                    if r.get(k) != v:
                        return False
            return True
        return _AsyncCursor([r for r in self.rows if match(r)])
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return r
        return None


class _FakeDB:
    def __init__(self, inbox=None, settings=None, invoices=None):
        self.integration_inbox = _FakeCollection(inbox or [])
        self.qoyod_settings    = _FakeCollection(settings or [])
        self.qoyod_invoices    = _FakeCollection(invoices or [])


# ─── Real Make body for order 268632361 ─────────────────────────────
RAW_MAKE_BODY = {
    "tax": 0,
    "items": [{
        "sku": "AMS11980", "name": "عباية ستيتش بناتي",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 199, "currency": "SAR"},
            "total_discount":    {"amount": 11.94, "currency": "SAR"},
            "tax": {"percent": "8.00",
                    "amount": {"amount": 14.96, "currency": "SAR"}},
            "total":             {"amount": 202.02, "currency": "SAR"},
        },
    }],
    "currency": "SAR",
    "order_id": "536444300",
    "subtotal": 199,
    "created_at": "2026-06-27T01:09:26+00:00",
    "event_type": "order_completed",
    "completed_at": "2026-06-27T20:10:45+00:00",
    "order_number": "268632361",
    "order_status": "تم التنفيذ",
    "total_amount": 228.02,
    "customer_name": "محمد العتيبي",
    "shipping_cost": 24.07,
    "payment_method": "tamara_installment",
    "customer_mobile": "505589357",
    "order_status_slug": "completed",
}

INBOX_ROW = {
    "trace_id":             "33c07a10a2994f6796a44fa386a33c00",
    "id":                   "33c07a10a2994f6796a44fa386a33c00",
    "user_id":              "main",
    "salla_order_number":   "268632361",
    "salla_order_id":       "536444300",
    "received_at":          datetime(2026, 6, 27, 20, 10, 45,
                                     tzinfo=timezone.utc).isoformat(),
    "pipeline_stage":       "DEAD_LETTER",
    "dry_run":              True,
    "raw_payload":          RAW_MAKE_BODY,
    "canonical_payload":    None,
}

SETTINGS = {
    "user_id":             "main",
    "default_tax_id":      "1",
    "tax_mode":            "mezan_fixed_15",
    # Iter-290e — this fixture pre-dates the match_salla_total policy;
    # keep it on legacy passthrough so the discount column assertions
    # below remain valid (the new policy is exercised in
    # test_qoyod_match_salla_total_iter290e.py).
    "invoice_total_policy": "legacy_passthrough",
    "default_branch_id":   "",
    "default_product_type": "service",
    "invoice_trigger_statuses": ["completed"],
    "dry_run_mode":        True,
    "trigger_once_only":   True,
    "payment_method_mapping": [
        {"salla_method": "tamara", "qoyod_account_id": "ACCT-tamara"},
    ],
}


# ─── Happy path ─────────────────────────────────────────────────────
async def test_preview_returns_ok_for_real_make_body():
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    assert out["ok"] is True
    assert out["mode"] == "preview"
    assert out["qoyod_request_sent"] is False
    assert out["would_send_to_qoyod"]["customer"] is False
    assert out["would_send_to_qoyod"]["products"] is False
    assert out["would_send_to_qoyod"]["invoice"]  is False
    assert out["would_send_to_qoyod"]["receipt"]  is False


async def test_preview_runs_normalizer_correctly():
    """The normalize stage must show the correct line item values."""
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    norm = out["stages"]["normalize"]
    assert norm["ok"] is True
    assert norm["canonical_preview"]["order_number"] == "268632361"
    assert norm["canonical_preview"]["order_status"] == "completed"
    assert norm["canonical_preview"]["items_count"]  == 1
    item = norm["items"][0]
    assert item["sku"]             == "AMS11980"
    assert item["unit_price"]      == 199.0
    assert item["tax_amount"]      == 14.96
    assert item["discount_amount"] == 11.94
    assert item["total"]           == 202.02


async def test_preview_builds_customer_payload():
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    cust = out["stages"]["customer_preview"]
    assert cust["ok"] is True
    assert cust["would_send_to_qoyod"] is False
    assert cust["endpoint"] == "POST /customers"
    body = cust["request_body"]["contact"]
    # Belt-and-suspenders fix: both `name` AND `contact_name` populated
    assert body["name"]         == "محمد العتيبي"
    assert body["contact_name"] == "محمد العتيبي"
    assert "505589357" in body["phone_number"]   # Salla normalises to E.164


async def test_preview_builds_product_payloads_per_sku():
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    prods = out["stages"]["products_preview"]
    assert prods["ok"] is True
    assert prods["would_send_to_qoyod"] is False
    assert len(prods["items"]) == 1
    body = prods["items"][0]["request_body"]["product"]
    assert body["sku"]           == "AMS11980"
    assert body["selling_price"] == 199.0
    # Iter-286 lock-in: sale_item MUST be 1 (supersedes Iter-272 `is_sold`)
    assert body["sale_item"] == 1


async def test_preview_builds_invoice_and_receipt_payloads():
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    inv = out["stages"]["invoice_preview"]
    rec = out["stages"]["receipt_preview"]
    assert inv["ok"] is True
    assert rec["ok"] is True
    assert inv["endpoint"] == "POST /invoices"
    assert rec["endpoint"] == "POST /receipts"
    inv_body = inv["request_body"]["invoice"]
    assert inv_body["reference"]    == "268632361"
    assert inv_body["currency_code"] == "SAR"
    assert len(inv_body["line_items"]) == 1
    line = inv_body["line_items"][0]
    assert line["unit_price"] == 199.0
    assert line["discount"]   == 11.94
    rec_body = rec["request_body"]["receipt"]
    assert rec_body["amount"]   == 228.02
    assert rec_body["currency"] == "SAR"
    # Payment account resolved via alias (tamara_installment → tamara)
    assert rec["resolved_account_id"] == "ACCT-tamara"


async def test_preview_runs_totals_guard():
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    tg = out["stages"]["totals_guard"]
    assert "ok" in tg
    # Whether it passes depends on the real guard math; the contract
    # is that the result is SURFACED for the operator to read.


async def test_preview_detects_drift_vs_stored_canonical():
    """Stored canonical has zeros (legacy bug); live re-norm produces
    the correct values. Drift must be surfaced."""
    row = dict(INBOX_ROW)
    row["canonical_payload"] = {
        "order_number": "268632361",
        "items": [{"sku": "AMS11980", "unit_price": 0,
                   "tax_amount": 0, "discount_amount": 0,
                   "total": 0, "quantity": 1}],
        "subtotal": 0, "tax_amount": 0,
        "total_amount": 0,
    }
    db = _FakeDB(inbox=[row], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=row["trace_id"])
    drift = out["stages"]["normalize"]["live_vs_stored_drift"]
    assert drift["any_drift"] is True
    assert drift["first_item_drift"]["unit_price"] == \
        {"stored": 0, "live": 199.0}


# ─── Idempotency — never silently double-bill ───────────────────────
async def test_preview_blocks_when_real_invoice_already_exists():
    db = _FakeDB(
        inbox=[INBOX_ROW], settings=[SETTINGS],
        invoices=[{
            "user_id": "main",
            "salla_order_id": "536444300",
            "qoyod_invoice_id": "QYD-12345",
            "qoyod_invoice_number": "INV-2026-001",
            "status": "sent",
            "dry_run": False,
        }],
    )
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    # Preview still RUNS — but idempotency flag surfaces the block.
    assert out["ok"] is True
    assert out["idempotency"]["blocked"] is True
    assert out["idempotency"]["code"] == "invoice_already_created"
    assert out["idempotency"]["existing_qoyod_invoice_id"] == "QYD-12345"


async def test_preview_does_not_block_when_existing_invoice_is_dry_run():
    db = _FakeDB(
        inbox=[INBOX_ROW], settings=[SETTINGS],
        invoices=[{
            "user_id": "main",
            "salla_order_id": "536444300",
            "qoyod_invoice_id": "DRY:invoice:abc12345",
            "status": "sent",
            "dry_run": True,
        }],
    )
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    assert out["idempotency"]["blocked"] is False


# ─── Error envelopes — every failure path is structured ─────────────
async def test_preview_returns_structured_error_when_row_not_found():
    db = _FakeDB(inbox=[], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id="nonexistent")
    assert out["ok"] is False
    assert out["error_code"] == "row_not_found"
    assert out["failed_at_stage"] == "lookup"
    assert "errors" in out


async def test_preview_returns_structured_error_when_no_lookup_supplied():
    db = _FakeDB(inbox=[], settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main")
    assert out["ok"] is False
    assert out["error_code"] == "missing_lookup"


async def test_preview_never_touches_inbox_row():
    """Read-only contract: row stays at DEAD_LETTER, no mutation."""
    db = _FakeDB(inbox=[INBOX_ROW], settings=[SETTINGS])
    before = dict(db.integration_inbox.rows[0])
    await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    after = db.integration_inbox.rows[0]
    assert before["pipeline_stage"] == after["pipeline_stage"] == "DEAD_LETTER"
    assert before["trace_id"]       == after["trace_id"]
    # No new keys added
    assert set(before.keys()) == set(after.keys())


async def test_preview_isolates_tenants():
    db = _FakeDB(
        inbox=[
            {**INBOX_ROW, "user_id": "other"},
        ],
        settings=[SETTINGS])
    out = await preview_reprocess_one_order(
        db, user_id="main", trace_id=INBOX_ROW["trace_id"])
    assert out["ok"] is False
    assert out["error_code"] == "row_not_found"


# ─── Helpers ────────────────────────────────────────────────────────
def test_shallow_preview_compacts_payload():
    p = {
        "event": "order_completed",
        "data": {
            "id": "536444300", "currency": "SAR",
            "status": {"slug": "completed"},
            "items": [{"sku": "A"}, {"sku": "B"}, {"sku": "C"},
                      {"sku": "D"}, {"sku": "E"}, {"sku": "F"}],
        }
    }
    out = _shallow_preview(p)
    assert out["event"]       == "order_completed"
    assert out["order_id"]    == "536444300"
    assert out["items_count"] == 6
    assert len(out["items_preview"]) == 5   # truncated


def test_drift_detects_first_item_changes():
    stored = {"items": [{"unit_price": 0, "quantity": 1}]}
    live   = {"items": [{"unit_price": 199, "quantity": 1}]}
    d = _drift(stored, live)
    assert d["any_drift"] is True
    assert d["first_item_drift"]["unit_price"] == {"stored": 0, "live": 199}
