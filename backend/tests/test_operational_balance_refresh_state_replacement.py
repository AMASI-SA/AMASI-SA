"""Refresh owns one independent successor while preserving the complete prior state.

Offline source adapters exercise the real collector, reducer, archive and CAS.
The copy counter is a deterministic resource check, not a wall-clock assertion.
"""
import asyncio
from copy import deepcopy

from bson import Decimal128
from fastapi import HTTPException
import pytest

import operational_balance_archive as archive
import operational_balance_engine as engine
import operational_balance_service as service
import operational_balance_store as store
import operational_demand_refresh as demand
import operational_profit_sources as profit
from test_operational_balance_demand_refresh import Collection, DB as StoreDB, Request, allowed
from test_operational_balance_source_stage_timing import DB, NOW, START


OWNER = "owner"


def setup(monkeypatch):
    database = DB()
    state = database.data[store.STATES][OWNER]
    state["requests"] = {"retained-request": {"hash": "original", "result": {"id": "retained"}}}
    state["audit"] = [{"id": "retained-audit", "action": "opening_saved", "before": None,
                       "after": {"amount": "250.00", "proof": b"original proof"}}]
    state["engine"] = {"audit": [{"id": "retained-engine-audit", "action": "operational_reconciled",
                                  "before": None, "after": {"amount": "10.00"}}]}

    async def saved_profit(db, owner, period, *, as_of):
        assert db is database and owner == period["owner_id"] == OWNER
        assert period["started_at"] == START and as_of == NOW
        return {"owner_id": owner, "as_of": as_of, "fixture": "same profit observation"}

    monkeypatch.setattr(profit, "load_profit_snapshot", saved_profit)
    return database


async def visible(database, *, request=None):
    return await demand.run_visible_refresh(
        request or Request(), database, OWNER, OWNER, allowed,
        lambda: service.refresh(database, OWNER, clock=NOW))


def observe_inputs(monkeypatch):
    observed = []
    original = store.logical_state

    async def read(db, owner, stored):
        state = await original(db, owner, stored)
        observed.append((state, deepcopy(state)))
        return state

    monkeypatch.setattr(store, "logical_state", read)
    return observed


def assert_finished(database):
    assert database.data[demand.LEASES] == {}
    assert database.inflight == 0
    assert demand.demand_context() is None
    assert asyncio.all_tasks() == {asyncio.current_task()}


def test_refresh_copies_the_complete_aggregate_once_and_preserves_its_input(monkeypatch):
    async def scenario():
        database = setup(monkeypatch)
        source_before = deepcopy(database.sources.data)
        observed = observe_inputs(monkeypatch)
        copies = []

        def observe(copier):
            def counted(value, *args, **kwargs):
                if isinstance(value, dict) and value.get("owner_id") == OWNER and "revision" in value:
                    copies.append(value["revision"])
                return copier(value, *args, **kwargs)
            return counted

        monkeypatch.setattr(store, "deepcopy", observe(deepcopy))
        monkeypatch.setattr(engine, "copy_report_value", observe(engine.copy_report_value))
        result = await visible(database)
        assert result == database.data[store.STATES][OWNER]
        assert result["revision"] == 2 and result["engine"]["as_of"] == NOW
        assert result["requests"] == observed[0][1]["requests"]
        assert result["audit"] == observed[0][1]["audit"]
        assert result["engine"]["audit"][:1] == observed[0][1]["engine"]["audit"]
        assert all(state == original and state is not result for state, original in observed)
        assert database.sources.data == source_before
        assert copies == [1]
        assert_finished(database)

    asyncio.run(scenario())


def test_full_refresh_result_matches_the_original_copy_then_mutate_contract(monkeypatch):
    async def scenario():
        current = setup(monkeypatch)
        original_sources = deepcopy(current.sources.data)
        actual = await visible(current)
        reads = list(current.sources.reads)
        current_saved = deepcopy(current.data[store.STATES])

        legacy = setup(monkeypatch)
        original_mutate = store.mutate

        async def legacy_copy(db, owner, operation, **options):
            options.pop("replacement_state", None)

            async def in_place(state):
                successor = await operation(state)
                state.update(successor)
                return state

            return await original_mutate(db, owner, in_place, **options)

        monkeypatch.setattr(service, "mutate", legacy_copy)
        expected = await visible(legacy)
        assert actual == expected
        assert current_saved == legacy.data[store.STATES]
        assert reads == legacy.sources.reads
        assert current.sources.data == legacy.sources.data == original_sources
        assert [row for row in current.writes if row[0] != demand.LEASES] == [
            row for row in legacy.writes if row[0] != demand.LEASES]
        assert_finished(current)
        assert_finished(legacy)

    asyncio.run(scenario())


