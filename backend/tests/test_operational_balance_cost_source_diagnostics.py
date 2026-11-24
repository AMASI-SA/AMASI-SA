"""A failed cost read names only its safe source, without changing the failure."""
import asyncio
from copy import deepcopy
import logging
import traceback

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pymongo
import pytest

import operational_open_cost_exposure as exposure
import operational_read_runtime as runtime
from operational_source_diagnostics import source_failure_detail
from test_operational_open_cost_exposure import NOW, operating_state


CODE = "operational_source_read_timeout"
OWNER = "private-owner-fixture-783"
PRIVATE = "private-receipt-fixture-925"
PREFIX = "operational_cost_source_read_failure stage=source_collection outcome=failed"


def message(collection, cause=CODE):
    return f"{PREFIX} cause={cause} source_collection={collection}"


def install_failure(monkeypatch, failure, collection="mezan_products_v2"):
    calls = []

    async def reader(database, owner, name):
        assert owner == OWNER
        calls.append(name)
        if name == collection:
            raise failure
        return []

    monkeypatch.setattr(exposure, "rows", reader)
    return calls


def records(caplog):
    return [record for record in caplog.records if record.name == exposure.__name__]


@pytest.mark.parametrize("collection", exposure.READS)
@pytest.mark.asyncio
async def test_failed_cost_read_names_source_in_log_and_trace_preserving_exception(
        monkeypatch, caplog, collection):
    caplog.set_level(logging.WARNING, logger=exposure.__name__)
    failure = runtime.SourceReadError(CODE, collection)
    failure.private_document = {"receipt": PRIVATE, "owner": OWNER}
    calls = install_failure(monkeypatch, failure, collection)
    state = operating_state()
    before = deepcopy(state)

    with pytest.raises(runtime.SourceReadError) as caught:
        await exposure._project(object(), OWNER, state, as_of=NOW)

    assert caught.value is failure and type(caught.value) is runtime.SourceReadError
    assert (failure.args, str(failure), failure.code, failure.collection) == (
        (CODE,), CODE, CODE, collection)
    assert state == before and len(calls) == len(set(calls))
    rendered = "".join(traceback.format_exception(failure))
    assert message(collection) in rendered
    logged, = records(caplog)
    assert logged.getMessage() == message(collection)
    assert logged.exc_info is None and logged.stack_info is None
    for private in (OWNER, PRIVATE):
        assert private not in caplog.text and private not in rendered


def test_every_cost_source_has_a_fixed_safe_name():
    for collection in exposure.READS:
        detail = source_failure_detail(CODE, collection)
        assert detail["cause"] == CODE and detail["source_collection"] == collection


@pytest.mark.asyncio
async def test_note_survives_a_long_traceback_tail(monkeypatch):
    failure = runtime.SourceReadError(CODE, "mezan_products_v2")
    install_failure(monkeypatch, failure)

    async def even(depth):
        if depth:
            return await odd(depth - 1)
        return await exposure._project(object(), OWNER, operating_state(), as_of=NOW)

    async def odd(depth):
        return await even(depth - 1)

    with pytest.raises(runtime.SourceReadError) as caught:
        await even(120)
    lines = "".join(traceback.format_exception(caught.value)).splitlines()
    assert len(lines) > 100
    assert lines[-1] == message("mezan_products_v2")
    assert message("mezan_products_v2") in lines[-100:]
    assert caught.value is failure and failure.args == (CODE,) and str(failure) == CODE


class Hostile:
    def __str__(self):
        raise AssertionError("must not format untrusted fields")

    __repr__ = __str__

    def __hash__(self):
        raise AssertionError("must not hash untrusted fields")


@pytest.mark.parametrize("untrusted", [PRIVATE + "\nowner=" + OWNER, Hostile()],
                         ids=["log-injection", "untrusted-object"])
