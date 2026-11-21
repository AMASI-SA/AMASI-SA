"""Bounded, read-only historical veto before excluding waiting payment rows."""
import asyncio
from copy import deepcopy
import os
import uuid

from mongomock_motor import AsyncMongoMockClient
import pytest

import awaiting_payment_history as history
from awaiting_payment_eligibility import ELIGIBILITY_FIELD
from operational_balance_archive import compact_financial_snapshot


def test_pure_dashboard_import_and_no_candidate_history_do_not_require_motor():
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-B", "-c", """
import asyncio
import importlib.abc
import sys

class NoMotor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'motor' or fullname.startswith('motor.'):
            raise ModuleNotFoundError('Motor must remain optional for pure dashboard imports', name='motor')

sys.meta_path.insert(0, NoMotor())
import dashboard_v2_routes
from awaiting_payment_history import guard_financial_cohort_history

class NoDatabaseReads:
    def __getitem__(self, name):
        raise AssertionError('No-candidate path must not access the database')

assert asyncio.run(guard_financial_cohort_history(NoDatabaseReads(), 'owner', [])) == []
assert 'operational_read_runtime' not in sys.modules
"""], cwd=backend, env={**os.environ, "PYTHONPATH": str(backend), "PYTHON_DOTENV_DISABLED": "1"},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def candidate(number="300000001"):
    return {"user_id": "owner", "order_number": number, "order_id": "native-" + number,
            "payment_method": "wating", "total_product_cost": 50,
            ELIGIBILITY_FIELD: {"eligible": False, "review_required": False,
                                "reason_code": "awaiting_payment_zero_paid"}}


def state(facts=None, obligations=None, movements=None):
    return {"owner_id": "owner", "engine": {"facts": facts or {}, "obligations": obligations or {}},
            "movements": movements or []}


def run(fn):
    async def scenario():
        db = AsyncMongoMockClient()["awaiting_history"]
        return await fn(db)
    return asyncio.run(scenario())


def retained(row):
    result = row[ELIGIBILITY_FIELD]
    assert result["eligible"] is True and result["review_required"] is True
    return result


@pytest.mark.parametrize("count", [1, 1000])
def test_owner_history_is_scanned_once_off_event_loop_across_batches(monkeypatch, count):
    """Large cohorts must not rescan the whole merchant ledger per batch."""
    import threading
    original = history.known_financial_history
    scans = []
    main_thread = threading.get_ident()

    def instrumented(current, orders, *, owner):
        scans.append((threading.get_ident(), len(orders)))
        return original(current, orders, owner=owner)

    async def empty_sources(db, owner, specs):
        return {name: [] for name in specs}

    monkeypatch.setattr(history, "known_financial_history", instrumented)
    monkeypatch.setattr(history, "_load", empty_sources)

    async def scenario(db):
        rows = [candidate(str(300000001 + index)) for index in range(count)]
        # Evidence for the last batch is retained after a single global scan.
        last = rows[-1]
        given = state({"capture:opaque": {"order_id": last["order_id"], "amount": "10"}})
        result = await history.guard_financial_cohort_history(db, "owner", rows, state=given)
        assert retained(result[-1])["reason_code"] == "awaiting_payment_prior_financial_history"
        assert all(row[ELIGIBILITY_FIELD]["eligible"] is False for row in result[:-1])
        assert len(scans) == 1
        assert scans[0][1] == count
        assert scans[0][0] != main_thread

    run(scenario)


def test_empty_history_authorizes_only_proposed_exclusion_preserves_registry_and_inputs():
    async def scenario(db):
        rows = [candidate(), {**candidate("300000002"), "payment_method": "cod",
                             ELIGIBILITY_FIELD: {"eligible": True, "review_required": False}}]
        before = deepcopy(rows)
        result = await history.guard_financial_cohort_history(db, "owner", rows)
        assert len(result) == len(rows) and rows == before
        assert result[0][ELIGIBILITY_FIELD]["eligible"] is False
        assert result[0][ELIGIBILITY_FIELD]["history_checked"] is True
        assert result[1] == rows[1]
        assert await db[history.STATES].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize("key,payload", [
    ("capture:opaque", {"order_id": "native-300000001", "amount": "10"}),
    ("refund:paypal:opaque", {"order_id": "native-300000001", "amount": "10"}),
    ("provider_order_balance:native-300000001", {"paid": "10"}),
    ("provider_split:native-300000001", {"amount": "10"}),
    ("cod_collection:native-300000001", {"amount": "10"}),
    ("shipping:native-300000001:return", {"cost": "10"}),
    ("supplier-receipt-opaque", {"order_id": "native-300000001", "item_id": "piece"}),
    ("completed_shipping:native-300000001", {"amount": "0"}),
])
def test_immutable_payment_or_cost_facts_veto_exclusion(key, payload):
    async def scenario(db):
        given = state({key: payload})
        before = deepcopy(given)
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()], state=given)
        assert retained(result[0])["reason_code"] == "awaiting_payment_prior_financial_history"
        assert given == before
    run(scenario)


def test_state_binding_is_exact_and_reserved_obligation_is_not_actual():
    given = state({"capture:native-3000000019": {"amount": "100"}, "capture:1": {"order_id": True}},
                  {"supplier:native-300000001": {"confirmed": "0.00", "expected": "50"}})
    assert history.known_financial_history(given, [candidate()], owner="owner") == set()
    given["engine"]["obligations"]["supplier:native-300000001"]["confirmed"] = "15"
    assert history.known_financial_history(given, [candidate()], owner="owner") == {"300000001"}


@pytest.mark.parametrize("orphan", [False, True])
def test_previous_movement_allocation_retains_order_even_if_obligation_is_now_zero(orphan):
    async def scenario(db):
        given = state(obligations={} if orphan else {"shipping:native-300000001": {"confirmed": "0.00", "expected": "17.25"}},
                      movements=[{"allocations": [{"obligation_id": "shipping:native-300000001", "amount": "17.25"}]}])
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()], state=given)
        assert retained(result[0])["reason_code"] == "awaiting_payment_prior_financial_history"
    run(scenario)


@pytest.mark.parametrize("allocations", [True, {}, [True], [{"obligation_id": True}],
                                         [{"obligation_id": ""}], [{"obligation_id": "  "}]])
def test_malformed_allocation_cannot_prove_history_absence(allocations):
    async def scenario(db):
        given = state(movements=[{"allocations": allocations}])
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()], state=given)
        assert retained(result[0])["reason_code"] == "awaiting_payment_history_unavailable"
    run(scenario)


@pytest.mark.parametrize("packed", [False, True])
def test_owner_state_reads_inline_or_validated_packed_history(packed):
    async def scenario(db):
        given = state({"capture:opaque": {"order_id": "native-300000001"}})
        if packed:
            given = compact_financial_snapshot(given, 1000000)
        await db[history.STATES].insert_one(deepcopy(given))
        before = await db[history.STATES].find_one({})
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()])
        retained(result[0])
        assert await db[history.STATES].find_one({}) == before
    run(scenario)


@pytest.mark.parametrize("collection,document", [
    ("mz2_bank_transfer_receipt_events", {"facts": {"order_number": "300000001"}, "status": "posted"}),
    ("mz2_bank_transfer_receipt_events", {"facts": {"order_number": "300000001"}, "status": "posting"}),
    ("order_payment_transactions_v2", {"provider_order_id": "native-300000001"}),
    ("payment_transactions", {"order_reference_id": "300000001"}),
    ("payment_refunds", {"order_number": 300000001}),
    ("store_delivery_collections", {"order_number": "300000001"}),
    ("mz2_shipping_accounting_events", {"facts": {"order_number": "300000001"}}),
    ("mz2_shipping_native_events_v2", {"order_number": "300000001", "kind": "fee"}),
    ("store_delivery_driver_earnings", {"order_number": "300000001"}),
    ("mezan_component_consumption_units_v1", {"order_id": "300000001", "state": "consumed"}),
    ("mezan_component_consumption_units_v1", {"order_id": "300000001", "state": "released", "consumed_at": "2026-11-20"}),
    ("mezan_supplier_receiving_events_v1", {"order_number": "300000001", "event_type": "supplier_piece_service_recorded"}),
    ("mezan_preparation_pieces_v1", {"order_number": "300000001", "supplier_receiving_history": [{"invoice_id": "invoice"}]}),
    ("mezan_order_item_cost_snapshots_v2", {"order_number": "300000001", "cost_stage": "actual"}),
    ("mezan_order_item_cost_snapshots_v2", {"order_number": "300000001", "cost": {"cost_stage": "actual"}}),
    ("mezan_order_item_cost_snapshots_v2", {"order_number": "300000001", "historical_cost_verified": True}),
    ("mezan_order_item_cost_snapshots_v2", {"order_number": "300000001", "cost": True}),
    ("mezan_component_order_lifecycle_v1", {"order_number": "300000001", "reporting_cost_snapshots": {"line-1": {"cost": {"cost_stage": "actual"}}}}),
    ("mezan_order_cost_corrections_v1", {"order_number": "300000001", "state": "applied"}),
    ("general_ledger", {"metadata": {"order_reference_id": "300000001"}}),
])
def test_independent_financial_history_is_an_exclusion_veto(collection, document):
    async def scenario(db):
        await db[collection].insert_one({"user_id": "owner", **document})
        result = await history.guard_financial_cohort_history(db, "owner", [candidate(), candidate("300000002")])
        assert retained(result[0])["history_checked"] is True
        assert result[1][ELIGIBILITY_FIELD]["eligible"] is False
    run(scenario)


@pytest.mark.parametrize("collection,document", [
    ("mezan_component_consumption_units_v1", {"order_id": "300000001", "state": "reserved"}),
    ("mezan_order_item_cost_snapshots_v2", {"order_number": "300000001", "cost_stage": "expected"}),
    ("mezan_component_order_lifecycle_v1", {"order_number": "300000001", "reporting_cost_snapshots": {"line-1": {"cost": {"cost_stage": "expected"}}}}),
    ("mezan_order_cost_corrections_v1", {"order_number": "300000001", "state": "draft"}),
    ("mezan_supplier_receiving_events_v1", {"order_number": "300000001", "experiment_mode": True}),
])
def test_forecasts_reservations_and_explicit_experiments_do_not_claim_incurred_cost(collection, document):
    async def scenario(db):
        await db[collection].insert_one({"user_id": "owner", **document})
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()])
        assert result[0][ELIGIBILITY_FIELD]["eligible"] is False
        assert result[0][ELIGIBILITY_FIELD]["history_checked"] is True
    run(scenario)


def test_missing_receiving_event_does_not_hide_an_approved_supplier_invoice():
    async def scenario(db):
        await db.mezan_preparation_pieces_v1.insert_one({"user_id": "owner", "order_number": "300000001", "id": "piece-1"})
        await db.mezan_supplier_invoices_v2.insert_one({"user_id": "owner", "id": "invoice-1", "status": "payable_posted",
                                                       "approved_at": "2026-11-20", "lines": [{"piece_ids": ["piece-1"]}]})
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()])
        retained(result[0])
    run(scenario)


def test_other_owner_history_is_never_attached():
    async def scenario(db):
        await db.mz2_bank_transfer_receipt_events.insert_one({"user_id": "other", "facts": {"order_number": "300000001"}})
        await db[history.STATES].insert_one({**state({"capture:native-300000001": {}}), "owner_id": "other"})
        result = await history.guard_financial_cohort_history(db, "owner", [candidate()])
        assert result[0][ELIGIBILITY_FIELD]["eligible"] is False
    run(scenario)


@pytest.mark.parametrize("fault", ["exception", "source-limit", "corrupt-state", "foreign-state"])
def test_unavailable_or_ambiguous_history_is_review_never_exclusion(monkeypatch, fault):
    async def scenario(db):
        rows, given = [candidate()], None
        if fault == "exception":
            async def broken(*args, **kwargs):
                raise RuntimeError("unavailable")
            monkeypatch.setattr(history, "_load", broken)
        elif fault == "source-limit":
            monkeypatch.setattr(history, "MAX_SOURCE_ROWS", 1)
            await db.payment_transactions.insert_many([{"user_id": "owner", "order_number": "300000001"} for _ in range(2)])
        elif fault == "corrupt-state": given = {"owner_id": "owner", "engine": {"facts": ["corrupt"]}}
        else: given = {**state(), "owner_id": "other"}
        result = await history.guard_financial_cohort_history(db, "owner", rows, state=given)
        assert all(retained(row)["history_checked"] is False for row in result)
    run(scenario)


def test_large_cohort_uses_bounded_batches_and_reuses_owner_state(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(history, "MAX_CANDIDATES", 1)
        rows = [candidate(), candidate("300000002"), candidate("300000003")]
        await db.payment_transactions.insert_one({"user_id": "owner", "order_number": "300000002"})
        result = await history.guard_financial_cohort_history(db, "owner", rows)
        assert [row[ELIGIBILITY_FIELD]["eligible"] for row in result] == [False, True, False]
        assert all(row[ELIGIBILITY_FIELD]["history_checked"] for row in result)
    run(scenario)


def test_cancelled_read_propagates_instead_of_certifying_absence(monkeypatch):
    async def broken(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(history, "_load", broken)
    with pytest.raises(asyncio.CancelledError):
        run(lambda db: history.guard_financial_cohort_history(db, "owner", [candidate()]))


def test_real_mongo_owner_projection_and_candidate_queries_preserve_history():
    from motor.motor_asyncio import AsyncIOMotorClient
    uri = os.getenv("OPERATIONAL_TEST_MONGO_URI", "mongodb://127.0.0.1:27305")
    assert uri.startswith("mongodb://127.0.0.1:")

    async def scenario():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1000)
        db = client["awaiting_history_" + uuid.uuid4().hex]
        try:
            given = state(movements=[{"order_number": "300000001", "automatic_order_bank": True,
                                      "direction": "incoming", "party_type": "bank", "amount": "10"}])
            await db[history.STATES].insert_one(given)
            await db.mezan_order_item_cost_snapshots_v2.insert_one({"user_id": "owner", "order_number": "300000002",
                                                                 "cost": {"cost_stage": "actual"}, "buyer_email": "not-read"})
            result = await history.guard_financial_cohort_history(db, "owner", [candidate(), candidate("300000002"), candidate("300000003")])
            retained(result[0]); retained(result[1])
            assert result[2][ELIGIBILITY_FIELD]["eligible"] is False
            assert result[2][ELIGIBILITY_FIELD]["history_checked"] is True
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(scenario())
