"""Actual source conversion yields to disconnect and joins every preparation."""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi import FastAPI, Request
import pytest

import operational_balance_sources as sources
from operational_visible_report_read import run_visible_report_read
from test_operational_balance_sources import DB, NOW, START, order, row
from test_operational_balance_supplier_preview_lifetime import begin_request


@pytest.mark.parametrize('stage', ['orders', 'items', 'invoices', 'employees', 'advertising', 'recurring'])
def test_actual_source_conversion_stops_before_draining_rows_and_joins_on_disconnect(monkeypatch, stage):
    async def scenario():
        count, total = 0, 20000
        heartbeat = False
        finished = False
        preparation_finished = Event()
        source_code = {
            'orders': 'canonical_salla_source_missing',
            'items': 'order_item_stable_identity_missing',
            'invoices': 'supplier_invoice_supplier_identity_incomplete',
            'employees': 'employee_salary_contract_missing_or_ambiguous',
            'recurring': 'recurring_contract_incomplete',
        }.get(stage)
        if stage == 'orders':
            data = {'unified_orders': [row(order_number=str(index)) for index in range(total)]}
        elif stage == 'items':
            source = order()
            source['raw_by_source']['salla_direct']['items'] = [{} for _ in range(total)]
            data = {'unified_orders': [source]}
        elif stage == 'invoices':
            data = {'mezan_supplier_invoices_v2': [row(id=str(index), approved_at=NOW, supplier_id='missing') for index in range(total)]}
        elif stage == 'employees':
            data = {'mezan_employees_v2': [row(id=str(index), status='active') for index in range(total)]}
        elif stage == 'advertising':
            data = {'mz2_ad_account_bindings_v2': [row(id=str(index), probe=True) for index in range(total)]}
        else:
            data = {'operating_recurring_obligations_v2': [row(id=str(index), status='active') for index in range(total)]}
        before = deepcopy(data)
        db = DB(data)
        loop = asyncio.get_running_loop()
        incoming = None

        def disconnect():
            nonlocal heartbeat
            heartbeat = True
            incoming.put_nowait({'type': 'http.disconnect'})

        def observed():
            nonlocal count
            count += 1
            if count == 10:
                loop.call_soon_threadsafe(disconnect)

        original_issue, original_usable = sources.issue, sources.usable

        def issue(output, code, *args, **kwargs):
            if code == source_code:
                observed()
            return original_issue(output, code, *args, **kwargs)

        def usable(value):
            if value.get('probe'):
                observed()
            return original_usable(value)

        monkeypatch.setattr(sources, 'issue', issue)
        if stage == 'advertising':
            monkeypatch.setattr(sources, 'usable', usable)
        if stage in {'employees', 'advertising', 'recurring'}:
            name = '_' + stage
            original_preparation = getattr(sources, name)

            def prepare(*args, **kwargs):
                try:
                    return original_preparation(*args, **kwargs)
                finally:
                    preparation_finished.set()

            monkeypatch.setattr(sources, name, prepare)

        app = FastAPI()

        @app.get('/api/operational-balances/source-conversion-probe')
        async def read(request: Request):
            async def collect():
                nonlocal finished
                try:
                    return await sources.collect_sources(db, 'owner', START, NOW)
                finally:
                    finished = True
            return await run_visible_report_read(request, collect)

        task, incoming, sent = begin_request(app, '/source-conversion-probe')
        await asyncio.wait_for(task, 10)
        assert heartbeat and finished
        assert 10 <= count < total
        assert next(message['status'] for message in sent if message['type'] == 'http.response.start') == 499
        if stage in {'employees', 'advertising', 'recurring'}:
            assert preparation_finished.is_set()
        observed_count = count
        await asyncio.sleep(0)
        assert count == observed_count
        assert data == before
        assert set(db.reads) <= sources.ALLOWED_COLLECTIONS

    asyncio.run(scenario())


@pytest.mark.parametrize('helper', ['_employees', '_advertising', '_recurring'])
def test_actual_source_helper_deadline_stops_preparation_before_refresh_releases_lease(monkeypatch, helper):
    from fastapi import HTTPException
    import operational_demand_refresh as demand
    from operational_balance_store import STATES
    from test_operational_balance_demand_refresh import DB as LeaseDB, Request as RefreshRequest, allowed

    async def scenario():
        source_db, lease_db = DB(), LeaseDB()
        entered, stopped, emergency_stop = Event(), Event(), Event()
        loop = asyncio.get_running_loop()
        deadline_scopes = []

        class AsyncioWithArmedDeadline:
            """Use a real timeout, armed only after the preparation thread starts."""
            def __getattr__(self, name):
                return getattr(asyncio, name)

            def timeout(self, seconds):
                assert seconds == demand.REQUEST_SECONDS
                deadline = asyncio.timeout(None)
                deadline_scopes.append(deadline)
                return deadline

        class Database:
            def __getitem__(self, name):
                return lease_db[name] if name == demand.LEASES else source_db[name]

        def arm_deadline():
            assert len(deadline_scopes) == 1
            deadline_scopes[0].reschedule(loop.time() + .02)

        def prepare(*_args, **_kwargs):
            entered.set()
            loop.call_soon_threadsafe(arm_deadline)
            try:
                while not emergency_stop.is_set():
                    demand.calculation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(demand, 'asyncio', AsyncioWithArmedDeadline())
        monkeypatch.setattr(sources, helper, prepare)
        db = Database()
        work = asyncio.create_task(demand.run_visible_refresh(
            RefreshRequest(), db, 'owner', 'staff', allowed,
            lambda: sources.collect_sources(db, 'owner', START, NOW)))
        try:
            with pytest.raises(HTTPException) as failure:
                await asyncio.wait_for(work, 3)
            assert failure.value.status_code == 408
            assert failure.value.detail['code'] == 'operational_refresh_timeout'
            assert entered.is_set() and stopped.is_set()
            assert lease_db.data[demand.LEASES] == {}
            assert not [write for write in lease_db.writes if write[0] == STATES]
            assert source_db.reads and set(source_db.reads) <= sources.ALLOWED_COLLECTIONS
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        finally:
            emergency_stop.set()
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    asyncio.run(scenario())
