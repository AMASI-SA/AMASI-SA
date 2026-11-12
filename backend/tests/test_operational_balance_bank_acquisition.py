"""Bank identity enrichment preserves source revisions and every financial/workflow object."""
from asyncio import gather
from copy import deepcopy
from decimal import Decimal

import pytest

from operational_balance_service import refresh, report
from operational_balance_store import read
from operational_balance_worker import tick
from salla_integration import bank_payment_sync
from test_operational_balance_bank_orders import native_accounts, native_bank_order
from test_operational_balance_integration import NOW, run, started
from test_operational_balance_visible_evidence import visible_refresh


def pair(number="992001"):
    source = native_bank_order(number)
    details = deepcopy(source["raw_by_source"]["salla_direct"])
    source["raw_by_source"]["salla_direct"].pop("bank")
    source["receiving_bank_name"] = "مصرف الراجحي"
    return source, details


def request(details):
    return {"code": "operational_order_bank_binding_incomplete", "source_id": str(details["id"])}


def provider(monkeypatch, details, calls, *, during_read=None):
    async def get(db, owner, method, path):
        assert owner == "owner" and method == "GET" and path.startswith("/orders/")
        identity = path.removeprefix("/orders/")
        calls.append(identity)
        if during_read:
            await during_read(db)
        return {"data": deepcopy(details[identity])}
    monkeypatch.setattr(bank_payment_sync, "call_salla", get)


def test_bounded_concurrent_acquisition_writes_bank_identity_and_retry_metadata_only(monkeypatch):
    details, issues, calls = {}, [], []
    provider(monkeypatch, details, calls)

    async def scenario(db):
        await started(db)
        financial = await read(db, "owner")
        originals = {}
        for index in range(6):
            source, native = pair(str(992100 + index))
            native["status"] = {"slug": "delivered"}
            source["raw_by_source"]["salla_direct"]["status"] = deepcopy(native["status"])
            identity = str(native["id"])
            details[identity] = native
            issues.append(request(native))
            for owner in ("owner", "other"):
                row = {**deepcopy(source), "user_id": owner}
                await db.unified_orders.insert_one(row)
                originals[owner, source["order_number"]] = await db.unified_orders.find_one(
                    {"user_id": owner, "order_number": source["order_number"]}, {"_id": 0})
        preserved = {
            "assembly_delivery": {"user_id": "owner", "order_number": "992100", "state": "delivered", "order_confirmed": True},
            "store_delivery_assignments": {"user_id": "owner", "order_number": "992100", "delivered_at": NOW},
            "driver_earnings": {"user_id": "owner", "order_number": "992100", "amount": "25.00", "paid": True},
        }
        for name, row in preserved.items():
            await db[name].insert_one(deepcopy(row))
        await bank_payment_sync.acquire_missing(db, "owner", issues)
        assert len(calls) == 4
        await gather(*(bank_payment_sync.acquire_missing(db, "owner", issues) for _ in range(2)))
        assert len(calls) == len(set(calls)) == 6
        await bank_payment_sync.acquire_missing(db, "owner", issues)
        assert len(calls) == 6
        for (owner, number), original in originals.items():
            current = await db.unified_orders.find_one({"user_id": owner, "order_number": number}, {"_id": 0})
            if owner == "other":
                assert current == original
            else:
                assert current.pop(bank_payment_sync._RETRY)
                bank = current["raw_by_source"]["salla_direct"].pop("bank")
                assert bank == {"bank_id": "native-bank-id", "bank_name": "مصرف الراجحي"}
                assert current == original
        assert await read(db, "owner") == financial
        for name, row in preserved.items():
            assert await db[name].find_one({"user_id": "owner"}, {"_id": 0}) == row
    run(scenario)


