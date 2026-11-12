import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest
from bson import BSON, Decimal128

from operational_balance_archive import (
    ArchiveError, SNAPSHOT, SNAPSHOT_MARK, compact_financial_snapshot, prepare,
    unpack, unpack_financial_snapshot,
)
from operational_balance_store import initial


def large_snapshot():
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T00:30:00+03:00", revision=7)
    state["engine"] = {"as_of": "2026-11-12T01:07:44+03:00", "audit": [], "issues": [],
        "facts": {f"proof:{i}": {"order_id": str(i), "amount": "100.01", "proof": "source evidence " * 180} for i in range(90)},
        "obligations": {f"shipping:{i}": {"id": f"shipping:{i}", "confirmed": "20.01", "currency": "SAR"} for i in range(90)},
        "provider_fee_inputs": {"version": 1, "orders": {str(i): {"gross": "100.01", "data": "known source " * 100} for i in range(90)}},
        "salary_days": {"salary:employee:2026-11-01": "100.00"}}
    state["movements"] = [{"id": "retained", "amount": "97.01", "allocation": "provider:o"}]
    state["requests"] = {"retry": {"id": "retained"}}
    state["openings"] = {"bank": {"amount": "300.01"}}
    return state


def test_oversized_current_snapshot_roundtrips_all_evidence_and_history_without_dropping_rows():
    state = large_snapshot()
    before = deepcopy(state)
    assert len(BSON.encode(state)) > 100_000
    stored, pages = prepare("owner", initial("owner"), initial("owner"), state, 30_000)
    assert not pages and SNAPSHOT in stored and len(BSON.encode(stored)) < 30_000
    logical = asyncio.run(unpack(None, "owner", stored))
    assert logical == before == state
    assert stored["movements"] == state["movements"]
    assert stored["requests"] == state["requests"]
    assert stored["openings"] == state["openings"]
    assert stored["engine"]["salary_days"] == state["engine"]["salary_days"]


@pytest.mark.parametrize("damage", ["bytes", "hash", "owner", "marker", "length", "trailing", "field"])
def test_corruption_or_foreign_packets_are_rejected_before_any_amount_is_exposed(damage):
    stored = compact_financial_snapshot(large_snapshot(), 30_000)
    if damage == "bytes": stored[SNAPSHOT]["data"] = b"broken"
    elif damage == "hash": stored[SNAPSHOT]["sha256"] = "0" * 64
    elif damage == "owner": stored["owner_id"] = "other"
    elif damage == "marker": stored["engine"]["obligations"] = {}
    elif damage == "length": stored[SNAPSHOT]["size"] += 1
    elif damage == "trailing": stored[SNAPSHOT]["data"] += b"junk"
    elif damage == "field": stored[SNAPSHOT]["fields"].append("movements")
    with pytest.raises(ArchiveError): unpack_financial_snapshot(stored)


def test_missing_packet_never_turns_stored_marker_into_empty_ledger():
    with pytest.raises(ArchiveError):
        unpack_financial_snapshot({"owner_id": "owner", "engine": {"facts": SNAPSHOT_MARK}})


def test_narrow_reader_restores_only_requested_fields_and_keeps_identity_validation():
    stored = compact_financial_snapshot(large_snapshot(), 30_000)
    projected = {"owner_id": "owner", SNAPSHOT: stored[SNAPSHOT], "engine": {"obligations": SNAPSHOT_MARK}}
    result = unpack_financial_snapshot(projected, selected={"obligations"})
    assert result["engine"]["obligations"] == large_snapshot()["engine"]["obligations"]
    assert "facts" not in result["engine"]
    projected["owner_id"] = "foreign"
    with pytest.raises(ArchiveError): unpack_financial_snapshot(projected, selected={"obligations"})


def test_bson_types_and_exact_cents_survive_two_updates():
    state = large_snapshot()
    state["engine"]["facts"]["typed"] = {"amount": Decimal128(Decimal("9007199254740993.01")), "bytes": b"proof"}
    first, _ = prepare("owner", initial("owner"), initial("owner"), state, 30_000)
    logical = asyncio.run(unpack(None, "owner", first))
    changed = deepcopy(logical)
    changed["engine"]["facts"]["new"] = {"amount": "0.01"}
    second, _ = prepare("owner", first, logical, changed, 30_000)
    assert asyncio.run(unpack(None, "owner", second)) == changed


def test_narrow_reader_rejects_a_marker_not_covered_by_the_packet():
    state = initial('owner')
    state['engine']['facts'] = {'proof': 'value'}
    stored = compact_financial_snapshot(state, 30000)
    stored['engine']['obligations'] = dict(SNAPSHOT_MARK)
    with pytest.raises(ArchiveError):
        unpack_financial_snapshot(stored, selected={'obligations'})
