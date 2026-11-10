import pytest

from integrations.qoyod.api_client import QoyodAPIError
from integrations.qoyod.fresh_start_audit import _paginate


@pytest.mark.asyncio
@pytest.mark.parametrize('tail,total,expected', [
    ('We found nothing', None, True),
    ("{'error': 'We found nothing'}", None, True),
    ('URL not found', None, False),
    ('We found nothing', 3, False),
    ('We could not retrieve your Invoices, we found nothing.', None, True),
    ('We could not retrieve your Invoices, we found nothing.', 3, False),
])
async def test_full_final_page_requires_confirmed_end_and_consistent_total(tail, total, expected):
    async def fetch(*, page, limit):
        if page == 1:
            result = {'invoices': [{'id': 1}, {'id': 2}]}
            if total is not None:
                result['meta'] = {'total': total}
            return result
        raise QoyodAPIError(404, 'qoyod_not_found', 'not used', response_excerpt=tail,
                            endpoint='GET /invoices')
    evidence = {}
    result = await _paginate(fetch, page_size=2, max_pages=3,
                             extract_keys=('invoices',), evidence=evidence)
    assert len(result) == 2
    assert evidence['complete'] is expected
    assert evidence['unique_ids'] == 2


@pytest.mark.asyncio
async def test_reported_total_can_complete_a_full_page_without_extra_request():
    calls = []
    async def fetch(*, page, limit):
        calls.append(page)
        return {'invoices': [{'id': 1}, {'id': 2}], 'meta': {'total': 2}}
    evidence = {}
    await _paginate(fetch, page_size=2, max_pages=3, extract_keys=('invoices',), evidence=evidence)
    assert calls == [1]
    assert evidence['complete'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['first_404', 'repeated', 'cap', 'short_with_missing_total'])
async def test_incomplete_or_ambiguous_pages_never_prove_absence(kind):
    async def fetch(*, page, limit):
        if kind == 'first_404':
            raise QoyodAPIError(404, 'qoyod_not_found', '', response_excerpt='We found nothing')
        if kind == 'short_with_missing_total':
            return {'invoices': [{'id': 1}], 'meta': {'total': 3}}
        if kind == 'repeated':
            return {'invoices': [{'id': 1}, {'id': 2}]}
        return {'invoices': [{'id': page * 2}, {'id': page * 2 + 1}]}
    evidence = {}
    await _paginate(fetch, page_size=2, max_pages=2, extract_keys=('invoices',), evidence=evidence)
    assert evidence['complete'] is False
    assert evidence['completion_reason']


@pytest.mark.asyncio
async def test_incomplete_sync_exposes_bounded_counts_without_provider_data():
    from types import SimpleNamespace
    from mongomock_motor import AsyncMongoMockClient
    from integrations.qoyod.qoyod_invoices_sync import sync_qoyod_invoices
    from integrations.qoyod.scheduled_scan import ScanFailure

    async def fetch(**kwargs):
        return {'invoices': [{'id': 1, 'issue_date': '2026-07-01',
                              'notes': 'SECRET'}], 'meta': {'total': 3}}
    result = await sync_qoyod_invoices(AsyncMongoMockClient().db, user_id='main',
                                     api_client=SimpleNamespace(list_invoices=fetch), page_size=2)
    failure = ScanFailure('provider_refresh', result['diagnostic'])
    assert failure.diagnostic['completion_reason'] == 'missing_rows'
    assert failure.diagnostic['unique_ids'] == 1
    assert failure.diagnostic['expected_count'] == 3
    assert 'SECRET' not in str(failure.diagnostic)
