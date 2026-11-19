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
        daily = (await client.get(BASE + "/movements")).json()
        assert daily["total"] == 7  # Six openings and one linked salary event.
        assert daily["items"][0]["party_type"] == "employee"
        assert daily["items"][0]["event_id"] == state["movements"][0]["workspace_group"]
        assert (await client.post(BASE + "/entries", json=payload)).json() == response.json()
        rejected_payload = {**payload, "request_id": "salary-over-limit", "amount": "141"}
        rejected = await client.post(BASE + "/entries", json=rejected_payload)
        assert rejected.status_code == 409 and rejected.json()["detail"]["not_applied"] is True
        assert len((await read(db, "owner"))["movements"]) == 2
        assert await db.general_ledger.count_documents({}) == 0
        credit = entry(party_type="employee", party_id="employee", reason="accrual", side="for_party",
                       amount="100", source_type=None, source_id=None)
        assert (await client.post(BASE + "/entries", json=credit)).status_code == 200
        retried = await client.post(BASE + "/entries", json=rejected_payload)
        assert retried.status_code == 409 and retried.json() == rejected.json()
        assert balance(await read(db, "owner"), "employee", "employee")["outstanding_payable"] == "240.00"
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


def test_company_charity_is_one_expense_with_one_external_credit_and_no_bank_leg():
    async def scenario(db, client):
        await db.expense_categories.insert_one({"id": "charity-id", "user_id": "owner", "code": "charity",
            "name": "صدقات", "source": "operational_balance", "status": "active"})
        payload = entry(side="for_party", reason="charity_company", expense_id="charity",
                        amount="35", source_type=None, source_id=None)
        response = await client.post(BASE + "/entries", json=payload)
        assert response.status_code == 200, response.text
        assert (await client.post(BASE + "/entries", json=payload)).json() == response.json()
        state = await read(db, "owner")
        assert len(state["movements"]) == 2
        assert balance(state, "operating_expense", "charity")["expense_paid"] == "35.00"
        assert balance(state, "external_person", "external")["outstanding_payable"] == "435.00"
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert not any(row["party_type"] == "owner_withdrawal" for row in report(state)["parties"])
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


def test_owner_return_restores_bank_and_reduces_drawings_without_new_expense():
    async def scenario(db, client):
        draw = entry(party_type="owner_withdrawal", party_id="owner", reason="payment", amount="100")
        assert (await client.post(BASE + "/entries", json=draw)).status_code == 200
        returned = entry(party_type="owner_withdrawal", party_id="owner", side="for_party", reason="collection", amount="40")
        response = await client.post(BASE + "/entries", json=returned)
        assert response.status_code == 200, response.text
        state = await read(db, "owner")
        assert balance(state, "bank", "bank")["actual"] == "940.00"
        assert balance(state, "owner_withdrawal", "owner")["owner_withdrawals"] == "60.00"
        assert (await client.post(BASE + "/entries", json={**returned, "request_id": "owner-return-too-large", "amount": "61"})).status_code == 409
        assert len((await read(db, "owner"))["movements"]) == 2
    run(scenario)


def test_external_salary_respects_existing_source_conflict_guard():
    async def scenario(db, client):
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {
            "engine.salary_conflicts.salary:employee:2026-11": {"reason": "changed_source"}}})
        response = await client.post(BASE + "/entries", json=entry(
            side="for_party", reason="salary_settlement", employee_id="employee",
            source_type=None, source_id=None))
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "operational_salary_adjustment_required"
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


@pytest.mark.parametrize("indexed", [False, True])
def test_executed_refund_ledger_uses_confirmer_not_requester_or_request_date(indexed):
    from operational_balance_access import AccessPolicy
    from operational_balance_workspace import _index_ledger_sources, ledger_items
    bank = {"kind": "bank", "id": "bank", "name": "الإنماء", "currency": "SAR",
            "access": {"use": False, "movements": "own", "balance": False}}
    policy = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), [
        resource("bank", "bank", use=False)])
    confirmed = {"id": "confirmed", "amount": "40.00", "currency": "SAR", "status": "refunded",
                 "refund_source_type": "bank", "refund_source_id": "bank", "actor_id": "owner",
                 "recorded_by": {"id": "owner", "name": "طالب الاسترجاع"},
                 "business_date": "2026-11-01", "created_at": "2026-11-01T12:00:00+00:00",
                 "refunded_at": "2026-11-10T12:00:00+00:00", "confirmed_by": "staff",
                 "confirmed_by_snapshot": {"id": "staff", "name": "من سجّل التنفيذ"}}
    state = {"customer_returns": [confirmed,
        {**confirmed, "id": "pending", "status": "pending"},
        {**confirmed, "id": "someone-else", "actor_id": "staff", "confirmed_by": "owner"}]}
    sources = _index_ledger_sources(state, {"obligations": []}, [bank])[("bank", "bank", "SAR")] if indexed else None
    rows = ledger_items(state, {"obligations": []}, bank, policy, sources=sources)
    assert len(rows) == 1
    assert rows[0]["id"] == "customer_return:confirmed"
    assert rows[0]["recorded_by"] == {"id": "staff", "name": "من سجّل التنفيذ"}
    assert rows[0]["business_date"] == "2026-11-10"
    assert rows[0]["amount"] == "40.00" and rows[0]["side"] == "for_party"


