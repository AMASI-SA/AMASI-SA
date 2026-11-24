"""Owned loopback Mongo/HTTP, real native snapshots and financial CAS only.

Other financial source families are an isolated empty cohort; advertising uses
the real source normalizer. No production URI, token, server import or provider.
"""
import asyncio
from copy import deepcopy
import os
from threading import Event
from urllib.parse import urlsplit

from fastapi import FastAPI, Header
import httpx
import pytest

from demand_read_mongo_fixture import read_db, read_mongo_uri as demand_read_mongo_uri
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, read
from test_operational_balance_ad_corrections import (
    OWNER, ACCOUNT, DAY, STAMP, KEY, SELECTION, closed_state, native_context, approved_payload, snapshot,
)


BASE = "/api/operational-balances"
pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    # Existing operational/stock CI owns its isolated replica. Accept only that
    # explicit equal loopback capability, never a production/default database.
    if not (os.environ.get("DEMAND_READ_TEST_MONGO_URI") or os.environ.get("DEMAND_READ_TEST_MONGOD")):
        supplied = {os.environ[name] for name in ("OPERATIONAL_TEST_MONGO_URI", "MZ2_TEST_MONGO_URI") if os.environ.get(name)}
        if supplied:
            assert len(supplied) == 1, "Conflicting isolated Mongo test capabilities"
            uri = supplied.pop()
            parsed = urlsplit(uri)
            assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
            assert parsed.username is None and parsed.password is None and parsed.path in ("", "/")
            yield uri
            return
    yield from demand_read_mongo_uri.__wrapped__(tmp_path_factory, request)


async def setup(db, monkeypatch, *, platform="meta", currency="SAR", wallet_first=False):
    import operational_ad_corrections as corrections
    import operational_balance_service as service
    import operational_balance_sources as sources
    import operational_profit_sources as profit
    import operational_visible_evidence as evidence
    from operational_balance_sources import _advertising, instant
    from operational_balance_store import digest

    await db.users.insert_many([
        {"id": OWNER, "role": "owner", "is_active": True},
        {"id": "synthetic-employee", "role": "employee", "created_by": OWNER,
         "is_active": True, "operational_balance_permissions": ["reports"]},
    ])
    from operational_ad_funding import party_id
    native = native_context(platform=platform, currency=currency,
        timezone_name="America/Los_Angeles" if currency == "USD" else "Asia/Riyadh")
    identity = party_id(OWNER, "synthetic-integration") if wallet_first else ACCOUNT
    selection = {**SELECTION, "account_id": identity}
    funding = None
    original = snapshot(account_id=identity, currency=currency, timezone=native["account"]["timezone"],
                        observed_at="2030-04-03T12:00:00+00:00")
    if currency != "SAR":
        fx = native["data"]["mz2_ad_fx_snapshots_v2"][0]
        original.update(fx_rate="3.7", fx_source=fx["fx_source"], fx_date=DAY, fx_at=fx["fx_at"],
                        fx_snapshot_id=fx["id"], fx_evidence=fx["evidence"], sar_amount="45.68")
    if wallet_first:
        funding = {identity: {"mode": "wallet_first", "currency": currency, "platform": platform,
            "integration_account_id": "synthetic-integration", "platform_account_id": "synthetic-external",
            "timezone": native["account"]["timezone"], "confirmed_by": OWNER,
            "confirmed_at": "2030-04-01T00:00:00+00:00", "version": 1, "evidence": "synthetic funding approval"}}
        native["data"]["mz2_ad_account_bindings_v2"] = []
        original.update(funding_type="hybrid", funding_policy="wallet_first")
    state = closed_state(original, funding=funding)
    state["movements"] = [{"id": "synthetic-manual", "party_type": "ad_account", "party_id": identity,
        "amount": "3.00", "currency": currency, "direction": "outgoing", "kind": "correction",
        "occurred_at": "2030-04-01T03:00:00+00:00", "recorded_at": "2030-04-01T03:00:00+00:00",
        "actor_id": OWNER, "source": "mezan2", "allocations": []}]
    state["openings"] = {"synthetic-opening": {"id": "synthetic-opening", "party_type": "ad_account",
        "party_id": identity, "currency": currency, "amount": "4.00", "direction": "for_party"}}
    state["requests"] = {"old-synthetic": {"hash": "old-synthetic", "result": {"ok": True}}}
    await db[STATES].insert_one(deepcopy(state))
    for name, rows in native["data"].items():
        if rows:
            await db[name].insert_many(deepcopy(rows))
    native_calls = []

    async def no_provider(database, owner, **kwargs):
        assert database is db and owner == OWNER
        native_calls.append(owner)
        return {"status": "synthetic_no_network"}

    async def collect(database, owner, started_at, stamp, **kwargs):
        state = kwargs["operational_state"]
        _, context = await corrections.read_context(database, owner, selection, state=state)
        result = {"ad_snapshots": [], "ad_balance_sar_basis": {}, "issues": [], "orders": []}
        _advertising(context["data"], result, instant(started_at), instant(stamp), state.get("source_baselines"))
        ready = kwargs.get("on_primary_reads_complete")
        if ready is not None:
            ready()
        return result

    async def no_profit(*args, **kwargs):
        return {"status": "incomplete", "source_completeness_verified": False}

    monkeypatch.setattr(corrections, "now", lambda: STAMP)
    monkeypatch.setattr(service, "now", lambda: STAMP)
    monkeypatch.setattr(evidence, "acquire_visible_evidence", no_provider)
    monkeypatch.setattr(sources, "collect_sources", collect)
    monkeypatch.setattr(profit, "load_profit_snapshot", no_profit)

    async def principal(x_test_actor: str = Header(default=OWNER)):
        return {"id": x_test_actor, "_session_client": "web"}
    app = FastAPI()
    app.include_router(make_operational_balance_router(db, principal), prefix="/api")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated")
    params = {**selection, "expected_session_scope": digest([OWNER, OWNER])}
    return client, state, params, native_calls


