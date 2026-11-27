"""Read-only stage badge semantics and identity scope."""
from unittest.mock import AsyncMock

import pytest

import preparation_stage_card_counts as cards


@pytest.mark.asyncio
async def test_card_counts_are_unique_and_share_employee_scope(monkeypatch):
    calls = []
    async def progress(db, *, user_id, employee_id):
        calls.append(("progress", user_id, employee_id))
        return {"remaining": 20}
    async def waiting(db, owner, employee):
        calls.append(("waiting", owner, employee))
        return {"w1", "w1", "w2"}
    async def received(db, owner, employee):
        calls.append(("received", owner, employee))
        return {"r1", "r2", "r3"}
    monkeypatch.setattr(cards, "progress_count", progress)
    monkeypatch.setattr(cards, "_waiting_ids", waiting)
    monkeypatch.setattr(cards, "_received_ids", received)
    result = await cards.progress_stage_card_counts(None, user_id="owner", employee_id="khaled")
    assert result == {"ok": True, "read_only": True, "employee_id": "khaled",
                      "remaining": 20, "waiting_for_supplier": 2,
                      "received_from_supplier": 3}
    assert calls == [("progress", "owner", "khaled"), ("waiting", "owner", "khaled"),
                     ("received", "owner", "khaled")]


@pytest.mark.asyncio
async def test_waiting_matches_exact_eligibility_and_no_product_hydration(monkeypatch):
    class Cursor:
        def __init__(self):
            self.done = False
        def batch_size(self, n):
            assert n == cards.CHUNK
            return self
        async def to_list(self, size):
            assert size == cards.CHUNK
            if self.done:
                return []
            self.done = True
            return [{"piece_id": "p1", "order_number": "o1"},
                    {"piece_id": "p2", "order_number": "o2"}]
        async def close(self):
            pass
    class Pieces:
        def find(self, query, projection):
            assert query == {"user_id": "owner", "responsible_employee_id": "e"}
            assert "image_b64" not in projection and "product_name" not in projection
            return Cursor()
    class DB:
        def __getitem__(self, name):
            assert name == cards.PIECES
            return Pieces()
    monkeypatch.setattr(cards, "_waiting_query",
                        lambda owner, employee: {"user_id": owner, "responsible_employee_id": employee})
    monkeypatch.setattr(cards, "piece_is_available_for_supplier_dispatch", lambda row: True)
    async def annotate(db, *, user_id, pieces):
        assert user_id == "owner"
        return [{**row, "waiting_review_eligible": row["piece_id"] == "p1"} for row in pieces]
    monkeypatch.setattr(cards, "annotate_waiting_pieces", annotate)
    assert await cards._waiting_ids(DB(), "owner", "e") == {"p1"}


@pytest.mark.asyncio
async def test_received_excludes_ready_without_receipt_and_is_scoped(monkeypatch):
    class Cursor:
        def batch_size(self, size):
            assert size == cards.CHUNK
            return self
        def __init__(self):
            self.called = False
        async def to_list(self, size):
            if self.called:
                return []
            self.called = True
            return [{"piece_id": "received", "status": "received", "branch_handoff_at": ""},
                    {"piece_id": "ready", "status": "ready_for_receipt"},
                    {"piece_id": "received", "status": "received"}]
        async def close(self):
            pass
    class Pieces:
        def find(self, query, projection):
            assert query["user_id"] == "owner"
            assert query["responsible_employee_id"] == "e"
            assert query["experiment_archived_at"] == {"$in": [None, ""]}
            assert "image_url" not in projection and "image_b64" not in projection
            return Cursor()
    class DB:
        def __getitem__(self, name):
            assert name == cards.PIECES
            return Pieces()
    monkeypatch.setattr(cards, "_supplier_receipt_awaiting_handoff",
                        lambda row: row["piece_id"] == "received")
    assert await cards._received_ids(DB(), "owner", "e") == {"received"}


@pytest.mark.asyncio
async def test_actual_mongo_received_badge_equals_piece_grain_received_details():
    """One tenant/employee returns exactly the same piece IDs as the native detail."""
    import os
    from uuid import uuid4
    from motor.motor_asyncio import AsyncIOMotorClient
    from preparation_supplier_dispatch import _piece_products

    uri = os.environ.get("TEST_PROGRESS_MONGO_URI")
    if not uri:
        pytest.skip("No isolated test Mongo configured")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    db = client["stage_card_parity_" + uuid4().hex]
    base = {"user_id": "merchant-1", "responsible_employee_id": "khaled",
            "experiment_archived_at": None, "status": "processing",
            "supplier_dispatch_status": "sent", "branch_handoff_at": None,
            "order_number": "synthetic-order", "unit_index": 1}
    variants = [
        {"piece_id": "r1", "status": "received"},
        {"piece_id": "r2", "supplier_dispatch_status": "partial_received",
         "experiment_archived_at": ""},
        {"piece_id": "sent"},
        {"piece_id": "ready", "supplier_dispatch_status": "ready"},
        {"piece_id": "handed", "status": "received",
         "branch_handoff_at": "2026-11-27T10:00:00+03:00"},
        {"piece_id": "cancelled", "status": "cancelled",
         "supplier_dispatch_status": "received"},
        {"piece_id": "archived", "status": "received",
         "experiment_archived_at": "2026-11-26T10:00:00+03:00"},
        {"piece_id": "foreign-employee", "responsible_employee_id": "other",
         "status": "received"},
        {"piece_id": "foreign-tenant", "user_id": "other", "status": "received"},
    ]
    try:
        await db[cards.PIECES].insert_many([{**base, **v} for v in variants])
        counted = await cards._received_ids(db, "merchant-1", "khaled")
        assert counted == {"r1", "r2"}
        details = await db[cards.PIECES].find({
            "user_id": "merchant-1", "responsible_employee_id": "khaled",
            "experiment_archived_at": {"$in": [None, ""]},
            "status": {"$ne": "cancelled"},
        }, {"_id": 0}).to_list(20)
        actual = {row["piece_id"] for row in _piece_products(details)
                  if row["supplier_received_quantity"] > 0}
        assert actual == counted
    finally:
        await client.drop_database(db.name)
        client.close()
