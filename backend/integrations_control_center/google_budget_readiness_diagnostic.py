"""Bounded, read-only snapshot diagnostics for legacy Google execution state.

No route, configuration, secrets, network/provider calls, or mutations. The
caller supplies an already-authorized database object; this function never
identifies that database as production and cannot certify migration readiness.
"""
import asyncio
from collections import Counter
from datetime import datetime, timezone

from pymongo.read_concern import ReadConcern
from pymongo import ReadPreference

COLLECTIONS = {
    'standing': ('mezan_google_ads_automation_policies_v1', {'user_id': 1, 'account_id': 1, '_dispatch_claim': 1}),
    'creation': ('mezan_google_ads_automation_creation_policies_v1', {'user_id': 1, 'account_id': 1, '_creation_claim': 1, '_dispatch_claim': 1, '_creation_ledger': 1}),
    'fences': ('mezan_google_ads_execution_fences_v1', {'user_id': 1, 'account_id': 1, 'status': 1, 'execution_claim_id': 1}),
    'guards': ('mezan_google_ads_automation_budget_guards_v1', {'user_id': 1, 'account_id': 1, 'active_claim': 1, 'allocations': 1}),
    'proposals': ('mezan_google_ads_proposals_v1', {'user_id': 1, 'account_id': 1, 'status': 1, 'submission_started': 1, 'execution_claim_id': 1}),
}
MAX_ROWS = 2000
MAX_ALLOCATIONS = 100


def _scope(row):
    a, b = row.get('user_id'), row.get('account_id')
    return (a, b) if isinstance(a, str) and a and isinstance(b, str) and b else None


def _nonempty(value):
    return isinstance(value, str) and bool(value)


def _analyze(rows):
    counts = Counter()
    guards = {}
    malformed = False
    for row in rows['guards']:
        scope = _scope(row)
        allocs = row.get('allocations')
        if scope is None or scope in guards or not isinstance(allocs, dict) or len(allocs) > MAX_ALLOCATIONS:
            malformed = True
            continue
        guards[scope] = allocs
        if row.get('active_claim') is not None:
            counts['active_guard_claims'] += 1
        for entry in allocs.values():
            if not isinstance(entry, dict) or entry.get('state') not in {'reserved', 'native_enabled', 'cancelled_before_http', 'settled'}:
                malformed = True
            elif entry['state'] in {'reserved', 'native_enabled'}:
                counts['outstanding_allocations'] += 1
    for kind, fields in [('standing', ('_dispatch_claim',)), ('creation', ('_dispatch_claim', '_creation_claim'))]:
        seen = set()
        for row in rows[kind]:
            scope = _scope(row)
            if scope is None or scope in seen:
                malformed = True
                continue
            seen.add(scope)
            if kind == 'creation':
                ledger = row.get('_creation_ledger', {})
                if not isinstance(ledger, dict) or len(ledger) > MAX_ALLOCATIONS:
                    malformed = True
                else:
                    for key, entry in ledger.items():
                        if (not isinstance(entry, dict) or entry.get('creation_identity') != key
                            or entry.get('user_id') != scope[0] or entry.get('account_id') != scope[1]
                            or entry.get('state') not in {'reserved', 'owned', 'no_campaign'}):
                            malformed = True
                        elif entry['state'] == 'reserved':
                            counts['nonterminal_creation_ledger_entries'] += 1
                            if row.get('_creation_claim') != entry:
                                counts['nonterminal_ledger_without_matching_claim'] += 1
            for field in fields:
                claim = row.get(field)
                if claim is None:
                    continue
                counts['private_claims'] += 1
                if not isinstance(claim, dict):
                    malformed = True
                    continue
                claim_id = claim.get('execution_claim_id') or claim.get('claim_id')
                if not _nonempty(claim_id):
                    malformed = True
                    continue
                # Correlation is only a presence diagnostic, never a recovery proof.
                matched = any(isinstance(entry, dict) and entry.get('execution_claim_id') == claim_id
                              and entry.get('state') == 'reserved' for entry in guards.get(scope, {}).values())
                counts['private_claims_with_reserved_record' if matched else 'private_claims_without_reserved_record'] += 1
    for kind in ('fences', 'proposals'):
        for row in rows[kind]:
            if _scope(row) is None or not _nonempty(row.get('status')):
                malformed = True
                continue
            if kind == 'fences':
                if row['status'] not in {'claimed', 'released'}:
                    malformed = True
                elif row['status'] == 'claimed':
                    counts['claimed_fences'] += 1
            if kind == 'proposals':
                if row['status'] not in {'previewed', 'executing', 'approved', 'submitted', 'verifying', 'uncertain', 'completed', 'failed'}:
                    malformed = True
                if row['status'] in {'executing', 'approved', 'submitted', 'verifying', 'uncertain'} or (row.get('submission_started') is True and row['status'] != 'completed'):
                    counts['unresolved_proposals'] += 1
    return counts, malformed


async def _scan(db, max_rows):
    rows = {}
    async with await db.client.start_session() as session:
        session.start_transaction(read_concern=ReadConcern('snapshot'), read_preference=ReadPreference.PRIMARY)
        try:
            for name, (collection, projection) in COLLECTIONS.items():
                # One cursor per collection inside one snapshot. limit+1 proves
                # whether this bounded traversal exhausted each collection.
                cursor = db[collection].find({}, {'_id': 0, **projection}, session=session).limit(max_rows + 1).batch_size(min(100, max_rows + 1))
                docs = await cursor.to_list(length=max_rows + 1)
                if len(docs) > max_rows:
                    return {'complete': False, 'reason': 'row_limit', 'migration_safe': False, 'database_identity_verified': False, 'provider_writes': 0}
                rows[name] = docs
            counts, malformed = _analyze(rows)
            return {'complete': not malformed, 'reason': 'malformed_or_duplicate_state' if malformed else None,
                'snapshot_at': datetime.now(timezone.utc).isoformat(),
                'counts': {k: counts[k] for k in ('private_claims', 'private_claims_with_reserved_record', 'private_claims_without_reserved_record', 'nonterminal_creation_ledger_entries', 'nonterminal_ledger_without_matching_claim', 'claimed_fences', 'unresolved_proposals', 'active_guard_claims', 'outstanding_allocations')},
                'migration_safe': False, 'database_identity_verified': False, 'provider_writes': 0}
        finally:
            await session.abort_transaction()


async def diagnose(db, *, max_rows=MAX_ROWS, timeout_seconds=15):
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 30:
        raise ValueError('invalid diagnostic bounds')
    try:
        return await asyncio.wait_for(_scan(db, max_rows), timeout_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never echo connection details, identities or exception contents.
        return {'complete': False, 'reason': 'snapshot_unavailable', 'migration_safe': False, 'database_identity_verified': False, 'provider_writes': 0}