@pytest.mark.parametrize("indexed", [False, True])
def test_matched_provider_refund_ledger_requires_evidence_and_tracks_matching_actor_without_posting(indexed):
    from fastapi import HTTPException
    from operational_balance_access import AccessPolicy
    from operational_balance_workspace import _index_ledger_sources, ledger_items
    provider = {"kind": "provider", "id": "tamara", "name": "تمارا", "currency": "SAR",
                "access": {"use": False, "movements": "own", "balance": False}}
    policy = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), [
        resource("provider", "tamara", use=False)])
    fact = {"order_id": "order", "amount": "40.00"}
    case = {"id": "matched", "amount": "40.00", "order_id": "order", "currency": "SAR",
            "status": "provider_matched", "method": "original",
            "refund_source_type": "provider", "refund_source_id": "tamara",
            "provider_evidence_id": "refund:tamara:REF", "provider_evidence_reference": "REF",
            "provider_evidence_hash": digest(fact), "actor_id": "owner",
            "created_at": "2026-11-01T12:00:00+00:00", "business_date": "2026-11-01",
            "matched_by": "staff", "matched_by_snapshot": {"id": "staff", "name": "مسجّل المطابقة"},
            "matched_at": "2026-11-10T12:00:00+00:00", "refunded_at": None}
    state = {"customer_returns": [case], "engine": {"facts": {"refund:tamara:REF": fact}}, "movements": []}
    def project():
        sources = _index_ledger_sources(state, {"obligations": []}, [provider])[("provider", "tamara", "SAR")] if indexed else None
        return ledger_items(state, {"obligations": []}, provider, policy, sources=sources)
    before = deepcopy(state)
    rows = project()
    assert state == before
    assert len(rows) == 1 and rows[0]["id"] == "customer_return:matched"
    assert rows[0]["recorded_by"] == {"id": "staff", "name": "مسجّل المطابقة"}
    assert rows[0]["recorded_at"] == case["matched_at"]
    assert rows[0]["business_date"] == ""  # Unknown provider execution date stays unknown.
    case["refunded_at"] = "2026-11-08T22:00:00+00:00"
    assert project()[0]["business_date"] == "2026-11-09"
    case["matched_by"] = "owner"
    assert project() == []
    case["matched_by"] = "staff"
    state["engine"]["facts"]["refund:tamara:REF"] = {"order_id": "another-order", "amount": "40.00"}
    with pytest.raises(HTTPException) as error:
        project()
    assert error.value.detail["code"] == "operational_refund_evidence_changed"


def test_daily_projection_scans_saved_history_once_for_many_resources(monkeypatch):
    """Five displayed rows must not reread all history for every visible party."""
    import operational_balance_workspace as workspace
    visits = {"movements": 0, "obligations": 0}
    source_sizes = {}
    original_catalog = workspace.resource_catalog

    class CountedRows(list):
        def __init__(self, values, source):
            super().__init__(values)
            self.source = source

        def __iter__(self):
            for row in super().__iter__():
                visits[self.source] += 1
                yield row

    async def measured_catalog(db, owner, policy, state, **options):
        # Count only the endpoint projection, after the real report and catalog.
        resources, computed = await original_catalog(db, owner, policy, state, **options)
        for source, container in (("movements", state), ("obligations", computed)):
            source_sizes[source] = len(container[source])
            container[source] = CountedRows(container[source], source)
        return resources, computed

    monkeypatch.setattr(workspace, "resource_catalog", measured_catalog)

    async def scenario(db, client):
        await db.mezan_suppliers_v2.insert_many([
            {"id": f"supplier-{index:02d}", "user_id": "owner", "name": f"Supplier {index:02d}", "status": "active"}
            for index in range(20)
        ])
        movements = [
            {"id": f"movement-{index:04d}", "party_type": "supplier", "party_id": f"supplier-{index % 20:02d}",
             "currency": "SAR", "amount": "1.00", "direction": "outgoing", "kind": "payment",
             "bank_kind": "bank", "bank_id": "bank", "bank_name": "الإنماء", "allocations": [],
             "actor_id": "owner", "business_date": "2026-11-17", "occurred_at": "2026-11-17T12:00:00+00:00"}
            for index in range(500)
        ]
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"movements": movements}})
        before = await db[STATES].find_one({"owner_id": "owner"})
        response = await client.get(BASE + "/movements")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["total"] == 506  # One row per event, plus six opening balances.
        assert [row["id"] for row in result["items"]] == [f"movement-{index:04d}" for index in range(499, 494, -1)]
        assert all(row["amount"] == "1.00" and row["side"] == "for_us" and row["party_type"] == "supplier"
                   for row in result["items"])
        assert await db[STATES].find_one({"owner_id": "owner"}) == before
        assert visits["movements"] <= 2 * source_sizes["movements"], visits
        assert visits["obligations"] <= 2 * source_sizes["obligations"], visits
    run(scenario)


