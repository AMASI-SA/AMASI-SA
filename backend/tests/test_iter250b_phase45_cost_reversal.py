"""Iter-250b · Phase 4.5 — Tests for reversal-aware product cost engine.

Validates:
  • `recalculate_product_cost` ignores reversed entries.
  • `mark_supplier_invoice_cost_reversed` flips status & recomputes.
  • When all entries are reversed → cost_current=0, cost_avg=0,
    needs_cost=True.
  • cost_history is never deleted (append-only).
  • Multiple invoices: only the reversed one is flipped.
"""
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
    recalculate_product_cost,
    mark_supplier_invoice_cost_reversed,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_phase45_reversal_marks_history_and_recomputes(db):
    """Two invoices for same product. Reversing the 2nd one should:
    - mark its cost_history row as reversed (not delete)
    - revert cost_current to 1st invoice's unit_cost
    - revert cost_avg to 1st invoice's qty-weighted avg
    """
    uid = f"t45_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "Test", "cost_history": [],
        "cost_current": None, "cost_avg": None, "needs_cost": True,
    })
    # Two invoices
    mv1 = {"id": "MV-A", "user_id": uid,
           "movement_type": "supplier_invoice",
           "supplier_id": "S1", "doc_date": "2026-01-01",
           "line_items": [{"product_id": pid, "quantity": 10,
                            "unit_price": 5.0, "description": "x"}]}
    mv2 = {"id": "MV-B", "user_id": uid,
           "movement_type": "supplier_invoice",
           "supplier_id": "S2", "doc_date": "2026-02-01",
           "line_items": [{"product_id": pid, "quantity": 30,
                            "unit_price": 7.0, "description": "x"}]}
    await _apply_product_cost_updates(db, uid, mv1)
    await _apply_product_cost_updates(db, uid, mv2)

    # Need to link MV-B to a ledger group so the reversal can locate it.
    await db.financial_movements.insert_one({
        "id": "MV-B", "user_id": uid,
        "movement_type": "supplier_invoice",
        "ledger_txn_group_id": "GRP-X",
        "line_items": mv2["line_items"],
    })

    # Before reversal: cost_avg = (10*5 + 30*7) / 40 = 6.5
    p = await db.products.find_one({"id": doc_id})
    assert p["cost_avg"] == 6.5
    assert p["cost_current"] == 7.0
    assert len(p["cost_history"]) == 2

    # Reverse MV-B
    res = await mark_supplier_invoice_cost_reversed(db, uid, "GRP-X")
    assert res["reversed_lines"] == 1
    assert res["products_recomputed"] == 1

    p2 = await db.products.find_one({"id": doc_id})
    # cost_history still has 2 entries (append-only)
    assert len(p2["cost_history"]) == 2
    statuses = [h.get("status") for h in p2["cost_history"]]
    assert statuses.count("reversed") == 1
    assert statuses.count("active") == 1
    # cost_current reverted to 5.0, cost_avg reverted to 5.0
    assert p2["cost_current"] == 5.0
    assert p2["cost_avg"] == 5.0
    assert p2["needs_cost"] is False

    # Reversed entry has metadata
    rev_entry = next(h for h in p2["cost_history"]
                     if h.get("status") == "reversed")
    assert rev_entry["reversal_txn_group_id"] == "GRP-X"
    assert rev_entry.get("reversed_at")
    # Original data preserved (audit trail intact)
    assert rev_entry["quantity"] == 30
    assert rev_entry["unit_cost"] == 7.0

    # Cleanup
    await db.products.delete_many({"user_id": uid})
    await db.financial_movements.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase45_all_reversed_sets_needs_cost(db):
    """When EVERY cost_history entry is reversed, product should be
    flagged as needs_cost=True with current=avg=0."""
    uid = f"t45_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "Test", "cost_history": [],
        "cost_current": None, "cost_avg": None, "needs_cost": True,
    })

    mv = {"id": "MV-ONLY", "user_id": uid,
          "movement_type": "supplier_invoice",
          "supplier_id": "S1", "doc_date": "2026-02-01",
          "line_items": [{"product_id": pid, "quantity": 5,
                           "unit_price": 12.0, "description": "x"}]}
    await _apply_product_cost_updates(db, uid, mv)
    await db.financial_movements.insert_one({
        "id": "MV-ONLY", "user_id": uid,
        "movement_type": "supplier_invoice",
        "ledger_txn_group_id": "GRP-ONLY",
        "line_items": mv["line_items"],
    })

    res = await mark_supplier_invoice_cost_reversed(db, uid, "GRP-ONLY")
    assert res["reversed_lines"] == 1

    p = await db.products.find_one({"id": doc_id})
    assert p["cost_current"] == 0.0
    assert p["cost_avg"] == 0.0
    assert p["needs_cost"] is True
    # Audit trail preserved
    assert len(p["cost_history"]) == 1
    assert p["cost_history"][0]["status"] == "reversed"

    await db.products.delete_many({"user_id": uid})
    await db.financial_movements.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase45_idempotent_reversal(db):
    """Calling reversal twice should not double-reverse or alter
    already-reversed entries."""
    uid = f"t45_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "Test", "cost_history": [],
        "cost_current": None, "cost_avg": None, "needs_cost": True,
    })
    mv = {"id": "MV-X", "user_id": uid,
          "movement_type": "supplier_invoice",
          "supplier_id": "S1", "doc_date": "2026-02-01",
          "line_items": [{"product_id": pid, "quantity": 5,
                           "unit_price": 12.0, "description": "x"}]}
    await _apply_product_cost_updates(db, uid, mv)
    await db.financial_movements.insert_one({
        "id": "MV-X", "user_id": uid,
        "movement_type": "supplier_invoice",
        "ledger_txn_group_id": "GRP-IDEM",
        "line_items": mv["line_items"],
    })

    r1 = await mark_supplier_invoice_cost_reversed(db, uid, "GRP-IDEM")
    r2 = await mark_supplier_invoice_cost_reversed(db, uid, "GRP-IDEM")
    assert r1["reversed_lines"] == 1
    assert r2["reversed_lines"] == 0  # nothing more to flip

    await db.products.delete_many({"user_id": uid})
    await db.financial_movements.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_phase45_recalc_legacy_entries_treated_as_active(db):
    """Entries without `status` field (legacy pre-4.5 imports) must
    be treated as active by the recalc helper."""
    uid = f"t45_{uuid.uuid4().hex[:6]}"
    pid = f"P{uuid.uuid4().hex[:6]}"
    doc_id = str(uuid.uuid4())
    await db.products.insert_one({
        "id": doc_id, "user_id": uid, "product_id": pid,
        "name": "Legacy", "cost_history": [
            # Pre-4.5: no `status` field, has qty
            {"amount": 9.0, "source": "excel-import",
             "at": "2026-01-01T00:00:00+00:00",
             "quantity": 10, "unit_cost": 9.0},
        ],
        "cost_current": None, "cost_avg": None, "needs_cost": True,
    })
    r = await recalculate_product_cost(db, uid, pid)
    assert r["ok"] is True
    assert r["after"]["cost_current"] == 9.0
    assert r["after"]["cost_avg"] == 9.0
    assert r["after"]["needs_cost"] is False
    await db.products.delete_many({"user_id": uid})