async def preview(client, params):
    response = await client.get(BASE + "/ad-spend-corrections/preview", params=params)
    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "private, no-store"
    reviewed = response.json()
    assert reviewed["can_approve"], reviewed
    return reviewed


def body(review):
    return {"visible": True, "ad_correction_approval": approved_payload(review)}


async def change_native(db):
    source = native_context("25.67")
    await db[source["collection"]].replace_one({"_id": "synthetic-source", "user_id": OWNER}, source["data"][source["collection"]][0])


async def test_native_preview_is_read_only_and_approval_replay_preserves_rows(read_db, monkeypatch):
    client, before, params, calls = await setup(read_db, monkeypatch)
    async with client:
        native_before = await read_db.mezan_meta_performance_daily_v2.find({}).to_list(100)
        reviewed = await preview(client, params)
        assert await read(read_db, OWNER) == before
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 200, response.text
        receipt = response.json()["ad_correction"]
        saved = await read(read_db, OWNER)
        assert saved["revision"] == 8 and saved["engine"]["obligations"][KEY]["confirmed"] == "24.56"
        assert saved["engine"]["obligations"][KEY]["business_date"] == DAY
        assert set(saved["engine"]["obligations"]) == {KEY}
        assert saved["movements"] == before["movements"] and saved["openings"] == before["openings"]
        assert saved["requests"]["old-synthetic"] == before["requests"]["old-synthetic"]
        assert len(saved["audit"]) == 1 and saved["audit"][0]["actor_id"] == OWNER
        assert await read_db.mezan_meta_performance_daily_v2.find({}).to_list(100) == native_before
        assert await read_db.general_ledger.count_documents({}) == 0
        assert await read_db.ad_account_ledger.count_documents({}) == 0
        repeated = await client.post(BASE + "/refresh", json=body(reviewed))
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["replayed"] is True and repeated.json()["ad_correction"] == receipt
        assert len(calls) == 1 and await read(read_db, OWNER) == saved
        altered = body(reviewed)
        altered["ad_correction_approval"]["reason"] = "Changed reason for the same request"
        assert (await client.post(BASE + "/refresh", json=altered)).status_code == 409
        readback = await client.get(BASE + "/ad-spend-corrections/receipts/" + receipt["request_id"], params={
            "expected_session_scope": params["expected_session_scope"], "expected_preview_hash": reviewed["preview_hash"]})
        assert readback.status_code == 200 and readback.json()["receipt"] == receipt