def test_ledger_projection_observes_cancellation_while_scanning_unrelated_history():
    from threading import Event
    from operational_balance_access import AccessPolicy
    from operational_balance_workspace import ledger_items
    from operational_financial_activity_runtime import preparation_scope
    stop = Event()
    visited = []

    class StopDuringRows(list):
        def __iter__(self):
            for index, row in enumerate(super().__iter__()):
                visited.append(index)
                if index == 10:
                    stop.set()
                yield row

    state = {"movements": StopDuringRows([
        {"party_type": "supplier", "party_id": "unrelated", "currency": "SAR"} for _ in range(1000)
    ])}
    supplier = {"kind": "supplier", "id": "supplier", "name": "المورد", "currency": "SAR",
                "access": {"movements": "all"}}
    policy = AccessPolicy({"id": "owner"}, "owner", True, True, 1, (), [])
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        ledger_items(state, {"obligations": []}, supplier, policy)
    assert len(visited) <= 12


@pytest.mark.parametrize("mode", ["all", "own"])
def test_indexed_ledger_preserves_currency_counterparts_redaction_and_actor_scope(mode):
    from operational_balance_access import AccessPolicy
    from operational_balance_workspace import _index_ledger_sources, ledger_items
    resources = [{"kind": kind, "id": identity, "name": identity, "currency": currency,
                  "access": {"movements": mode, "balance": True}}
                 for kind, identity, currency in (("supplier", "supplier", "SAR"), ("supplier", "supplier", "USD"),
                                                  ("bank", "bank", "SAR"), ("bank", "bank", "USD"), ("cash", "cash", "SAR"))]
    policy = AccessPolicy({"id": "staff"}, "owner", False, True, 1, (), [
        resource(row["kind"], row["id"], movements=mode) for row in resources
    ])
    common = {"currency": "SAR", "amount": "10.00", "direction": "outgoing", "kind": "payment",
              "party_type": "supplier", "party_id": "supplier", "actor_id": "staff",
              "business_date": "2026-11-17", "occurred_at": "2026-11-17T12:00:00+00:00"}
    state = {
        "openings": {"opening": {**common, "id": "opening", "direction": "for_party", "amount": "5.00"}},
        "movements": [
            {**common, "id": "fx", "bank_kind": "bank", "bank_id": "bank", "bank_currency": "USD", "bank_amount": "2.50"},
            {**common, "id": "usd", "currency": "USD", "actor_id": "owner"},
            {**common, "id": "same", "party_type": "bank", "party_id": "bank", "bank_kind": "bank", "bank_id": "bank",
             "bank_amount": "3.00", "kind": "correction"},
            {**common, "id": "transfer", "party_type": "cash", "party_id": "cash", "bank_kind": "bank", "bank_id": "bank", "kind": "transfer"},
            {**common, "id": "hidden", "bank_kind": "bank", "bank_id": "hidden", "bank_name": "Hidden bank",
             "note": "Hidden note", "reference": "Hidden reference", "order_number": "Hidden order"},
        ],
        "customer_returns": [],
    }
    obligation = {**common, "id": "accrual", "kind": "accrual", "confirmed": "4.00", "direction": "payable"}
    computed = {"obligations": [obligation, {**obligation, "id": "baseline", "kind": "baseline"},
                                {**obligation, "id": "virtual", "virtual": True}]}
    before = deepcopy((state, computed))
    indexed = _index_ledger_sources(state, computed, resources)
    results = {}
    for row in resources:
        key = row["kind"], row["id"], row["currency"]
        results[key] = ledger_items(state, computed, row, policy, sources=indexed[key])
        assert results[key] == ledger_items(state, computed, row, policy)
    assert (state, computed) == before
    supplier = {row["id"]: row for row in results[("supplier", "supplier", "SAR")]}
    assert set(supplier) == ({"opening", "accrual", "fx", "hidden"} if mode == "all" else {"accrual", "fx", "hidden"})
    if mode == "all":
        assert (supplier["opening"]["amount"], supplier["opening"]["side"]) == ("5.00", "for_party")
    assert (supplier["accrual"]["amount"], supplier["accrual"]["side"]) == ("4.00", "for_party")
    assert (supplier["fx"]["amount"], supplier["fx"]["side"], supplier["fx"]["currency"]) == ("10.00", "for_us", "SAR")
    assert "Hidden" not in str(supplier["hidden"])
    assert supplier["hidden"]["order_number"] is None
    bank = {row["id"]: row for row in results[("bank", "bank", "SAR")]}
    assert set(bank) == {"same", "transfer"}
    assert (bank["same"]["amount"], bank["same"]["side"]) == ("3.00", "for_party")
    assert results[("bank", "bank", "USD")][0]["amount"] == "2.50"
    assert results[("cash", "cash", "SAR")][0]["side"] == "for_us"
    assert [row["id"] for row in results[("supplier", "supplier", "USD")]] == (["usd"] if mode == "all" else [])


