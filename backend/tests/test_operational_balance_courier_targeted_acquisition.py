"""One visible pending-order recovery keeps owner, source and receipt boundaries."""
import asyncio
from copy import deepcopy

from fastapi import HTTPException
import httpx
import pytest

from operational_balance_store import STATES, digest
from operational_courier_transitions import WEBHOOK_PROOF
from operational_demand_refresh import LEASES, run_visible_refresh
import operational_visible_evidence as acquisition
from salla_integration import bank_payment_sync, courier_delivery_sync as sync, paid_sar_sync
from salla_shipping import CURRENT_SHIPPING
from test_operational_balance_courier_acquisition import accept, native_provider
from test_operational_balance_courier_waybill_acquisition import WAYBILL_GAP, binding_gap, resolve, seed
from test_operational_balance_delivery_confirmation_acquisition import LocalProofDB
from test_operational_balance_delivery_cutover import DELIVERED
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_demand_refresh_routes import post


async def targeted(db, identity):
    return await run_visible_refresh(Request(), db, "owner", "owner", allowed,
        lambda: acquisition.acquire_visible_evidence(db, "owner", courier_order_id=identity))


def assert_receipt(receipt, *, selected=1, verified=0, saved=0, unchanged=0,
                   blocked=0, conflicted=0, unavailable=0):
    assert receipt["selected"] == selected
    assert receipt["verified"] == verified
    assert receipt["saved"] == saved
    assert receipt["unchanged"] == unchanged
    assert receipt["blocked"] == blocked
    assert receipt["conflicted"] == conflicted
    assert receipt["unavailable"] == unavailable


def test_target_reaches_later_pending_order_without_other_native_sources_and_obeys_cooldown(monkeypatch):
    snapshots, calls = {}, []
    native_provider(monkeypatch, snapshots, calls)

    async def forbidden(*args, **kwargs):
        raise AssertionError("A single courier target started an unrelated native source")

    monkeypatch.setattr(bank_payment_sync, "acquire_missing", forbidden)
    monkeypatch.setattr(paid_sar_sync, "acquire_missing", forbidden)

    async def scenario():
        db, originals = LocalProofDB(), {}
        for index in range(6):
            identity = str(8700 + index)
            data, source, shipment, histories = binding_gap(identity, str(9700 + index))
            snapshots[identity] = shipment, histories
            originals[identity] = deepcopy(source)
            await seed(db, source)
            if identity == "8705":
                target_data = data
                foreign = {**deepcopy(source), "user_id": "other"}
                await seed(db, foreign, issue=False)
        engine = db.data[STATES]["owner"]["engine"]
        engine["issues"].append({"code": "operational_order_bank_binding_incomplete", "source_id": "bank"})
        engine["customer_tender_coverage"] = {"sar": {"proof_lookups": [
            {"order_id": "sar", "order_number": "synthetic-sar"},
        ]}}
        before = deepcopy(db.data[STATES])

        result = await targeted(db, "8705")

        assert calls == [("8705", "/orders/8705/histories"), ("8705", "/shipments")]
        receipt = result["courier"]
        assert receipt["status"] == "completed"
        assert_receipt(receipt, verified=1, saved=1)
        assert len(receipt["outcomes"]) == 1
        assert receipt["outcomes"][0]["order_id"] == "8705"
        assert receipt["outcomes"][0]["status"] == "saved"
        recovered = await db.sources.unified_orders.find_one({"user_id": "owner", "order_id": "8705"})
        assert resolve(target_data, recovered)["delivered_at"] == DELIVERED
        assert recovered["raw_by_source"] == originals["8705"]["raw_by_source"]
        assert recovered["g47_salla_snapshot"] == originals["8705"]["g47_salla_snapshot"]

        again = await targeted(db, "8705")
        assert_receipt(again["courier"], selected=0)
        assert again["courier"]["outcomes"] == []
        assert len(calls) == 2
        for identity, original in originals.items():
            if identity != "8705":
                saved = await db.sources.unified_orders.find_one(
                    {"user_id": "owner", "order_id": identity}, {"_id": 0})
                assert saved == original
        saved_foreign = await db.sources.unified_orders.find_one(
            {"user_id": "other", "order_id": "8705"}, {"_id": 0})
        assert saved_foreign == foreign
        assert set(db.local_reads) == {"unified_orders"}
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("pending_kind", ["absent", "unrelated_issue", "other_owner"])
def test_target_must_belong_to_saved_owner_courier_gaps_before_claim_or_native_read(monkeypatch, pending_kind):
    calls = []
    _, source, shipment, histories = binding_gap("8800", "9800")
    native_provider(monkeypatch, {"8800": (shipment, histories)}, calls)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source, issue=False)
        if pending_kind == "unrelated_issue":
            db.data[STATES]["owner"]["engine"]["issues"].append(
                {"code": "operational_order_bank_binding_incomplete", "source_id": "8800"})
        elif pending_kind == "other_owner":
            foreign_state = deepcopy(db.data[STATES]["owner"])
            foreign_state.update(_id="other", owner_id="other")
            foreign_state["engine"]["issues"] = [{"code": WAYBILL_GAP, "source_id": "8800"}]
            db.data[STATES]["other"] = foreign_state
        before = deepcopy(db.data[STATES])

        with pytest.raises(HTTPException) as failed:
            await targeted(db, "8800")

        assert failed.value.status_code == 409
        assert failed.value.detail["code"] == "operational_courier_target_not_pending"
        assert calls == [] and "unified_orders" not in db.local_reads
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"}, {"_id": 0})
        assert saved == source
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


