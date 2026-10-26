import pytest
from integrations_control_center.google_budget_readiness_diagnostic import COLLECTIONS, diagnose

class Session:
    def __init__(self): self.aborted = False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def start_transaction(self, **kwargs): self.options = kwargs
    async def abort_transaction(self): self.aborted = True
class Cursor:
    def __init__(self, rows): self.rows = rows
    def limit(self, n): self.bound = n; return self
    def batch_size(self, n): return self
    async def to_list(self, length): return self.rows[:min(length, self.bound)]
class Collection:
    def __init__(self, rows, session): self.rows, self.session = rows, session
    def find(self, query, projection, *, session):
        assert query == {} and session is self.session and projection['_id'] == 0
        return Cursor(self.rows)
class Client:
    def __init__(self, session): self.session = session
    async def start_session(self): return self.session
class DB:
    def __init__(self, rows=None): self.rows = rows or {}; self.session = Session(); self.client = Client(self.session)
    def __getitem__(self, name): return Collection(self.rows.get(name, []), self.session)

def coll(key): return COLLECTIONS[key][0]
def row(**kwargs): return {'user_id': 'private_owner', 'account_id': 'private_account', **kwargs}

@pytest.mark.asyncio
async def test_readonly_empty_snapshot_does_not_certify_migration_or_production():
    db = DB(); result = await diagnose(db)
    assert result['complete'] is False and result['inventory_exhausted'] and not result['migration_safe'] and not result['database_identity_verified']
    assert result['counts']['private_claims'] == 0 and db.session.aborted
    assert db.session.options['read_concern'].level == 'snapshot'

@pytest.mark.asyncio
async def test_legacy_claim_and_fence_are_counted_without_leaking_identity():
    db = DB({coll('standing'): [row(_dispatch_claim={'execution_claim_id': 'UNIQUE_CLAIM_X'})],
             coll('fences'): [row(status='claimed', execution_claim_id='UNIQUE_CLAIM_X')]})
    result = await diagnose(db)
    assert result['inventory_exhausted'] and not result['complete'] and result['counts']['private_claims_without_reserved_record'] == 1
    assert result['counts']['claimed_fences'] == 1
    assert all(secret not in repr(result) for secret in ('private_owner', 'private_account', 'UNIQUE_CLAIM_X')) and db.session.aborted

@pytest.mark.asyncio
async def test_guard_matching_stays_scoped_and_pending_is_never_safe():
    db = DB({coll('standing'): [row(_dispatch_claim={'execution_claim_id': 'claim'})],
             coll('guards'): [row(active_claim={'execution_claim_id': 'claim'}, allocations={'a': {'execution_claim_id': 'claim', 'state': 'reserved'}})]})
    result = await diagnose(db)
    assert result['counts']['private_claims_without_reserved_record'] == 0
    assert result['counts']['outstanding_allocations'] == 1 and not result['migration_safe']

@pytest.mark.asyncio
async def test_truncation_is_fail_closed_and_provides_no_partial_counts():
    db = DB({coll('standing'): [row(), row()]})
    result = await diagnose(db, max_rows=1)
    assert result['reason'] == 'row_limit' and result['complete'] is False
    assert 'counts' not in result and db.session.aborted

@pytest.mark.asyncio
async def test_database_exception_is_sanitized_and_incomplete():
    db = DB(); db.client.start_session = lambda: (_ for _ in ()).throw(RuntimeError('secret'))
    result = await diagnose(db)
    assert result['reason'] == 'snapshot_unavailable' and 'secret' not in repr(result)

@pytest.mark.asyncio
async def test_malformed_or_duplicate_scope_cannot_prove_completeness():
    db = DB({coll('standing'): [row(), row()]})
    result = await diagnose(db)
    assert not result['complete'] and result['reason'] == 'malformed_or_duplicate_state'

@pytest.mark.asyncio
async def test_uncertain_and_failed_after_submission_remain_unresolved():
    db = DB({coll('proposals'): [row(status='uncertain', submission_started=True), row(status='failed', submission_started=True)]})
    result = await diagnose(db)
    assert result['counts']['unresolved_proposals'] == 2 and not result['migration_safe']

@pytest.mark.asyncio
async def test_unknown_fence_status_is_unproven():
    result = await diagnose(DB({coll('fences'): [row(status='future_state')]}))
    assert not result['complete']

@pytest.mark.asyncio
async def test_reserved_ledger_without_claim_is_reported():
    entry = row(creation_identity='identity', state='reserved')
    result = await diagnose(DB({coll('creation'): [row(_creation_ledger={'identity': entry})]}))
    assert result['inventory_exhausted'] and not result['complete'] and result['counts']['nonterminal_creation_ledger_entries'] == 1
    assert result['counts']['nonterminal_ledger_without_matching_claim'] == 1

@pytest.mark.asyncio
async def test_terminal_allocation_is_not_counted_as_reserved_record():
    db = DB({coll('standing'): [row(_dispatch_claim={'execution_claim_id': 'claim'})],
             coll('guards'): [row(allocations={'a': {'execution_claim_id': 'claim', 'state': 'settled'}})]})
    result = await diagnose(db)
    assert result['counts']['private_claims_with_reserved_record'] == 0
    assert result['counts']['private_claims_without_reserved_record'] == 1

@pytest.mark.asyncio
async def test_unknown_ledger_state_is_incomplete():
    result = await diagnose(DB({coll('creation'): [row(_creation_ledger={'x': row(creation_identity='x', state='future')})]}))
    assert not result['complete']

@pytest.mark.asyncio
async def test_plausible_but_incomplete_ledger_never_claims_full_authority_validation():
    entry = row(creation_identity='x', state='owned')
    result = await diagnose(DB({coll('creation'): [row(_creation_ledger={'x': entry})]}))
    assert result['inventory_exhausted'] and not result['complete']
    assert result['structural_validation'] == 'limited_not_authority'
    assert not result['migration_safe'] and not result['database_identity_verified']
