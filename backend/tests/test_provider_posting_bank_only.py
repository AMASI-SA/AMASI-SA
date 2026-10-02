"""Final provider posting resolves only active SAR bank identities, never cash."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
import accounting_settlement_service as service


class CanonicalBankCollection:
    def __init__(self, row): self.row = row
    async def find_one(self, query, projection=None):
        if self.row is None: return None
        for key, expected in query.items():
            actual = self.row.get(key)
            if isinstance(expected, dict):
                if '$in' in expected and actual not in expected['$in']: return None
                if '$ne' in expected and actual == expected['$ne']: return None
            elif actual != expected: return None
        return dict(self.row)


class Database:
    def __init__(self, row):
        self.canonical = CanonicalBankCollection(row)
        self.general_ledger = SimpleNamespace(find_one=AsyncMock(return_value=None))
    def __getitem__(self, name):
        assert name == 'mz2_financial_accounts', 'Legacy bank reads are forbidden'
        return self.canonical


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,allowed', [({}, True), ({'account_type': 'cash'}, False),
    ({'currency': 'USD'}, False), ({'status': 'inactive'}, False),
    ({'user_id': 'other-owner'}, False), (None, False)],
    ids=['sar-bank', 'cash', 'foreign-currency', 'inactive', 'foreign-owner', 'legacy-only'])
async def test_final_provider_post_requires_canonical_sar_bank(monkeypatch, changes, allowed):
    row = None if changes is None else {
        'user_id': 'owner', 'id': 'bank-1', 'name': 'Canonical bank',
        'account_type': 'bank', 'status': 'active', 'currency': 'SAR', **changes}
    db = Database(row)
    post = AsyncMock(return_value={'txn_group_id': 'test-group', 'entries': []})
    audit = AsyncMock()
    balances = AsyncMock(return_value=SimpleNamespace(net_balance=lambda **kwargs: 100))
    monkeypatch.setattr(service, 'post_txn_group', post)
    monkeypatch.setattr(service, 'write_audit', audit)
    monkeypatch.setattr(service, 'read_mz2_write_balances', balances)
    draft = {'id': 'test-draft', 'status': 'reviewed', 'provider': 'salla',
             'bank_account_id': 'bank-1', 'idempotency_key': 'test-post-bank',
             'amounts': {'gross_sales': 100, 'reported_net': 100}, 'review_reasons': []}
    if allowed:
        result = await service._post_reviewed_settlement_transaction(
            db, owner_id='owner', actor={'id': 'owner'}, draft=draft)
        assert result['bank_snapshot']['id'] == 'bank-1'
        assert result['bank_snapshot']['account_type'] == 'bank'
        post.assert_awaited_once()
        entries = post.await_args.kwargs['entries']
        assert sum(r['amount'] for r in entries if r['side'] == 'debit') == 100
        assert sum(r['amount'] for r in entries if r['side'] == 'credit') == 100
    else:
        with pytest.raises(HTTPException) as error:
            await service._post_reviewed_settlement_transaction(
                db, owner_id='owner', actor={'id': 'owner'}, draft=draft)
        assert error.value.status_code == 400
        post.assert_not_awaited()
        audit.assert_not_awaited()
        balances.assert_not_awaited()
