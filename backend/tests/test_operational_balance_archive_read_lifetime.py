"""A closed visible request joins packed-state/audit work before returning."""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi import HTTPException
import pytest

import operational_balance_archive as archive
import operational_balance_store as store
from operational_demand_refresh import calculation_checkpoint, run_visible_refresh
from operational_visible_report_read import run_visible_report_read
from test_operational_balance_demand_refresh import DB, Request, allowed, update


def archived_state(db):
    state = db.data[store.STATES]["owner"]
    pages = []
    for index in range(2):
        events = [{"action": "source_refresh", "ordinal": index}]
        key = archive.content_key("owner", index, events)
        db.data.setdefault(archive.AUDIT_PAGES, {})[key] = {
            "_id": key, "owner_id": "owner", "start": index, "events": events}
        pages.append({"id": key, "count": 1})
    state[archive.MARKER] = archive.manifest(2, pages)
    state["engine"]["audit"] = {"tail": []}
    return state


@pytest.mark.parametrize("stage", ["snapshot", "issues", "manifest", "page"])
def test_disconnect_joins_large_saved_state_preparation_without_changing_data(monkeypatch, stage):
    async def scenario():
        db, request = DB(), Request()
        state = db.data[store.STATES]["owner"]
        if stage == "snapshot":
            state["engine"]["facts"] = {"proof": {"amount": "100.01"}}
            state = archive.compact_financial_snapshot(state, 10000)
            name = "unpack_financial_snapshot"
        elif stage == "issues":
            state["engine"]["issues"] = [{"code": "source_incomplete", "source_id": "one"}]
            state = archive.compact_issues(state, 10000)
            name = "unpack_issues"
        else:
            state = archived_state(db)
            name = "_audit_metadata" if stage == "manifest" else "validated_events"
        db.data[store.STATES]["owner"] = state
        before = deepcopy(db.data)
        started, stopped = Event(), Event()

        def expensive_read(*args, **kwargs):
            started.set()
            try:
                while True:
                    calculation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(archive, name, expensive_read)
        task = asyncio.create_task(run_visible_report_read(request, lambda: store.read(db, "owner")))
        assert await asyncio.to_thread(started.wait, 2)
        request.disconnect()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499 and stopped.is_set()
        assert db.data == before and not db.writes
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_visible_refresh_cancels_storage_preparation_before_atomic_save(monkeypatch):
    async def scenario():
        db, request = DB(), Request()
        before = deepcopy(db.data[store.STATES])
        started, stopped = Event(), Event()

        def expensive_pack(*args, **kwargs):
            started.set()
            try:
                while True:
                    calculation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(store, "prepare", expensive_pack)
        task = asyncio.create_task(run_visible_refresh(request, db, "owner", "owner", allowed, lambda: update(db)))
        assert await asyncio.to_thread(started.wait, 2)
        request.disconnect()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499 and stopped.is_set()
        assert db.data[store.STATES] == before
        assert not [write for write in db.writes if write[0] == store.STATES]
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())
