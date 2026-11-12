"""Daily feed truth, immutable after-balances, and account ACL regression.

Only the explicitly named real-Mongo cases exercise the existing money writer;
the other cases use synthetic state and a read-only local mocked database.
"""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import operational_financial_activity as activity
from operational_balance_store import STATES, audit, digest, initial, read

STAMP = "2026-11-07T12:00:00+00:00"
OWNER = {"id": "owner", "role": "owner", "name": "مالك تجريبي", "is_active": True}
STAFF = {"id": "staff", "role": "employee", "created_by": "owner", "name": "موظف تجريبي", "is_active": True}


def state_fixture():
    state = initial("owner")
    state.update(status="active", started_at="2026-11-01T09:00:00+00:00", revision=1)
    for kind, identity, amount, direction in (("bank", "bank", "1000.00", "for_us"),
                                              ("cash", "cash", "200.00", "for_us"),
                                              ("supplier", "supplier", "500.00", "for_party")):
        state["openings"][identity] = {"id": identity, "party_type": kind, "party_id": identity,
            "name": identity, "currency": "SAR", "amount": amount, "direction": direction}
    state["engine"] = {"obligations": {}, "issues": [], "as_of": STAMP}
    return state


def movement(identity="one", **values):
    return {"id": identity, "party_type": "supplier", "party_id": "supplier", "name": "مورد تجريبي",
        "bank_id": "bank", "bank_kind": "bank", "bank_name": "بنك تجريبي", "amount": "100.00",
        "currency": "SAR", "direction": "outgoing", "kind": "payment", "actor_id": "staff",
        "occurred_at": STAMP, "reference": identity, "source": "employee_app",
        "allocations": [{"obligation_id": "supplier", "amount": "100.00"}], **values}


def append(state, row, *, action="movement_saved"):
    state["movements"].append(row)
    audit(state, row.get("actor_id"), action, None, row, "حركة اختبار", "test", row.get("occurred_at"))


def projected(state):
    rows, _ = activity.operational_rows(state)
    activity.attach_snapshots(state, rows)
    return {row["id"]: row for row in rows}


async def database(state=None, *, assigned=("bank",), cash=()):
    db = AsyncMongoMockClient()["isolated_daily_feed"]
    await db.users.insert_many([OWNER, STAFF, {"id": "foreign", "role": "owner", "name": "اسم محظور"}])
    await db.mz2_financial_accounts.insert_many([
        {"id": identity, "user_id": "owner", "name": identity, "account_type": kind,
         "currency": "SAR", "status": "active"}
        for kind, identity in (("bank", "bank"), ("bank", "secret"), ("cash", "cash"))])
    await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": "staff", "enabled": True,
        "permissions": ["operational_balance_reports_read"],
        "operational_banks": {"bank_ids": list(assigned), "cash_ids": list(cash)}})
    if state is not None:
        await db[STATES].insert_one(deepcopy(state))
    return db


async def feed(db, **values):
    return await activity.daily_financial_movements(db, owner="owner", actor=OWNER,
        from_date="2026-11-07", to_date="2026-11-07", **values)


@pytest.fixture(autouse=True)
def captured_clock(monkeypatch):
    monkeypatch.setattr(activity, "now", lambda: STAMP)


def test_each_after_balance_is_captured_at_its_effect_and_immutable():
    state = state_fixture()
    first = movement("one", amount="300.00", allocations=[{"obligation_id": "supplier", "amount": "300.00"}])
    append(state, first)
    append(state, movement("two"))
    rows = projected(state)
    assert rows["movement:one"]["account_balance_after"] == "700.00"
    assert rows["movement:one"]["counterparty_balance_after"] == "-200.00"
    assert rows["movement:one"]["counterparty_balance_basis"] == "confirmed_obligations"
    assert rows["movement:two"]["account_balance_after"] == "600.00"
    assert rows["movement:two"]["counterparty_balance_after"] == "-100.00"
    assert rows["movement:one"]["balance_as_of"] == STAMP
    assert "financial_activity_snapshots" not in first
    state["openings"]["supplier"]["amount"] = "900.00"
    assert projected(state)["movement:one"]["counterparty_balance_after"] == "-200.00"