def test_cas_retry_reloads_the_new_revision_without_mutating_either_input(monkeypatch):
    async def scenario():
        database = setup(monkeypatch)
        observed = observe_inputs(monkeypatch)
        original_replace = Collection.replace_one
        revisions = []
        concurrent = {"id": "concurrent-owner-audit", "action": "owner_operation",
                      "after": {"amount": "17.25"}}

        async def replace(collection, query, successor):
            if collection.name == store.STATES:
                revisions.append(query["revision"])
                if len(revisions) == 1:
                    database.reject_cas = True
                    try:
                        result = await original_replace(collection, query, successor)
                    finally:
                        database.reject_cas = False
                    saved = database.data[store.STATES][OWNER]
                    saved["revision"] = 2
                    saved["audit"].append(deepcopy(concurrent))
                    return result
            return await original_replace(collection, query, successor)

        monkeypatch.setattr(Collection, "replace_one", replace)
        result = await visible(database)
        assert revisions == [1, 2] and result["revision"] == 3
        assert len(observed) == 2 and observed[0][0] is not observed[1][0]
        assert [original["revision"] for _, original in observed] == [1, 2]
        assert all(state == original for state, original in observed)
        assert concurrent not in observed[0][1]["audit"]
        assert observed[1][1]["audit"][-1] == result["audit"][-1] == concurrent
        assert len([entry for entry in result["audit"] if entry["id"] == concurrent["id"]]) == 1
        assert len([name for name in database.sources.reads if name == "unified_orders"]) >= 2
        assert_finished(database)

    asyncio.run(scenario())


def test_failed_reconciliation_keeps_the_previous_complete_state(monkeypatch):
    async def scenario():
        database = setup(monkeypatch)
        before = deepcopy(database.data[store.STATES])
        source_before = deepcopy(database.sources.data)
        observed = observe_inputs(monkeypatch)
        original_reconcile = engine.reconcile

        def fail_after_preparation(*args):
            successor = original_reconcile(*args)
            successor["audit"][0]["after"]["amount"] = "999.99"
            raise ValueError("synthetic failure after independent preparation")

        monkeypatch.setattr(engine, "reconcile", fail_after_preparation)
        with pytest.raises(ValueError, match="synthetic failure"):
            await visible(database)
        assert database.data[store.STATES] == before
        assert all(state == original for state, original in observed)
        assert database.sources.data == source_before
        assert not [write for write in database.writes if write[0] != demand.LEASES]
        assert_finished(database)

    asyncio.run(scenario())


def test_disconnect_after_reconciliation_discards_successor_and_joins_profit(monkeypatch):
    async def scenario():
        database = setup(monkeypatch)
        before = deepcopy(database.data[store.STATES])
        observed = observe_inputs(monkeypatch)
        prepared, profit_stopped = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        original_reconcile = engine.reconcile

        def calculate(*args):
            result = original_reconcile(*args)
            loop.call_soon_threadsafe(prepared.set)
            return result

        async def pending_profit(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                profit_stopped.set()

        monkeypatch.setattr(engine, "reconcile", calculate)
        monkeypatch.setattr(profit, "load_profit_snapshot", pending_profit)
        request = Request()
        work = asyncio.create_task(visible(database, request=request))
        try:
            await asyncio.wait_for(prepared.wait(), 5)
            request.disconnect()
            with pytest.raises(HTTPException) as failure:
                await asyncio.wait_for(work, 5)
            assert failure.value.status_code == 499
        finally:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        assert profit_stopped.is_set()
        assert database.data[store.STATES] == before
        assert all(state == original for state, original in observed)
        assert not [write for write in database.writes if write[0] != demand.LEASES]
        assert_finished(database)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["draft", "newer_snapshot"])
def test_refresh_no_op_returns_unchanged_state_without_copy_or_cas(monkeypatch, reason):
    async def scenario():
        database = setup(monkeypatch)
        state = database.data[store.STATES][OWNER]
        if reason == "draft":
            state["status"] = "draft"
        else:
            state["engine"]["as_of"] = "2099-01-01T00:00:00+00:00"
        before = deepcopy(state)
        observed = observe_inputs(monkeypatch)

        def unexpected_copy(*args, **kwargs):
            pytest.fail("A refresh no-op must not copy the complete aggregate")

        monkeypatch.setattr(store, "deepcopy", unexpected_copy)
        result = await visible(database)
        assert result == before == database.data[store.STATES][OWNER]
        assert result is observed[0][0]
        assert database.sources.reads == []
        assert not [write for write in database.writes if write[0] != demand.LEASES]
        assert_finished(database)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [None, "receipt", "_id", "owner_id", "revision", "missing_revision", "bool_revision"])
