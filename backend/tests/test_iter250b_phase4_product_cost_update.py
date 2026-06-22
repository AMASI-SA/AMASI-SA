"""Iter-250b · Phase 4 — Tests for product cost auto-update on
supplier-invoice save.

Validates _apply_product_cost_updates():
  • Appends a new cost_history entry per line item with product_id.
  • cost_current = latest unit_cost.
  • cost_avg = quantity-weighted average across supplier-invoice entries.
  • needs_cost flips to False.
  • Lines without product_id are skipped.
  • Existing cost_history entries are preserved (append-only).
  • Multiple invoices for the same product create separate records.
"""
import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from financial_movements_routes import (  # noqa: E402
    _apply_product_cost_updates,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_phase4_creates_history_entry(db):
    uid = f"test_user_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    # Seed product without a cost.
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "Test Product", "cost_history": [],
        "cost_current": None, "cost_avg": None, "needs_cost": True,
    })

    mv = {
        "id": "MV1", "user_id": uid, "movement_type": "supplier_invoice",
        "supplier_id": "SUP1", "doc_date": "2026-02-01",
        "line_items": [{
            "product_id": pid, "quantity": 10, "unit_price": 5.0,
            "description": "x",
        }],
    }
    res = await _apply_product_cost_updates(db, uid, mv)
    assert res["updated"] == 1, res

    p = await db.products.find_one({"id": doc_id, "user_id": uid})
    assert len(p["cost_history"]) == 1
    h = p["cost_history"][0]
    assert h["supplier_id"] == "SUP1"
    assert h["supplier_invoice_id"] == "MV1"
    assert h["invoice_date"] == "2026-02-01"
    assert h["quantity"] == 10
    assert h["unit_cost"] == 5.0
    assert h["total_cost"] == 50.0
    assert h["source"] == "supplier-invoice"
    assert p["cost_current"] == 5.0
    assert p["cost_avg"] == 5.0
    assert p["needs_cost"] is False

    # Cleanup.
    await db.products.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase4_weighted_average_across_invoices(db):
    uid = f"test_user_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "P", "cost_history": [], "cost_current": None,
        "cost_avg": None, "needs_cost": True,
    })

    # Invoice 1: 10 units @ 5.00 = 50.00
    await _apply_product_cost_updates(db, uid, {
        "id": "MV1", "user_id": uid, "movement_type": "supplier_invoice",
        "supplier_id": "S1", "doc_date": "2026-01-01",
        "line_items": [{"product_id": pid, "quantity": 10,
                          "unit_price": 5.0, "description": "x"}],
    })

    # Invoice 2: 30 units @ 7.00 = 210.00
    await _apply_product_cost_updates(db, uid, {
        "id": "MV2", "user_id": uid, "movement_type": "supplier_invoice",
        "supplier_id": "S2", "doc_date": "2026-02-01",
        "line_items": [{"product_id": pid, "quantity": 30,
                          "unit_price": 7.0, "description": "x"}],
    })

    p = await db.products.find_one({"id": doc_id, "user_id": uid})
    assert len(p["cost_history"]) == 2, p["cost_history"]
    # Weighted avg = (10*5 + 30*7) / (10+30) = 260/40 = 6.50
    assert p["cost_avg"] == 6.5
    # cost_current = latest = 7.00
    assert p["cost_current"] == 7.0
    assert p["needs_cost"] is False

    await db.products.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase4_skips_lines_without_product_id(db):
    uid = f"test_user_{uuid.uuid4().hex[:6]}"
    mv = {
        "id": "MV1", "user_id": uid, "movement_type": "supplier_invoice",
        "supplier_id": "S1", "doc_date": "2026-02-01",
        "line_items": [
            {"product_id": None, "quantity": 1, "unit_price": 1.0,
             "description": "no-pid"},
            {"product_id": "", "quantity": 1, "unit_price": 1.0,
             "description": "empty"},
        ],
    }
    res = await _apply_product_cost_updates(db, uid, mv)
    assert res["updated"] == 0
    assert res["skipped"] == 2


@pytest.mark.asyncio
async def test_phase4_no_op_on_non_supplier_invoice(db):
    uid = f"test_user_{uuid.uuid4().hex[:6]}"
    mv = {
        "id": "MV1", "user_id": uid, "movement_type": "general_expense",
        "line_items": [{"product_id": "X", "quantity": 1,
                          "unit_price": 1.0, "description": "x"}],
    }
    res = await _apply_product_cost_updates(db, uid, mv)
    assert res == {"updated": 0, "skipped": 0, "errors": []}


@pytest.mark.asyncio
async def test_phase4_preserves_legacy_excel_history(db):
    """Legacy entries from excel-import (no quantity) must be kept
    intact and ignored from weighted-average calc."""
    uid = f"test_user_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "P", "cost_history": [
            {"amount": 9.99, "source": "excel-import",
             "at": "2026-01-01T00:00:00+00:00"},
        ],
        "cost_current": 9.99, "cost_avg": 9.99, "needs_cost": False,
    })

    await _apply_product_cost_updates(db, uid, {
        "id": "MV1", "user_id": uid, "movement_type": "supplier_invoice",
        "supplier_id": "S1", "doc_date": "2026-02-01",
        "line_items": [{"product_id": pid, "quantity": 5,
                          "unit_price": 12.0, "description": "x"}],
    })

    p = await db.products.find_one({"id": doc_id, "user_id": uid})
    # Legacy excel-import row still present.
    assert len(p["cost_history"]) == 2
    sources = [h.get("source") for h in p["cost_history"]]
    assert "excel-import" in sources and "supplier-invoice" in sources
    # cost_avg = weighted avg over qty entries only → 12.0 (only the
    # supplier-invoice carries qty).
    assert p["cost_avg"] == 12.0
    assert p["cost_current"] == 12.0

    await db.products.delete_many({"user_id": uid})