def test_supplier_advance_positive_and_expense_has_no_invented_debt():
    state = state_fixture()
    state["openings"].pop("supplier")
    append(state, movement("advance", allocations=[]))
    append(state, movement("expense", party_type="operating_expense", party_id="rent", allocations=[]))
    rows = projected(state)
    assert rows["movement:advance"]["counterparty_balance_after"] == "100.00"
    assert rows["movement:expense"]["counterparty_balance_after"] is None
    assert rows["movement:expense"]["counterparty_balance_status"] == "not_applicable"
    assert rows["movement:expense"]["counterparty_balance_basis"] is None


def test_unknown_history_and_corrupt_seal_never_show_current_or_zero_balance():
    state = state_fixture()
    state["movements"].append(movement("old"))
    append(state, movement("new"))
    assert projected(state)["movement:old"]["account_balance_after"] is None
    assert projected(state)["movement:new"]["account_balance_after"] == "800.00"
    state["audit"][0]["financial_activity_snapshots"][0]["account_balance_after"] = "999.00"
    assert projected(state)["movement:new"]["account_balance_after"] is None


def test_changed_source_cannot_reuse_earlier_after_balance():
    state = state_fixture()
    append(state, movement())
    state["movements"][0]["amount"] = "150.00"
    assert projected(state)["movement:one"]["account_balance_after"] is None


def test_changed_allocation_cannot_reuse_counterparty_after_balance():
    state = state_fixture()
    append(state, movement())
    state["movements"][0]["allocations"] = []
    assert projected(state)["movement:one"]["counterparty_balance_after"] is None


