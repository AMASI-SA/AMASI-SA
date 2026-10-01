"""Readiness distinguishes connected evidence adapters from financial permission."""
from bson import json_util
import pytest
import test_financial_accounts_real_mongo as fixtures
from accounting_shipping_native_routes import readiness

mongo_db = fixtures.mongo_db


@pytest.mark.asyncio
async def test_destination_metadata_is_truthful_without_changing_opening_pause_or_phase_gates(mongo_db):
    owner = fixtures.OWNER
    await mongo_db.mz2_atomic_owners.update_one({"_id": owner}, {"$set": {"writes_paused": True}})
    before = {name: sorted(json_util.dumps(row, sort_keys=True) for row in await mongo_db[name].find({}).to_list(None))
              for name in await mongo_db.list_collection_names()}
    result = await readiness(mongo_db, owner)
    destination = result["driver_payment_destination"]
    assert destination["ready"] is False
    assert destination["direct_pos_to_bank_on_accept"] is False
    assert destination["methods"]["bank_transfer"] == {
        "adapter_connected": True, "destination_kind": "bank",
        "evidence_required": "verified_bank_statement_arrival", "approval_gates_unchanged": True}
    assert destination["methods"]["card_terminal"] == {
        "adapter_connected": False, "destination_kind": "pos_receivable",
        "code": "mz2_driver_payment_destination_not_integrated",
        "evidence_required": "canonical_successful_pos_transaction"}
    assert result["p02"] == "LOCKED_BY_EXISTING_ACTIVATION_GATE"
    assert result["activation_performed"] is False
    assert all(stage["ready"] is False for stage in result["stages"].values())
    after = {name: sorted(json_util.dumps(row, sort_keys=True) for row in await mongo_db[name].find({}).to_list(None))
             for name in await mongo_db.list_collection_names()}
    assert after == before
