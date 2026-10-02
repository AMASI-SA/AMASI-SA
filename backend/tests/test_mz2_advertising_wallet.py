"""Real replica-set tests of the foreign-unit ledger, never production data."""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from accounting_atomic import atomic_owner
from accounting_advertising_contract import FX, FxSnapshot, WalletOpening, leg, money
from accounting_advertising_setup import setup
from accounting_ledger_v2 import post_opening_journal_v2
from accounting_advertising_wallet import (
    MOVEMENTS, OPENINGS, append_wallet_movement, materialize_opening,
    opening_hash, opening_key, validate_opening_evidence, wallet_capacity,
)
from tests.test_mz2_advertising_v2 import db, OWNER, AT, binding


def wallet():
    return binding(mode="prepaid", currency="USD").model_dump(mode="json")


async def seed_native_wallet_opening(db, binding, original_amount="1000", rate="3.75"):
    """Seed approved opening proof in a random local test database only."""
    assert db.name.startswith("mz2_track_e_test_")
    binding = binding.model_dump(mode="json") if hasattr(binding, "model_dump") else binding
    sar = money(Decimal(original_amount) * Decimal(rate))
    async def commit(scoped):
        entries = [leg("ad_account", binding["wallet_financial_account_id"], "balance", "debit", sar),
                   leg("equity", "fixture", None, "credit", sar)]
        entries = [dict(entry, entry_type="opening_balance") for entry in entries]
        return await post_opening_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER,
            actor_name="Isolated test owner", opening_operation_id="test-wallet-opening",
            approved_preview_hash="a" * 64, effective_at=AT, entries=entries, mongo_session=scoped._session)
    result = await atomic_owner(db, OWNER, commit)
    group = result["group"]["txn_group_id"]
    await db.settings.update_one({"user_id": OWNER}, {"$set": {
        "mezan2_financial_cutover.opening_active_txn_group_id": group,
        "mezan2_financial_cutover.opening_root_txn_group_id": group}})
    fx = await setup(db, OWNER, FxSnapshot(currency=binding["currency"], business_date=AT[:10],
        fx_rate_to_sar=rate, fx_at=AT, fx_source="Opening test statement", evidence="Original test opening conversion"))
    return await setup(db, OWNER, WalletOpening(platform=binding["platform"],
        integration_account_id=binding["integration_account_id"], currency=binding["currency"],
        original_currency_amount=original_amount, opening_sar_amount=sar, opening_txn_group_id=group,
        effective_at=AT, fx_snapshot_id=fx["id"], evidence="Approved existing original-currency opening statement"))


async def movement(scoped, amount, source="first", at=AT, kind="opening"):
    return await append_wallet_movement(scoped, OWNER, wallet(), amount, kind,
        source, at[:10], at, "fx-proof", "native-group-" + source, OWNER, "Isolated fixture evidence")