def test_unrelated_unprojectable_history_does_not_break_new_audit_or_invent_balance():
    state = state_fixture()
    state["movements"].append(movement("legacy", amount="malformed"))
    append(state, movement("new"))
    snapshots = state["audit"][-1]["financial_activity_snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["account_balance_status"] == "unavailable"
    assert snapshots[0]["account_balance_after"] is None
    assert state["movements"][-1]["amount"] == "100.00"


def test_missing_opening_is_explicit_not_a_fabricated_approved_zero():
    state = state_fixture()
    append(state, movement("new-account", bank_id="unopened"))
    row = projected(state)["movement:new-account"]
    assert row["account_balance_status"] == "unavailable"
    assert row["account_balance_after"] is None


def test_fx_amount_is_actual_gross_bank_leg_and_fee_is_not_a_second_movement():
    state = state_fixture()
    append(state, movement("fx", party_type="ad_account", party_id="ad", currency="USD", amount="25.00",
        bank_currency="SAR", bank_amount="100.00", bank_fee_amount="5.00", bank_net_amount="95.00", allocations=[]))
    rows = projected(state)
    assert len(rows) == 1
    assert rows["movement:fx"]["amount"] == "100.00"
    assert rows["movement:fx"]["currency"] == "SAR"
    assert rows["movement:fx"]["account_balance_after"] == "900.00"
    assert rows["movement:fx"]["counterparty_balance_currency"] == "USD"


def test_confirmed_refund_only_and_reconfirmation_keeps_original_snapshot():
    state = state_fixture()
    case = {"id": "refund", "status": "pending", "currency": "SAR", "amount": "50.00", "order_number": "123",
        "refund_source_type": "bank", "refund_source_id": "bank", "refund_source_name": "بنك تجريبي",
        "refunded_at": "2026-11-06T09:00:00+00:00", "confirmed_by": "staff", "refund_reference": "RF-1"}
    state["customer_returns"] = [case]
    assert projected(state) == {}
    pending = deepcopy(case)
    case["status"] = "refunded"
    audit(state, "staff", "customer_return_saved", pending, case, "refund", "test", STAMP)
    audit(state, "staff", "customer_return_saved", deepcopy(case), case, "repeat", "test", STAMP)
    rows = projected(state)
    assert len(rows) == 1
    row = rows["refund:refund"]
    assert row["account_balance_after"] == "950.00"
    assert row["counterparty_balance_after"] is None
    assert row["activity_at"] == STAMP and row["time_basis"] == "recorded"
    assert row["occurred_at"] == "2026-11-06T09:00:00+00:00"
    assert "financial_activity_snapshots" not in state["audit"][1]


def test_late_prepaid_event_is_filtered_by_real_recording_date_and_keeps_original(monkeypatch):
    state = state_fixture()
    append(state, movement("late", actor_id="operational_order_worker", occurred_at="2026-11-01T09:00:00+00:00"),
           action="prepaid_shipping_paid")
    async def scenario():
        db = await database(state)
        today = await feed(db)
        old = await activity.daily_financial_movements(db, owner="owner", actor=OWNER,
            from_date="2026-11-01", to_date="2026-11-01")
        assert today["total"] == 1 and old["total"] == 0
        row = today["items"][0]
        assert row["employee_source"] == "system" and row["employee_name"] == "النظام"
        assert row["recorded_at"] == row["activity_at"] == row["balance_as_of"] == STAMP
        assert row["occurred_at"] == "2026-11-01T09:00:00+00:00"
    asyncio.run(scenario())


def test_transfer_acl_hides_private_leg_identity_balance_and_free_text_before_counts():
    state = state_fixture()
    state["openings"]["secret"] = {**state["openings"]["bank"], "id": "secret", "party_id": "secret", "name": "اسم سري"}
    append(state, movement("transfer", kind="transfer", party_type="bank", party_id="secret", name="اسم سري",
        reference="اسم سري", note="نقل إلى اسم سري", allocations=[]))
    append(state, movement("hidden", bank_id="secret", bank_name="اسم سري"))
    async def scenario():
        db = await database(state)
        result = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        assert result["total"] == 1 and len(result["items"]) == 1
        row = result["items"][0]
        assert row["account_id"] == "bank" and row["account_balance_after"] == "900.00"
        assert row["counterparty_id"] is None and row["counterparty_name"] is None
        assert row["counterparty_balance_after"] is None and row["reference"] is None
        assert row["counterparty_balance_basis"] is None
        assert "اسم سري" not in str(result) and "secret" not in str(result)
        assert (await feed(db))["total"] == 3
    asyncio.run(scenario())


def test_bank_cash_ids_are_type_scoped_and_unassigned_is_empty():
    state = state_fixture()
    append(state, movement("cash", bank_kind="cash", bank_id="bank"))
    async def scenario():
        db = await database(state)
        result = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        assert result["total"] == 0
        await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"operational_banks": {}}})
        result = await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
            from_date="2026-11-07", to_date="2026-11-07")
        assert result["total"] == 0 and any("مسندة" in note for note in result["coverage"]["notes"])
    asyncio.run(scenario())


def test_actor_lookup_never_substitutes_owner_or_foreign_profile_and_get_does_not_write():
    state = state_fixture()
    append(state, movement("foreign", actor_id="foreign"))
    append(state, movement("staff"))
    async def scenario():
        db = await database(state)
        before = await db[STATES].find_one({"_id": "owner"})
        result = await feed(db)
        rows = {row["id"]: row for row in result["items"]}
        assert rows["movement:foreign"]["employee_name"] is None
        assert rows["movement:foreign"]["employee_id"] is None
        assert rows["movement:staff"]["employee_name"] == STAFF["name"]
        assert rows["movement:staff"]["employee_source"] == "current_profile"
        assert result["coverage"]["missing_actor_count"] == 1
        assert await db[STATES].find_one({"_id": "owner"}) == before
        assert await db.general_ledger.count_documents({}) == 0
    asyncio.run(scenario())


