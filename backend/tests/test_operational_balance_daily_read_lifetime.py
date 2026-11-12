"""Visible readers own shared preparation and join its cancellation completely."""
import asyncio
from threading import Event

import pytest
from fastapi import HTTPException

import operational_financial_activity as activity
from operational_financial_activity_runtime import preparation_checkpoint, run_visible_daily_read
from test_operational_balance_daily_financial_movements import database, feed, movement, state_fixture


class VisibleRequest:
    def __init__(self, *, disconnected=False):
        self.disconnected = disconnected
        self.events = asyncio.Queue()
        self.listening = asyncio.Event()
        self.receivers = 0

    async def is_disconnected(self):
        return self.disconnected

    async def receive(self):
        self.receivers += 1
        self.listening.set()
        try:
            return await self.events.get()
        finally:
            self.receivers -= 1

    def hide(self):
        self.disconnected = True
        self.events.put_nowait({'type': 'http.disconnect'})


def test_cancelled_preparation_without_inherited_scope_stops_and_joins_its_thread():
    from operational_financial_activity_runtime import prepare_in_thread

    async def scenario():
        started, stopped = Event(), Event()

        def calculate():
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        task = asyncio.create_task(prepare_in_thread(calculate))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert stopped.is_set()
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def empty_prepared_cache():
    activity.clear_page_cache()
    assert not activity._BUILDING
    yield
    assert not activity._BUILDING
    activity.clear_page_cache()


def test_disconnected_request_never_reads_or_starts_a_listener():
    async def scenario():
        request = VisibleRequest(disconnected=True)
        calls = []
        async def read():
            calls.append(True)
        with pytest.raises(HTTPException) as error:
            await run_visible_daily_read(request, read)
        assert error.value.status_code == 499
        assert error.value.detail['code'] == 'financial_movements_read_cancelled'
        assert not calls and not request.listening.is_set() and request.receivers == 0
    asyncio.run(scenario())


def test_other_saved_reports_can_supply_their_own_cancellation_message():
    async def scenario():
        async def must_not_read():
            raise AssertionError('disconnected request cannot enter the report')
        with pytest.raises(HTTPException) as error:
            await run_visible_daily_read(VisibleRequest(disconnected=True), must_not_read,
                code='saved_report_cancelled', message='أُلغيت قراءة التقرير')
        assert error.value.status_code == 499
        assert error.value.detail == {'code': 'saved_report_cancelled', 'message': 'أُلغيت قراءة التقرير'}
    asyncio.run(scenario())


@pytest.mark.parametrize('fails', [False, True])
def test_finished_or_failed_read_removes_the_disconnect_listener(fails):
    async def scenario():
        request = VisibleRequest()
        async def read():
            await request.listening.wait()
            if fails:
                raise HTTPException(503, 'unavailable')
            return {'total': 1}
        if fails:
            with pytest.raises(HTTPException) as error:
                await run_visible_daily_read(request, read)
            assert error.value.status_code == 503
        else:
            assert await run_visible_daily_read(request, read) == {'total': 1}
        assert request.receivers == 0
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_outer_task_cancellation_joins_the_read_and_listener():
    async def scenario():
        request = VisibleRequest()
        entered, ended = asyncio.Event(), asyncio.Event()
        async def read():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                ended.set()
        task = asyncio.create_task(run_visible_daily_read(request, read))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ended.is_set() and request.receivers == 0
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def held_preparation(monkeypatch, *, wait_after_stop=False):
    """Use the real projector with a controllable, cooperatively cancelled loop."""
    loop = asyncio.get_running_loop()
    entered, stopped = asyncio.Event(), asyncio.Event()
    release, cleanup = Event(), Event()
    if not wait_after_stop:
        cleanup.set()
    original = activity._prepare_rows
    builds = []
    def prepare(*args):
        builds.append(True)
        loop.call_soon_threadsafe(entered.set)
        try:
            while not release.wait(0.001):
                preparation_checkpoint()
            preparation_checkpoint()
            return original(*args)
        except asyncio.CancelledError:
            loop.call_soon_threadsafe(stopped.set)
            assert cleanup.wait(2), 'test did not release cancellation cleanup'
            raise
    monkeypatch.setattr(activity, '_prepare_rows', prepare)
    return entered, stopped, release, cleanup, builds


