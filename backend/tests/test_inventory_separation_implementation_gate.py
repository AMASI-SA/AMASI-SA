"""Design boundary probes only: no proposed writer or accounting approval.

Real isolated Mongo; the current opening-cost guard and stock reader are not
mocked. Empty accounting ledger is deliberate: this does NOT prove cutover or
full accounting readiness. Fixtures are not a receiving implementation.
"""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from test_operational_balance_integration import run
from purchase_receiving_service import assert_opening_inventory_initialized, identity_key, COST_POLICY
from fulfillment_v2_routes import _inventory_rows

OWNER='owner'
IDENTITY={'item_type':'product','product_id':'gate-product','variant_id':None}

async def location(db):
    doc={'id':'gate-location','user_id':OWNER,'warehouse_id':'gate-warehouse','state':'occupied',
         'occupancy':{'total_quantity':10,'items':[{**IDENTITY,'quantity':10,'receipt_id':'gate-receipt',
         'valuation_status':'PENDING_FINANCIAL_VALUATION','operational_unit_cost':'15'}]}}
    await db.warehouse_locations.insert_one(deepcopy(doc))
    return doc

def test_pending_flag_in_occupancy_does_not_bypass_actual_cost_guard():
    async def scenario(db):
        await location(db)
        with pytest.raises(HTTPException) as error:
            await assert_opening_inventory_initialized(db,OWNER,{})
        assert error.value.detail['code']=='inventory_cost_reconciliation_required'
        assert await db.mz2_inventory_cost_states.count_documents({})==0
    run(scenario)

def test_current_commercial_reader_ignores_pending_flag():
    async def scenario(db):
        doc=await location(db)
        rows=_inventory_rows([doc])
        assert len(rows)==1 and rows[0]['remaining']==10
    run(scenario)

def test_existing_cost_same_identity_masks_pending_quantity_from_guard():
    async def scenario(db):
        await location(db)
        key=identity_key(OWNER,IDENTITY)
        await db.mz2_inventory_cost_states.insert_one({'_id':key,'user_id':OWNER,
            'inventory_identity':IDENTITY,'average_cost':'7','authoritative':True,'cost_policy_version':COST_POLICY})
        before=await db.mz2_inventory_cost_states.find_one({'_id':key})
        await assert_opening_inventory_initialized(db,OWNER,{})
        assert await db.mz2_inventory_cost_states.find_one({'_id':key})==before
        # Passing this guard does not make the pending 10 units financially valued.
    run(scenario)

def test_receipt_only_pending_evidence_is_invisible_to_current_stock_and_cost_guard():
    async def scenario(db):
        await db.mezan_inventory_receipts_v2.insert_one({'id':'pending','user_id':OWNER,
            'schema_version':'operational-receipt-design-only','status':'PENDING_FINANCIAL_VALUATION',
            'quantity':10,'operational_unit_cost':'15','location_id':'gate-location',**IDENTITY})
        before=await db.mezan_inventory_receipts_v2.find_one({'id':'pending'})
        await assert_opening_inventory_initialized(db,OWNER,{})
        assert _inventory_rows(await db.warehouse_locations.find({'user_id':OWNER}).to_list(10))==[]
        assert await db.mz2_inventory_cost_states.count_documents({})==0
        assert await db.mezan_inventory_receipts_v2.find_one({'id':'pending'})==before
    run(scenario)