def test_replacement_contract_rejects_wrong_identity_or_receipt_without_save(monkeypatch, invalid):
    async def scenario():
        database = setup(monkeypatch)
        before = deepcopy(database.data[store.STATES])
        observed = observe_inputs(monkeypatch)

        async def wrong(state):
            if invalid is None:
                return None
            if invalid == "receipt":
                return {"status": "accepted"}
            candidate = deepcopy(state)
            if invalid == "missing_revision":
                candidate.pop("revision")
            elif invalid == "bool_revision":
                candidate["revision"] = True
            else:
                candidate[invalid] = 400 if invalid == "revision" else "another-owner"
            return candidate

        with pytest.raises(HTTPException) as failure:
            await store.mutate(database, OWNER, wrong, replacement_state=True)
        assert failure.value.detail["code"] == "operational_replacement_state_invalid"
        assert database.data[store.STATES] == before
        assert all(state == original for state, original in observed)
        assert database.writes == []

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [None, {"status": "accepted", "movement_id": "manual"}])
def test_default_manual_writer_still_gets_an_isolated_mutable_copy(monkeypatch, response):
    async def scenario():
        database = setup(monkeypatch)
        observed = observe_inputs(monkeypatch)

        async def manual(state):
            state["audit"][0]["after"]["amount"] = "50.00"
            state["requests"]["manual-request"] = {"result": {"id": "manual"}}
            return response

        result = await store.mutate(database, OWNER, manual)
        saved = database.data[store.STATES][OWNER]
        assert result == response
        assert saved["revision"] == 2 and saved["audit"][0]["after"]["amount"] == "50.00"
        assert saved["requests"]["manual-request"]["result"]["id"] == "manual"
        assert observed[0][0] == observed[0][1]
        assert observed[0][0]["audit"][0]["after"]["amount"] == "250.00"
        assert [write[1] for write in database.writes] == ["replace"]

    asyncio.run(scenario())


def archived_setup(monkeypatch):
    database = setup(monkeypatch)
    state = database.data[store.STATES][OWNER]
    events = [{"id": "immutable-audit", "action": "operational_reconciled", "before": None,
               "after": {"amount": Decimal128("9007199254740993.01"), "proof": b"exact bytes"}}]
    key = archive.content_key(OWNER, 0, events)
    database.data[archive.AUDIT_PAGES] = {
        key: {"_id": key, "owner_id": OWNER, "start": 0, "events": events}}
    state[archive.MARKER] = archive.manifest(1, [{"id": key, "count": 1}])
    state["engine"]["audit"] = {"tail": state["engine"]["audit"]}
    state["engine"]["facts"] = {"proof:retained": {"amount": Decimal128("10.01"), "bytes": b"saved"}}
    database.data[store.STATES][OWNER] = archive.compact_financial_snapshot(state, 30000)
    return database


def test_refresh_preserves_packed_types_archive_hash_and_original_prefix(monkeypatch):
    async def scenario():
        database = archived_setup(monkeypatch)
        before = await store.read(database, OWNER)
        pages = deepcopy(database.data[archive.AUDIT_PAGES])
        manifest = deepcopy(database.data[store.STATES][OWNER][archive.MARKER])
        observed = observe_inputs(monkeypatch)
        result = await visible(database)
        saved = await store.read(database, OWNER)
        assert result == saved
        assert saved["engine"]["audit"][:len(before["engine"]["audit"])] == before["engine"]["audit"]
        assert saved["engine"]["facts"]["proof:retained"] == before["engine"]["facts"]["proof:retained"]
        assert database.data[archive.AUDIT_PAGES] == pages
        assert database.data[store.STATES][OWNER][archive.MARKER] == manifest
        assert all(state == original for state, original in observed)
        assert not [write for write in database.writes if write[0] == archive.AUDIT_PAGES]
        assert_finished(database)

    asyncio.run(scenario())


def test_corrupt_archive_still_blocks_replacement_before_source_collection(monkeypatch):
    async def scenario():
        database = archived_setup(monkeypatch)
        next(iter(database.data[archive.AUDIT_PAGES].values()))["events"][0]["action"] = "tampered"
        before = deepcopy(database.data[store.STATES])
        with pytest.raises(HTTPException) as failure:
            await visible(database)
        assert failure.value.detail["code"] == "operational_audit_archive_invalid"
        assert database.data[store.STATES] == before and database.sources.reads == []
        assert not [write for write in database.writes if write[0] != demand.LEASES]
        assert_finished(database)

    asyncio.run(scenario())


