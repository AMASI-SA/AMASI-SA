from datetime import datetime, timezone

import pytest

from integrations_control_center.google_order_source_coverage import read_source_date_coverage


class Cursor:
    def __init__(self, rows): self.rows = rows
    def limit(self, n): self.n = n; return self
    def max_time_ms(self, n): assert n == 3000; return self
    async def to_list(self, length): return self.rows[:min(length, self.n)]


class Collection:
    def __init__(self, rows): self.rows = rows; self.query = None; self.projection = None
    def find(self, query, projection):
        self.query, self.projection = query, projection
        return Cursor([r for r in self.rows if r['user_id'] == query['user_id'] and query['order_date']['$gte'] <= r['order_date'] <= query['order_date']['$lte']])


def order(owner='owner', raw_date='2026-08-14T21:30:00Z', normalized='2026-08-14'):
    return {'user_id': owner, 'order_id': '42', 'order_number': '77', 'order_date': normalized,
            'raw_by_source': {'salla_direct': {'id': 42, 'reference_id': 77, 'created_at': raw_date}}}


async def read(rows, limit=10):
    collection = Collection(rows)
    result = await read_source_date_coverage({'unified_orders': collection}, owner_id='owner',
        date_from='2026-08-15', date_to='2026-08-15', as_of=datetime(2026, 8, 17, tzinfo=timezone.utc), limit=limit)
    return result, collection


@pytest.mark.asyncio
async def test_bounded_discovery_uses_riyadh_instant_and_never_claims_complete():
    result, coll = await read([order(), order(owner='other')])
    assert coll.query == {'user_id': 'owner', 'order_date': {'$gte': '2026-08-14', '$lte': '2026-08-16'},
                          '$expr': {'$lte': [{'$bsonSize': '$$ROOT'}, 16384]}}
    assert result['observed_bound_inside'] == 1 and result['coverage_complete'] is False
    assert result['sales'] is None and result['snapshot_consistent'] is False
    assert 'oversized_documents_excluded' in result['limitations']
    assert 'raw_by_source.salla_direct.created_at' in coll.projection and 'products' not in coll.projection


@pytest.mark.asyncio
async def test_unknown_and_outside_are_separate_without_order_or_customer_payload():
    unknown = order(raw_date='2026-08-15')
    outside = order(raw_date='2026-08-14T17:00:00Z')
    result, _ = await read([unknown, outside])
    assert result['observed_unverified'] == 1 and result['observed_bound_outside'] == 1
    assert '77' not in str(result) and result['coverage_complete'] is False


@pytest.mark.asyncio
async def test_truncation_hides_partial_counts_and_empty_is_not_zero_sales():
    result, _ = await read([order(), order()], limit=1)
    assert result['truncated'] and result['observed_bound_inside'] is None and result['sales'] is None
    result, _ = await read([])
    assert result['observed_bound_inside'] == 0 and result['sales'] is None and not result['coverage_complete']


@pytest.mark.asyncio
async def test_current_open_day_and_unbounded_inputs_are_rejected():
    with pytest.raises(ValueError):
        await read_source_date_coverage({'unified_orders': Collection([])}, owner_id='owner', date_from='2026-08-17', date_to='2026-08-17', as_of=datetime(2026, 8, 17, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_candidate_drift_never_becomes_proof_of_absence():
    result, _ = await read([order(normalized='2026-08-10')])
    assert result['observed_bound_inside'] == 0 and not result['coverage_complete'] and result['sales'] is None


@pytest.mark.asyncio
async def test_leaky_database_result_fails_closed():
    class Leaky(Collection):
        def find(self, query, projection): return Cursor([order(owner='other')])
    with pytest.raises(ValueError, match='scope'):
        await read_source_date_coverage({'unified_orders': Leaky([])}, owner_id='owner',
            date_from='2026-08-15', date_to='2026-08-15', as_of=datetime(2026, 8, 17, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_database_timeout_does_not_become_empty_or_zero():
    from pymongo.errors import ExecutionTimeout
    class TimedOutCursor(Cursor):
        async def to_list(self, length): raise ExecutionTimeout('diagnostic timed out')
    class TimedOut(Collection):
        def find(self, query, projection): return TimedOutCursor([])
    with pytest.raises(ExecutionTimeout):
        await read_source_date_coverage({'unified_orders': TimedOut([])}, owner_id='owner',
            date_from='2026-08-15', date_to='2026-08-15', as_of=datetime(2026, 8, 17, tzinfo=timezone.utc))
