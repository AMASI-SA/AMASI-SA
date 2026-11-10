import asyncio
from unittest.mock import AsyncMock

import pytest

from integrations.qoyod_manual import auto_cycle as cycles, auto_send


@pytest.mark.asyncio
@pytest.mark.parametrize('storage_fails', [False, True])
async def test_heartbeat_failure_cancels_batch_and_records_only_safe_cause(monkeypatch, storage_fails):
    db = AsyncMock()
    db.__getitem__.return_value = AsyncMock()
    db.qoyod_404_campaigns.find_one.return_value = None
    if storage_fails:
        db.__getitem__.return_value.update_one.side_effect = ConnectionError('SECRET')
    monkeypatch.setattr(auto_send, '_current_settings', AsyncMock(return_value={}))
    monkeypatch.setattr(auto_send, 'is_armed', lambda _: True)
    monkeypatch.setattr(cycles, '_claim', AsyncMock(return_value={
        'owner': 'lease-owner', 'cycle_id': 'cycle', 'orders_user_id': 'owner'}))
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def fail(*args):
        await entered.wait()
        raise TimeoutError('SECRET provider data')
    monkeypatch.setattr(cycles, '_drain', blocked)
    monkeypatch.setattr(cycles, '_heartbeat', fail)
    result = await cycles.run_due_cycle(db, batch_runner=AsyncMock(), use_manifest=False)
    assert result['status'] == 'lease_lost'
    assert cancelled.is_set()
    call = db.__getitem__.return_value.update_one.call_args
    db.__getitem__.return_value.update_one.assert_awaited_once()
    assert call.args[0] == {'_id': 'main', 'cycle_id': 'cycle'}
    details = call.args[1][0]['$set']['last_interruption']
    assert details['error_type'] == 'TimeoutError'
    assert details['stage'] == 'heartbeat'
    assert 'SECRET' not in str(call)
