from datetime import datetime, timezone

from integrations.qoyod.invoice_presence import verified_unsent_summary


NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
SNAPSHOT = {"complete": True, "started_at": NOW.isoformat(),
            "sync_start": "2026-07-01"}


def proof(number, **extra):
    return {"order_number": number, "order_date": "2026-10-01", **extra}


def test_missing_or_incomplete_provider_snapshot_never_claims_absence():
    for snapshot in (None, {}, {**SNAPSHOT, "complete": False}):
        result = verified_unsent_summary([proof("1")], snapshot, now=NOW)
        assert result["count"] is None
        assert result["verification_required"] == 1


def test_existing_invoices_and_uncertain_markers_are_not_unsent():
    rows = [proof("1", qoyod_invoice={"qoyod_invoice_id": "90", "remaining": 10}),
            proof("2", manual_qoyod_invoice_id="91"), proof("3"), proof("3")]
    result = verified_unsent_summary(rows, SNAPSHOT, now=NOW)
    assert result["count"] == 1
    assert result["order_numbers"] == ["3"]
    assert result["verification_required"] == 1


def test_stale_or_truncated_snapshot_and_new_orders_remain_unknown():
    assert verified_unsent_summary([proof("1")], SNAPSHOT,
        now=datetime(2026, 11, 6, tzinfo=timezone.utc))["count"] is None
    assert verified_unsent_summary([proof("1")], SNAPSHOT,
        now=NOW, scan_truncated=True)["count"] is None
    result = verified_unsent_summary([proof("1", order_date="2026-11-05")], SNAPSHOT, now=NOW)
    assert result["count"] == 0
    assert result["verification_required"] == 1


def test_narrow_sync_does_not_prove_absence_for_older_orders():
    result = verified_unsent_summary([proof("1")],
        {**SNAPSHOT, "sync_start": "2026-11-01"}, now=NOW)
    assert result["count"] == 0
    assert result["verification_required"] == 1


import pytest
from integrations.qoyod.fresh_start_audit import _paginate


@pytest.mark.asyncio
@pytest.mark.parametrize('response,complete', [
    ({'invoices': []}, True), ({'error': 'unavailable'}, False),
    ({'invoices': [{'id': 1}]}, True),
    ({'invoices': [{'reference': '1'}]}, False),
])
async def test_pagination_proof_requires_valid_provider_response(response, complete):
    async def fetch(**kwargs):
        return response
    evidence = {}
    await _paginate(fetch, page_size=2, max_pages=2,
                    extract_keys=('invoices', 'data', 'items'), evidence=evidence)
    assert evidence['complete'] is complete


@pytest.mark.asyncio
async def test_repeated_page_and_pagination_ceiling_cannot_prove_absence():
    async def fetch(**kwargs):
        return {'invoices': [{'id': 1}, {'id': 2}]}
    for max_pages in (1, 2):
        evidence = {}
        await _paginate(fetch, page_size=2, max_pages=max_pages,
                        extract_keys=('invoices',), evidence=evidence)
        assert evidence['complete'] is False


@pytest.mark.asyncio
async def test_proof_collection_preserves_legacy_sync_rows_and_page_count():
    pages = {1: {'invoices': [{'id': 1}, {'id': 2}]},
             2: {'invoices': [{'id': 1}, {'id': 2}]},
             3: {'invoices': [{'id': 3}]}}
    calls = []
    async def fetch(page, **kwargs):
        calls.append(page)
        return pages[page]
    legacy = await _paginate(fetch, page_size=2, max_pages=3,
                             extract_keys=('invoices',))
    assert calls == [1, 2, 3]
    calls.clear()
    evidence = {}
    observed = await _paginate(fetch, page_size=2, max_pages=3,
                               extract_keys=('invoices',), evidence=evidence)
    assert calls == [1, 2, 3]
    assert observed == legacy
    assert evidence['complete'] is False


@pytest.mark.asyncio
async def test_complete_sync_records_evidence_but_partial_sync_invalidates_it():
    from mongomock_motor import AsyncMongoMockClient
    from integrations.qoyod.qoyod_invoices_sync import sync_qoyod_invoices
    db = AsyncMongoMockClient().db
    class Client:
        async def list_invoices(self, **kwargs):
            return {'invoices': [{'id': 1, 'reference': '123', 'issue_date': '2026-10-01'}]}
    await sync_qoyod_invoices(db, user_id='merchant-a', api_client=Client())
    snapshot = (await db.qoyod_settings.find_one({'user_id': 'merchant-a'}))['invoice_presence_snapshot']
    assert snapshot['complete'] is True
    assert await db.qoyod_settings.find_one({'user_id': 'merchant-b'}) is None
    class FailedClient:
        async def list_invoices(self, **kwargs):
            raise TimeoutError('provider unavailable')
    result = await sync_qoyod_invoices(db, user_id='merchant-a', api_client=FailedClient())
    assert result['ok'] is False
    snapshot = (await db.qoyod_settings.find_one({'user_id': 'merchant-a'}))['invoice_presence_snapshot']
    assert snapshot['complete'] is False


@pytest.mark.asyncio
async def test_header_count_and_list_share_evidence_and_filter_before_limit():
    from mongomock_motor import AsyncMongoMockClient
    from qoyod_auto_unified.queue_api import _execute_list
    db = AsyncMongoMockClient().db
    await db.qoyod_settings.insert_one({'user_id': 'merchant-a', 'invoice_presence_snapshot': SNAPSHOT})
    proofs = [proof('1', qoyod_invoice={'qoyod_invoice_id': '91', 'status': 'paid'}),
              proof('2'), proof('3', manual_qoyod_invoice_id='92')]
    async def original(db, **kwargs):
        assert kwargs['_include_internal'] is True
        return {'orders': [{'order_number': p['order_number']} for p in proofs],
                '_candidate_audit': {'orders': proofs, 'eligible_references': {'1','2','3'}},
                '_manual_failures': {}}
    result = await _execute_list(original, db, user_id='merchant-a', now=NOW,
                                 limit=1, status='verified_absent')
    assert result['invoice_presence']['count'] == 1
    assert [r['order_number'] for r in result['orders']] == ['2']
    assert result['matched_order_count'] == 1
    assert result['truncated'] is False
    other = await _execute_list(original, db, user_id='merchant-b', now=NOW,
                                status='verified_absent')
    assert other['invoice_presence']['count'] is None
    assert other['orders'] == []
