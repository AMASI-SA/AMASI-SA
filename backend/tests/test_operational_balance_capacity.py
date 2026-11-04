"""Large owner snapshots must refresh without dropping history or replaying payments."""
import asyncio
from copy import deepcopy

import pytest
from bson import BSON
from fastapi import HTTPException

import operational_balance_store as store
from operational_balance_service import create_movement, refresh, report
from operational_balance_store import MAX_STATE_BYTES, STATES, mutate, read
from test_operational_balance_integration import movement, run
from test_operational_balance_order_page_payment_flow import (
    T1, T2, T3, T4, order_document, provider_view, start,
)


async def seed_legacy_history(db):
    limit = store.MAX_STATE_BYTES
    await start(db, "tamara")
    state = await read(db, "owner")
    history = [
        {"id": str(i), "action": "operational_reconciled",
         "before": None, "after": {"reference": str(i), "note": "a" * 4000}}
        for i in range(limit // 4200)
    ]
    state["engine"]["audit"] = history
    # Leave a valid inline record, just under the threshold. The new order
    # (including its claim, proof, and audit event) must cross it on refresh.
    extra = limit - len(BSON.encode(state)) - 256
    assert extra > 0
    history[-1]["after"]["note"] += "b" * extra
    # Seed the legacy layout directly in the isolated database.
    await db[STATES].delete_one({"_id": "owner"})
    await db[STATES].insert_one(state)
    return state


def test_large_legacy_state_refreshes_and_pays_a_new_claim_once():
    async def scenario(db):
        legacy = await seed_legacy_history(db)
        order = order_document("tamara_installment", "100.00", "0.00")
        await db.unified_orders.insert_one(order)

        await refresh(db, "owner", clock=T1)
        saved = await read(db, "owner")
        assert saved["engine"]["as_of"] == T1
        assert len(BSON.encode(saved)) > MAX_STATE_BYTES
        assert len(BSON.encode(await db[STATES].find_one({"_id": "owner"}))) <= MAX_STATE_BYTES
        history = legacy["engine"]["audit"]
        assert saved["engine"]["audit"][:len(history)] == history
        assert provider_view(saved, "tamara")["outstanding_receivable"] == "600.00"

        facts = deepcopy(saved["engine"]["facts"])
        repeated = await refresh(db, "owner", clock=T2)
        assert repeated["engine"]["facts"] == facts
        assert repeated["openings"] == legacy["openings"]
        assert repeated["movements"] == []

        payout = dict(movement(), request_id="same-statement", party_type="provider", party_id="tamara",
                      direction="incoming", kind="settlement", amount="97.00", actual_fee_amount="3.00",
                      reference="test-statement", allocations=[
                          {"obligation_id": "provider:" + order["salla_order_id"], "amount": "100.00"}])
        a, b = await asyncio.gather(*(create_movement(db, "owner", "owner", payout, clock=T3) for _ in range(2)))
        assert a["id"] == b["id"]
        await refresh(db, "owner", clock=T4)

        final = await read(db, "owner")
        assert final["engine"]["as_of"] == T4
        assert final["engine"]["audit"][:len(history)] == history
        assert final["audit"][:len(legacy["audit"])] == legacy["audit"]
        assert all(final["requests"][key] == row for key, row in legacy["requests"].items())
        assert final["openings"] == legacy["openings"]
        assert [m["id"] for m in final["movements"]] == [a["id"]]
        assert provider_view(final, "tamara")["outstanding_receivable"] == "500.00"
        assert report(final)["summary"]["actual_liquidity"] == "1097.00"

    run(scenario)


def test_damaged_or_missing_archive_blocks_reads_and_writes(monkeypatch):
    monkeypatch.setattr(store, "MAX_STATE_BYTES", 1024 * 1024)

    async def scenario(db):
        await seed_legacy_history(db)
        await db.unified_orders.insert_one(order_document("tamara_installment", "100.00", "0.00"))
        await refresh(db, "owner", clock=T1)
        head = await db[STATES].find_one({"_id": "owner"})
        page_id = head["engine_audit_archive"]["pages"][0]["id"]
        pages = db["operational_balance_audit_pages_v1"]
        original = await pages.find_one({"_id": page_id, "owner_id": "owner"})
        assert original and original["events"]

        async def unexpected_write(state):
            pytest.fail("Corrupted history must block the callback before it touches money")

        # Equal event count alone must not make damaged/foreign data trustworthy.
        for change in ({"events.0.action": "tampered"}, {"owner_id": "other"}):
            await pages.update_one({"_id": page_id}, {"$set": change})
            with pytest.raises(HTTPException):
                await read(db, "owner")
            with pytest.raises(HTTPException):
                await mutate(db, "owner", unexpected_write)
            assert await db[STATES].find_one({"_id": "owner"}) == head
            await pages.replace_one({"_id": page_id}, original)
        await pages.delete_one({"_id": page_id})
        with pytest.raises(HTTPException):
            await read(db, "owner")
        with pytest.raises(HTTPException):
            await mutate(db, "owner", unexpected_write)
        assert await db[STATES].find_one({"_id": "owner"}) == head

    run(scenario)


def test_interrupted_archive_publication_keeps_previous_snapshot_readable(monkeypatch):
    from pymongo.errors import AutoReconnect

    monkeypatch.setattr(store, "MAX_STATE_BYTES", 1024 * 1024)

    async def scenario(db):
        before = await seed_legacy_history(db)
        await db.unified_orders.insert_one(order_document("tamara_installment", "100.00", "0.00"))
        collection_type = type(db[STATES])
        replace = collection_type.replace_one

        async def interrupt_publication(collection, *args, **kwargs):
            if collection.name == STATES:
                raise AutoReconnect("injected failure before publishing the owner revision")
            return await replace(collection, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(collection_type, "replace_one", interrupt_publication)
            with pytest.raises(AutoReconnect):
                await refresh(db, "owner", clock=T1)

        assert await read(db, "owner") == before
        await refresh(db, "owner", clock=T1)
        saved = await read(db, "owner")
        assert saved["engine"]["audit"][:len(before["engine"]["audit"])] == before["engine"]["audit"]
        assert provider_view(saved, "tamara")["outstanding_receivable"] == "600.00"
        assert saved["movements"] == []
    run(scenario)


def test_oversized_issue_list_without_archivable_history_roundtrips_and_rejects_corruption(monkeypatch):
    monkeypatch.setattr(store, "MAX_STATE_BYTES", 256 * 1024)

    async def scenario(db):
        openings = await start(db, "tamara")
        before = await read(db, "owner")
        issues = [{"code": "payment_evidence_incomplete", "source_id": f"order-{i}",
                   "message": "A dated payment record is required."} for i in range(3200)]
        assert len(BSON.encode({"issues": issues})) > store.MAX_STATE_BYTES

        async def set_issues(state):
            state["engine"]["issues"] = issues

        await mutate(db, "owner", set_issues)
        saved = await read(db, "owner")
        assert saved["engine"]["issues"] == issues
        assert saved["openings"] == openings
        assert saved["movements"] == before["movements"]
        assert saved["requests"] == before["requests"]

        async def resolve_one(state):
            assert state["engine"]["issues"].pop(0) == issues[0]
            state["engine"]["issues"].append({"code": "currency_conflict", "source_id": "new"})

        await mutate(db, "owner", resolve_one)
        resolved = await read(db, "owner")
        assert resolved["engine"]["issues"] == issues[1:] + [{"code": "currency_conflict", "source_id": "new"}]
        assert resolved["openings"] == openings and resolved["movements"] == []
        assert await db["operational_balance_audit_pages_v1"].count_documents({}) == 0

        owner_row = await db[STATES].find_one({"_id": "owner"})
        foreign = store.initial("other")
        foreign["packed_source_issues_v1"] = owner_row["packed_source_issues_v1"]
        foreign["engine"]["issues"] = owner_row["engine"]["issues"]
        await db[STATES].insert_one(foreign)
        with pytest.raises(HTTPException):
            await read(db, "other")

        await db[STATES].update_one({"_id": "owner"}, {"$set": {"packed_source_issues_v1.data": b"broken"}})
        unchanged = await db[STATES].find_one({"_id": "owner"})

        async def unexpected_write(state):
            pytest.fail("Invalid packed issues must block mutation")

        with pytest.raises(HTTPException):
            await read(db, "owner")
        with pytest.raises(HTTPException):
            await mutate(db, "owner", unexpected_write)
        assert await db[STATES].find_one({"_id": "owner"}) == unchanged

    run(scenario)


def test_second_overflow_reuses_the_archived_prefix_in_order(monkeypatch):
    monkeypatch.setattr(store, "MAX_STATE_BYTES", 1024 * 1024)

    async def scenario(db):
        await seed_legacy_history(db)
        await db.unified_orders.insert_one(order_document("tamara_installment", "100.00", "0.00"))
        await refresh(db, "owner", clock=T1)
        previous = await read(db, "owner")
        first_head = await db[STATES].find_one({"_id": "owner"})
        first_pages = first_head["engine_audit_archive"]["pages"]
        additions = [{"id": "second-" + str(i), "action": "operational_reconciled",
                      "after": {"note": "c" * 4100}} for i in range(280)]

        async def append_evidence(state):
            state["engine"]["audit"].extend(additions)

        await mutate(db, "owner", append_evidence)
        current = await read(db, "owner")
        second_head = await db[STATES].find_one({"_id": "owner"})
        all_pages = second_head["engine_audit_archive"]["pages"]
        assert len(all_pages) > len(first_pages) and all_pages[:len(first_pages)] == first_pages
        assert current["engine"]["audit"] == previous["engine"]["audit"] + additions
        assert current["openings"] == previous["openings"]
        assert current["movements"] == previous["movements"]
        assert current["engine"]["obligations"] == previous["engine"]["obligations"]

    run(scenario)
