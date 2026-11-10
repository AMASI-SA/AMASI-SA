"""Financial refresh must leave shared-worker lease heartbeats responsive."""
import asyncio
import threading
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from operational_balance_engine import reconcile
from test_operational_balance_engine import initial, order, NOW


class CountedKey(str):
    calls = 0

    def startswith(self, *args):
        type(self).calls += 1
        return super().startswith(*args)


def test_repeated_order_projection_does_not_scan_every_other_obligation():
    size = 200
    source = {'orders': [order(id=f'o{i}') for i in range(size)]}
    state = reconcile(initial(), source, NOW)
    expected = deepcopy(state)
    state['engine']['obligations'] = {
        CountedKey(key): value for key, value in state['engine']['obligations'].items()}
    CountedKey.calls = 0
    actual = reconcile(state, source, NOW)
    assert actual == expected
    assert CountedKey.calls < size * 20


def test_supplier_index_keeps_prefix_and_legacy_colon_id_boundaries():
    rows = [order(id=value) for value in ('o1', 'o10', 'o1:legacy')]
    state = reconcile(initial(), {'orders': rows}, NOW)
    rows[0] = order(id='o1', items=[])
    result = reconcile(state, {'orders': rows}, NOW)['engine']['obligations']
    assert result['supplier:o1:i1']['expected'] == '0.00'
    assert result['supplier:o10:i1']['expected'] == '200.00'
    assert result['supplier:o1:legacy:i1']['expected'] == '200.00'


def test_failed_order_does_not_leave_a_phantom_indexed_obligation():
    invalid = order(id='failed', cod_amount='invalid')
    valid = order(id='failed', items=[])
    result = reconcile(initial(), {'orders': [invalid, valid]}, NOW)
    assert 'supplier:failed:i1' not in result['engine']['obligations']
    assert result['engine']['obligations']['cod:failed']['expected'] == '500.00'


@pytest.mark.asyncio
async def test_refresh_calculation_does_not_block_unrelated_heartbeat(monkeypatch):
    import operational_balance_service as service
    import operational_balance_sources as sources
    import operational_balance_inventory as inventory
    import operational_balance_engine as engine
    import operational_customer_returns as returns

    released = threading.Event()
    state = initial()

    async def mutate(db, owner, apply):
        return await apply(state)

    def slow_calculation(*args):
        assert released.wait(2), 'financial calculation blocked the heartbeat loop'
        return {'engine': {'obligations': {}}, 'movements': []}

    async def heartbeat():
        await asyncio.sleep(.01)
        released.set()

    monkeypatch.setattr(service, 'mutate', mutate)
    monkeypatch.setattr(service, 'active_gate', AsyncMock())
    monkeypatch.setattr(sources, 'collect_sources', AsyncMock(return_value={'orders': []}))
    monkeypatch.setattr(inventory, 'assert_no_source_collision', AsyncMock())
    monkeypatch.setattr(returns, 'attach_returns', lambda *_: None)
    monkeypatch.setattr(engine, 'reconcile', slow_calculation)
    beat = asyncio.create_task(heartbeat())
    try:
        result = await service.refresh(object(), 'owner', clock=NOW)
        assert result['engine']['as_of'] == NOW
    finally:
        released.set()
        await beat