@pytest.mark.parametrize("phase", ["before_approval", "during_reconciliation", "during_packing", "before_commit"])
async def test_native_change_at_each_boundary_never_commits_approved_money(read_db, monkeypatch, phase):
    import operational_balance_engine as engine
    import operational_balance_store as store
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        entered, released = Event(), Event()
        if phase == "before_approval":
            await change_native(read_db)
        elif phase in {"during_reconciliation", "during_packing"}:
            module, name = (engine, "reconcile") if phase == "during_reconciliation" else (store, "prepare")
            original = getattr(module, name)
            def paused(*args, **kwargs):
                result = original(*args, **kwargs)
                entered.set()
                assert released.wait(5), "test coordinator did not release the isolated CPU task"
                return result
            monkeypatch.setattr(module, name, paused)
        else:
            original = store.stage_pages
            async def staged(db, pages):
                await original(db, pages)
                await change_native(db)
            monkeypatch.setattr(store, "stage_pages", staged)
        task = asyncio.create_task(client.post(BASE + "/refresh", json=body(reviewed)))
        try:
            if phase in {"during_reconciliation", "during_packing"}:
                assert await asyncio.to_thread(entered.wait, 5)
                await change_native(read_db)
                released.set()
            response = await task
        finally:
            released.set()
            if not task.done():
                await task
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] in {"ad_correction_preview_stale", "ad_correction_source_changed"}
        assert await read(read_db, OWNER) == before


async def test_cas_retry_requires_a_new_preview_and_cannot_reapprove_automatically(read_db, monkeypatch):
    import operational_ad_corrections as corrections
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        original = corrections.verify_before_commit
        checks = []
        async def competing(db, owner, old, new, approval):
            admission = await original(db, owner, old, new, approval)
            checks.append(old["revision"])
            await db[STATES].update_one({"_id": owner, "revision": old["revision"]}, {"$inc": {"revision": 1}})
            return admission
        monkeypatch.setattr(corrections, "verify_before_commit", competing)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "ad_correction_preview_stale", response.text
        expected = deepcopy(before)
        expected["revision"] = 8
        assert await read(read_db, OWNER) == expected
        assert checks == [7]


async def test_fresh_owner_revocation_before_commit_rejects_every_financial_effect(read_db, monkeypatch):
    import operational_balance_store as store
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        original = store.stage_pages
        async def revoked(db, pages):
            await original(db, pages)
            await db.users.update_one({"id": OWNER}, {"$set": {"role": "employee", "created_by": "foreign-owner"}})
        monkeypatch.setattr(store, "stage_pages", revoked)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 403, response.text
        assert await read(read_db, OWNER) == before


async def test_owner_only_preview_and_client_flags_fail_before_financial_work(read_db, monkeypatch):
    client, before, params, calls = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        staff = {"x-test-actor": "synthetic-employee"}
        assert (await client.get(BASE + "/ad-spend-corrections/preview", params=params, headers=staff)).status_code == 403
        assert (await client.post(BASE + "/refresh", json=body(reviewed), headers=staff)).status_code == 403
        assert (await client.get(BASE + "/ad-spend-corrections/preview", params={**params, "expected_session_scope": "f" * 64})).status_code == 403
        forged = body(reviewed)
        forged["ad_correction_approval"]["correction_approved"] = True
        assert (await client.post(BASE + "/refresh", json=forged)).status_code == 422
        assert not calls and await read(read_db, OWNER) == before


async def test_departure_after_staging_before_source_validation_never_starts_cas(read_db, monkeypatch):
    import operational_balance_store as store
    from operational_demand_refresh import demand_context
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        original = store.stage_pages
        async def departed(db, pages):
            await original(db, pages)
            context = demand_context()
            assert context.committing is True
            context.cancel(disconnected=True)
        monkeypatch.setattr(store, "stage_pages", departed)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 499, response.text
        assert await read(read_db, OWNER) == before