def test_daily_source_index_stops_when_the_owned_preparation_is_cancelled():
    from threading import Event
    from operational_balance_workspace import _index_ledger_sources
    from operational_financial_activity_runtime import preparation_scope
    stop = Event()
    visited = []

    class StopDuringRows(list):
        def __iter__(self):
            for index, row in enumerate(super().__iter__()):
                visited.append(index)
                if index == 10:
                    stop.set()
                yield row

    state = {"movements": StopDuringRows([
        {"party_type": "supplier", "party_id": "unrelated", "currency": "SAR"} for _ in range(1000)
    ])}
    supplier = {"kind": "supplier", "id": "supplier", "currency": "SAR", "access": {"movements": "all"}}
    with preparation_scope(stop), pytest.raises(asyncio.CancelledError):
        _index_ledger_sources(state, {"obligations": []}, [supplier])
    assert len(visited) <= 12


async def archived_workspace_fixture(db):
    from operational_balance_archive import AUDIT_PAGES, MARKER, compact_financial_snapshot, compact_issues, content_key, manifest
    state = await read(db, "owner")
    fact = {"order_id": "order-refund", "amount": "40.00"}
    state["engine"] = {
        "facts": {"refund:tamara:REF": fact},
        "obligations": {"accrual": {"id": "accrual", "kind": "source", "party_type": "supplier", "party_id": "supplier",
            "currency": "SAR", "confirmed": "120.00", "expected": "0.00", "direction": "payable", "actor_id": "owner"}},
        "issues": [{"code": "fixture_source_gap", "source_id": "synthetic"}],
        "audit": {"tail": [{"id": "tail", "amount": "1.00"}]},
    }
    state["movements"] = [
        {"id": f"payment-{index}", "party_type": "supplier", "party_id": "supplier", "currency": "SAR",
         "amount": "5.00", "direction": "outgoing", "kind": "payment", "allocations": [],
         "bank_kind": "bank", "bank_id": "bank", "bank_name": "الإنماء", "actor_id": "owner",
         "business_date": "2026-11-17", "occurred_at": "2026-11-17T12:00:00+00:00"} for index in range(8)
    ]
    state["customer_returns"] = [{"id": "matched", "amount": "40.00", "order_id": "order-refund", "currency": "SAR",
        "status": "provider_matched", "method": "original", "refund_source_type": "provider", "refund_source_id": "tamara",
        "provider_evidence_id": "refund:tamara:REF", "provider_evidence_reference": "REF", "provider_evidence_hash": digest(fact),
        "actor_id": "owner", "matched_by": "owner", "matched_at": "2026-11-16T12:00:00+00:00", "refunded_at": None}]
    state["requests"] = {"private-request": {"hash": "synthetic-private-idempotency", "result": {"id": "saved"}}}
    state["audit"] = [{"id": "private-audit", "reason": "synthetic-private-audit"}]
    pages, refs = [], []
    for index in range(8):
        events = [{"id": f"audit-{index}-{offset}", "amount": "1.00"} for offset in range(2)]
        key = content_key("owner", index * 2, events)
        pages.append({"_id": key, "owner_id": "owner", "start": index * 2, "events": events})
        refs.append({"id": key, "count": len(events)})
    state[MARKER] = manifest(16, refs)
    packed = compact_issues(compact_financial_snapshot(state, 1_000_000), 1_000_000)
    await db[AUDIT_PAGES].insert_many(pages)
    await db[STATES].replace_one({"owner_id": "owner"}, packed)
    return packed, pages