def test_visible_refresh_completes_seven_missing_native_banks_then_credits_twenty_one_once(monkeypatch):
    details, calls = {}, []
    provider(monkeypatch, details, calls)

    async def scenario(db):
        await started(db)
        await native_accounts(db)
        for index in range(21):
            number = str(992200 + index)
            if index < 14:
                source = native_bank_order(number)
            else:
                source, native = pair(number)
                details[str(native["id"])] = native
            await db.unified_orders.insert_one(source)
        first = await refresh(db, "owner", clock=NOW)
        assert len(first["movements"]) == 14
        assert len(first["engine"]["order_versions"]) == 21
        assert sum(i["code"] == "operational_order_bank_binding_incomplete" for i in first["engine"]["issues"]) == 7
        await tick(db)
        assert await read(db, "owner") == first and calls == []
        for _ in range(3):
            await visible_refresh(db)
        current = await read(db, "owner")
        assert len(calls) == len(set(calls)) == 7
        credits = [row for row in current["movements"] if row.get("automatic_order_bank")]
        assert len(credits) == len({row["request_id"] for row in credits}) == 21
        assert {row["bank_id"] for row in credits} == {"account:rajhi"}
        assert sum(Decimal(row["amount"]) for row in credits) == Decimal("2100.00")
        assert current["openings"] == first["openings"]
        assert current["engine"]["order_versions"] == first["engine"]["order_versions"]
        assert report(current)["summary"]["actual_liquidity"] == "3100.00"
        await tick(db)
        assert (await read(db, "owner"))["movements"] == current["movements"]
        await visible_refresh(db)
        assert (await read(db, "owner"))["movements"] == current["movements"]
        assert len(calls) == 7
    run(scenario)


@pytest.mark.parametrize("fault", ["owner", "currency", "unpaid", "watermark", "known-bank", "unknown-bank",
                                  "conflicting-bank", "bank-code", "unknown-bank-code"])
