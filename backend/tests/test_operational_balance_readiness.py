"""Inspection has no write interface and cannot invent source or setup evidence."""
from copy import deepcopy
import pytest
from operational_cost_readiness import ReadOnlySources, summarize_issues, inspect_readiness
from test_operational_open_cost_exposure import seeded_db, operating_state, NOW
from test_operational_balance_sources import run


def test_readonly_capability_rejects_writes_and_unscoped_collections():
    db=seeded_db();ro=ReadOnlySources(db,'owner')
    for name in ('users','accounts','settings','operational_balance_states_v1','unknown'):
        with pytest.raises(ValueError, match='collection_rejected'):ro[name]
    collection=ro['unified_orders']
    with pytest.raises(ValueError, match='owner_scope_invalid'):collection.find({'user_id':'another'})
    for name in ('update_one','insert_one','delete_many','aggregate','bulk_write'):
        assert not hasattr(collection,name)
    with pytest.raises(ValueError, match='read_limit_invalid'):
        run(collection.find({'user_id':'owner'}).to_list(999999))


def test_summary_contains_codes_counts_not_raw_references_or_customer_records():
    result=summarize_issues([{'code':'x','source_id':'do-not-export','customer':'private'},
                            {'code':'x','count':3},'y'])
    assert result==[{'code':'x','count':4},{'code':'y','count':1}]
    assert 'do-not-export' not in repr(result)


def test_readiness_simulates_without_financial_or_source_mutations():
    db=seeded_db();before=deepcopy(db.data)
    state=operating_state();state.update(_id='owner',owner_id='owner',revision=4,
        openings={'bank':{'amount':'500'}},movements=[])
    original=deepcopy(state)
    result=run(inspect_readiness(db,'owner',state,as_of=NOW))
    assert state==original and db.data==before
    assert result['database_writes'] is False
    assert result['openings_unchanged'] is True and result['opening_count']==1
    assert result['product_cost_currency']=='SAR'
    assert result['cost_totals']['SAR']['prestart_supplier_unreceived']=='40.00'
    assert result['ready_for_operational_use'] is False  # shipping lacks native contract
    assert 'owner_id' not in result


def test_readiness_requires_exact_owner_and_active_state():
    state=operating_state();state.update(_id='owner',owner_id='owner')
    with pytest.raises(ValueError,match='active_owner_state_required'):
        run(inspect_readiness(seeded_db(),'other',state,as_of=NOW))
    state['status']='frozen'
    with pytest.raises(ValueError,match='active_owner_state_required'):
        run(inspect_readiness(seeded_db(),'owner',state,as_of=NOW))