def test_ten_row_pages_are_stable_and_changed_scope_or_rows_require_refresh():
    state = state_fixture()
    for index in range(13):
        append(state, movement(str(index), amount="1.00", allocations=[]))
    async def scenario():
        db = await database(state)
        first = await feed(db)
        second = await feed(db, page=2, snapshot=first["snapshot"])
        assert first["total"] == 13 and first["total_pages"] == 2
        assert len(first["items"]) == 10 and len(second["items"]) == 3
        assert first["has_next"] and second["has_previous"] and not second["has_next"]
        assert not ({row["id"] for row in first["items"]} & {row["id"] for row in second["items"]})
        await db[STATES].update_one({"_id": "owner"}, {"$inc": {"revision": 1}})
        with pytest.raises(HTTPException) as error:
            await feed(db, page=2, snapshot=first["snapshot"])
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "financial_movements_cursor_changed"
        with pytest.raises(HTTPException):
            await feed(db, page=2)
    asyncio.run(scenario())


def test_hidden_account_changes_do_not_change_staff_page_count_or_snapshot():
    state = state_fixture()
    append(state, movement("visible"))
    async def scenario():
        db = await database(state)
        kwargs = dict(owner="owner", actor=STAFF, from_date="2026-11-07", to_date="2026-11-07")
        before = await activity.daily_financial_movements(db, **kwargs)
        hidden = deepcopy(state)
        append(hidden, movement("hidden", bank_id="secret"))
        hidden["revision"] += 1
        await db[STATES].replace_one({"_id": "owner"}, hidden)
        after = await activity.daily_financial_movements(db, **kwargs, snapshot=before["snapshot"])
        assert after["snapshot"] == before["snapshot"]
        assert after["items"] == before["items"] and after["total"] == before["total"] == 1
    asyncio.run(scenario())


def test_redacted_transfer_metadata_does_not_change_public_snapshot():
    state = state_fixture()
    state["movements"].append(movement("private-transfer", kind="transfer", party_type="bank", party_id="secret",
        name="Private target A", reference="private-ref-A", note="Private target A", allocations=[]))
    async def scenario():
        db = await database(state)
        kwargs = dict(owner="owner", actor=STAFF, from_date="2026-11-07", to_date="2026-11-07")
        before = await activity.daily_financial_movements(db, **kwargs)
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"movements.0.reference": "private-ref-B",
            "movements.0.name": "Private target B", "movements.0.note": "Private target B"}, "$inc": {"revision": 1}})
        after = await activity.daily_financial_movements(db, **kwargs, snapshot=before["snapshot"])
        assert before["items"] == after["items"]
        assert before["snapshot"] == after["snapshot"] and before["total"] == after["total"] == 1
    asyncio.run(scenario())


def test_account_assignment_revoked_during_read_returns_no_previous_scope(monkeypatch):
    state = state_fixture()
    append(state, movement())
    original = activity._actor_names
    async def revoke(db, owner, rows):
        await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"operational_banks": {}}})
        await original(db, owner, rows)
    monkeypatch.setattr(activity, "_actor_names", revoke)
    async def scenario():
        db = await database(state)
        with pytest.raises(HTTPException) as error:
            await activity.daily_financial_movements(db, owner="owner", actor=STAFF,
                from_date="2026-11-07", to_date="2026-11-07")
        assert error.value.status_code == 403
    asyncio.run(scenario())


def test_riya_day_boundaries_and_historical_unknown_recording_are_explicit():
    state = state_fixture()
    for identity, at in (("before", "2026-11-06T20:59:59Z"), ("start", "2026-11-06T21:00:00Z"),
                         ("last", "2026-11-07T20:59:59Z"), ("next", "2026-11-07T21:00:00Z")):
        state["movements"].append(movement(identity, occurred_at=at))
    state["movements"].append(movement("missing-time", occurred_at="2026-11-07"))
    async def scenario():
        result = await feed(await database(state))
        assert {row["id"] for row in result["items"]} == {"movement:start", "movement:last"}
        assert all(row["recorded_at"] is None and row["time_basis"] == "occurred" for row in result["items"])
        assert result["coverage"]["undated_count"] == 1
        assert not result["coverage"]["complete"]
    asyncio.run(scenario())