@pytest.mark.asyncio
async def test_unknown_exception_fields_are_sanitized_without_changing_args(
        monkeypatch, caplog, untrusted):
    caplog.set_level(logging.WARNING, logger=exposure.__name__)
    failure = runtime.SourceReadError(CODE, untrusted)
    failure.code = untrusted
    install_failure(monkeypatch, failure)
    with pytest.raises(runtime.SourceReadError) as caught:
        await exposure._project(object(), OWNER, operating_state(), as_of=NOW)
    assert caught.value is failure and failure.args == (CODE,) and str(failure) == CODE
    logged, = records(caplog)
    assert logged.getMessage() == message("unknown", "unknown")
    assert failure.__notes__ == [message("unknown", "unknown")]
    assert OWNER not in caplog.text and PRIVATE not in caplog.text


@pytest.mark.parametrize("break_note,break_log", [(True, False), (False, True), (True, True)])
@pytest.mark.asyncio
async def test_observability_failure_cannot_replace_source_exception(
        monkeypatch, caplog, break_note, break_log):
    failure = runtime.SourceReadError(CODE, "mezan_products_v2")
    install_failure(monkeypatch, failure)

    def broken(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    if break_note:
        failure.add_note = broken
    if break_log:
        monkeypatch.setattr(logging.getLogger(exposure.__name__), "warning", broken)
    with pytest.raises(runtime.SourceReadError) as caught:
        await exposure._project(object(), OWNER, operating_state(), as_of=NOW)
    assert caught.value is failure and failure.args == (CODE,) and str(failure) == CODE
    if not break_note:
        assert failure.__notes__ == [message("mezan_products_v2")]
    if not break_log:
        logged, = records(caplog)
        assert logged.getMessage() == message("mezan_products_v2")
    assert PRIVATE not in caplog.text


@pytest.mark.asyncio
async def test_actual_read_deadline_and_cleanup_are_preserved(monkeypatch, caplog):
    budgets, stopped = [], asyncio.Event()

    async def reader(database, owner, name):
        if name != "unified_orders":
            return []
        budgets.append(pymongo._csot.remaining())
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(exposure, "rows", reader)
    with runtime.source_read_budget(.05):
        with pytest.raises(runtime.SourceReadError) as caught:
            await exposure._project(object(), OWNER, operating_state(), as_of=NOW)
    assert caught.value.code == CODE and caught.value.collection == "unified_orders"
    assert caught.value.__notes__ == [message("unified_orders")]
    assert stopped.is_set() and len(budgets) == 1 and 0 < budgets[0] <= .05
    assert pymongo._csot.remaining() is None and not runtime.source_read_is_owned()
    assert runtime._DEADLINE.get() is None


@pytest.mark.asyncio
async def test_cancellation_does_not_become_a_source_failure(monkeypatch, caplog):
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def reader(database, owner, name):
        if name != "unified_orders":
            return []
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(exposure, "rows", reader)
    task = asyncio.create_task(exposure._project(object(), OWNER, operating_state(), as_of=NOW))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert stopped.is_set() and not getattr(caught.value, "__notes__", [])
    assert not records(caplog)


@pytest.mark.asyncio
async def test_diagnostic_note_does_not_change_http_failure_body(monkeypatch):
    install_failure(monkeypatch, runtime.SourceReadError(CODE, "mezan_products_v2"))
    app = FastAPI(debug=False)

    @app.get("/baseline")
    async def baseline():
        return await runtime.load_collections(object(), OWNER, exposure.READS, reader=exposure.rows)

    @app.get("/cost-exposure")
    async def cost():
        return await exposure.project(object(), OWNER, operating_state(), as_of=NOW)

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://fixture") as client:
        before = await client.get("/baseline")
        after = await client.get("/cost-exposure")
    assert (after.status_code, after.content, after.headers["content-type"]) == (
        before.status_code, before.content, before.headers["content-type"])
    assert after.status_code == 500 and after.text == "Internal Server Error"
    assert PRIVATE not in after.text and OWNER not in after.text