def test_replacement_cannot_change_an_existing_archived_audit_prefix(monkeypatch):
    async def scenario():
        database = archived_setup(monkeypatch)
        before = deepcopy(database.data)
        observed = observe_inputs(monkeypatch)
        original_reconcile = engine.reconcile

        def tampered_successor(*args):
            candidate = original_reconcile(*args)
            candidate["engine"]["audit"][0]["after"]["proof"] = b"not the original proof"
            return candidate

        monkeypatch.setattr(engine, "reconcile", tampered_successor)
        with pytest.raises(HTTPException) as failure:
            await visible(database)
        assert failure.value.detail["code"] == "operational_audit_archive_invalid"
        assert {name: rows for name, rows in database.data.items() if name != demand.LEASES} == before
        assert all(state == original for state, original in observed)
        assert not [write for write in database.writes if write[0] != demand.LEASES]
        assert_finished(database)

    asyncio.run(scenario())


def test_real_ad_approval_and_final_admission_receive_distinct_unchanged_prior_state(monkeypatch):
    import operational_ad_corrections as ads
    import operational_balance_inventory as inventory
    import operational_balance_sources as sources
    import test_operational_balance_ad_corrections as fixture

    async def scenario():
        database = StoreDB()
        prior, native, reviewed, private = fixture.review_context()
        database.data[store.STATES] = {fixture.OWNER: deepcopy(prior)}
        saved_prior, saved_native = deepcopy(prior), deepcopy(native)
        payload = fixture.approved_payload(reviewed)
        approval = {"actor_id": fixture.OWNER, "payload": payload}
        observed = observe_inputs(monkeypatch)
        admitted = []

        async def read_context(db, owner, selection, *, state=None):
            assert db is database and owner == fixture.OWNER
            assert state == saved_prior and selection == fixture.SELECTION
            return state, native

        async def collect(db, owner, start, stamp, *, baselines, operational_state,
                          on_primary_reads_complete=None):
            assert operational_state == saved_prior
            if on_primary_reads_complete is not None:
                on_primary_reads_complete()
            return {"orders": [], "ad_snapshots": [deepcopy(private[fixture.DAY]["snapshot"])]}

        async def no_collision(*args):
            pass

        async def names(*args, **kwargs):
            return [{"id": fixture.ACCOUNT, "name": "Synthetic Ads"}]

        async def saved_profit(*args, **kwargs):
            return {"owner_id": fixture.OWNER, "as_of": fixture.STAMP}

        original_admission = ads.verify_before_commit

        async def admission(db, owner, before, after, accepted):
            assert before == saved_prior and before is not after
            assert before["engine"] is not after["engine"]
            assert accepted is approval
            admitted.append((before, deepcopy(before), after))
            return await original_admission(db, owner, before, after, accepted)

        monkeypatch.setattr(ads, "read_context", read_context)
        monkeypatch.setattr(ads, "now", lambda: fixture.STAMP)
        monkeypatch.setattr(ads, "verify_before_commit", admission)
        monkeypatch.setattr(inventory, "assert_no_source_collision", no_collision)
        monkeypatch.setattr(sources, "collect_sources", collect)
        monkeypatch.setattr(sources, "entities", names)
        monkeypatch.setattr(profit, "load_profit_snapshot", saved_profit)
        result = await demand.run_visible_refresh(
            Request(), database, fixture.OWNER, fixture.OWNER, allowed,
            lambda: service.refresh(database, fixture.OWNER, clock=fixture.STAMP, ad_correction=approval))
        assert result["engine"]["obligations"][fixture.KEY]["confirmed"] == "24.56"
        assert saved_prior["engine"]["obligations"][fixture.KEY]["confirmed"] == "12.35"
        assert result["revision"] == saved_prior["revision"] + 1
        receipt = ads.approval_receipt(result, approval)
        assert receipt["manual_history_preserved"] is True
        assert receipt["previous_revision"] == saved_prior["revision"]
        assert receipt["calculation_revision"] == result["revision"]
        assert len(admitted) == 1 and admitted[0][0] == admitted[0][1] == saved_prior
        assert all(state == original for state, original in observed)
        assert native == saved_native
        assert database.data[store.STATES][fixture.OWNER] == result
        assert database.data[demand.LEASES] == {} and demand.demand_context() is None
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())