@pytest.mark.parametrize("start,end", [("2026-02-30", "2026-03-01"), ("2026-11-08", "2026-11-07"),
                                      ("20261107", "2026-11-07")])
def test_invalid_ranges_are_rejected(start, end):
    with pytest.raises(HTTPException) as error:
        activity.date_range(start, end)
    assert error.value.status_code == 422


def test_custom_range_has_no_arbitrary_year_limit():
    lower, upper = activity.date_range("2020-01-01", "2026-11-07")
    assert lower.date().isoformat() == "2020-01-01" and upper.date().isoformat() == "2026-11-08"


def test_native_active_and_inactive_sources_never_fall_back_to_operational_or_legacy():
    state = state_fixture()
    append(state, movement())
    async def scenario():
        db = await database(state)
        await db.mz2_atomic_owners.insert_one({"_id": "owner", "ledger_backend_state": "v2_active"})
        result = await feed(db)
        assert result["financial_source"] == "unavailable" and result["items"] == []
        assert not result["coverage"]["complete"]
        other = await feed(await database())
        assert other["financial_source"] == "unavailable" and not other["coverage"]["complete"]
    asyncio.run(scenario())


def test_http_explicit_ten_row_query_is_parsed_before_authorization_without_relaxing_the_limit():
    from test_operational_balance_app_permissions import client
    async def scenario():
        state = state_fixture()
        state['movements'] = [movement(str(i)) for i in range(12)]
        db = await database(state)
        endpoint = '/api/operational-balances/daily-financial-movements'
        params = {'from_date': '2026-11-07', 'to_date': '2026-11-07', 'page_size': 10}
        async with client(db, mobile=False) as browser_staff:
            denied = await browser_staff.get(endpoint, params=params)
            assert denied.status_code == 403, denied.text
        async with client(db, actor='owner', mobile=False) as browser_owner:
            response = await browser_owner.get(endpoint, params=params)
            assert response.status_code == 200, response.text
            assert response.json()['page_size'] == 10
            assert len(response.json()['items']) == 10 and response.json()['total'] == 12
            for size in (1, 11, 'all'):
                rejected = await browser_owner.get(endpoint, params={**params, 'page_size': size})
                assert rejected.status_code == 422, rejected.text
                assert 'items' not in rejected.json()
    asyncio.run(scenario())


