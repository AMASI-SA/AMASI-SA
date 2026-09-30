"""Read-only bank identity consumers: legacy reads and writes explode."""
from copy import deepcopy
import pytest

from accounting_bank_transfer_receipts import _resolve_order_bank
from accounting_courier_bank_routes import _binding_view


class ReadOnlyCollection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.reads = []

    async def find_one(self, query, projection=None, **kwargs):
        self.reads.append(query)
        for row in self.rows:
            if all((row.get(key) in value['$in'] if '$in' in value
                    else row.get(key) != value['$ne']) if isinstance(value, dict)
                   else row.get(key) == value for key, value in query.items()):
                return deepcopy(row)
        return None


class ReadOnlyDB:
    def __init__(self, banks, binding=None):
        self.mz2_financial_accounts = ReadOnlyCollection(banks)
        self.accounting_provider_bank_bindings_v2 = ReadOnlyCollection([binding] if binding else [])

    def __getattr__(self, name):
        raise AssertionError(f'Unexpected collection read/write: {name}')

    def __getitem__(self, name):
        return getattr(self, name)


def bank(**overrides):
    return dict(user_id='owner', id='canonical-bank', name='MZ2 bank',
                account_type='bank', status='active', currency='SAR', **overrides)


@pytest.mark.asyncio
async def test_transfer_resolves_canonical_id_without_settings_legacy_or_writes():
    db = ReadOnlyDB([bank()])
    before = deepcopy(db.mz2_financial_accounts.rows)
    result = await _resolve_order_bank(db, 'owner', 'canonical-bank')
    assert result['state'] == 'resolved'
    assert result['bank_account_id'] == 'canonical-bank'
    assert result['bank_account_source'] == 'mz2_financial_accounts'
    assert db.mz2_financial_accounts.rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', ['legacy-only-bank', 'MZ2 bank'])
async def test_transfer_never_infers_bank_from_name_or_legacy_alias(selected):
    result = await _resolve_order_bank(ReadOnlyDB([bank()]), 'owner', selected)
    assert result['state'] == 'unresolved'
    assert result['code'] == 'MZ2_LINK_REQUIRED'
    assert result['bank_account_id'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'status': 'inactive'}, {'currency': 'USD'}, {'user_id': 'other'}])
async def test_transfer_rejects_inactive_currency_and_other_tenant(change):
    row = bank(); row.update(change)
    result = await _resolve_order_bank(ReadOnlyDB([row]), 'owner', row['id'])
    assert result['state'] == 'unresolved'


@pytest.mark.asyncio
async def test_unmarked_courier_binding_does_not_gain_authority_from_same_id_collision():
    binding = {'user_id': 'owner', 'provider': 'shipping:smsa',
               'bank_account_id': 'canonical-bank', 'verification_status': 'verified'}
    db = ReadOnlyDB([bank()], binding)
    result = await _binding_view(db, 'owner', {'provider_id': 'shipping:smsa'})
    assert result['configured'] is False
    assert result['code'] == 'MZ2_LINK_REQUIRED'
    assert result['bank_account_id'] is None
    assert not db.mz2_financial_accounts.reads
    assert db.accounting_provider_bank_bindings_v2.rows == [binding]


@pytest.mark.asyncio
async def test_explicit_courier_binding_resolves_mz2_bank_without_financial_write():
    binding = {'user_id': 'owner', 'provider': 'shipping:smsa',
               'bank_account_id': 'canonical-bank', 'verification_status': 'verified',
               'bank_account_source': 'mz2_financial_accounts', 'identity_contract_version': 1}
    db = ReadOnlyDB([bank()], binding)
    result = await _binding_view(db, 'owner', {'provider_id': 'shipping:smsa'})
    assert result['configured'] is True
    assert result['bank_account_id'] == 'canonical-bank'
    assert result['p02_financial_logic_locked'] is True
    assert db.accounting_provider_bank_bindings_v2.rows == [binding]
