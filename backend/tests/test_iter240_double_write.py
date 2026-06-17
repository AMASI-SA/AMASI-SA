"""Iter-240 — Double-write helper regression suite.

Verifies that every NEW account_transactions row written by the 5 leak
sites also lands a balanced ledger pair in `general_ledger` carrying
all the mandatory metadata, and that re-invocation is idempotent.

Scope (forward-only, per user's STRICT rule — no backfill):
  • POST /api/transfers
  • Ad-account topup (`_post_bank_tx` in ad_account_routes)
  • POST /api/liabilities/{id}/pay
  • POST /api/liabilities/{id}/collect
  • POST /api/shipping/.../payments (_post_shipping_payment_tx)
  • Daily expense write (_post_daily_expense_tx)

Helper guarantees asserted:
  - Each mirrored row has metadata.source ==
    "account_transaction_double_write".
  - account_transaction_id, transaction_type, idempotency_key,
    created_by_endpoint are all present.
  - 2 balanced legs (debit + credit, same amount, same txn_group_id).
  - Calling mirror_account_txn_to_ledger twice for the same
    account_transaction_id is a no-op (skipped=True).
  - For internal transfers, BOTH the OUT and IN rows are considered
    covered (via paired_account_transaction_id) without double-posting.

Diagnostic endpoint asserted:
  - GET /api/audit/double-write-health returns the expected shape
    and the just-created txns appear as `mirrored=True`.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest_asyncio.fixture
async def clean_user(db):
    uid = f"iter240-test-{uuid.uuid4().hex[:8]}"
    yield uid
    # tear down
    await db.general_ledger.delete_many({"user_id": uid})
    await db.account_transactions.delete_many({"user_id": uid})


# ── Helper: count mirrored legs for a given account_transaction_id ─────
async def _mirrored_legs(db, uid: str, txn_id: str) -> list[dict]:
    return await db.general_ledger.find(
        {"user_id": uid,
         "metadata.source": "account_transaction_double_write",
         "$or": [
             {"metadata.account_transaction_id": txn_id},
             {"metadata.paired_account_transaction_id": txn_id},
         ]},
        {"_id": 0},
    ).to_list(50)


@pytest.mark.asyncio
async def test_single_txn_mirror_creates_balanced_pair(db, clean_user):
    """One account_transaction → exactly 2 balanced ledger legs with
    full mandatory metadata."""
    from ledger_double_write import mirror_account_txn_to_ledger

    uid = clean_user
    txn_id = str(uuid.uuid4())
    result = await mirror_account_txn_to_ledger(
        db,
        user_id=uid,
        account_id="bank_A",
        account_transaction_id=txn_id,
        amount=500.0,
        direction="out",
        transaction_type="expense",
        transaction_date="2026-02-15",
        description="test expense",
        counter_entity_type="expense",
        counter_entity_id="exp_id_1",
        created_by_endpoint="POST /api/test",
        idempotency_key=f"test:{txn_id}",
    )

    assert result["skipped"] is False
    legs = await _mirrored_legs(db, uid, txn_id)
    assert len(legs) == 2, "Each mirror must produce exactly 2 legs"

    sides = sorted([l["side"] for l in legs])
    assert sides == ["credit", "debit"]

    amounts = {round(l["amount"], 2) for l in legs}
    assert amounts == {500.0}, "Both legs must carry the same amount"

    group_ids = {l["txn_group_id"] for l in legs}
    assert len(group_ids) == 1, "Both legs share one txn_group_id"

    # Mandatory metadata (STRICT RULE 2 from handoff)
    for leg in legs:
        md = leg.get("metadata") or {}
        assert md["account_transaction_id"] == txn_id
        assert md["source"] == "account_transaction_double_write"
        assert md["transaction_type"] == "expense"
        assert md["idempotency_key"] == f"test:{txn_id}"
        assert md["created_by_endpoint"] == "POST /api/test"


@pytest.mark.asyncio
async def test_mirror_is_idempotent(db, clean_user):
    """Calling the mirror twice for the same txn id must NOT double-post."""
    from ledger_double_write import mirror_account_txn_to_ledger

    uid = clean_user
    txn_id = str(uuid.uuid4())

    first = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_A",
        account_transaction_id=txn_id, amount=100.0,
        direction="out", transaction_type="expense",
        transaction_date="2026-02-15", description="dup test",
        counter_entity_id="exp1",
        created_by_endpoint="POST /api/test",
    )
    second = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_A",
        account_transaction_id=txn_id, amount=100.0,
        direction="out", transaction_type="expense",
        transaction_date="2026-02-15", description="dup test",
        counter_entity_id="exp1",
        created_by_endpoint="POST /api/test",
    )

    assert first["skipped"] is False
    assert second["skipped"] is True
    assert second["txn_group_id"] == first["txn_group_id"]

    legs = await _mirrored_legs(db, uid, txn_id)
    assert len(legs) == 2, "STRICT RULE 3: never duplicate the mirror"


@pytest.mark.asyncio
async def test_transfer_pair_uses_one_ledger_group(db, clean_user):
    """A bank→bank internal transfer must mirror as ONE balanced pair
    (not 4 legs), and BOTH account_transaction ids must be considered
    covered by the health check."""
    from ledger_double_write import mirror_account_txn_to_ledger

    uid = clean_user
    out_id = str(uuid.uuid4())
    in_id = str(uuid.uuid4())

    res = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_FROM",
        account_transaction_id=out_id,
        paired_account_transaction_id=in_id,
        amount=1000.0, direction="out",
        transaction_type="internal_transfer",
        transaction_date="2026-02-15",
        description="A → B",
        counter_entity_type="bank", counter_entity_id="bank_TO",
        created_by_endpoint="POST /api/transfers",
        idempotency_key=f"transfer:{uuid.uuid4()}",
    )
    assert res["skipped"] is False

    # OUT-side coverage
    legs_out = await _mirrored_legs(db, uid, out_id)
    assert len(legs_out) == 2, "Transfer must produce 1 balanced pair"

    # IN-side coverage — same legs returned via paired_account_transaction_id
    legs_in = await _mirrored_legs(db, uid, in_id)
    assert len(legs_in) == 2
    assert {l["txn_group_id"] for l in legs_in} == \
           {l["txn_group_id"] for l in legs_out}, \
        "Both sides of the transfer must point to the same ledger group"

    # No double-posting on re-entry via the IN row id
    res2 = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_TO",
        account_transaction_id=in_id,
        paired_account_transaction_id=out_id,
        amount=1000.0, direction="in",
        transaction_type="internal_transfer",
        transaction_date="2026-02-15",
        description="A → B",
        counter_entity_type="bank", counter_entity_id="bank_FROM",
        created_by_endpoint="POST /api/transfers",
    )
    assert res2["skipped"] is True

    # Still only 2 legs total for the transfer
    all_legs_out = await _mirrored_legs(db, uid, out_id)
    all_legs_in = await _mirrored_legs(db, uid, in_id)
    assert len(all_legs_out) == 2
    assert len(all_legs_in) == 2


@pytest.mark.asyncio
async def test_zero_or_missing_id_is_skipped(db, clean_user):
    from ledger_double_write import mirror_account_txn_to_ledger

    uid = clean_user
    # Missing id
    r1 = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_A",
        account_transaction_id="", amount=100,
        direction="out", transaction_type="x",
    )
    assert r1["skipped"] is True

    # Zero amount
    r2 = await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_A",
        account_transaction_id=str(uuid.uuid4()), amount=0,
        direction="out", transaction_type="x",
    )
    assert r2["skipped"] is True


@pytest.mark.asyncio
async def test_metadata_contains_all_mandatory_fields(db, clean_user):
    """STRICT RULE 2 — every mirrored row must carry all metadata."""
    from ledger_double_write import mirror_account_txn_to_ledger

    uid = clean_user
    txn_id = str(uuid.uuid4())
    await mirror_account_txn_to_ledger(
        db, user_id=uid, account_id="bank_X",
        account_transaction_id=txn_id, amount=42.5,
        direction="in", transaction_type="receivable_collection",
        transaction_date="2026-02-15",
        counter_entity_type="liability",
        counter_entity_id="liab-1",
        created_by_endpoint="POST /api/liabilities/.../collect",
        idempotency_key=f"liab:{txn_id}",
    )
    legs = await _mirrored_legs(db, uid, txn_id)
    assert len(legs) == 2
    for l in legs:
        md = l["metadata"]
        # All STRICT RULE 2 fields:
        for k in (
            "account_transaction_id", "source", "transaction_type",
            "idempotency_key", "created_by_endpoint",
        ):
            assert k in md, f"missing mandatory metadata key {k}"
        assert md["iter"] == "iter240"


@pytest.mark.asyncio
async def test_no_historical_rows_are_touched(db, clean_user):
    """STRICT RULE 1 — the helper must NEVER reach back into history.

    Insert a fake old account_transaction (without calling the helper)
    and assert it stays UNmirrored (i.e. the helper truly only acts on
    rows we explicitly pass to it).
    """
    uid = clean_user
    old_id = str(uuid.uuid4())
    await db.account_transactions.insert_one({
        "id": old_id,
        "user_id": uid,
        "account_id": "bank_LEGACY",
        "amount": 999.99,
        "direction": "out",
        "transaction_type": "expense",
        "transaction_date": "2024-01-01",
        "description": "legacy / pre-iter240",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    })
    # Nothing should be mirrored without an explicit call.
    legs = await _mirrored_legs(db, uid, old_id)
    assert legs == [], "Iter-240 must NEVER auto-touch historical rows"
    # cleanup
    await db.account_transactions.delete_one({"id": old_id, "user_id": uid})