def test_workspace_report_reads_preserve_full_state_results_without_loading_audit_pages(monkeypatch):
    import operational_financial_position_state as financial_state
    from fastapi import HTTPException
    from operational_balance_archive import AUDIT_PAGES

    async def scenario(db, client):
        packed, pages = await archived_workspace_fixture(db)
        full_state = await read(db, "owner")
        projected = await financial_state.read_financial_position_state(db, "owner")
        assert report(projected) == report(full_state)
        assert "audit" not in projected and "requests" not in projected and "audit" not in projected["engine"]
        collection = db[AUDIT_PAGES]
        collection_type = type(collection)
        original_find = collection_type.find_one
        reads = []

        async def observed_find(current, *args, **kwargs):
            if current.name == AUDIT_PAGES and current.database.name == db.name:
                reads.append(args[0])
            return await original_find(current, *args, **kwargs)

        monkeypatch.setattr(collection_type, "find_one", observed_find)
        urls = ["/context", "/parties?kind=supplier", "/ledger/provider/tamara", "/movements?page=2",
                "/ledger/supplier/supplier?focus_id=payment-0"]
        # Use the real complete store reader as the independent reference seam.
        with monkeypatch.context() as reference:
            reference.setattr(financial_state, "read_financial_position_state", read)
            expected = [await client.get(BASE + path) for path in urls]
        assert all(response.status_code == 200 for response in expected)
        assert len(reads) == len(urls) * len(pages)
        reads.clear()
        responses = [await client.get(BASE + path) for path in urls]
        assert all(response.status_code == 200 for response in responses)
        assert [response.json() for response in responses] == [response.json() for response in expected]
        assert responses[2].json()["items"][0]["id"] == "customer_return:matched"
        assert responses[2].json()["items"][0]["amount"] == "40.00"
        assert reads == []
        assert await db[STATES].find_one({"owner_id": "owner"}) == packed
        # Financial readers validate manifest metadata; historical page contents
        # remain the responsibility of unchanged audit and mutation readers.
        await collection.delete_one({"_id": pages[0]["_id"]})
        assert (await client.get(BASE + "/context")).status_code == 200
        with pytest.raises(HTTPException) as error:
            await read(db, "owner")
        assert error.value.detail["code"] == "operational_audit_archive_invalid"
    run(scenario)


@pytest.mark.parametrize("damage", ["financial_hash", "financial_marker", "audit_manifest"])
def test_workspace_report_reads_fail_closed_on_corrupt_financial_packets_or_audit_manifest(damage):
    from operational_balance_archive import MARKER, SNAPSHOT

    async def scenario(db, client):
        packed, _ = await archived_workspace_fixture(db)
        if damage == "financial_hash":
            packed[SNAPSHOT]["sha256"] = "0" * 64
        elif damage == "financial_marker":
            packed["engine"]["facts"] = {}
        else:
            packed[MARKER]["sha256"] = "0" * 64
        await db[STATES].replace_one({"owner_id": "owner"}, packed)
        for path in ("/context", "/parties", "/ledger/provider/tamara", "/movements"):
            response = await client.get(BASE + path)
            assert response.status_code == 503, response.text
            assert "items" not in response.json() and "resources" not in response.json()
    run(scenario)



def test_prepaid_cash_transfer_requires_reference_and_survives_retries():
    from operational_balance_store import mutate

    async def scenario(db, client):
        async def configure(state):
            state['courier_tariffs'] = [{'payment_mode': 'prepaid', 'cash_account': {'id': 'cash'}}]

        await mutate(db, 'owner', configure)
        command = entry(party_type='cash', party_id='cash', reason='transfer', amount='120.00')
        missing = await client.post(BASE + '/entries', json=command)
        assert missing.status_code == 422
        command.update(request_id='documented-wallet-topup', reference='TEST-TOPUP')
        saved = await client.post(BASE + '/entries', json=command)
        assert saved.status_code == 200, saved.text
        replay = await client.post(BASE + '/entries', json=command)
        assert replay.json()['movement_id'] == saved.json()['movement_id']
        repeated_reference = await client.post(BASE + '/entries', json={**command, 'request_id': 'another-topup-command'})
        assert repeated_reference.status_code == 409
        assert len((await read(db, 'owner'))['movements']) == 1
        for kind, expected in [('bank', '880.00'), ('cash', '620.00')]:
            ledger = (await client.get(f'{BASE}/ledger/{kind}/{kind}')).json()
            row = next(item for item in ledger['items'] if item['id'] == saved.json()['movement_id'])
            assert row['balance_after'] == expected and row['reference'] == 'TEST-TOPUP'

    run(scenario)


