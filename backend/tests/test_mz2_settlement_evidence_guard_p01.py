import pytest
from fastapi import HTTPException

import accounting_settlement_evidence_guard as guard


class Collection:
    def __init__(self, document):
        self.document = document

    async def find_one(self, *_args, **_kwargs):
        return self.document


class Db:
    def __init__(self, document):
        self.accounting_settlements_v2 = Collection(document)


@pytest.mark.asyncio
async def test_linked_draft_evidence_cannot_be_deleted(monkeypatch):
    db = Db({
        "id": "draft-1",
        "status": "needs_review",
        "ledger_txn_group_id": None,
    })
    called = False

    async def base_delete(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"removed": True}

    monkeypatch.setattr(guard, "_base_delete_file", base_delete)
    with pytest.raises(HTTPException) as error:
        await guard.delete_unlinked_settlement_file(
            db,
            "owner-1",
            "file-1",
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "accounting_settlement_evidence_locked"
    assert error.value.detail["settlement_draft_id"] == "draft-1"
    assert called is False


@pytest.mark.asyncio
async def test_posted_evidence_mentions_reversal_not_deletion(monkeypatch):
    db = Db({
        "id": "draft-1",
        "status": "posted",
        "ledger_txn_group_id": "group-1",
    })
    monkeypatch.setattr(
        guard,
        "_base_delete_file",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(HTTPException) as error:
        await guard.delete_unlinked_settlement_file(
            db,
            "owner-1",
            "file-1",
        )
    assert "بالعكس" in error.value.detail["message"]
    assert error.value.detail["ledger_txn_group_id"] == "group-1"


@pytest.mark.asyncio
async def test_unlinked_legacy_file_can_still_use_normal_delete(monkeypatch):
    db = Db(None)
    captured = {}

    async def base_delete(db_arg, user_id, file_id):
        captured.update({
            "db": db_arg,
            "user_id": user_id,
            "file_id": file_id,
        })
        return {"removed": True, "orders_rolled_back": 3}

    monkeypatch.setattr(guard, "_base_delete_file", base_delete)
    result = await guard.delete_unlinked_settlement_file(
        db,
        "owner-1",
        "file-1",
    )
    assert result == {"removed": True, "orders_rolled_back": 3}
    assert captured == {
        "db": db,
        "user_id": "owner-1",
        "file_id": "file-1",
    }
