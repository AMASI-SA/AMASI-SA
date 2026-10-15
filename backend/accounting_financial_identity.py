"""Read-only bank/cash identity contract for all MZ2 operational consumers.

No Legacy lookup, balance migration, default currency or setup mutation. Pass the
transaction-bound database through unchanged when resolving for a financial write.
"""
from fastapi import HTTPException

COLLECTION = 'mz2_financial_accounts'
BINDING_IDENTITY_VERSION = 1
FIELDS = {key: 1 for key in ('user_id', 'id', 'name', 'account_type', 'currency',
                            'status', 'version', 'archived', 'is_archived',
                            'deleted', 'is_deleted', 'active', 'is_active')}


def _query(owner, account_types, currency):
    types = tuple(account_types)
    if not types or any(t not in {'bank', 'cash'} for t in types):
        raise ValueError('canonical_bank_cash_type_required')
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ValueError('explicit_currency_required')
    return {'user_id': owner, 'status': 'active', 'account_type': {'$in': types},
            'currency': currency,
            **{key: {'$ne': True} for key in ('archived','is_archived','deleted','is_deleted')},
            **{key: {'$ne': False} for key in ('active','is_active')}}


async def find_financial_account(db, owner, financial_account_id, *,
                                 account_types=('bank', 'cash'), currency='SAR'):
    """Resolve an exact owner-scoped canonical FK or return None. Never aliases."""
    if not isinstance(owner, str) or not owner or not isinstance(financial_account_id, str) or not financial_account_id:
        return None
    query = _query(owner, account_types, currency)
    query['id'] = financial_account_id
    return await db[COLLECTION].find_one(query, {'_id': 0, **FIELDS})


async def list_financial_accounts(db, owner, *, account_types=('bank', 'cash'), currency='SAR'):
    """Selectable accounts for an explicit currency; fail closed on truncation."""
    if not isinstance(owner, str) or not owner:
        return []
    rows = await db[COLLECTION].find(_query(owner, account_types, currency),
                                     {'_id': 0, **FIELDS}).to_list(1001)
    if len(rows) > 1000:
        raise HTTPException(409, detail={'code': 'MZ2_FINANCIAL_ACCOUNT_SCOPE_TOO_LARGE'})
    ids = [r.get('id') for r in rows]
    if any(not isinstance(id, str) or not id for id in ids) or len(set(ids)) != len(ids):
        raise HTTPException(409, detail={'code': 'MZ2_FINANCIAL_ACCOUNT_IDENTITY_INVALID'})
    return sorted(rows, key=lambda r: r['id'])


def _ledger_identity(account):
    # Use the established opening mapping: cash is bank/<canonical id>/main,
    # not a separate cash ledger entity. This is identity, never authorization.
    from accounting_financial_accounts import FINANCIAL_ACCOUNT_RULES
    rule = FINANCIAL_ACCOUNT_RULES[account['account_type']]
    return {**{key: account[key] for key in ('id', 'account_type', 'currency', 'status')},
            'entity_type': rule['entity_type'], 'entity_id': account['id'],
            'sub_account': rule['sub_account']}


async def require_financial_ledger_identity(db, owner, financial_account_id, *,
                                            account_types=('bank', 'cash'), currency='SAR'):
    """Require a canonical bank/cash ledger key; no read grants write authority.

    Pass a transaction-bound db unchanged when called inside a writer. The
    caller still owns actor permission, cutover, pause and posting gates.
    """
    account = await find_financial_account(db, owner, financial_account_id,
                                           account_types=account_types, currency=currency)
    if account is None:
        raise HTTPException(409, detail={'code': 'MZ2_LINK_REQUIRED'})
    return _ledger_identity(account)


async def list_financial_ledger_identities(db, owner, *,
                                          account_types=('bank', 'cash'), currency='SAR'):
    """Read-only selectable canonical identities with their existing ledger keys."""
    accounts = await list_financial_accounts(db, owner, account_types=account_types,
                                             currency=currency)
    return [_ledger_identity(account) for account in accounts]
