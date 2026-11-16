"""HTTP contract for the approved simple ledger; isolated synthetic resources."""
import asyncio
import os
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient

from operational_balance_routes import make_operational_balance_router
from operational_balance_service import report
from operational_balance_store import STATES, digest, initial, read

BASE = "/api/operational-balances/workspace"


def run(scenario):
    async def execute():
        uri = os.environ.get("OPERATIONAL_TEST_MONGO_URI")
        if uri:
            assert uri.startswith("mongodb://127.0.0.1:"), "Dedicated local test Mongo only"
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000) if uri else AsyncMongoMockClient()
        db = client["workspace_" + uuid4().hex]
        if uri:
            await client.admin.command("ping")
        await db.users.insert_many([
            {"id": "owner", "role": "owner", "name": "محمد عبدالله", "is_active": True},
            {"id": "staff", "role": "employee", "created_by": "owner", "name": "خالد أحمد", "is_active": True},
            {"id": "other", "role": "owner", "name": "منشأة أخرى", "is_active": True},
        ])
        await db.mz2_financial_accounts.insert_many([
            {"id": "bank", "user_id": "owner", "name": "الإنماء", "account_type": "bank", "currency": "SAR", "status": "active"},
            {"id": "cash", "user_id": "owner", "name": "الصندوق", "account_type": "cash", "currency": "SAR", "status": "active"},
            {"id": "foreign", "user_id": "other", "name": "بنك سري", "account_type": "bank", "currency": "SAR", "status": "active"},
        ])
        await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner", "name": "الموظف المستفيد", "status": "active"})
        await db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner", "name": "المورد", "status": "active"})
        await db.mz2_external_persons_v2.insert_one({"id": "external", "user_id": "owner", "name": "شركة آفاق", "currency": "SAR", "status": "active"})
        state = initial("owner")
        state.update(status="active", started_at="2026-11-01T00:00:00+00:00", revision=1)
        for kind, identity, amount, side in [
            ("bank", "bank", "1000.00", "for_us"), ("cash", "cash", "500.00", "for_us"),
            ("employee_custody", "employee", "150.00", "for_us"),
            ("employee", "employee", "200.00", "for_party"),
            ("supplier", "supplier", "200.00", "for_party"),
            ("external_person", "external", "400.00", "for_party"),
        ]:
            key = kind + ":" + identity
            state["openings"][key] = {"id": key, "party_type": kind, "party_id": identity,
                "amount": amount, "currency": "SAR", "direction": side, "name": identity,
                "actor_id": "owner", "created_at": "2026-11-01T00:00:00+00:00"}
        await db[STATES].insert_one(state)
        app = FastAPI()
        async def principal(x_test_user: str = Header(default="owner"), x_test_mobile: str = Header(default="no")):
            return {"id": x_test_user, "_session_client": "amasi_mobile" if x_test_mobile == "yes" else "web"}
        app.include_router(make_operational_balance_router(db, principal), prefix="/api")
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                await scenario(db, http)
        finally:
            await client.drop_database(db.name)
            client.close()
    asyncio.run(execute())


def entry(**updates):
    return {"request_id": "entry-" + uuid4().hex, "expected_session_scope": digest(["owner", "owner"]),
            "party_type": "external_person", "party_id": "external", "side": "for_us",
            "amount": "100.00", "business_date": "2026-11-10", "source_type": "bank", "source_id": "bank", **updates}


def balance(state, kind, identity):
    return next(r for r in report(state)["parties"] if (r["party_type"], r["party_id"]) == (kind, identity))


async def grant(db, *, operations, resources, version=1):
    await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access": {
        "owner_id": "owner", "version": version, "enabled": True, "operations": operations, "resources": resources}}})


def resource(kind, identity, *, use=True, movements="own", balance=False):
    return {"kind": kind, "id": identity, "use": use, "movements": movements, "balance": balance}


