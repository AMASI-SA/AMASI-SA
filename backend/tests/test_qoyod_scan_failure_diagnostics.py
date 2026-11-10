import asyncio
from types import SimpleNamespace

import pytest
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod import scheduled_scan as scans
from integrations.qoyod.api_client import QoyodAPIError
from integrations.qoyod.qoyod_invoices_sync import sync_qoyod_invoices


@pytest.mark.asyncio
async def test_provider_failure_retains_safe_cause_without_response_or_credentials():
    db = AsyncMongoMockClient().db

    async def fail(**kwargs):
        raise QoyodAPIError(status_code=429, code='qoyod_rate_limited',
                            message='SECRET customer payload', endpoint='GET /invoices',
                            response_excerpt='SECRET api key')

    result = await sync_qoyod_invoices(db, user_id='main',
                                     api_client=SimpleNamespace(list_invoices=fail))
    assert not result['ok']
    assert result['diagnostic'] == {'stage': 'provider_read',
                                    'error_type': 'QoyodAPIError', 'http_status': 429}
    assert 'SECRET' not in str(result)


@pytest.mark.asyncio
async def test_scan_failure_keeps_safe_diagnostic_and_last_good_result():
    db = AsyncMongoMockClient().db
    saved = {'orders_user_id': 'owner', 'report': {'ok': True},
             'slot': scans.slot_key(scans.utcnow())}
    await db.qoyod_full_scans.insert_one({'_id': 'main', 'last_success': saved})

    async def fail(*args, **kwargs):
        raise scans.ScanFailure('provider_refresh', {
            'stage': 'provider_read', 'error_type': 'QoyodAPIError',
            'http_status': 429, 'message': 'SECRET', 'response': 'SECRET'})

    await scans.request_scan(db, tenant='main', owner='owner', manual=True, executor=fail)
    await asyncio.gather(*list(scans._TASKS))
    state = await db.qoyod_full_scans.find_one({'_id': 'main'})
    assert state['last_success'] == saved
    assert state['last_failure']['stage'] == 'provider_refresh'
    assert state['last_failure']['http_status'] == 429
    assert state['last_failure']['error_type'] == 'QoyodAPIError'
    assert 'SECRET' not in str(state)
    assert state['status'] == 'failed'
