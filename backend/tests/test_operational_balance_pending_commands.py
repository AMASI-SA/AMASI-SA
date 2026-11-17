"""HTTP recovery boundaries: private server commands and unchanged money CAS."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

import operational_workspace_pending as pending_module
from operational_balance_store import digest, read
from operational_workspace_pending import PENDING_COMMANDS
from test_operational_balance_workspace import balance, entry, grant, resource, run
from test_operational_workspace_refunds import command as refund_command, confirm, created, order_fixture


BASE = "/api/operational-balances/workspace/pending-commands"
WORKSPACE = "/api/operational-balances/workspace"
STAFF = {"x-test-user": "staff"}


def registration(*, action="entry", target_id=None, body=None, actor="owner"):
    identity = str(uuid4())
    body = deepcopy(body) if body is not None else entry()
    body.update(request_id=identity, expected_session_scope=digest(["owner", actor]))
    return identity, {"action": action, "target_id": target_id, "command": body}


def query(actor="owner", owner="owner"):
    return {"expected_session_scope": digest([owner, actor])}


async def register(client, identity, packet, *, headers=None):
    response = await client.post(BASE + "/" + identity, json=packet, headers=headers)
    assert response.status_code == 200, response.text
    return response


async def retire(client, identity, *, actor="owner", headers=None):
    return await client.request("DELETE", BASE + "/" + identity, json=query(actor), headers=headers)


def test_registration_is_private_recovery_only_and_returns_stable_normalized_command():
    async def scenario(db, client):
        before = await read(db, "owner")
        identity, packet = registration(body=entry(amount="١٢٫٥", note="ملاحظة مالية خاصة"))
        saved = await register(client, identity, packet)
        assert saved.headers["cache-control"] == "no-store"
        result = saved.json()
        assert set(result) == {"id", "action", "target_id", "command"}
        assert result["id"] == result["command"]["request_id"] == identity
        assert result["command"]["amount"] == "12.50"
        assert result["command"]["note"] == "ملاحظة مالية خاصة"
        assert result["target_id"] is None
        assert "balance" not in result and "actor" not in result
        restored = await client.get(BASE + "/" + identity, params=query())
        assert restored.status_code == 200 and restored.json() == result
        assert restored.headers["cache-control"] == "no-store"
        assert await read(db, "owner") == before
        assert await db[PENDING_COMMANDS].count_documents({}) == 1
    run(scenario)


def test_concurrent_registration_is_once_and_changed_content_cannot_overwrite_it():
    async def scenario(db, client):
        identity, packet = registration()
        responses = await asyncio.gather(*[client.post(BASE + "/" + identity, json=packet) for _ in range(4)])
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == responses[0].json() for response in responses)
        changed = deepcopy(packet)
        changed["command"]["amount"] = "99.00"
        conflict = await client.post(BASE + "/" + identity, json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "operational_pending_command_conflict"
        assert "not_applied" not in conflict.json()["detail"]
        assert (await client.get(BASE + "/" + identity, params=query())).json() == responses[0].json()
        assert await db[PENDING_COMMANDS].count_documents({}) == 1
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_recovery_replays_the_original_financial_request_once_after_a_lost_response():
    async def scenario(db, client):
        identity, packet = registration()
        command = (await register(client, identity, packet)).json()["command"]
        first = await client.post(WORKSPACE + "/entries", json=command)
        assert first.status_code == 200, first.text
        # The browser only retained identity and did not observe that response.
        restored = await client.get(BASE + "/" + identity, params=query())
        retry = await client.post(WORKSPACE + "/entries", json=restored.json()["command"])
        assert retry.status_code == 200 and retry.json() == first.json()
        state = await read(db, "owner")
        assert len(state["movements"]) == 1
        assert balance(state, "bank", "bank")["actual"] == "900.00"
        assert balance(state, "external_person", "external")["outstanding_payable"] == "300.00"
    run(scenario)


def test_retirement_erases_payload_but_never_resurrects_or_changes_financial_history():
    async def scenario(db, client):
        identity, packet = registration()
        command = (await register(client, identity, packet)).json()["command"]
        assert (await client.post(WORKSPACE + "/entries", json=command)).status_code == 200
        state = await read(db, "owner")
        deleted = await retire(client, identity)
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "id": identity}
        assert deleted.headers["cache-control"] == "no-store"
        assert (await retire(client, identity)).json() == deleted.json()
        tombstone = await db[PENDING_COMMANDS].find_one({"_id": identity})
        assert tombstone["status"] == "retired" and "command" not in tombstone
        missing = await client.get(BASE + "/" + identity, params=query())
        delayed = await client.post(BASE + "/" + identity, json=packet)
        assert missing.status_code == delayed.status_code == 410
        assert missing.json()["detail"]["code"] == "operational_pending_command_retired"
        assert "not_applied" not in missing.json()["detail"]
        assert await read(db, "owner") == state
        assert await db[PENDING_COMMANDS].count_documents({}) == 1
    run(scenario)


def test_same_tenant_owner_and_other_tenant_cannot_read_or_retire_staff_command():
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"), resource("bank", "bank")])
        identity, packet = registration(actor="staff", body=entry(note="لا يظهر لحساب آخر"))
        saved = await register(client, identity, packet, headers=STAFF)
        for actor, owner in [("owner", "owner"), ("other", "other")]:
            response = await client.get(BASE + "/" + identity, params=query(actor, owner), headers={"x-test-user": actor})
            cleanup = await client.request("DELETE", BASE + "/" + identity, json=query(actor, owner), headers={"x-test-user": actor})
            assert response.status_code == cleanup.status_code == 404
            assert response.json()["detail"]["code"] == "operational_pending_command_not_found"
            assert "لا يظهر" not in response.text + cleanup.text
        owner_packet = deepcopy(packet)
        owner_packet["command"]["expected_session_scope"] = digest(["owner", "owner"])
        collision = await client.post(BASE + "/" + identity, json=owner_packet)
        assert collision.status_code == 404
        assert (await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)).json() == saved.json()
    run(scenario)


def test_use_without_balance_or_movement_access_can_recover_only_own_input():
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[
            resource("external_person", "external", movements="none", balance=False),
            resource("bank", "bank", movements="none", balance=False)])
        identity, packet = registration(actor="staff")
        await register(client, identity, packet, headers=STAFF)
        response = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert response.status_code == 200
        assert "balance" not in response.text and "1000.00" not in response.text
        assert (await client.get(WORKSPACE + "/ledger/bank/bank", headers=STAFF)).status_code == 404
    run(scenario)


@pytest.mark.parametrize("revocation", ["source", "party", "operation", "disabled", "actor"])
def test_current_permission_revocation_blocks_payload_and_all_recovery_operations(revocation):
    async def scenario(db, client):
        resources = [resource("external_person", "external"), resource("bank", "bank")]
        await grant(db, operations=["external.pay"], resources=resources)
        identity, packet = registration(actor="staff")
        await register(client, identity, packet, headers=STAFF)
        if revocation in {"source", "party"}:
            resources[1 if revocation == "source" else 0]["use"] = False
            await grant(db, operations=["external.pay"], resources=resources, version=2)
        elif revocation == "operation":
            await grant(db, operations=[], resources=resources, version=2)
        elif revocation == "disabled":
            await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access.enabled": False}})
        else:
            await db.users.update_one({"id": "staff"}, {"$set": {"is_active": False}})
        replies = [await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF),
                   await client.post(BASE + "/" + identity, json=packet, headers=STAFF),
                   await retire(client, identity, actor="staff", headers=STAFF)]
        assert all(response.status_code == 403 for response in replies)
        assert all("command" not in response.json() for response in replies)
        assert (await db[PENDING_COMMANDS].find_one({"_id": identity}))["status"] == "pending"
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_scope_reassignment_cannot_transfer_private_command_to_a_new_tenant():
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"), resource("bank", "bank")])
        identity, packet = registration(actor="staff")
        await register(client, identity, packet, headers=STAFF)
        await db.users.update_one({"id": "staff"}, {"$set": {
            "created_by": "other", "operational_balance_access.owner_id": "other"}})
        old_scope = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert old_scope.status_code == 409
        new_scope = await client.get(BASE + "/" + identity, params=query("staff", "other"), headers=STAFF)
        assert new_scope.status_code == 404
        assert "command" not in new_scope.json()
    run(scenario)


@pytest.mark.parametrize("shape", ["transfer_destination", "salary_employee", "custody_operation"])
def test_all_linked_resources_and_custody_operation_are_required(shape):
    async def scenario(db, client):
        if shape == "transfer_destination":
            body = entry(party_type="cash", party_id="cash", reason="transfer")
            operations, resources = ["accounts.transfer"], [resource("bank", "bank")]
        elif shape == "salary_employee":
            body = entry(side="for_party", reason="salary_settlement", employee_id="employee", source_type=None, source_id=None)
            operations, resources = ["external.salary"], [resource("external_person", "external")]
        else:
            body = entry(source_type="employee_custody", source_id="employee")
            operations, resources = ["external.pay"], [resource("external_person", "external"), resource("employee_custody", "employee")]
        await grant(db, operations=operations, resources=resources)
        identity, packet = registration(actor="staff", body=body)
        denied = await client.post(BASE + "/" + identity, json=packet, headers=STAFF)
        assert denied.status_code == 403, denied.text
        assert await db[PENDING_COMMANDS].count_documents({}) == 0
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_revocation_during_resource_validation_prevents_registration_write(monkeypatch):
    original_entity = pending_module.entity
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"), resource("bank", "bank")])
        async def revoke_before_write(database, owner, kind, identity, currency):
            value = await original_entity(database, owner, kind, identity, currency)
            if kind == "bank":
                await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access.operations": []}})
            return value
        monkeypatch.setattr(pending_module, "entity", revoke_before_write)
        identity, packet = registration(actor="staff")
        response = await client.post(BASE + "/" + identity, json=packet, headers=STAFF)
        assert response.status_code == 403
        assert await db[PENDING_COMMANDS].count_documents({}) == 0
    run(scenario)


def test_revocation_after_recovery_read_is_checked_before_returning_payload(monkeypatch):
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"), resource("bank", "bank")])
        identity, packet = registration(actor="staff")
        await register(client, identity, packet, headers=STAFF)
        collection_class = type(db[PENDING_COMMANDS])
        original_find = collection_class.find_one
        reads = 0
        async def revoke_after_database_read(collection, *args, **kwargs):
            nonlocal reads
            result = await original_find(collection, *args, **kwargs)
            if collection.name == PENDING_COMMANDS and collection.database.name == db.name:
                reads += 1
                if reads == 2:
                    await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access.operations": []}})
            return result
        monkeypatch.setattr(collection_class, "find_one", revoke_after_database_read)
        response = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert reads == 2
        assert response.status_code == 403 and "command" not in response.json()
    run(scenario)


def test_revocation_during_insert_keeps_private_recovery_but_returns_no_payload_or_money(monkeypatch):
    async def scenario(db, client):
        resources = [resource("external_person", "external"), resource("bank", "bank")]
        await grant(db, operations=["external.pay"], resources=resources)
        collection_class = type(db[PENDING_COMMANDS])
        original_insert = collection_class.insert_one
        async def revoke_during_database_insert(collection, *args, **kwargs):
            result = await original_insert(collection, *args, **kwargs)
            if collection.name == PENDING_COMMANDS and collection.database.name == db.name:
                await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access.operations": []}})
            return result
        monkeypatch.setattr(collection_class, "insert_one", revoke_during_database_insert)
        identity, packet = registration(actor="staff")
        response = await client.post(BASE + "/" + identity, json=packet, headers=STAFF)
        assert response.status_code == 403 and "command" not in response.json()
        assert "not_applied" not in response.json()["detail"]
        assert await db[PENDING_COMMANDS].count_documents({"_id": identity, "actor_id": "staff"}) == 1
        assert (await read(db, "owner"))["movements"] == []
        denied = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert denied.status_code == 403
        # Restoring permission recovers the original request, not a new intent.
        await grant(db, operations=["external.pay"], resources=resources, version=2)
        restored = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert restored.status_code == 200
        assert restored.json()["command"]["request_id"] == identity
    run(scenario)


def test_conflicting_concurrent_registration_has_one_immutable_winner(monkeypatch):
    async def scenario(db, client):
        collection_class = type(db[PENDING_COMMANDS])
        original_insert = collection_class.insert_one
        both_waiting = asyncio.Event()
        waiting = 0
        async def synchronized_insert(collection, *args, **kwargs):
            nonlocal waiting
            if collection.name == PENDING_COMMANDS and collection.database.name == db.name:
                waiting += 1
                if waiting == 2:
                    both_waiting.set()
                await asyncio.wait_for(both_waiting.wait(), timeout=5)
            return await original_insert(collection, *args, **kwargs)
        monkeypatch.setattr(collection_class, "insert_one", synchronized_insert)
        identity, first = registration(body=entry(amount="25.00"))
        second = deepcopy(first)
        second["command"]["amount"] = "75.00"
        replies = await asyncio.gather(client.post(BASE + "/" + identity, json=first),
                                       client.post(BASE + "/" + identity, json=second))
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        winner = next(reply.json() for reply in replies if reply.status_code == 200)
        assert (await client.get(BASE + "/" + identity, params=query())).json() == winner
        assert await db[PENDING_COMMANDS].count_documents({}) == 1
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


@pytest.mark.parametrize("change", ["forged_actor", "wrong_scope", "hidden_bank", "wrong_target", "unknown_action"])
def test_invalid_or_forged_registration_never_creates_a_record(change):
    async def scenario(db, client):
        identity, packet = registration()
        if change == "forged_actor":
            packet["command"]["recorded_by"] = {"id": "staff", "name": "مزور"}
        elif change == "wrong_scope":
            packet["command"]["expected_session_scope"] = digest(["other", "owner"])
        elif change == "hidden_bank":
            packet = {"action": "refund.create", "command": refund_command(method="original")}
        elif change == "wrong_target":
            packet["target_id"] = "a" * 64
        else:
            packet["action"] = "arbitrary.execute"
        response = await client.post(BASE + "/" + identity, json=packet)
        assert response.status_code in {409, 422}, response.text
        assert await db[PENDING_COMMANDS].count_documents({}) == 0
        assert (await read(db, "owner"))["movements"] == []
    run(scenario)


def test_missing_pointer_does_not_disclose_or_invent_any_payload():
    async def scenario(db, client):
        identity = str(uuid4())
        response = await client.get(BASE + "/" + identity, params=query())
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "operational_pending_command_not_found"
        assert "command" not in response.json()
        assert (await client.get(BASE + "/not-random", params=query())).status_code == 422
        assert await db[PENDING_COMMANDS].count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize("method", ["bank", "original"])
def test_refund_request_recovery_preserves_reservation_once_and_requires_original_source(method):
    async def scenario(db, client):
        await order_fixture(db)
        source = resource("bank", "bank") if method == "bank" else resource("provider", "tamara")
        await grant(db, operations=["customer.refund_request"], resources=[source])
        body = refund_command(method=method, bank_id="bank" if method == "bank" else None)
        identity, packet = registration(action="refund.create", body=body, actor="staff")
        before = await read(db, "owner")
        saved = await register(client, identity, packet, headers=STAFF)
        assert await read(db, "owner") == before
        first = await client.post(WORKSPACE + "/refunds", json=saved.json()["command"], headers=STAFF)
        assert first.status_code == 200, first.text
        recovered = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        again = await client.post(WORKSPACE + "/refunds", json=recovered.json()["command"], headers=STAFF)
        assert again.json() == first.json()
        state = await read(db, "owner")
        assert len(state["customer_returns"]) == 1 and state["movements"] == []
        assert balance(state, "bank", "bank")["actual"] == "1000.00"
        source["use"] = False
        await grant(db, operations=["customer.refund_request"], resources=[source], version=2)
        assert (await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)).status_code == 403
    run(scenario)


@pytest.mark.parametrize("action", ["refund.confirm", "refund.cancel", "refund.reconcile"])
def test_refund_followup_recovery_uses_case_source_and_has_no_financial_effect(action):
    async def scenario(db, client):
        await order_fixture(db)
        method = "original" if action == "refund.reconcile" else "bank"
        case = await created(client, method=method, bank_id=None if method == "original" else "bank")
        body = confirm() if action == "refund.confirm" else (
            {"reason": "إلغاء الطلب المحفوظ"} if action == "refund.cancel" else
            {"evidence_id": "refund:tamara:source-proof", "expected_evidence_hash": "a" * 64})
        identity, packet = registration(action=action, target_id=case["id"], body=body)
        before = await read(db, "owner")
        saved = await register(client, identity, packet)
        assert saved.json()["target_id"] == case["id"]
        assert (await client.get(BASE + "/" + identity, params=query())).json() == saved.json()
        assert await read(db, "owner") == before
    run(scenario)


def test_provider_request_cannot_register_a_fake_local_confirmation():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client, method="original", bank_id=None)
        identity, packet = registration(action="refund.confirm", target_id=case["id"], body=confirm())
        response = await client.post(BASE + "/" + identity, json=packet)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "operational_refund_provider_execution_pending"
        assert await db[PENDING_COMMANDS].count_documents({}) == 0
        assert (await read(db, "owner"))["customer_returns"][0]["status"] == "pending"
    run(scenario)


def test_case_visibility_is_rechecked_when_staff_all_movements_becomes_own():
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client)
        access = resource("bank", "bank", movements="all")
        await grant(db, operations=["customer.refund_execute"], resources=[access])
        identity, packet = registration(action="refund.confirm", target_id=case["id"], body=confirm(), actor="staff")
        await register(client, identity, packet, headers=STAFF)
        access["movements"] = "own"
        await grant(db, operations=["customer.refund_execute"], resources=[access], version=2)
        response = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert response.status_code == 404
        assert "command" not in response.json()
    run(scenario)


def test_visibility_change_during_database_read_cannot_return_an_old_all_scope(monkeypatch):
    async def scenario(db, client):
        await order_fixture(db)
        case = await created(client)
        access = resource("bank", "bank", movements="all")
        await grant(db, operations=["customer.refund_execute"], resources=[access])
        identity, packet = registration(action="refund.confirm", target_id=case["id"], body=confirm(), actor="staff")
        await register(client, identity, packet, headers=STAFF)
        collection_class = type(db[PENDING_COMMANDS])
        original_find = collection_class.find_one
        reads = 0
        async def restrict_visibility_after_read(collection, *args, **kwargs):
            nonlocal reads
            result = await original_find(collection, *args, **kwargs)
            if collection.name == PENDING_COMMANDS and collection.database.name == db.name:
                reads += 1
                if reads == 2:
                    access["movements"] = "own"
                    await grant(db, operations=["customer.refund_execute"], resources=[access], version=2)
            return result
        monkeypatch.setattr(collection_class, "find_one", restrict_visibility_after_read)
        response = await client.get(BASE + "/" + identity, params=query("staff"), headers=STAFF)
        assert reads == 2
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "operational_access_changed"
        assert "command" not in response.json()
    run(scenario)
