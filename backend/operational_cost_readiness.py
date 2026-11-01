"""Read-only owner-scoped readiness inspection; no financial writer capability."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

from operational_balance_sources import collect_sources, ALLOWED_COLLECTIONS, MAX_ROWS
from operational_balance_engine import reconcile
from operational_open_cost_exposure import project


class ReadOnlyCursor:
    def __init__(self, cursor):
        self.__cursor = cursor

    async def to_list(self, count):
        if not isinstance(count, int) or not 0 < count <= MAX_ROWS + 1:
            raise ValueError('readiness_read_limit_invalid')
        return await self.__cursor.to_list(count)


class ReadOnlyCollection:
    def __init__(self, collection, owner):
        self.__collection = collection
        self.__owner = owner

    def find(self, query):
        if query != {'user_id': self.__owner}:
            raise ValueError('readiness_owner_scope_invalid')
        return ReadOnlyCursor(self.__collection.find(query))


class ReadOnlySources:
    """Only scoped find/to_list are reachable, not update/insert/delete or command."""
    def __init__(self, db, owner):
        self.__db = db
        self.__owner = owner

    def __getitem__(self, name):
        if name not in ALLOWED_COLLECTIONS or name in {
            'users', 'settings', 'accounts', 'mz2_operational_bank_accounts'
        }:
            raise ValueError('readiness_collection_rejected')
        return ReadOnlyCollection(self.__db[name], self.__owner)


def summarize_issues(items):
    counts = Counter()
    for item in items:
        if isinstance(item, str):
            code, count = item, 1
        elif isinstance(item, dict):
            code, count = item.get('code') or 'unknown_issue', item.get('count', 1)
        else:
            code, count = 'unknown_issue', 1
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            count = 1
        counts[str(code)] += count
    return [{'code': code, 'count': count}
            for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


async def inspect_readiness(db, owner, state, *, as_of=None):
    if (not owner or state.get('owner_id') != owner or state.get('_id') != owner
            or state.get('status') != 'active' or not state.get('started_at')):
        raise ValueError('readiness_active_owner_state_required')
    before = deepcopy(state)
    now = as_of or datetime.now(timezone.utc).isoformat()
    readonly = ReadOnlySources(db, owner)
    sources = await collect_sources(readonly, owner, state['started_at'], now,
        baselines=state.get('source_baselines'), operational_state=state)
    simulated = reconcile(state, sources, now)
    # Native refresh sets this observation timestamp when persisting; this
    # private simulation sets it in memory only, never via a DB mutation.
    simulated['engine']['as_of'] = now
    exposure = await project(readonly, owner, simulated, as_of=now)
    if state != before or simulated.get('openings') != state.get('openings') or simulated.get('movements') != state.get('movements'):
        raise ValueError('readiness_input_changed')
    current_issues = summarize_issues(simulated['engine'].get('issues', []))
    exposure_issues = summarize_issues(exposure.get('issues', []))
    informational = {'historical_current_catalog_estimate_used'}
    blocking = current_issues + [item for item in exposure_issues if item['code'] not in informational]
    return {
        'schema': 'mezan_operational_readiness_read_only_v1',
        'mode': 'read_only_in_memory_simulation', 'as_of': now,
        'owner_scope_fingerprint': sha256(owner.encode()).hexdigest()[:12],
        'state_revision': state.get('revision'), 'started_at': state['started_at'],
        'opening_count': len(state.get('openings', {})),
        'movement_count': len(state.get('movements', [])),
        'openings_unchanged': True, 'movements_unchanged': True,
        'database_writes': False, 'published': False,
        'normalized_order_count': len(sources.get('orders', [])),
        'product_cost_currency': exposure.get('product_cost_currency'),
        'component_complete': exposure.get('component_complete'),
        'cost_totals': exposure.get('currencies'),
        'financial_source_issues': current_issues,
        'cost_preview_issues': exposure_issues,
        'ready_for_operational_use': not blocking and exposure.get('status') == 'preview',
        'note': 'Counts are issue occurrences, not distinct orders. This inspection is not a database transaction or a publication proof.'
    }