def test_ineligible_existing_source_never_triggers_a_provider_read(monkeypatch, fault):
    source, native = pair()
    raw = source["raw_by_source"]["salla_direct"]
    if fault == "owner": source["user_id"] = "other"
    elif fault == "currency": raw["currency"] = "USD"
    elif fault == "unpaid": raw["payment_methods"][0]["amount"] = "0.00"
    elif fault == "watermark": source["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    elif fault == "known-bank": raw["bank"] = {"bank_name": "مصرف الراجحي"}
    elif fault == "unknown-bank": raw["bank"] = {"bank_name": "Unknown bank"}
    elif fault == "conflicting-bank": raw.update(bank={"bank_name": "مصرف الراجحي"}, receiving_bank_name="مصرف الإنماء")
    else: raw["receiving_bank_code"] = "bank_rajhi" if fault == "bank-code" else "unknown-bank"
    calls = []
    provider(monkeypatch, {str(native["id"]): native}, calls)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await bank_payment_sync.acquire_missing(db, "owner", [request(native)])
        current = await db.unified_orders.find_one({}, {"_id": 0})
        current.pop(bank_payment_sync._RETRY, None)
        assert current == source and calls == []
    run(scenario)


@pytest.mark.parametrize("fault", ["id", "reference", "created", "stale", "newer", "currency", "total-currency", "leg-currency",
                                  "paid", "refund", "method", "receipt", "bank-conflict", "unknown-bank", "bank-id",
                                  "bank-code", "conflicting-bank-code"])
def test_mismatched_native_details_never_supply_bank_identity(monkeypatch, fault):
    source, native = pair()
    identity = str(native["id"])
    if fault == "id": native["id"] = "999999"
    elif fault == "reference": native["reference_id"] = "different"
    elif fault == "created": native["created_at"] = "2026-10-06T09:00:00Z"
    elif fault == "stale": native["updated_at"] = "2026-10-06T09:59:59Z"
    elif fault == "newer": native["updated_at"] = "2026-10-06T10:00:01Z"
    elif fault == "currency": native["currency"] = "USD"
    elif fault == "total-currency": native["total"]["currency"] = "USD"
    elif fault == "leg-currency": native["payment_methods"][0]["currency"] = "USD"
    elif fault == "paid": native["payment_methods"][0]["amount"] = "90.00"
    elif fault == "refund": native["payment_actions"]["refund_action"]["refund_amount"]["amount"] = "1.00"
    elif fault == "method": native["payment_method"] = "mada"
    elif fault == "receipt": native["payment_methods"][0]["transaction_reference"] = "different-receipt"
    elif fault == "bank-conflict": native["receiving_bank_name"] = "مصرف الإنماء"
    elif fault == "unknown-bank": native["bank"]["bank_name"] = "Unknown bank"
    elif fault == "bank-id": source["raw_by_source"]["salla_direct"]["bank"] = {"id": "different-store-bank"}
    else: native["receiving_bank_code"] = "bank_rajhi" if fault == "bank-code" else "bank_inma"
    calls = []
    provider(monkeypatch, {identity: native}, calls)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await bank_payment_sync.acquire_missing(db, "owner", [{"code": "operational_order_bank_binding_incomplete", "source_id": identity}])
        current = await db.unified_orders.find_one({}, {"_id": 0})
        current.pop(bank_payment_sync._RETRY)
        assert current == source and calls == [identity]
    run(scenario)


@pytest.mark.parametrize("changed", ["raw", "watermark", "owner"])
def test_source_change_during_native_read_loses_the_compare_and_set(monkeypatch, changed):
    source, native = pair()
    calls = []
    async def concurrent(db):
        path, value = {"raw": ("raw_by_source.salla_direct.status.slug", "in_progress"),
                       "watermark": ("g47_salla_snapshot.revision", 2), "owner": ("user_id", "other")}[changed]
        await db.unified_orders.update_one({"user_id": "owner"}, {"$set": {path: value}})
    provider(monkeypatch, {str(native["id"]): native}, calls, during_read=concurrent)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await bank_payment_sync.acquire_missing(db, "owner", [request(native)])
        current = await db.unified_orders.find_one({}, {"_id": 0})
        assert "bank" not in current["raw_by_source"]["salla_direct"]
        assert len(calls) == 1
    run(scenario)


@pytest.mark.parametrize("payload", ["source", "details"])
@pytest.mark.parametrize("conflicting", ["payment", "root", "bank"])
def test_conflicting_store_bank_identifiers_are_held_before_enrichment(monkeypatch, payload, conflicting):
    source, native = pair()
    target = source["raw_by_source"]["salla_direct"] if payload == "source" else native
    target.setdefault("payment", {})["store_bank_id"] = "selected-store-bank"
    target["store_bank_id"] = "selected-store-bank"
    target.setdefault("bank", {})["id"] = "selected-store-bank"
    if conflicting == "payment":
        target["payment"]["store_bank_id"] = "conflicting-store-bank"
    elif conflicting == "root":
        target["store_bank_id"] = "conflicting-store-bank"
    else:
        target["bank"]["id"] = "conflicting-store-bank"
    if payload == "source":
        native.setdefault("payment", {})["store_bank_id"] = "selected-store-bank"
        native["bank"]["id"] = "selected-store-bank"
    calls = []
    provider(monkeypatch, {str(native["id"]): native}, calls)

    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await bank_payment_sync.acquire_missing(db, "owner", [request(native)])
        current = await db.unified_orders.find_one({}, {"_id": 0})
        current.pop(bank_payment_sync._RETRY)
        assert current == source
        assert calls == ([] if payload == "source" else [str(native["id"])])
    run(scenario)


def test_existing_bank_enrichment_cache_supplies_only_native_identity(monkeypatch):
    source, native = pair()
    native["bank"] = {"id": "synthetic-store-bank"}
    raw = source["raw_by_source"]["salla_direct"]
    raw["bank"] = {"id": "synthetic-store-bank"}
    for target in (raw, native):
        target.setdefault("payment", {})["store_bank_id"] = "synthetic-store-bank"
        target["store_bank_id"] = "synthetic-store-bank"
    calls = []
    provider(monkeypatch, {str(native["id"]): native}, calls)
    async def scenario(db):
        await db.unified_orders.insert_one(deepcopy(source))
        await db.salla_payment_banks.insert_one({"user_id": "owner", "salla_bank_id": "synthetic-store-bank",
            "bank_name": "مصرف الراجحي", "account_name": "private account holder"})
        await bank_payment_sync.acquire_missing(db, "owner", [request(native)])
        current = await db.unified_orders.find_one({}, {"_id": 0})
        assert current["raw_by_source"]["salla_direct"]["bank"] == {
            "id": "synthetic-store-bank", "bank_name": "مصرف الراجحي"}
        assert calls == [str(native["id"])]
    run(scenario)
