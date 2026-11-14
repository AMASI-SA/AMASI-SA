"""Source timings identify request-owned work without exposing source data."""
import asyncio
from copy import deepcopy
import logging
import re

from fastapi import HTTPException
import pytest

import operational_balance_service as service
import operational_balance_sources as sources
from operational_balance_store import STATES
from operational_demand_refresh import (
    LEASES, calculation_checkpoint, demand_context, refresh_stage, run_visible_refresh,
)
from test_operational_balance_demand_refresh import (
    Collection as DemandCollection, DB as DemandDB, Request, allowed,
)
from test_operational_balance_demand_refresh_routes import post
from test_operational_balance_refresh_stage_timing import timing_messages
from test_operational_balance_sources import DB as SourceDB, NOW, START, order


PRIVATE = 'private-source-stage order=hidden-order amount=9182.37 token=never-log'
ACTOR = 'private-source-stage-actor'
SOURCE_STAGES = (
    'source_primary_reads', 'source_fee_settings', 'source_setup', 'source_statements',
    'source_delivery_history', 'source_transactions', 'source_native_links',
    'source_bundles', 'source_option_catalog', 'source_orders', 'source_invoices',
    'source_other_sources', 'source_native_overlaps', 'source_finalize',
)
LOG = logging.getLogger('operational_demand_refresh')


def source_data():
    record = order(id='private-source-stage-order', reference_id='private-source-stage-number',
                   payment_method='mada')
    record['order_number'] = 'private-source-stage-number'
    return {'unified_orders': [record]}


class Gate:
    """Pause only an existing source cursor; never add a production task/read."""
    def __init__(self, collection, outcome):
        self.collection, self.outcome = collection, outcome
        self.entered = self.stopped = False
        self.sibling_entered = self.sibling_stopped = False

    async def read(self, collection):
        if (self.collection == 'mezan_suppliers_v2'
                and collection == 'mezan_ad_account_cost_settings_v2'):
            self.sibling_entered = True
            try:
                await asyncio.Event().wait()
            finally:
                self.sibling_stopped = True
        if collection != self.collection:
            return
        self.entered = True
        try:
            # Exit records must survive a UI showing only the latest 100 lines.
            for _ in range(120):
                LOG.info('synthetic unrelated activity')
            if self.outcome == 'failure':
                raise RuntimeError(PRIVATE)
            await asyncio.Event().wait()
        finally:
            self.stopped = True


class ObservedCursor:
    def __init__(self, db, collection, cursor):
        self.db, self.collection, self.cursor = db, collection, cursor

    async def to_list(self, *args, **kwargs):
        if self.db.require_visible:
            context = demand_context()
            assert context is not None and context.db is self.db and context.owner == 'owner'
        self.db.inflight += 1
        try:
            if self.db.gate is not None:
                await self.db.gate.read(self.collection)
            return await self.cursor.to_list(*args, **kwargs)
        finally:
            self.db.inflight -= 1


class SourceCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, projection=None):
        cursor = self.db.sources[self.name].find(query, projection)
        return ObservedCursor(self.db, self.name, cursor)

    def aggregate(self, pipeline):
        cursor = self.db.sources[self.name].aggregate(pipeline)
        return ObservedCursor(self.db, self.name, cursor)

    async def find_one(self, query, projection=None):
        # The real service's active gate reads settings metadata separately.
        assert self.name == 'settings' and query == {'user_id': 'owner'}
        return await DemandCollection(self.db, self.name).find_one(query, projection)


class DB(DemandDB):
    def __init__(self, *, gate=None, require_visible=True):
        super().__init__()
        self.data[STATES]['owner']['started_at'] = START
        self.sources = SourceDB(source_data())
        self.gate, self.require_visible, self.inflight = gate, require_visible, 0

    def __getitem__(self, name):
        if name in sources.ALLOWED_COLLECTIONS:
            return SourceCollection(self, name)
        return super().__getitem__(name)


async def collect(database):
    return await sources.collect_sources(database, 'owner', START, NOW,
                                         operational_state=database.data[STATES]['owner'])


async def refresh(database):
    with refresh_stage('aggregate'):
        return await service.refresh(database, 'owner', clock=NOW)


async def assert_request_finished(database):
    assert demand_context() is None
    assert database.inflight == 0
    assert database.data[LEASES] == {}
    assert {entry[0] for entry in database.writes} == {LEASES}
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    reads = list(database.sources.reads)
    await asyncio.sleep(0)
    assert database.sources.reads == reads


def assert_private_data_absent(caplog):
    assert PRIVATE not in caplog.text
    assert 'private-source-stage' not in caplog.text
    assert '9182.37' not in caplog.text
    assert 'never-log' not in caplog.text
    for message in timing_messages(caplog):
        assert re.fullmatch(r'operational_refresh_stage stage=[a-z_]+ '
                            r'duration_ms=\d+\.\d{3} outcome=(complete|failed|cancelled)', message)


