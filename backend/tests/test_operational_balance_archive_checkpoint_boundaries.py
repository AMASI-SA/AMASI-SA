"""One fresh gate may serve both sides of an adjacent audit-page boundary."""
import asyncio
from copy import deepcopy

from fastapi import HTTPException
import pytest

import operational_audit_prefetch as prefetch
import operational_balance_archive as archive
import operational_balance_store as store
import operational_demand_refresh as demand
from test_operational_balance_archive_prefetch import activate_fake
from test_operational_balance_archive_visible_batches import (
    archived_fixture, assert_read_only, load_visible,
)


async def observed_read(monkeypatch, *, reader, pages, packed=True):
    """Exercise the real replacement-state load with an isolated in-memory DB."""
    db, expected = archived_fixture(pages, packed=packed)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db) if reader == "prefetch" else None
    stats = {"gates": [], "operation_calls": 0}
    page_gates, page_renewals = [], []
    checkpoint = demand.checkpoint

    async def observed_checkpoint():
        page_gates.append(db.page_reads)
        previous_calls, previous_writes = len(stats["gates"]), len(db.writes)
        await checkpoint()
        # A counted boundary must execute the fresh authorization callback and
        # renew the same owned lease, rather than merely record a diagnostic.
        assert len(stats["gates"]) == previous_calls + 1
        renewals = [write for write in db.writes[previous_writes:]
                    if write[:2] == (demand.LEASES, "renew")]
        assert len(renewals) == 1
        page_renewals.append(db.page_reads)

    monkeypatch.setattr(demand, "checkpoint", observed_checkpoint)
    monkeypatch.setattr(prefetch, "checkpoint", observed_checkpoint)

    async def unchanged(state):
        stats["operation_calls"] += 1
        return state

    result, _ = await load_visible(db, monkeypatch, stats=stats,
        operation=lambda: store.mutate(db, "owner", unchanged, replacement_state=True))
    assert result == expected
    assert stats["operation_calls"] == 1
    assert db.page_reads == pages
    refs = before[store.STATES]["owner"][archive.MARKER]["pages"]
    assert [query for name, query, _ in db.reads if name == archive.AUDIT_PAGES] == [
        {"_id": ref["id"], "owner_id": "owner"} for ref in refs]
    if raw is not None:
        assert raw.started == raw.finished == pages
        assert raw.active == 0 and raw.peak <= 4
    assert_read_only(db, before)
    return {"reader": reader, "pages": pages, "packed": packed,
            "page_checkpoints": page_gates, "page_lease_renewals": page_renewals,
            "snapshot_load_checkpoints": stats["snapshot_gates"],
            "audit_page_reads": db.page_reads,
            "full_state_equal": True, "input_unchanged": True,
            "nonlease_writes": 0, "orphan_tasks": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["serial", "prefetch"])
@pytest.mark.parametrize("pages", [1, 8, 9, 16, 17, 64])
@pytest.mark.parametrize("packed", [False, True])
async def test_each_audit_boundary_has_one_fresh_checkpoint(monkeypatch, reader, pages, packed):
    observed = await observed_read(monkeypatch, reader=reader, pages=pages, packed=packed)
    boundaries = [0, *range(8, pages, 8), pages]
    assert observed["page_checkpoints"] == boundaries
    assert observed["page_lease_renewals"] == boundaries
    assert max(b - a for a, b in zip(boundaries, boundaries[1:])) <= 8
    # Metadata, restoration, and optional packet preparation remain separately
    # guarded. This assertion isolates the page-boundary saving from them.
    assert observed["snapshot_load_checkpoints"] == len(boundaries) + (6 if packed else 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["serial", "prefetch"])
@pytest.mark.parametrize("boundary", [8, 16])
@pytest.mark.parametrize("cause", ["revoked", "lease_lost"])
async def test_boundary_denial_stops_before_the_next_page(monkeypatch, reader, boundary, cause):
    db, _ = archived_fixture(boundary + 1)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db) if reader == "prefetch" else None
    stats = {"gates": [], "operation_calls": 0}

    async def deny(database, _stats):
        if database.page_reads == boundary:
            if cause == "revoked":
                raise HTTPException(403, detail={"code": "fixture_permission_revoked"})
            database.data[demand.LEASES]["owner"]["token"] = "successor-fixture"

    with pytest.raises(HTTPException) as caught:
        await load_visible(db, monkeypatch, verify_hook=deny, stats=stats)
    assert caught.value.status_code == (403 if cause == "revoked" else 409)
    assert caught.value.detail["code"] == (
        "fixture_permission_revoked" if cause == "revoked" else "operational_refresh_lease_lost")
    assert db.page_reads == boundary and stats["operation_calls"] == 0
    if raw is not None:
        assert raw.started == raw.finished == boundary and raw.active == 0
    assert {name: rows for name, rows in db.data.items() if name != demand.LEASES} == before
    assert not [write for write in db.writes if write[0] != demand.LEASES]
    if cause == "lease_lost":
        assert db.data[demand.LEASES]["owner"]["token"] == "successor-fixture"
    else:
        assert db.data.get(demand.LEASES, {}) == {}
    assert asyncio.all_tasks() == {asyncio.current_task()}
