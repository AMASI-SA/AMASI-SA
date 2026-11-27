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