def test_target_has_no_acquisition_capability_outside_visible_request():
    class NoReads:
        def __getitem__(self, key):
            raise AssertionError("A hidden target request accessed storage")

    async def scenario():
        with pytest.raises(HTTPException) as failed:
            await acquisition.acquire_visible_evidence(NoReads(), "owner", courier_order_id="8800")
        assert failed.value.status_code == 403
        assert failed.value.detail["code"] == "operational_visible_refresh_required"

    asyncio.run(scenario())


@pytest.mark.parametrize("exit_kind", ["disconnect", "revoked", "budget"])
def test_target_stops_owned_native_work_before_proof_save_when_request_or_authority_ends(monkeypatch, exit_kind):
    _, source, shipment, histories = binding_gap("8850", "9850")
    calls = []
    native_provider(monkeypatch, {"8850": (shipment, histories)}, calls)
    original_native = sync.call_salla
    if exit_kind == "budget":
        monkeypatch.setattr(acquisition, "ACQUISITION_SECONDS", 0.05)

    async def scenario():
        db, request = LocalProofDB(), Request()
        await seed(db, source)
        before = deepcopy(db.data[STATES])
        entered, stopped = asyncio.Event(), asyncio.Event()
        revoked = False

        async def verify():
            if revoked:
                raise HTTPException(403, detail={"code": "operational_actor_scope_changed"})

        async def native(*args, **kwargs):
            nonlocal revoked
            entered.set()
            try:
                if exit_kind == "revoked":
                    revoked = True
                    return await original_native(*args, **kwargs)
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(sync, "call_salla", native)
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", verify,
            lambda: acquisition.acquire_visible_evidence(db, "owner", courier_order_id="8850")))
        await asyncio.wait_for(entered.wait(), timeout=2)
        if exit_kind == "disconnect":
            request.disconnect()
        if exit_kind == "budget":
            receipt = (await work)["courier"]
            assert receipt["status"] == "deferred"
            assert receipt["reason"] == "native_evidence_budget_exhausted"
            assert "saved" not in receipt  # Cancellation cannot claim known save counts.
        else:
            with pytest.raises(HTTPException) as failed:
                await work
            assert failed.value.status_code == (499 if exit_kind == "disconnect" else 403)
            if exit_kind == "revoked":
                assert failed.value.detail["code"] == "operational_actor_scope_changed"
                assert calls == [("8850", "/orders/8850/histories")]
        assert stopped.is_set()
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"}, {"_id": 0})
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        saved.pop(sync._RETRY)
        assert saved == source
        assert db.data[STATES] == before and db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["guard", "native_binding", "transport", "source_race"])
