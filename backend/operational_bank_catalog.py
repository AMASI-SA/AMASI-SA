"""Bank/terminal identities for operational cash flows; never imports balances.

Native IDs remain unchanged. Other catalogs use namespaced IDs, never name-based
matching. Operational identities do not create or activate accounting accounts.
"""
from operational_balance_sources import MAX_ROWS, usable

IDENTITY_FIELDS = ('id', 'user_id', 'name', 'display_name', 'currency', 'account_type',
                   'status', 'active', 'is_active', 'archived', 'deleted',
                   'is_archived', 'is_deleted')
PROJECTION = {'_id': 0, **{key: 1 for key in IDENTITY_FIELDS}}


async def bank_identities(db, owner):
    if not owner:
        raise ValueError('operational_source_rejected')
    result, seen = [], set()
    for collection, prefix in (('mz2_financial_accounts', ''), ('accounts', 'account:'),
                               ('mz2_operational_bank_accounts', 'operational-bank:')):
        records = await db[collection].find({'user_id': owner}, PROJECTION).to_list(MAX_ROWS + 1)
        if len(records) > MAX_ROWS:
            raise ValueError('operational_source_scope_too_large')
        for row in records:
            if row.get('user_id') != owner:
                raise ValueError('operational_source_owner_mismatch')
            if row.get('account_type') != 'bank' or row.get('status') != 'active' or not usable(row):
                continue
            identity = row.get('id')
            if not isinstance(identity, str) or not identity or len(prefix + identity) > 200:
                raise ValueError('operational_entity_identity_ambiguous')
            identity = prefix + identity
            if identity in seen:
                raise ValueError('operational_entity_identity_ambiguous')
            seen.add(identity)
            result.append({key: row[key] for key in IDENTITY_FIELDS if key in row} | {'id': identity})
    return result