def test_saved_source_ledger_pagination_backdated_focus_and_authenticated_recorder():
    async def scenario(db, client):
        context = await client.get(BASE + "/context")
        assert context.status_code == 200, context.text
        assert context.json()["actor"] == {"id": "owner", "name": "محمد عبدالله", "is_owner": True}
        assert "foreign" not in {r["id"] for r in context.json()["resources"]}
        last = None
        for index in range(7):
            payload = entry(amount="10", business_date="2026-11-02" if index == 6 else "2026-11-10")
            response = await client.post(BASE + "/entries", json=payload)
            assert response.status_code == 200, response.text
            last = response.json()
        ledger = (await client.get(BASE + "/ledger/external_person/external")).json()
        assert len(ledger["items"]) == 5 and ledger["total"] == 8 and ledger["total_pages"] == 2
        assert ledger["resource"]["balance"]["amount"] == "330.00"
        assert ledger["items"][0]["recorded_by"]["name"] == "محمد عبدالله"
        await db.users.update_one({"id": "owner"}, {"$set": {"name": "اسم جديد"}})
        focused = (await client.get(BASE + "/ledger/external_person/external", params={"focus_id": last["movement_id"]})).json()
        assert focused["page"] == 2
        assert any(r["id"] == last["movement_id"] for r in focused["items"])
        assert next(r for r in focused["items"] if r["id"] == last["movement_id"])["recorded_by"]["name"] == "محمد عبدالله"
        assert focused["items"][-1]["recorded_by"] is None  # no invented legacy authors
    run(scenario)


def test_exact_retry_is_once_and_payload_actor_cannot_be_forged():
    async def scenario(db, client):
        payload = entry()
        first = await client.post(BASE + "/entries", json=payload)
        assert first.status_code == 200, first.text
        second = await client.post(BASE + "/entries", json=payload)
        assert second.json() == first.json()
        assert (await client.post(BASE + "/entries", json={**payload, "amount": "99"})).status_code == 409
        assert (await client.post(BASE + "/entries", json=entry(recorded_by={"id": "staff", "name": "مزور"}))).status_code == 422
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert balance(state, "bank", "bank")["actual"] == "900.00"
        assert state["movements"][0]["recorded_by"]["id"] == "owner"
    run(scenario)


def test_external_salary_is_atomic_linked_and_never_debits_bank_or_records_salary_twice():
    async def scenario(db, client):
        payload = entry(side="for_party", reason="salary_settlement", employee_id="employee", amount="60", source_type=None, source_id=None)
        response = await client.post(BASE + "/entries", json=payload)
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert len(state["movements"]) == 2
        assert balance(state, "external_person", "external")["outstanding_payable"] == "460.00"
        assert balance(state, "employee", "employee")["outstanding_payable"] == "140.00"
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert all(m["bank_id"] is None and m["recorded_by"]["id"] == "owner" for m in state["movements"])
        assert len({m["workspace_group"] for m in state["movements"]}) == 1
        assert (await client.post(BASE + "/entries", json=payload)).json() == response.json()
        assert (await client.post(BASE + "/entries", json={**payload, "request_id": "salary-over-limit", "amount": "141"})).status_code == 409
        assert len((await read(db, "owner"))["movements"]) == 2
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_custody_payment_reduces_actual_custody_without_expense_or_bank_movement():
    async def scenario(db, client):
        response = await client.post(BASE + "/entries", json=entry(source_type="employee_custody", source_id="employee"))
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert balance(state, "employee_custody", "employee")["custody_remaining"] == "50.00"
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert not any(r["party_type"] == "operating_expense" for r in report(state)["parties"])
        assert (await client.post(BASE + "/entries", json=entry(source_type="employee_custody", source_id="employee", amount="51"))).status_code == 409
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)


@pytest.mark.parametrize("changes", [
    {"reason": "payment"}, {"employee_id": "employee"}, {"source_type": None},
    {"side": "for_party", "reason": "salary_settlement", "employee_id": "employee"},
    {"source_id": "foreign"}, {"side": "for_party", "reason": "anything"},
])
def test_hidden_stale_fields_or_foreign_resources_cannot_change_money(changes):
    async def scenario(db, client):
        before = await read(db, "owner")
        response = await client.post(BASE + "/entries", json=entry(**changes))
        assert response.status_code in {403, 409, 422}, response.text
        after = await read(db, "owner")
        assert after["movements"] == before["movements"] and after["openings"] == before["openings"]
    run(scenario)