def test_target_receipt_distinguishes_blocked_unavailable_and_verified_but_conflicted(monkeypatch, fault):
    _, source, shipment, histories = binding_gap("8900", "9900")
    if fault == "guard":
        source["g47_salla_snapshot"]["component_pending"] = True
    elif fault == "native_binding":
        shipment["shipping_number"] = "SYNTHETIC-WRONG-WAYBILL"
    calls = []

    async def race(db):
        if fault == "source_race":
            await db.unified_orders.update_one({"user_id": "owner"},
                {"$set": {"g47_salla_snapshot.revision": 100}})

    native_provider(monkeypatch, {"8900": (shipment, histories)}, calls, during_read=race)
    if fault == "transport":
        async def unavailable(*args, **kwargs):
            raise httpx.ReadTimeout("SYNTHETIC-PRIVATE-PROVIDER-BODY")
        monkeypatch.setattr(sync, "call_salla", unavailable)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        before = deepcopy(db.data[STATES])
        receipt = (await targeted(db, "8900"))["courier"]

        if fault in {"guard", "native_binding"}:
            assert_receipt(receipt, blocked=1)
            expected_status = "blocked"
        elif fault == "transport":
            assert_receipt(receipt, unavailable=1)
            assert receipt["status"] == "deferred"
            expected_status = "unavailable"
        else:
            assert_receipt(receipt, verified=1, conflicted=1)
            expected_status = "source_changed"
        assert len(receipt["outcomes"]) == 1
        outcome = receipt["outcomes"][0]
        assert outcome["order_id"] == "8900" and outcome["status"] == expected_status
        if fault == "source_race":
            assert outcome["reason"] == "courier_native_source_changed"
        assert "SYNTHETIC-PRIVATE-PROVIDER-BODY" not in str(receipt)
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"})
        assert WEBHOOK_PROOF not in saved[CURRENT_SHIPPING]
        assert saved["raw_by_source"] == source["raw_by_source"]
        if fault == "guard":
            assert calls == []
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


def test_matched_but_unmodified_source_is_unchanged_not_a_new_saved_recovery(monkeypatch):
    _, source, shipment, histories = binding_gap("8950", "9950")
    accept(source, shipment, histories)
    calls = []
    native_provider(monkeypatch, {"8950": (shipment, histories)}, calls)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        before = deepcopy(db.data[STATES])
        receipt = (await targeted(db, "8950"))["courier"]

        assert_receipt(receipt, verified=1, unchanged=1)
        assert receipt["status"] == "completed"
        assert len(receipt["outcomes"]) == 1
        assert receipt["outcomes"][0]["order_id"] == "8950"
        assert receipt["outcomes"][0]["status"] == "unchanged"
        saved = await db.sources.unified_orders.find_one({"user_id": "owner"}, {"_id": 0})
        saved.pop(sync._RETRY)
        assert saved == source
        assert db.data[STATES] == before and db.data[LEASES] == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("scope_value,status,code", [
    (None, 400, "operational_actor_scope_required"),
    (digest(["other", "staff"]), 403, "operational_actor_scope_changed"),
])
def test_targeted_http_action_requires_current_session_scope_before_lease(monkeypatch, scope_value, status, code):
    db = DB()
    payload = {"visible": True, "courier_order_id": "8705"}
    if scope_value is not None:
        payload["expected_session_scope"] = scope_value
    response, _ = asyncio.run(post(db, monkeypatch, payload=payload))
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    assert db.writes == []


@pytest.mark.parametrize("target", ["", "x" * 81, "unsafe/../order", "unsafe order", {"$ne": None}])
def test_targeted_http_action_rejects_unsafe_or_unbounded_identity_before_work(monkeypatch, target):
    db = DB()
    response, _ = asyncio.run(post(db, monkeypatch, payload={"visible": True,
        "courier_order_id": target, "expected_session_scope": digest(["owner", "staff"])}))
    assert response.status_code == 422, response.text
    assert db.writes == []


def test_targeted_http_action_exposes_native_receipt_and_performs_exactly_one_aggregate(monkeypatch):
    _, source, shipment, histories = binding_gap("8960", "9960")
    calls = []
    native_provider(monkeypatch, {"8960": (shipment, histories)}, calls)

    async def scenario():
        db = LocalProofDB()
        await seed(db, source)
        response, _ = await post(db, monkeypatch, payload={"visible": True,
            "courier_order_id": "8960", "expected_session_scope": digest(["owner", "staff"])})

        assert response.status_code == 200, response.text
        value = response.json()
        assert value["status"] == "refreshed"
        assert value["session_scope"] == digest(["owner", "staff"])
        assert_receipt(value["native_evidence"]["courier"], verified=1, saved=1)
        assert calls == [("8960", "/orders/8960/histories"), ("8960", "/shipments")]
        assert value["revision"] == db.data[STATES]["owner"]["revision"] == 2
        assert sum(name == STATES and operation == "replace" for name, operation, *_ in db.writes) == 1
        assert db.data[LEASES] == {}

    asyncio.run(scenario())
