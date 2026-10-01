"""Ledger identity equals real opening and operational preview keys, read-only."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from accounting_atomic import SessionDatabase
from accounting_financial_accounts import OpeningDraftCreate, EVIDENCE_SECTION_IDS, _compile_opening
from accounting_financial_identity import require_financial_ledger_identity, list_financial_ledger_identities
from accounting_settlement_service import build_journal_preview


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, size): return deepcopy(self.rows[:size])


class Collection:
    name = 'mz2_financial_accounts'
    def __init__(self, db): self.db = db
    def find(self, query, projection=None, **kwargs):
        self.db.calls.append((query, kwargs))
        def matches(row):
            return all((row.get(key) in value['$in'] if '$in' in value else row.get(key) != value['$ne'])
                       if isinstance(value, dict) else row.get(key) == value for key, value in query.items())
        return Cursor([row for row in self.db.rows if matches(row)])
    async def find_one(self, query, projection=None, **kwargs):
        rows = await self.find(query, projection, **kwargs).to_list(1)
        return rows[0] if rows else None
    def __getattr__(self, method): raise AssertionError('Write forbidden: ' + method)


class DB:
    def __init__(self, rows): self.rows = deepcopy(rows); self.calls = []
    def __getitem__(self, name):
        assert name == 'mz2_financial_accounts', 'Unexpected source: ' + name
        return Collection(self)
    def __getattr__(self, name): return self[name]


def account(kind='bank', **changes):
    return {**dict(user_id='owner', id='canonical-' + kind, account_type=kind,
                   currency='SAR', status='active', name='Canonical ' + kind), **changes}


def ledger_key(row):
    return tuple(row[key] for key in ('entity_type', 'entity_id', 'sub_account'))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['bank', 'cash'])
async def test_identity_matches_actual_opening_compilation_and_bank_only_settlement_preview(kind):
    row = account(kind); db = DB([row]); before = deepcopy(db.rows)
    identity = await require_financial_ledger_identity(db, 'owner', row['id'])
    compiled = await _compile_opening(db, owner='owner', payload=OpeningDraftCreate(
        idempotency_key='identity-proof', cutover_at='2026-10-01T00:00:00+03:00',
        cutover_evidence_file_id='cutover',
        section_evidence_file_ids={key: 'evidence-' + key for key in EVIDENCE_SECTION_IDS},
        lines=[dict(category='financial_account', financial_account_id=row['id'],
                    meaning='available_to_us', original_amount='25', original_currency='SAR',
                    evidence_file_id='evidence-banks_cash')]))
    assert ledger_key(identity) == ledger_key(compiled['lines'][0])
    # Provider settlement is bank-only. Cash equivalence is exercised below
    # through the daily outflow writer, not by pretending it is a provider bank.
    if kind == 'bank':
        preview = build_journal_preview(provider='salla', bank_account_id=identity['entity_id'],
                                        bank_account_name=row['name'],
                                        amounts={'gross_sales': 25, 'reported_net': 25})
        bank_entry = next(item for item in preview['entries'] if item['role'] == 'bank_net')
        assert ledger_key(identity) == ledger_key(bank_entry)
    assert ledger_key(identity) == ('bank', row['id'], 'main')
    assert identity == {'id': row['id'], 'account_type': kind, 'currency': 'SAR', 'status': 'active',
                        'entity_type': 'bank', 'entity_id': row['id'], 'sub_account': 'main'}
    assert db.rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [dict(user_id='foreign'), dict(status='inactive'),
    dict(currency='USD'), dict(archived=True), dict(account_type='ad_payable')])
async def test_require_and_list_reject_invalid_identity(change):
    row = account(**change); db = DB([row])
    with pytest.raises(HTTPException) as error:
        await require_financial_ledger_identity(db, 'owner', row['id'])
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'MZ2_LINK_REQUIRED'
    assert await list_financial_ledger_identities(db, 'owner') == []


@pytest.mark.asyncio
async def test_transaction_bound_db_remains_bound_and_identity_is_not_write_authorization():
    raw = DB([account(), account('cash')]); session = object()
    scoped = SessionDatabase(raw, session); scoped._owner = 'owner'
    result = await require_financial_ledger_identity(scoped, 'owner', 'canonical-cash')
    listed = await list_financial_ledger_identities(scoped, 'owner')
    assert result in listed
    assert len(listed) == 2
    assert all(kwargs == {'session': session} for query, kwargs in raw.calls)
    assert scoped._ledger_groups == set()
    assert not {'can_post', 'can_write', 'write_authorized'} & result.keys()


@pytest.mark.asyncio
async def test_requested_currency_and_type_preserved_without_default_conversion():
    row = account('cash', currency='USD'); db = DB([row])
    result = await require_financial_ledger_identity(db, 'owner', row['id'], account_types=('cash',), currency='USD')
    assert result['currency'] == 'USD'
    assert result['account_type'] == 'cash'
    assert await list_financial_ledger_identities(db, 'owner', account_types=('bank',), currency='USD') == []



@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['bank', 'cash'])
async def test_daily_payment_writer_emits_exact_opening_identity_at_captured_ledger_sink(kind, monkeypatch):
    """Exercise production outflow leg construction, stop at the ledger sink.

    Cutover/category/balance collaborators are controlled here; this verifies
    writer identity, not real posting authorization or transaction persistence.
    """
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import accounting_daily_movements as daily

    row = account(kind)
    canonical_db = DB([row])
    identity = await require_financial_ledger_identity(canonical_db, 'owner', row['id'])
    compiled = await _compile_opening(canonical_db, owner='owner', payload=OpeningDraftCreate(
        idempotency_key='payment-identity-proof', cutover_at='2026-10-01T00:00:00+03:00',
        cutover_evidence_file_id='cutover',
        section_evidence_file_ids={key: 'evidence-' + key for key in EVIDENCE_SECTION_IDS},
        lines=[dict(category='financial_account', financial_account_id=row['id'],
                    meaning='available_to_us', original_amount='25', original_currency='SAR',
                    evidence_file_id='evidence-banks_cash')]))
    # The real incoming identity check accepts both bank and cash for this path.
    assert (await daily._bank_or_409(canonical_db, 'owner', row['id']))['id'] == row['id']
    movement = dict(id='outflow', user_id='owner', bank_account_id=row['id'], amount='5',
                    status='unclassified', direction='out', currency='SAR', movement_date='2026-10-02')
    class PaymentDB(DB):
        def __getattr__(self, name):
            if name == 'mz2_daily_movements':
                return SimpleNamespace(find_one=AsyncMock(return_value=movement))
            if name == 'mz2_outgoing_financial_events':
                return SimpleNamespace(find_one=AsyncMock(return_value=None))
            return super().__getattr__(name)
    db = PaymentDB([row])
    monkeypatch.setattr(daily, '_post_cutover_accounting_at', AsyncMock(return_value='2026-10-02T00:00:00Z'))
    monkeypatch.setattr(daily, '_expense_category', AsyncMock(return_value={'code': 'supplies', 'name': 'Supplies'}))
    monkeypatch.setattr(daily, 'read_mz2_write_balances', AsyncMock(return_value=SimpleNamespace(net_balance=lambda **kwargs: Decimal('25'))))
    captured = {}
    class LedgerSinkReached(Exception): pass
    async def sink(same_db, **kwargs):
        assert same_db is db
        captured.update(kwargs)
        raise LedgerSinkReached()
    monkeypatch.setattr(daily, 'post_operational_journal', sink)
    with pytest.raises(LedgerSinkReached):
        await daily.classify_outgoing_movement(db, owner='owner', actor={'id': 'owner'}, movement_id='outflow',
            payload=daily.OutgoingMovementClassifyIn(action='expense', expense_category='supplies', reason='Identity proof'))
    payment_leg = next(leg for leg in captured['entries'] if leg['side'] == 'credit')
    assert ledger_key(payment_leg) == ledger_key(identity) == ledger_key(compiled['lines'][0])
    assert payment_leg['amount'] == 5
    assert captured['txn_type'] == 'mz2_general_expense'
    assert db.rows == [row]