def test_employee_usd_ad_funding_from_assigned_sar_bank_posts_both_legs_once():
    from backend.tests.test_operational_balance_integration import seed_ad_account

    async def scenario(db, client):
        await seed_ad_account(db)
        await db.mezan_integration_accounts_v2.update_one(
            {"mezan_integration_account_id": "ia"}, {"$set": {"currency": "USD"}})
        await db[STATES].update_one({"owner_id": "owner"}, {"$set": {"engine.ad_balance_sar_basis.ad": {
            "exchange_rate_to_sar": "3.75", "bank_commission_pct": "2",
            "apply_bank_commission": True, "bank_commission_vat_pct": "15"}}})
        resources = [resource("ad_account", "ad"), resource("bank", "bank")]
        await grant(db, operations=["ads.fund"], resources=resources)
        headers = {"x-test-user": "staff", "x-test-mobile": "yes"}
        async def catalog(params=""):
            result = await client.get(BASE + "/context" + params, headers=headers)
            assert result.status_code == 200, result.text
            return next(r for r in result.json()["resources"] if r["kind"] == "ad_account")
        assert (await catalog())["operations"] == []
        ad = await catalog("?ad_bank_fx=true")
        assert ad["operations"] == ["ads.fund"] and ad["ad_bank_funding"]["mode"] == "prepaid"
        assert ad["ad_bank_funding"]["terms"]["exchange_rate_to_sar"] == "3.75"
        for currency, amount in (("SAR", "383.63"), ("USD", "100.00")):
            quote = await client.post(BASE + "/ad-funding-quote", headers=headers,
                                      json={"party_id": "ad", "currency": currency, "amount": amount})
            assert quote.status_code == 200, quote.text
            assert quote.json() == {"amount_usd": "100.00", "gross_sar": "383.63", "fee_sar": "8.63",
                                    "commission_sar": "7.50", "fee_vat_sar": "1.13", "exchange_rate": "3.75"}
        body = entry(expected_session_scope=digest(["owner", "staff"]), party_type="ad_account",
                     party_id="ad", reason="payment", reference="BANK-TEST-USD", amount="100",
                     ad_bank_funding={"bank_amount": "383.63", "bank_fee_amount": "8.63", "kind": "wallet_funding"})
        async def send(**changes):
            return await client.post(BASE + "/entries", headers=headers, json={**body, **changes})
        assert (await send(source_type="cash", source_id="cash")).status_code == 422
        assert (await send(source_id="foreign")).status_code in {403, 404}
        await grant(db, operations=[], resources=resources, version=2)
        assert "ad_bank_funding" not in await catalog("?ad_bank_fx=true")
        assert (await send()).status_code == 403
        await grant(db, operations=["ads.fund"], resources=resources, version=3)
        saved = await send()
        assert saved.status_code == 200, saved.text
        assert (await send()).json() == saved.json()
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        row = state["movements"][0]
        assert (row["currency"], row["amount"], row["bank_currency"], row["bank_amount"], row["bank_fee_amount"],
                row["bank_net_amount"], row["actor_id"]) == ("USD", "100.00", "SAR", "383.63", "8.63", "375.00", "staff")
        assert balance(state, "bank", "bank")["actual"] == "616.37"
        for path, expected, currency in [("ad_account/ad", "100.00", "USD"), ("bank/bank", "383.63", "SAR")]:
            res = await client.get(BASE + "/ledger/" + path + "?ad_bank_fx=true", headers=headers)
            assert res.status_code == 200
            movement = next(r for r in res.json()["items"] if r.get("recorded_by", {}) and r["recorded_by"]["id"] == "staff")
            assert (movement["amount"], movement["currency"]) == (expected, currency)
    run(scenario)


@pytest.mark.parametrize("currency", ["USD", "SAR"])
def test_owner_can_assign_operational_wallet_to_employee(currency):
    from operational_ad_funding import party_id
    from backend.tests.test_operational_balance_integration import seed_ad_account

    async def scenario(db, client):
        await seed_ad_account(db)
        await db.mz2_ad_account_bindings_v2.delete_one({"_id": "ad"})
        await db.mezan_integration_accounts_v2.update_one(
            {"mezan_integration_account_id": "ia"}, {"$set": {"currency": currency}})
        account = party_id("owner", "ia")
        setting = {"mode": "wallet_first", "platform": "meta", "platform_account_id": "external",
                   "integration_account_id": "ia", "confirmed_by": "owner", "confirmed_at": "2026-11-01T00:00:00Z",
                   "currency": currency, "timezone": "Asia/Riyadh"}
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"ad_funding": {account: setting}}})
        access = "/api/operational-balances/access"
        rows = (await client.get(access + "/catalog")).json()["resources"]
        assert [(r["id"], r["currency"]) for r in rows if r["kind"] == "ad_account"] == [(account, currency)]
        command = {"enabled": True, "expected_version": 0, "operations": ["ads.fund"],
                   "resources": [resource("ad_account", account), resource("bank", "bank")]}
        saved = await client.put(access + "/users/staff", json=command)
        assert saved.status_code == 200, saved.text
        stored = (await client.get(access + "/users/staff")).json()["access"]
        assert {r["id"] for r in stored["resources"]} == {"bank", account}
        for headers in ({}, {"x-test-user": "staff", "x-test-mobile": "yes"}):
            ledger = await client.get(BASE + "/ledger/ad_account/" + account + "?ad_bank_fx=true", headers=headers)
            assert ledger.status_code == 200, ledger.text
            assert ledger.json()["resource"]["operations"] == ["ads.fund"]
        command.update(expected_version=1, resources=[resource("ad_account", "unbound:other")])
        assert (await client.put(access + "/users/staff", json=command)).status_code == 422
    run(scenario)


