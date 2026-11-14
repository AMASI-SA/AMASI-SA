"""Report-only snapshots preserve packet trust without expanding audit pages."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import sys
from types import SimpleNamespace
import zlib

from bson import BSON
from fastapi import HTTPException
import pytest

import operational_balance_archive as archive
from operational_balance_store import STATES, read
from operational_financial_position import build_financial_position
from operational_financial_position_state import _report_state, read_financial_position_state
from test_operational_balance_financial_position_routes import get
from test_operational_balance_supplier_workspace import ReadOnlyCollection, ReadOnlyDB


class ArchivedDB(ReadOnlyDB):
    def __getitem__(self, name):
        if name == archive.AUDIT_PAGES:
            return ReadOnlyCollection(self, name)
        return super().__getitem__(name)


def install_archive(db, *, missing_page=False):
    state = db.data[STATES][0]
    events = [{"action": "source_refresh", "source_id": "synthetic-order", "amount": "11.00"}]
    key = archive.content_key("owner", 0, events)
    state[archive.MARKER] = archive.manifest(1, [{"id": key, "count": 1}])
    state["engine"]["audit"] = {"tail": [{"action": "synthetic-tail"}]}
    db.data[archive.AUDIT_PAGES] = [] if missing_page else [{
        "_id": key, "owner_id": "owner", "start": 0, "events": events,
    }]
    return state


def test_new_report_reader_keeps_financial_projection_and_omits_audit_materialization():
    db = ArchivedDB()
    install_archive(db)
    before = deepcopy(db.data)
    full = asyncio.run(read(db, "owner"))
    db.reads.clear()
    scoped = asyncio.run(read_financial_position_state(db, "owner"))
    assert [name for name, _ in db.reads] == [STATES]
    assert "audit" not in scoped and "audit" not in scoped["engine"] and "requests" not in scoped
    assert archive.MARKER not in scoped
    assert build_financial_position(scoped, view="summary") == build_financial_position(full, view="summary")
    assert db.data == before


def test_legacy_full_report_keeps_audit_failure_while_scoped_reader_checks_only_manifest(monkeypatch):
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", None)
    db = ArchivedDB()
    install_archive(db, missing_page=True)
    before = deepcopy(db.data)
    summary = asyncio.run(get(db, suffix="?view=summary"))
    assert summary.status_code == 200, summary.text
    assert archive.AUDIT_PAGES not in [name for name, _ in db.reads]
    full = asyncio.run(get(db))
    assert full.status_code == 503
    assert full.json()["detail"]["code"] == "operational_audit_archive_invalid"
    assert archive.AUDIT_PAGES in [name for name, _ in db.reads]
    assert db.data == before


@pytest.mark.parametrize("kind", ["hash", "count", "reference", "duplicate", "tail", "missing_manifest"])
def test_narrow_reader_still_rejects_invalid_audit_metadata(kind):
    db = ArchivedDB()
    state = install_archive(db)
    if kind == "hash":
        state[archive.MARKER]["sha256"] = "0" * 64
    elif kind == "count":
        state[archive.MARKER] = archive.manifest(2, state[archive.MARKER]["pages"])
    elif kind == "reference":
        state[archive.MARKER] = archive.manifest(1, [{"id": "synthetic", "count": True}])
    elif kind == "duplicate":
        state[archive.MARKER] = archive.manifest(2, state[archive.MARKER]["pages"] * 2)
    elif kind == "tail":
        state["engine"]["audit"] = []
    else:
        state.pop(archive.MARKER)
    before = deepcopy(db.data)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(read_financial_position_state(db, "owner"))
    assert rejected.value.status_code == 503
    assert rejected.value.detail["code"] == "operational_financial_snapshot_invalid"
    assert archive.AUDIT_PAGES not in [name for name, _ in db.reads] and db.data == before


def packed_state(db):
    state = db.data[STATES][0]
    state["engine"]["facts"] = {"saved-proof": {"amount": "15.00"}}
    state["engine"]["issues"] = [{"code": "synthetic_incomplete", "source_id": "saved-proof"}]
    state = archive.compact_financial_snapshot(state, 100_000)
    state = archive.compact_issues(state, 100_000)
    db.data[STATES][0] = state
    return state


def test_packed_report_read_matches_full_decode_without_any_mutation():
    db = ReadOnlyDB()
    packed_state(db)
    before = deepcopy(db.data)
    scoped = asyncio.run(read_financial_position_state(db, "owner"))
    full = asyncio.run(read(db, "owner"))
    assert scoped["engine"] == {key: value for key, value in full["engine"].items() if key != "audit"}
    assert build_financial_position(scoped, view="summary") == build_financial_position(full, view="summary")
    assert db.data == before


@pytest.mark.parametrize("packet_name", [archive.SNAPSHOT, archive.ISSUES])
@pytest.mark.parametrize("damage", ["hash", "length", "trailing", "marker", "missing", "owner"])
def test_scoped_http_rejects_corrupt_or_foreign_financial_packets(packet_name, damage, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("corrupt snapshots cannot reach source readers")
    monkeypatch.setitem(sys.modules, "operational_open_cost_exposure", SimpleNamespace(project=forbidden))
    db = ReadOnlyDB()
    state = packed_state(db)
    packet = state[packet_name]
    if damage == "hash":
        packet["sha256"] = "0" * 64
    elif damage == "length":
        packet["size"] += 1
    elif damage == "trailing":
        packet["data"] += b"trailing-bytes"
    elif damage == "marker":
        if packet_name == archive.SNAPSHOT:
            state["engine"][packet["fields"][0]] = {}
        else:
            state["engine"]["issues"] = []
    elif damage == "missing":
        state.pop(packet_name)
    else:
        content = BSON(zlib.decompress(packet["data"])).decode()
        content["owner_id"] = "another-owner"
        raw = BSON.encode(content)
        packet.update(data=zlib.compress(raw), size=len(raw), sha256=sha256(raw).hexdigest())
    before = deepcopy(db.data)
    response = asyncio.run(get(db, suffix="?view=summary"))
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "operational_financial_snapshot_invalid"
    assert db.data == before


@pytest.mark.parametrize("damage", ["owner", "id", "revision", "engine"])
def test_returned_report_state_scope_and_header_are_checked_independently_of_database_filter(damage):
    state = deepcopy(ReadOnlyDB().data[STATES][0])
    if damage == "owner":
        state["owner_id"] = "another-owner"
    elif damage == "id":
        state["_id"] = "another-owner"
    elif damage == "revision":
        state["revision"] = True
    else:
        state["engine"] = []
    with pytest.raises(archive.ArchiveError):
        _report_state(state, "owner")