@pytest.mark.parametrize("cause,status", [("disconnect", 499), ("deadline", 408)])
async def test_final_authorization_cancellation_or_deadline_cannot_dispatch_cas(read_db, monkeypatch, cause, status):
    import time
    import operational_balance_store as store
    from operational_demand_refresh import demand_context
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        original = store.check_authorization
        async def final_authorization():
            await original()
            context = demand_context()
            if context is not None and context.committing:
                if cause == "disconnect": context.cancel(disconnected=True)
                else: context.read_deadline = time.monotonic() - 1
        monkeypatch.setattr(store, "check_authorization", final_authorization)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == status, response.text
        assert await read(read_db, OWNER) == before


async def test_full_existing_source_collector_accepts_the_same_native_review(read_db, monkeypatch):
    import operational_balance_sources as sources
    original = sources.collect_sources
    client, before, params, _ = await setup(read_db, monkeypatch)
    monkeypatch.setattr(sources, "collect_sources", original)
    async with client:
        reviewed = await preview(client, params)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 200, response.text
        after = await read(read_db, OWNER)
        assert after["engine"]["obligations"][KEY]["confirmed"] == "24.56"
        assert after["engine"]["obligations"][KEY]["business_date"] == DAY
        assert after["movements"] == before["movements"]


async def test_disconnect_after_cas_dispatch_observes_the_actual_saved_receipt(read_db, monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorCollection
    from operational_demand_refresh import demand_context
    original = AsyncIOMotorCollection.replace_one
    client, before, params, _ = await setup(read_db, monkeypatch)
    async with client:
        reviewed = await preview(client, params)
        observed = []
        async def dispatch(collection, *args, **kwargs):
            result = await original(collection, *args, **kwargs)
            if collection.name == STATES:
                context = demand_context()
                assert context.committing and result.matched_count == 1
                context.cancel(disconnected=True)
                observed.append(result.matched_count)
            return result
        monkeypatch.setattr(AsyncIOMotorCollection, "replace_one", dispatch)
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 200, response.text
        assert response.json()["ad_correction"]["calculation_revision"] == 8
        after = await read(read_db, OWNER)
        assert observed == [1] and after["revision"] == before["revision"] + 1
        assert after["engine"]["obligations"][KEY]["confirmed"] == "24.56"


@pytest.mark.parametrize("platform,currency,wallet_first", [
    ("snapchat", "SAR", False), ("snapchat", "USD", True), ("meta", "SAR", True),
    ("tiktok", "SAR", False), ("google_ads", "SAR", False),
])
async def test_all_native_provider_shapes_and_operational_wallet_bindings_apply_once(read_db, monkeypatch, platform, currency, wallet_first):
    client, before, params, calls = await setup(read_db, monkeypatch, platform=platform, currency=currency, wallet_first=wallet_first)
    async with client:
        reviewed = await preview(client, params)
        assert reviewed["items"][0]["currency"] == currency
        response = await client.post(BASE + "/refresh", json=body(reviewed))
        assert response.status_code == 200, response.text
        key = "advertising:" + params["account_id"] + ":" + DAY
        state = await read(read_db, OWNER)
        assert set(state["engine"]["obligations"]) == {key}
        assert state["engine"]["obligations"][key]["confirmed"] == "24.56"
        assert state["engine"]["obligations"][key]["business_date"] == DAY
        assert state["engine"]["obligations"][key]["currency"] == currency
        assert state["movements"] == before["movements"] and state["openings"] == before["openings"]
        if currency == "USD":
            assert response.json()["ad_correction"]["reviewed_changes"][0]["parts"][0]["sar_difference"] == "45.19"
        repeated = await client.post(BASE + "/refresh", json=body(reviewed))
        assert repeated.status_code == 200 and repeated.json()["replayed"] is True
        assert len(calls) == 1 and await read(read_db, OWNER) == state