def test_supplier_invoice_is_one_searchable_row_with_recorded_approver_and_lazy_lines():
    async def scenario(db, client):
        stamp = "2026-11-14T12:00:00+00:00"
        terms = {"kind": "supplier", "party_type": "supplier", "party_id": "supplier", "currency": "SAR",
                 "direction": "payable", "confirmed": "13.00", "expected": "0.00", "gross_amount": "13.00",
                 "business_date": "2026-11-14", "source_invoice_id": "inv1", "source_invoice_number": "SI-26",
                 "source_invoice_approved_at": stamp}
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"engine.obligations": {
            key: {**terms, "id": key} for key in ("pieceA", "pieceB")}}})
        await db.mezan_supplier_invoices_v2.insert_one({"user_id": "owner", "supplier_id": "supplier", "id": "inv1",
            "invoice_number": "SI-26", "approved_at": stamp, "approved_by": "staff", "total_halalas": 2600,
            "lines": [{"product_name": "غلاف", "sku": "AMS-26", "selected_image_url": "/products/cover.jpg",
                       "quantity": 2, "product_unit_price_halalas": 1300, "total_halalas": 2600}]})
        path = BASE + "/ledger/supplier/supplier"
        for route in (path, BASE + "/movements"):
            page = (await client.get(route, params={"q": "خالد أحمد"})).json()
            assert page["total"] == 1
            row = page["items"][0]
            assert (row["amount"], row["reference"], row["recorded_by"]["name"]) == ("26.00", "SI-26", "خالد أحمد")
            assert row["invoice_id"] == "inv1" and "lines" not in row
        detail = BASE + "/supplier-invoices/supplier/"
        invoice = (await client.get(detail + "inv1")).json()
        assert invoice["total"] == "26.00" and invoice["lines"] == [
            {"name": "غلاف", "sku": "AMS-26", "image_url": "/products/cover.jpg",
             "quantity": 2, "unit": "13.00", "total": "26.00", "services": []}]
        await db[STATES].update_one({"_id":"owner"}, {"$set":{"engine.obligations.pieceA.returned_amount":"13.00",
                                                              "engine.obligations.pieceA.confirmed":"0.00"}})
        adjusted = (await client.get(path, params={"q":"SI-26"})).json()
        assert adjusted["total"] == 1
        assert (adjusted["items"][0]["amount"], adjusted["items"][0]["invoice_gross"]) == ("13.00", "26.00")
        await grant(db, operations=[], resources=[resource("supplier", "supplier", movements="own")])
        staff = {"x-test-user": "staff"}
        assert (await client.get(detail + "inv1", headers=staff)).status_code == 200
        await db.mezan_supplier_invoices_v2.update_one({"id": "inv1"}, {"$set": {"approved_by": "owner"}})
        assert (await client.get(detail + "inv1", headers=staff)).status_code == 404
        assert (await client.get(detail + "absent")).status_code == 404
    run(scenario)


def test_paypal_payout_moves_net_to_bank_once_after_arrival():
    from test_operational_balance_configured_provider_fees import paid_state, settings
    async def scenario(db, client):
        wallet, _ = paid_state("paypal", terms=settings("PayPal", "4.9", "1.13", "0"))
        wallet["engine"]["provider_fee_inputs"]["source_mode"] = "mz2_policies"
        await db[STATES].update_one({"_id": "owner"}, {"$set": {"engine": wallet["engine"]}})
        async def available():
            context = (await client.get(BASE + "/context")).json()
            return next(r["balance"]["amount"] for r in context["resources"] if r["kind"] == "provider" and r["id"] == "paypal")
        assert await available() == "93.97"
        body = entry(party_type="provider", party_id="paypal", reason="collection", side="for_party",
                     amount="93.97", reference="PP-1", bank_arrival_confirmed=True)
        for bad in ({"source_type": "cash", "source_id": "cash"}, {"reference": ""}, {"amount": "100.00"}):
            invalid = await client.post(BASE + "/entries", json={**body, **bad, "request_id": "entry-" + uuid4().hex})
            assert invalid.status_code in {409, 422}
        assert (await client.post(BASE + "/entries", json=body)).status_code == 200
        assert (await client.post(BASE + "/entries", json=body)).status_code == 200
        assert await available() == "0.00"
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert balance(state, "bank", "bank")["actual"] == "1093.97"
    run(scenario)