def test_real_mongo_http_permission_acl_and_idempotent_writer_snapshot():
    from test_operational_balance_integration import run, started, movement as command
    from test_operational_balance_app_permissions import grant, client, READ, WRITE
    async def scenario(db):
        await started(db)
        await grant(db, [WRITE, READ])
        params = {"from_date": "2026-11-07", "to_date": "2026-11-07", "page_size": 10}
        endpoint = "/api/operational-balances/daily-financial-movements"
        async with client(db, mobile=False) as browser_staff:
            assert (await browser_staff.get(endpoint, params=params)).status_code == 403
        async with client(db, actor="owner", mobile=False) as browser_owner:
            assert (await browser_owner.get(endpoint, params=params)).status_code == 200
        async with client(db) as http:
            payload = {**command(), "expected_session_scope": digest(["owner", "staff"])}
            first = await http.post("/api/operational-balances/movements", json=payload)
            again = await http.post("/api/operational-balances/movements", json=payload)
            assert first.status_code == again.status_code == 200, first.text
            assert first.json() == again.json()
            saved = await read(db, "owner")
            evidence = [event for event in saved["audit"] if event.get("financial_activity_snapshots")]
            assert len(saved["movements"]) == len(evidence) == 1
            response = await http.get("/api/operational-balances/daily-financial-movements",
                params={"from_date": "2026-11-07", "to_date": "2026-11-07", "page_size": 10})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["total"] == 1 and body["items"][0]["account_balance_after"] == "700.00"
            assert body["items"][0]["counterparty_balance_after"] == "-200.00"
            assert await read(db, "owner") == saved
            await grant(db, [WRITE])
            assert (await http.get("/api/operational-balances/daily-financial-movements",
                params={"from_date": "2026-11-07", "to_date": "2026-11-07"})).status_code == 403
            await grant(db, [READ])
            await db.mezan_mobile_app_access_v1.update_one({"user_id": "staff"}, {"$set": {"enabled": False}})
            assert (await http.get(endpoint, params=params)).status_code == 403
        await db.users.insert_one({"id": "driver-user", "role": "store_driver", "created_by": "owner", "is_active": True})
        await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": "driver-user",
            "enabled": True, "permissions": [READ], "operational_banks": {"bank_ids": ["bank"]}})
        async with client(db, actor="driver-user") as driver:
            assert (await driver.get(endpoint, params=params)).status_code == 403
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize("method", ["bank_transfer", "card_terminal"])
def test_real_mongo_approved_bank_or_pos_is_one_fact_and_settlement_keeps_frozen_balances(method):
    from .test_operational_driver_review_mongo import run
    from .test_store_delivery_noncash_review import seed, NOW
    import operational_driver_payment_review as review
    from operational_balance_service import create_movement
    async def scenario(db):
        assignment, payload = await seed(db, method)
        await db.users.update_one({"id": "owner"}, {"$set": {"name": "مالك تجريبي"}})
        assert (await feed(db))["items"] == []  # Pending evidence is not money.
        result = await review.review_payment(db, owner="owner", actor="owner", assignment_id=assignment, payload=payload, clock=NOW)
        again = await review.review_payment(db, owner="owner", actor="owner", assignment_id=assignment, payload=payload, clock=NOW)
        assert result == again
        first = await feed(db)
        assert first["total"] == 1
        approved = first["items"][0]
        assert approved["counterparty_type"] == "store_driver" and approved["counterparty_id"] == "driver"
        assert approved["counterparty_name"] == "Synthetic driver"
        assert approved["counterparty_balance_after"] == "-20.00"
        assert approved["account_balance_after"] == ("1200.00" if method == "bank_transfer" else "200.00")
        assert approved["direction"] == ("incoming" if method == "bank_transfer" else "noncash")
        if method == "card_terminal":
            await create_movement(db, "owner", "owner", {"request_id": "pos-settle-daily", "party_type": "pos_receivable",
                "party_id": payload.destination_financial_id, "currency": "SAR", "direction": "incoming", "kind": "collection",
                "amount": "150.00", "bank_id": "bank", "allocations": [{"obligation_id": result["obligation_id"], "amount": "150.00"}]}, clock=NOW)
            after = await feed(db)
            assert after["total"] == 2
            assert next(row for row in after["items"] if row["id"] == approved["id"])["account_balance_after"] == "200.00"
            settlement = next(row for row in after["items"] if row["id"] != approved["id"])
            assert settlement["account_balance_after"] == "1150.00"
            assert settlement["counterparty_balance_after"] == "50.00"
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_real_mongo_read_grant_revoked_before_response_blocks_result(monkeypatch):
    from test_operational_balance_integration import run, started
    from test_operational_balance_app_permissions import grant, client, READ
    original = activity._actor_names
    async def revoke(db, owner, rows):
        await grant(db, [])
        await original(db, owner, rows)
    monkeypatch.setattr(activity, "_actor_names", revoke)
    async def scenario(db):
        await started(db)
        await grant(db, [READ])
        async with client(db) as http:
            result = await http.get("/api/operational-balances/daily-financial-movements",
                params={"from_date": "2026-11-07", "to_date": "2026-11-07"})
            assert result.status_code == 403
    run(scenario)