def assert_failed_tail(caplog, stage, outcome):
    messages = timing_messages(caplog)
    for name in (stage, 'source_collection', 'aggregate'):
        records = [message for message in messages if f'stage={name} ' in message]
        assert len(records) == 1 and records[0].endswith(f'outcome={outcome}')
    tail = [record.getMessage() for record in caplog.records][-100:]
    assert any(f'stage={stage} ' in message and message.endswith(f'outcome={outcome}')
               for message in tail)
    assert_private_data_absent(caplog)


def test_real_collection_stage_sequence_preserves_output_and_reads_and_is_silent_outside_visible(caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')

    async def scenario():
        ordinary = DB(require_visible=False)
        original_sources = deepcopy(ordinary.sources.data)
        expected = await collect(ordinary)
        assert timing_messages(caplog) == []
        assert ordinary.writes == []
        visible = DB()
        actual = await run_visible_refresh(Request(), visible, 'owner', ACTOR,
                                           allowed, lambda: collect(visible))
        assert actual == expected
        assert visible.sources.reads == ordinary.sources.reads
        assert visible.sources.data == ordinary.sources.data == original_sources
        await assert_request_finished(visible)

    asyncio.run(scenario())
    messages = timing_messages(caplog)
    assert [message.split('stage=')[1].split()[0] for message in messages] == list(SOURCE_STAGES)
    assert all(message.endswith('outcome=complete') for message in messages)
    assert_private_data_absent(caplog)


@pytest.mark.parametrize('stage,collection', [
    ('source_collision', 'mezan_supplier_invoices_v2'),
    ('source_primary_reads', 'mezan_suppliers_v2'),
    ('source_fee_settings', 'settings'),
    ('source_transactions', 'order_payment_transactions_v2'),
])
@pytest.mark.parametrize('outcome', ['deadline', 'failure'])
def test_real_source_read_exit_is_identified_and_joined(monkeypatch, caplog, stage, collection, outcome):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    monkeypatch.setattr('operational_demand_refresh.REQUEST_SECONDS', 1)

    async def scenario():
        gate = Gate(collection, outcome)
        database = DB(gate=gate)
        if stage == 'source_collision':
            database.data[STATES]['owner']['inventory_purchases'] = [
                {'supplier_id': 'private-source-stage-supplier', 'invoice_number': 'private-source-stage-invoice'}]
        original_state, original_sources = deepcopy(database.data[STATES]), deepcopy(database.sources.data)
        if outcome == 'deadline':
            with pytest.raises(HTTPException) as caught:
                await run_visible_refresh(Request(), database, 'owner', ACTOR,
                                          allowed, lambda: refresh(database))
            assert caught.value.status_code == 408
            assert caught.value.detail['code'] == 'operational_refresh_timeout'
        else:
            with pytest.raises(RuntimeError, match='private-source-stage'):
                await run_visible_refresh(Request(), database, 'owner', ACTOR,
                                          allowed, lambda: refresh(database))
        assert gate.entered and gate.stopped
        if stage == 'source_primary_reads':
            assert gate.sibling_entered and gate.sibling_stopped
        assert database.data[STATES] == original_state
        assert database.sources.data == original_sources
        await assert_request_finished(database)

    asyncio.run(scenario())
    assert_failed_tail(caplog, stage, 'cancelled' if outcome == 'deadline' else 'failed')


@pytest.mark.parametrize('outcome', ['cancelled', 'failed'])
def test_real_order_loop_exit_is_identified_without_saving_or_continuing(monkeypatch, caplog, outcome):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    reached = False

    def stop_at_payment(*args, **kwargs):
        nonlocal reached
        reached = True
        assert demand_context() is not None
        if outcome == 'failed':
            raise RuntimeError(PRIVATE)
        demand_context().cancel(disconnected=True)
        calculation_checkpoint()

    monkeypatch.setattr(sources, 'normalize_operational_payment', stop_at_payment)

    async def scenario():
        database = DB()
        original_state, original_sources = deepcopy(database.data[STATES]), deepcopy(database.sources.data)
        if outcome == 'cancelled':
            with pytest.raises(HTTPException) as caught:
                await run_visible_refresh(Request(), database, 'owner', ACTOR,
                                          allowed, lambda: refresh(database))
            assert caught.value.status_code == 499
            assert caught.value.detail['code'] == 'operational_refresh_cancelled'
        else:
            with pytest.raises(RuntimeError, match='private-source-stage'):
                await run_visible_refresh(Request(), database, 'owner', ACTOR,
                                          allowed, lambda: refresh(database))
        assert reached
        assert database.data[STATES] == original_state
        assert database.sources.data == original_sources
        await assert_request_finished(database)

    asyncio.run(scenario())
    assert_failed_tail(caplog, 'source_orders', outcome)
    assert not any('stage=source_invoices ' in message for message in timing_messages(caplog))


@pytest.mark.parametrize('payload,principal', [({'visible': False}, None), ({'visible': True}, {})])
def test_hidden_or_unauthorized_route_starts_no_source_work_or_timing(monkeypatch, caplog, payload, principal):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = DB()
    response, _ = asyncio.run(post(database, monkeypatch, payload=payload, principal=principal))
    assert response.status_code in {401, 422}
    assert database.sources.reads == [] and database.writes == []
    assert timing_messages(caplog) == []