def test_external_supplier_invoice_can_book_additional_services_without_touching_receipt_stock_or_cash():
    from operational_balance_engine import reconcile
    from operational_supplier_credit_purchase import DETAILS

    async def scenario(db, client):
        native = {"user_id": "owner", "supplier_id": "supplier", "id": "original-receipt", "invoice_number": "SUP-001",
                  "lines": [{"product_name": "قطعة مستلمة", "quantity": 1}], "total_halalas": 1300}
        await db.mezan_supplier_invoices_v2.insert_one(deepcopy(native))
        before = await read(db, "owner")
        await grant(db, operations=["supplier.accrual"], resources=[resource("supplier", "supplier")])
        staff = {"x-test-user": "staff"}
        body = entry(request_id=str(uuid4()), expected_session_scope=digest(["owner", "staff"]),
                     party_type="supplier", party_id="supplier", side="for_party", reason="credit_purchase",
                     source_id=None, source_type=None, reference="EXT-12", note="خدمات إضافية مستقلة", amount="115",
                     invoice={"lines":[{"kind":"service", "name":"تطريز اسم", "quantity":"10", "unit_price":"8"},
                                       {"kind":"product", "name":"غلاف", "quantity":"2.5", "unit_price":"12"}], "tax":"5"})
        pending = BASE + "/pending-commands/" + body["request_id"]
        registered = await client.post(pending, headers=staff, json={"action":"entry","target_id":None,"command":body})
        assert registered.status_code == 200, registered.text
        command = (await client.get(pending, headers=staff, params={"expected_session_scope":body["expected_session_scope"]})).json()["command"]
        first, retry = await asyncio.gather(*(client.post(BASE + "/entries", headers=staff, json=command) for _ in range(2)))
        assert first.status_code == retry.status_code == 200, (first.text, retry.text)
        assert first.json() == retry.json()
        saved = first.json()
        state = await read(db, "owner")
        assert state["movements"] == before["movements"]
        assert state.get("inventory_purchases") == before.get("inventory_purchases")
        assert await db.mezan_supplier_invoices_v2.find_one({}, {"_id":0}) == native
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        assert balance(state, "supplier", "supplier")["outstanding_payable"] == "315.00"
        refreshed = reconcile(state, {}, "2026-11-20T00:00:00+00:00")
        assert balance(refreshed, "supplier", "supplier")["outstanding_payable"] == "315.00"
        assert await db[DETAILS].count_documents({}) == 1
        for path in ("/ledger/supplier/supplier", "/movements"):
            page = (await client.get(BASE + path, params={"q":"EXT-12"}, headers=staff)).json()
            assert page["total"] == 1, page
            row = page["items"][0]
            assert row["invoice_id"] == saved["invoice_id"] and row["amount"] == "115.00"
            assert row["recorded_by"] == {"id":"staff", "name":"خالد أحمد"} and "lines" not in row
        url = BASE + "/supplier-invoices/supplier/" + saved["invoice_id"]
        doc = (await client.get(url, headers=staff)).json()
        assert doc["tax"] == "5.00" and doc["total"] == "115.00" and doc["number"] == saved["invoice_number"]
        assert doc["supplier_reference"] == "EXT-12" and doc["note"] == body["note"]
        assert [(line["kind"],line["total"]) for line in doc["lines"]] == [("service","80.00"),("product","30.00")]
        for changes, code in [({"reference":"ext-12"},409), ({"reference":"SUP-001"},409),
                              ({"reference":"EXT-13","amount":"100"},422), ({"source_id":"bank","source_type":"bank"},422)]:
            rejected = await client.post(BASE + "/entries", headers=staff, json={**body,**changes,"request_id":str(uuid4())})
            assert rejected.status_code == code, rejected.text
        await grant(db, operations=["supplier.payment"], resources=[resource("supplier", "supplier", movements="none")], version=2)
        assert (await client.get(url, headers=staff)).status_code in (403,404)
        assert (await client.post(BASE + "/entries", headers=staff, json={**body,"request_id":str(uuid4())})).status_code == 403
        payment = entry(party_type="supplier",party_id="supplier",reason="payment",side="for_us",amount="315.00")
        paid = await client.post(BASE + "/entries", json=payment)
        assert paid.status_code == 200, paid.text
        state = await read(db, "owner")
        assert balance(state, "supplier", "supplier")["outstanding_payable"] == "0.00"
        assert balance(state, "bank", "bank")["actual"] == "685.00"
        assert sum(a["amount"] == "115.00" and a["obligation_id"] == saved["movement_id"]
                   for a in state["movements"][-1]["allocations"]) == 1
        assert (await client.get(url)).json() == doc
    run(scenario)