def test_own_scope_never_returns_full_balance_or_other_user_rows_and_revocation_is_live():
    async def scenario(db, client):
        owner_payment = await client.post(BASE + "/entries", json=entry())
        assert owner_payment.status_code == 200
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"), resource("bank", "bank", movements="none")])
        headers = {"x-test-user": "staff", "x-test-mobile": "yes"}
        result = await client.get(BASE + "/context", headers=headers)
        assert result.status_code == 200, result.text
        context = result.json()
        assert {r["id"] for r in context["resources"]} == {"external", "bank"}
        assert all(r["balance"] is None for r in context["resources"])
        assert (await client.get(BASE + "/ledger/external_person/external", headers=headers)).json()["total"] == 0
        payload = entry(expected_session_scope=digest(["owner", "staff"]))
        saved = await client.post(BASE + "/entries", json=payload, headers=headers)
        assert saved.status_code == 200, saved.text
        ledger = (await client.get(BASE + "/ledger/external_person/external", headers=headers)).json()
        assert ledger["total"] == 1 and ledger["resource"]["balance"] is None
        assert ledger["items"][0]["recorded_by"] == {"id": "staff", "name": "خالد أحمد"}
        assert (await client.get(BASE + "/ledger/bank/bank", headers=headers)).status_code == 404
        assert (await client.get("/api/operational-balances/reports", headers=headers)).status_code == 403
        await grant(db, operations=[], resources=[], version=2)
        assert (await client.post(BASE + "/entries", json=payload, headers=headers)).status_code == 403
        assert len((await read(db, "owner"))["movements"]) == 2
    run(scenario)


def test_linked_party_and_custody_operations_require_all_grants_before_any_leg():
    async def scenario(db, client):
        await grant(db, operations=["external.salary", "external.pay"], resources=[resource("external_person", "external"), resource("employee_custody", "employee")])
        headers = {"x-test-user": "staff"}
        scope = digest(["owner", "staff"])
        salary = entry(expected_session_scope=scope, side="for_party", reason="salary_settlement", employee_id="employee", source_type=None, source_id=None)
        custody = entry(expected_session_scope=scope, source_type="employee_custody", source_id="employee")
        assert (await client.post(BASE + "/entries", json=salary, headers=headers)).status_code == 403
        assert (await client.post(BASE + "/entries", json=custody, headers=headers)).status_code == 403
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_bank_transfer_checks_both_accounts_and_same_source_cannot_be_destination():
    async def scenario(db, client):
        payload = entry(party_type="cash", party_id="cash", side="for_us", reason="transfer", amount="120")
        response = await client.post(BASE + "/entries", json=payload)
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert balance(state, "bank", "bank")["actual"] == "880.00"
        assert balance(state, "cash", "cash")["actual"] == "620.00"
        await grant(db, operations=["accounts.transfer"], resources=[resource("bank", "bank")])
        payload.update(request_id="transfer-not-allowed", expected_session_scope=digest(["owner", "staff"]))
        assert (await client.post(BASE + "/entries", json=payload, headers={"x-test-user": "staff"})).status_code == 403
        assert len((await read(db, "owner"))["movements"]) == 1
    run(scenario)


def test_home_and_personal_money_is_owner_draw_not_operating_expense():
    async def scenario(db, client):
        for reason in ("household", "personal", "charity_owner"):
            payload = entry(side="for_party", reason=reason, owner_id="owner", source_type=None, source_id=None, amount="10")
            response = await client.post(BASE + "/entries", json=payload)
            assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert balance(state, "external_person", "external")["outstanding_payable"] == "430.00"
        assert balance(state, "owner_withdrawal", "owner")["owner_withdrawals"] == "30.00"
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert not any(r["party_type"] == "operating_expense" for r in report(state)["parties"])
    run(scenario)


def test_concurrent_external_salary_never_overpays_or_leaves_one_leg():
    async def scenario(db, client):
        payloads = [entry(side="for_party", reason="salary_settlement", employee_id="employee",
                         amount="150", source_type=None, source_id=None) for _ in range(2)]
        replies = await asyncio.gather(*(client.post(BASE + "/entries", json=p) for p in payloads))
        assert sorted(r.status_code for r in replies) == [200, 409]
        state = await read(db, "owner")
        assert len(state["movements"]) == 2
        assert balance(state, "employee", "employee")["outstanding_payable"] == "50.00"
        assert balance(state, "external_person", "external")["outstanding_payable"] == "550.00"
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
    run(scenario)


def test_two_concurrent_cash_payments_cannot_overdraw_bank():
    async def scenario(db, client):
        replies = await asyncio.gather(*(client.post(BASE + "/entries", json=entry(amount="700")) for _ in range(2)))
        assert sorted(r.status_code for r in replies) == [200, 409]
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert balance(state, "bank", "bank")["actual"] == "300.00"
    run(scenario)