async def pair(monkeypatch, db):
    original = activity._shared_page
    arrivals = 0
    together = asyncio.Event()
    async def observed(*args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            together.set()
        return await original(*args, **kwargs)
    monkeypatch.setattr(activity, '_shared_page', observed)
    requests = [VisibleRequest(), VisibleRequest()]
    tasks = [asyncio.create_task(run_visible_daily_read(request, lambda: feed(db))) for request in requests]
    await asyncio.wait_for(together.wait(), 2)
    return requests, tasks


def test_one_hidden_reader_does_not_cancel_another_visible_reader_or_rebuild_pages(monkeypatch):
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement(str(i)) for i in range(12)]
        db = await database(state)
        entered, stopped, release, _, builds = held_preparation(monkeypatch)
        requests, tasks = await pair(monkeypatch, db)
        await asyncio.wait_for(entered.wait(), 2)
        requests[0].hide()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(tasks[0], 2)
        assert error.value.status_code == 499
        assert not stopped.is_set() and not tasks[1].done() and len(builds) == 1
        release.set()
        first = await asyncio.wait_for(tasks[1], 2)
        assert first['total'] == 12 and len(first['items']) == 10
        second = await feed(db, page=2, snapshot=first['snapshot'])
        assert len(second['items']) == 2 and len(builds) == 1
        assert all(request.receivers == 0 for request in requests)
        assert not activity._BUILDING
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_hiding_all_readers_stops_preparation_then_a_new_visible_read_succeeds(monkeypatch):
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement()]
        db = await database(state)
        entered, stopped, release, _, builds = held_preparation(monkeypatch)
        requests, tasks = await pair(monkeypatch, db)
        await asyncio.wait_for(entered.wait(), 2)
        for request in requests:
            request.hide()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert all(isinstance(result, HTTPException) and result.status_code == 499 for result in results)
        assert stopped.is_set() and len(builds) == 1
        assert not activity._BUILDING and not activity._PAGE_CACHE
        assert all(request.receivers == 0 for request in requests)
        assert asyncio.all_tasks() == {asyncio.current_task()}
        release.set()
        result = await run_visible_daily_read(VisibleRequest(), lambda: feed(db))
        assert result['total'] == 1 and len(builds) == 2
    asyncio.run(scenario())


def test_new_reader_waits_for_last_cancelled_worker_cleanup_before_rebuilding(monkeypatch):
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement()]
        db = await database(state)
        entered, stopped, release, cleanup, builds = held_preparation(monkeypatch, wait_after_stop=True)
        request = VisibleRequest()
        first = asyncio.create_task(run_visible_daily_read(request, lambda: feed(db)))
        await asyncio.wait_for(entered.wait(), 2)
        request.hide()
        await asyncio.wait_for(stopped.wait(), 2)
        next_request = VisibleRequest()
        second = asyncio.create_task(run_visible_daily_read(next_request, lambda: feed(db)))
        await next_request.listening.wait()
        assert not first.done() and not second.done() and len(builds) == 1
        # Both cleanup and the new preparation are explicitly released, in order.
        release.set()
        cleanup.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(first, 2)
        assert error.value.status_code == 499
        result = await asyncio.wait_for(second, 2)
        assert result['total'] == 1 and len(builds) == 2
        assert request.receivers == next_request.receivers == 0
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_late_disconnect_preserves_a_fully_prepared_page(monkeypatch):
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement(str(i)) for i in range(11)]
        db = await database(state)
        ready = asyncio.Event()
        original = activity.account_scope
        calls = 0
        async def after_preparation(*args):
            nonlocal calls
            result = await original(*args)
            calls += 1
            if calls == 2:
                ready.set()
                await asyncio.Future()
            return result
        monkeypatch.setattr(activity, 'account_scope', after_preparation)
        request = VisibleRequest()
        task = asyncio.create_task(run_visible_daily_read(request, lambda: feed(db)))
        await asyncio.wait_for(ready.wait(), 2)
        request.hide()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499
        assert len(activity._PAGE_CACHE) == 1
        prepared = next(iter(activity._PAGE_CACHE.values()))['result']
        monkeypatch.setattr(activity, 'account_scope', original)
        async def forbidden_rebuild(*args, **kwargs):
            raise AssertionError('a complete prepared page must remain reusable')
        monkeypatch.setattr(activity, '_build_page', forbidden_rebuild)
        result = await feed(db, page=2, snapshot=prepared['snapshot'])
        assert len(result['items']) == 1
        assert request.receivers == 0 and not activity._BUILDING
    asyncio.run(scenario())
