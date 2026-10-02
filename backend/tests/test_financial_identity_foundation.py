"""Canonical identity contract: forbidden collections and writes fail immediately."""
from copy import deepcopy
import pytest
from accounting_financial_identity import find_financial_account, list_financial_accounts


def matches(row, query):
    return all((row.get(k) in v['$in'] if '$in' in v else row.get(k) != v['$ne'])
               if isinstance(v, dict) else row.get(k) == v for k, v in query.items())

class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, limit): return deepcopy(self.rows[:limit])

class Collection:
    def __init__(self, db): self.db = db
    def find(self, query, *args):
        self.db.reads.append(query)
        return Cursor([r for r in self.db.rows if matches(r, query)])
    async def find_one(self, query, *args):
        self.db.reads.append(query)
        return deepcopy(next((r for r in self.db.rows if matches(r, query)), None))
    def __getattr__(self, method): raise AssertionError('Unexpected write: '+method)

class DB:
    def __init__(self, rows): self.rows=rows; self.reads=[]
    def __getitem__(self, name):
        assert name == 'mz2_financial_accounts', 'Forbidden operational source: '+name
        return Collection(self)
    def __getattr__(self, name): return self[name]

def account(id='bank', **changes):
    return dict(user_id='owner', id=id, status='active', account_type='bank', currency='SAR', **changes)

@pytest.mark.asyncio
async def test_bank_cash_listing_is_canonical_and_readonly():
    cash={**account('cash'), 'account_type':'cash'}
    db=DB([account(), cash, {**account('foreign'), 'user_id':'other'}])
    rows=await list_financial_accounts(db,'owner')
    assert {r['id'] for r in rows} == {'bank','cash'}
    assert await find_financial_account(db,'owner','bank') == account()
    assert await find_financial_account(db,'owner','legacy-only') is None

@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(status='inactive'),dict(status=None),dict(currency='USD'),dict(currency=None),dict(account_type='ad_payable'),dict(user_id='other'),dict(archived=True),dict(is_active=False)])
async def test_invalid_identity_fails_closed(changes):
    db=DB([{**account(),**changes}])
    assert await find_financial_account(db,'owner','bank') is None
    assert await list_financial_accounts(db,'owner') == []

@pytest.mark.asyncio
async def test_same_id_legacy_cannot_override_canonical_and_context_is_explicit():
    # DB rejects even a diagnostic Legacy read; exact canonical record is enough.
    db=DB([account('collision'),{**account('usd'),'currency':'USD'}])
    assert (await find_financial_account(db,'owner','collision'))['currency']=='SAR'
    assert (await find_financial_account(db,'owner','usd',currency='USD'))['id']=='usd'
    assert await find_financial_account(db,'owner','collision',account_types=('cash',)) is None

@pytest.mark.asyncio
async def test_empty_owner_never_scans_accounts():
    db=DB([account()])
    assert await find_financial_account(db,'','bank') is None
    assert await list_financial_accounts(db,'') == []
    assert db.reads == []


def test_operational_bank_consumers_have_no_legacy_collection_access():
    """Central regression boundary; compatibility/history routes are separate."""
    import ast
    from pathlib import Path
    modules = ('accounting_financial_identity', 'accounting_bank_transfer_bindings', 'accounting_settlement_routes',
               'accounting_settlement_service', 'accounting_bank_transfer_receipts',
               'accounting_courier_bank_routes', 'accounting_customer_advances',
               'accounting_customer_refund_routes', 'accounting_daily_movements',
               'accounting_onboarding_identities', 'accounting_onboarding_domains')
    root = Path(__file__).resolve().parents[1]
    for module in modules:
        tree = ast.parse((root / (module+'.py')).read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr != 'accounts', (module, node.lineno)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value != 'accounts', (module, node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get_collection':
                assert not node.args or not isinstance(node.args[0], ast.Constant) or node.args[0].value != 'accounts', (module, node.lineno)