@pytest.mark.asyncio
async def test_wallet_requires_financial_transaction(db):
    with pytest.raises(HTTPException, match="ad_wallet_financial_transaction_required"):
        await movement(db, "100")
    with pytest.raises(HTTPException, match="ad_wallet_financial_transaction_required"):
        await wallet_capacity(db, OWNER, wallet(), AT)
    assert await db[MOVEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_original_capacity_tracks_historical_minimum_independently(db):
    async def work(scoped):
        await movement(scoped, "1000")
        await movement(scoped, "-300", "spend", "2026-01-03T00:00:00Z", "spend")
        await movement(scoped, "500", "later", "2026-01-05T00:00:00Z")
        assert await wallet_capacity(scoped, OWNER, wallet(), "2026-01-02T00:00:00Z") == Decimal("700")
        assert await wallet_capacity(scoped, OWNER, wallet(), "2026-01-06T00:00:00Z") == Decimal("1200")
        assert await wallet_capacity(scoped, OWNER, wallet(), "2025-12-31T00:00:00Z") == 0
    await atomic_owner(db, OWNER, work)


@pytest.mark.asyncio
async def test_movement_idempotency_conflict_and_tampering(db):
    first = await atomic_owner(db, OWNER, lambda s: movement(s, "1000"))
    second = await atomic_owner(db, OWNER, lambda s: movement(s, "1000"))
    assert first == second
    assert await db[MOVEMENTS].count_documents({}) == 1
    with pytest.raises(HTTPException, match="ad_wallet_movement_idempotency_conflict"):
        await atomic_owner(db, OWNER, lambda s: movement(s, "999"))
    await db[MOVEMENTS].update_one({"_id": first["_id"]}, {"$set": {"original_currency_amount": "999999"}})
    with pytest.raises(HTTPException, match="ad_wallet_movement_integrity_failure"):
        await atomic_owner(db, OWNER, lambda s: wallet_capacity(s, OWNER, wallet(), AT))


@pytest.mark.asyncio
async def test_movement_aborts_and_paused_cannot_write(db):
    async def abort(scoped):
        await movement(scoped, "1000")
        raise RuntimeError("rollback")
    with pytest.raises(RuntimeError, match="rollback"):
        await atomic_owner(db, OWNER, abort)
    assert await db[MOVEMENTS].count_documents({}) == 0
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    with pytest.raises(HTTPException, match="mz2_writes_paused"):
        await atomic_owner(db, OWNER, lambda s: movement(s, "1000"))
    assert await db[MOVEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_original_opening_needs_native_wallet_proof(db):
    await db[FX].insert_one({"_id": "fx", "user_id": OWNER, "status": "active", "confirmed_by": OWNER,
        "currency": "USD", "business_date": "2026-01-01", "fx_rate_to_sar": "3.75"})
    payload = dict(platform="meta", integration_account_id="meta-v2", currency="USD",
        original_currency_amount="1000", opening_sar_amount="3750", opening_txn_group_id="zero:test",
        effective_at=AT, fx_snapshot_id="fx", evidence="Approved existing opening statement")
    normalized = await validate_opening_evidence(db, OWNER, wallet(), payload)
    assert normalized["opening_sar_amount"] == "3750.00"
    await db[OPENINGS].insert_one(dict(normalized, _id=opening_key(OWNER, wallet()),
        content_hash=opening_hash(normalized), status="active", confirmed_by=OWNER))
    # Explicit-zero P07 is valid globally, but is not proof of a funded wallet.
    with pytest.raises(HTTPException, match="ad_wallet_opening_native_amount_mismatch"):
        await atomic_owner(db, OWNER, lambda s: materialize_opening(s, OWNER, wallet()))
    assert await db[MOVEMENTS].count_documents({}) == 0
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    with pytest.raises(HTTPException, match="ad_wallet_opening_fx_evidence_invalid"):
        await validate_opening_evidence(db, OWNER, wallet(), dict(payload, opening_sar_amount="4000"))


@pytest.mark.asyncio
async def test_owner_scope_and_bank_funding_port_fail_closed(db):
    async def work(scoped):
        with pytest.raises(HTTPException, match="ad_wallet_financial_transaction_required"):
            await wallet_capacity(scoped, "other-owner", wallet(), AT)
        with pytest.raises(HTTPException, match="ad_wallet_movement_contract_invalid"):
            await movement(scoped, "1000", kind="funding")
    await atomic_owner(db, OWNER, work)
    assert await db[MOVEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_existing_native_opening_materializes_once_without_journal_mutation(db):
    from tests.test_mz2_advertising_v2 import seed
    await seed(db, currency="USD")
    await setup(db, OWNER, binding(mode="prepaid", currency="USD"))
    await seed_native_wallet_opening(db, wallet())
    ledger_before = await db.accounting_general_ledger_v2.find({}).to_list(100)
    async def materialize(scoped):
        result = await materialize_opening(scoped, OWNER, wallet())
        assert await wallet_capacity(scoped, OWNER, wallet(), AT) == Decimal("1000")
        return result
    first = await atomic_owner(db, OWNER, materialize)
    assert await atomic_owner(db, OWNER, materialize) == first
    assert await db[MOVEMENTS].count_documents({}) == 1
    assert await db.accounting_general_ledger_v2.find({}).to_list(100) == ledger_before
