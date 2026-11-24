"""The legacy report keeps all financial output without reading audit pages."""
import asyncio
from copy import deepcopy
from threading import Event

import pytest

import operational_balance_archive as archive
import operational_financial_position_state as financial_state
from operational_balance_store import STATES, read
from operational_financial_activity_runtime import preparation_checkpoint
from test_operational_balance_financial_order_labels import ReadOnlyDB as LabelDB
from test_operational_balance_visible_report_reads import app_for, begin_get, response
from test_operational_financial_position_state import ArchivedDB, install_archive
from test_operational_financial_position_views import rich_state


class ReportDB(ArchivedDB):
    def __init__(self, *, packed=False, missing_page=False):
        super().__init__()
        self.labels = LabelDB([])
        state = rich_state()
        state.update(_id="owner", owner_id="owner", audit=[{"action": "manual-audit"}],
                     requests={"synthetic-request": {"actor": "owner"}})
        state["engine"]["profit_snapshot"]["owner_id"] = "owner"
        self.data[STATES] = [state]
        install_archive(self, missing_page=missing_page)
        if packed:
            state = archive.compact_financial_snapshot(state, 100_000)
            self.data[STATES][0] = archive.compact_issues(state, 100_000)

    def __getitem__(self, name):
        if name == "unified_orders":
            return self.labels[name]
        return super().__getitem__(name)


def get(db, path="/reports"):
    async def scenario():
        task, _, sent = begin_get(app_for(db), path)
        await asyncio.wait_for(task, 3)
        return response(sent)
    return asyncio.run(scenario())


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("position_amounts", [False, True])
def test_full_report_output_matches_prior_reader_without_audit_hydration(monkeypatch, packed, position_amounts):
    db = ReportDB(packed=packed)
    before = deepcopy(db.data)
    path = "/reports?owner_id=other&position_amounts=" + str(position_amounts).lower()
    # Execute the real previous full-state reader through the same HTTP route,
    # labels, coverage and response encoding; compare the entire public body.
    with monkeypatch.context() as previous:
        previous.setattr(financial_state, "read_financial_position_state", read)
        old_status, expected = get(db, path)
    assert old_status == 200, expected
    assert archive.AUDIT_PAGES in [name for name, _ in db.reads]
    db.reads.clear()
    status, actual = get(db, path)
    assert status == 200, actual
    assert actual == expected
    assert actual["issues"] and actual["details"]["facts"]
    assert archive.AUDIT_PAGES not in [name for name, _ in db.reads]
    assert all(query == {"_id": "owner", "owner_id": "owner"}
               for name, query in db.reads if name == STATES)
    assert db.data == before


def test_missing_historical_page_does_not_block_report_but_audit_still_checks_it():
    db = ReportDB(missing_page=True)
    before = deepcopy(db.data)
    status, body = get(db)
    assert status == 200 and body["details"]["facts"]
    assert archive.AUDIT_PAGES not in [name for name, _ in db.reads]
    status, body = get(db, "/audit")
    assert status == 503 and body["detail"]["code"] == "operational_audit_archive_invalid"
    assert archive.AUDIT_PAGES in [name for name, _ in db.reads]
    assert db.data == before


@pytest.mark.parametrize("packet", [archive.SNAPSHOT, archive.ISSUES, archive.MARKER])
def test_report_keeps_financial_packet_and_audit_manifest_validation(packet):
    db = ReportDB(packed=True)
    db.data[STATES][0][packet]["sha256"] = "0" * 64
    before = deepcopy(db.data)
    status, body = get(db)
    assert status == 503 and body["detail"]["code"] == "operational_financial_snapshot_invalid"
    assert archive.AUDIT_PAGES not in [name for name, _ in db.reads]
    assert db.data == before


def test_inactive_owner_is_rejected_before_saved_state_read():
    db = ReportDB()
    db.data["users"][0]["disabled"] = True
    status, _ = get(db)
    assert status == 403
    assert STATES not in [name for name, _ in db.reads]


def test_owner_is_reauthorized_after_label_enrichment(monkeypatch):
    import operational_financial_order_labels as labels
    db = ReportDB()
    before = deepcopy(db.data[STATES])

    async def revoke(_db, owner, result):
        assert owner == "owner" and result["details"]["facts"]
        db.data["users"][0]["disabled"] = True
        return result

    monkeypatch.setattr(labels, "attach_order_numbers", revoke)
    status, body = get(db)
    assert status == 403 and body["detail"]["code"] == "operational_actor_inactive"
    assert db.data[STATES] == before


def test_report_disconnect_stops_and_joins_narrow_snapshot_decode(monkeypatch):
    async def scenario():
        db = ReportDB(packed=True)
        before = deepcopy(db.data)
        entered, stopped = Event(), Event()

        def decode(*_args):
            entered.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(financial_state, "_report_state", decode)
        task, queue, sent = begin_get(app_for(db), "/reports")
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            queue.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
            status, body = response(sent)
            assert status == 499 and body["detail"]["code"] == "operational_report_read_cancelled"
            assert stopped.is_set()
            assert archive.AUDIT_PAGES not in [name for name, _ in db.reads]
            assert db.data == before
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())
