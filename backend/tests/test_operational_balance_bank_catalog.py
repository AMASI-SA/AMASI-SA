from copy import deepcopy
import pytest
from test_operational_balance_sources import DB, row, run
from operational_balance_sources import entities, rows
from operational_bank_catalog import PROJECTION


def test_existing_banks_are_identity_only_and_tenant_scoped():
    db = DB({'accounts': [row(id='rajhi', name='الراجحي', account_type='bank',
                             currency='SAR', status='active', opening_balance=90000,
                             current_balance=99000, notes='private'),
                          dict(user_id='other', id='foreign', account_type='bank', status='active'),
                          row(id='hidden', account_type='bank', status='hidden')],
             'mz2_financial_accounts': [row(id='native', name='الأهلي', account_type='bank',
                                            currency='SAR', status='active')]})
    before = deepcopy(db.data)
    choices = run(entities(db, 'owner', 'bank'))
    assert {r['id'] for r in choices} == {'native', 'account:rajhi'}
    assert all(set(r) == {'id', 'name', 'currency', 'kind'} for r in choices)
    assert db.data == before
    assert not {'opening_balance', 'current_balance', 'notes', 'iban'} & set(PROJECTION)
    with pytest.raises(ValueError, match='source_rejected'):
        run(rows(db, 'owner', 'accounts'))


def test_terminal_is_independent_account_without_driver_or_accounting_fact():
    db = DB({'mz2_operational_bank_accounts': [row(id='terminal', name='الشبكة',
              account_type='bank', currency='SAR', status='active')]})
    assert run(entities(db, 'owner', 'bank')) == [
        {'id': 'operational-bank:terminal', 'name': 'الشبكة', 'currency': 'SAR', 'kind': 'bank'}]
    assert 'mz2_opening_facts_v2' not in db.reads and 'store_drivers' not in db.reads


def test_banks_are_never_merged_by_name_or_cross_catalog_raw_id():
    value = row(id='same', name='الراجحي', account_type='bank', currency='SAR', status='active')
    db = DB({'accounts': [value], 'mz2_financial_accounts': [value]})
    assert {r['id'] for r in run(entities(db, 'owner', 'bank'))} == {'same', 'account:same'}
    db.data['accounts'].append(value)
    with pytest.raises(ValueError, match='identity_ambiguous'):
        run(entities(db, 'owner', 'bank'))


def test_missing_currency_remains_unselectable():
    db = DB({'accounts': [row(id='missing', name='Bank', account_type='bank', status='active')]})
    assert run(entities(db, 'owner', 'bank'))[0]['ready'] is False
