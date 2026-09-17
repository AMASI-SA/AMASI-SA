"""Read-only API regression: historical and future audited ledger reversals."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import APIRouter, HTTPException
from accounting_settlement_ledger_state import with_ledger_state
import accounting_settlement_routes as drafts
import accounting_settlement_register_routes as register
import accounting_settlement_lifecycle_routes as lifecycle


def matches(row, query):
    return all(row.get(k) in v['$in'] if isinstance(v, dict) else row.get(k) == v
               for k, v in query.items())

class Cursor:
    def __init__(self, rows): self.rows = deepcopy(rows)
    def sort(self, *args): return self
    async def to_list(self, limit): return self.rows[:limit] if limit else self.rows

class Collection:
    def __init__(self, rows): self.rows = rows
    def find(self, query, projection=None): return Cursor([r for r in self.rows if matches(r, query)])
    async def find_one(self, query, projection=None):
        return next((deepcopy(r) for r in self.rows if matches(r, query)), None)
    # No mutation methods: reads and rejected replays must never write.

@pytest.fixture
def db():
    settlements = [dict(id='old', user_id='owner', status='posted', workflow_state='posted',
                        ledger_txn_group_id='g', amounts={}),
                   dict(id='new', user_id='owner', status='reviewed', amounts={})]
    legs = [dict(id=str(i), user_id='owner', txn_group_id='g', status='reversed',
                 reversed_by_entry_id='r'+str(i), metadata={'txn_type':'provider_settlement_v2'}) for i in range(4)]
    return SimpleNamespace(accounting_settlements_v2=Collection(settlements), general_ledger=Collection(legs))

async def current_user(): return {'id':'owner', 'role':'owner'}

async def scope(db, user, *args): return {'id':'owner', 'role':'owner'}, 'owner'

@pytest.mark.asyncio
async def test_full_reversal_is_terminal_without_rewriting_snapshot(db):
    original = deepcopy(db.accounting_settlements_v2.rows)
    result = await with_ledger_state(db, 'owner', original)
    assert result[0]['status'] == result[0]['workflow_state'] == 'reversed'
    assert result[0]['stored_status'] == 'posted'
    assert result[1]['status'] == 'reviewed'
    assert original == db.accounting_settlements_v2.rows

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['partial', 'missing_reference', 'empty', 'other_owner', 'wrong_type', 'one_leg'])
async def test_incomplete_or_foreign_evidence_never_claims_full_reversal(db, case):
    legs = db.general_ledger.rows
    if case == 'partial': legs[0]['status'] = 'posted'
    if case == 'missing_reference': legs[0].pop('reversed_by_entry_id')
    if case == 'empty': legs.clear()
    if case == 'other_owner':
        for leg in legs: leg['user_id'] = 'other'
    if case == 'wrong_type': legs[0]['metadata']['txn_type'] = 'other'
    if case == 'one_leg': del legs[1:]
    result = await with_ledger_state(db, 'owner', db.accounting_settlements_v2.rows)
    assert result[0]['status'] == 'posted'

@pytest.mark.asyncio
async def test_draft_detail_and_post_replay_409_without_write(db, monkeypatch):
    assert (await drafts._draft_or_404(db, 'owner', 'old'))['status'] == 'reversed'
    monkeypatch.setattr(lifecycle, '_scope', scope)
    router = APIRouter(); lifecycle.install_accounting_settlement_lifecycle_routes(router, db, current_user)
    post = next(r.endpoint for r in router.routes if r.path.endswith('/post'))
    with pytest.raises(HTTPException) as exc:
        await post('old', drafts.DraftActionIn(), {'id':'owner'})
    assert exc.value.status_code == 409

@pytest.mark.asyncio
@pytest.mark.parametrize('wanted,expected', [('reversed',['old']),('posted',[]), (None,['old','new'])])
async def test_register_filters_effective_state_before_limit(db, monkeypatch, wanted, expected):
    monkeypatch.setattr(register, '_scope', scope)
    router = APIRouter(); register.install_accounting_settlement_register_routes(router, db, current_user)
    listing = router.routes[0].endpoint
    result = await listing(q=None,provider=None,status=wanted,bank_account_id=None,
                           period_from=None,period_to=None,limit=100,user={'id':'owner'})
    assert [x['id'] for x in result['items']] == expected
    detail = await router.routes[1].endpoint('old', {'id':'owner'})
    assert detail['draft']['status'] == detail['register_item']['status'] == 'reversed'

@pytest.mark.asyncio
@pytest.mark.parametrize('wanted,expected', [('reversed',['old']),('posted',[])])
async def test_draft_list_filters_effective_state(db, monkeypatch, wanted, expected):
    monkeypatch.setattr(drafts, '_scope', scope)
    router = APIRouter(); drafts.install_accounting_settlement_routes(router, db, current_user)
    listing = next(r.endpoint for r in router.routes if r.path.endswith('/drafts') and 'GET' in r.methods)
    result = await listing(status=wanted,provider=None,limit=100,user={'id':'owner'})
    assert [x['id'] for x in result['items']] == expected
