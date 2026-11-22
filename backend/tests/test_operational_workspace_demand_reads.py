"""Demand-only workspace reads against the public route and canonical reducers."""
from copy import deepcopy
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from operational_web_page_policy import bind_page_request
from operational_workspace_read_builders import make_spec, build_projection, authorize_spec

from test_operational_balance_workspace import BASE, entry, grant, resource, run


async def project(db, family, path, *, filters=None, target=None, actor="owner", mobile=False):
    principal = {"id": actor, "_session_client": "amasi_mobile" if mobile else "web"}
    request = SimpleNamespace(url=SimpleNamespace(path=BASE + path), method="GET", query_params=filters or {})
    with bind_page_request(principal, request):
        spec = await make_spec(db, principal, request, family, filters=filters, resource=target)
    async def checkpoint():
        return None
    return spec, await build_projection(db, spec, checkpoint)


def test_bootstrap_renders_identity_without_financial_or_catalog_work(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap must not acquire finance or entity catalogs")
    monkeypatch.setattr("operational_balance_workspace.resource_catalog", forbidden)
    monkeypatch.setattr("operational_financial_position_state.read_financial_position_state", forbidden)
    async def scenario(db, client):
        result = await client.get(BASE + "/bootstrap")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["actor"] == {"id": "owner", "name": "محمد عبدالله", "is_owner": True}
        assert body["resources"] == []
        assert body["status"] == "active" and body["ledger_start"] == "2026-11-01"
        assert body["capabilities"]["create_entries"] is True
        assert "recent_movements" not in body
    run(scenario)


def test_bootstrap_uses_fresh_permissions_without_leaking_resource_grants():
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external")])
        headers = {"x-test-user": "staff", "x-test-mobile": "yes"}
        response = await client.get(BASE + "/bootstrap", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["resources"] == []
        assert response.json()["capabilities"]["read_movements"] is True
        assert response.json()["capabilities"]["manage_access"] is False
        assert response.json()["operations"] == [{"key": "external.pay", "label": "تسليم مبلغ لجهة خارجية"}]
        await db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        assert (await client.get(BASE + "/bootstrap", headers=headers)).status_code == 403
    run(scenario)


@pytest.mark.parametrize("principal,expected_actor,budget", [
    ({"id": "owner"}, "owner", 2),
    ({"id": "staff"}, "staff", 4),
    ({"id": "owner", "_mobile_actor_id": "staff"}, "staff", 4),
])
def test_bootstrap_identity_read_budget_retains_both_fresh_authority_passes(principal, expected_actor, budget):
    from operational_workspace_read_builders import bootstrap
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external")])
        observed = []
        class Users:
            async def find_one(self, query, *args, **kwargs):
                observed.append(query)
                return await db.users.find_one(query, *args, **kwargs)
        class ObservedDatabase:
            def __getitem__(self, name):
                return Users() if name == "users" else db[name]
        body = await bootstrap(ObservedDatabase(), principal)
        assert body["actor"]["id"] == expected_actor
        assert body["resources"] == []
        assert len(observed) == budget
        assert sum(query == {"id": expected_actor} for query in observed) == 2
        assert body["owner_override"] is (expected_actor == "owner")
    run(scenario)


@pytest.mark.parametrize("identity,change,status", [
    ("staff", {"disabled": True}, 403),
    ("owner", {"disabled": True}, 403),
    ("staff", {"created_by": "other"}, 403),
    ("staff", {"operational_balance_access.operations": []}, 409),
    ("staff", {"web_page_access.revision": 2, "web_page_access.grants": []}, 409),
])
def test_bootstrap_rejects_authority_changes_during_header_read(monkeypatch, identity, change, status):
    import operational_workspace_read_builders as builders
    original = builders.snapshot_header
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external")])
        await db.users.update_one({"id": "staff"}, {"$set": {"web_page_access": {
            "owner_id": "owner", "revision": 1, "grants": [{"id": "balances.entry", "mode": "write"}]}}})
        async def changed_header(*args, **kwargs):
            value = await original(*args, **kwargs)
            await db.users.update_one({"id": identity}, {"$set": change})
            return value
        monkeypatch.setattr(builders, "snapshot_header", changed_header)
        response = await client.get(BASE + "/bootstrap", headers={"x-test-user": "staff"})
        assert response.status_code == status, response.text
        assert "resources" not in response.json()
    run(scenario)


def test_bootstrap_preserves_canonical_owner_browser_page_semantics():
    async def scenario(db, client):
        await db.users.update_one({"id": "owner"}, {"$set": {"web_page_access": {
            "owner_id": "owner", "revision": 1, "grants": []}}})
        response = await client.get(BASE + "/bootstrap")
        # Canonical page grants narrow team members, not the owning account.
        assert response.status_code == 200, response.text
        assert response.json()["owner_override"] is True
    run(scenario)


def test_home_returns_exact_totals_and_four_recent_rows_without_closed_directories(monkeypatch):
    import operational_balance_sources as sources
    original = sources.entities
    calls = []
    async def observed(*args, **kwargs):
        calls.append((args[2], kwargs.get("selected_ids")))
        return await original(*args, **kwargs)
    async def scenario(db, client):
        for index in range(6):
            response = await client.post(BASE + "/entries", json=entry(amount="10", business_date=f"2026-11-{index+2:02}"))
            assert response.status_code == 200
        canonical = (await client.get(BASE + "/context", params={"home_preview": True, "provider_positions": True,
                                                                 "ad_bank_fx": True})).json()
        monkeypatch.setattr(sources, "entities", observed)
        _, actual = await project(db, "operational_home", "/home")
        header = actual["header"]
        assert header["summary"]["bank"] == "940.00"
        assert header["summary"]["cash"] == "500.00"
        assert header["summary"]["total"] == "1440.00"
        assert header["recent_movements"] == canonical["recent_movements"]
        assert len(header["recent_movements"]) == 4 and len(header["resources"]) <= 3
        for kind, identities in calls:
            assert kind in {"bank", "cash", "provider"} or kind == "external_person" and identities == ["external"]
        assert actual["rows"] is None
    run(scenario)


def test_home_never_invents_a_zero_for_hidden_money():
    async def scenario(db, client):
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external"),
            resource("bank", "bank", movements="none", balance=False)])
        _, result = await project(db, "operational_home", "/home", actor="staff", mobile=True)
        assert result["header"]["summary"]["bank"] is None
        assert result["header"]["summary"]["total"] is None
        assert all(row["balance"] is None for row in result["header"]["resources"])
    run(scenario)


@pytest.mark.parametrize("filters", [
    {}, {"q": "المورد"}, {"date_from": "2026-11-03", "date_to": "2026-11-07"},
    {"q": "مرجع", "date_from": "2026-11-03", "date_to": "2026-11-07"},
])
def test_saved_ledger_keeps_exact_backdated_search_and_period_balances(filters):
    async def scenario(db, client):
        for day in (8, 3, 5, 2, 7, 4, 6):
            response = await client.post(BASE + "/entries", json=entry(party_type="supplier", party_id="supplier",
                reason="payment", amount="10", business_date=f"2026-11-{day:02}", reference=("مرجع" if day % 2 else "آخر") + str(day)))
            assert response.status_code == 200, response.text
        path = "/ledger/supplier/supplier"
        expected = (await client.get(BASE + path, params={**filters, "page_size": 8})).json()
        _, actual = await project(db, "operational_ledger", path, filters=filters, target=("supplier", "supplier"))
        assert actual["rows"] == expected["items"]
        for key in ("resource", "balance_status", "balance_note", "period", "expected_collections"):
            assert actual["header"].get(key) == expected.get(key)
    run(scenario)


@pytest.mark.parametrize("filters", [{}, {"q": "موظف"}, {"direction": "out"},
    {"date_from": "2026-11-09", "date_to": "2026-11-11", "status": "done"}])
def test_saved_register_filters_before_linked_event_deduplication(filters):
    async def scenario(db, client):
        payload = entry(side="for_party", reason="salary_settlement", employee_id="employee", amount="60",
                        source_type=None, source_id=None)
        assert (await client.post(BASE + "/entries", json=payload)).status_code == 200
        assert (await client.post(BASE + "/entries", json=entry(amount="40"))).status_code == 200
        expected = (await client.get(BASE + "/movements", params={**filters, "page_size": 8})).json()
        _, actual = await project(db, "operational_register", "/movements", filters=filters)
        assert actual["rows"] == expected["items"]
        assert len(actual["rows"]) == expected["total"]
        assert len({row["event_id"] for row in actual["rows"]}) == len(actual["rows"])
    run(scenario)


def test_directory_is_scoped_ordered_and_searched_on_server():
    async def scenario(db, client):
        await db.mezan_suppliers_v2.insert_many([
            {"id": f"supplier-{i:03}", "user_id": "owner", "name": f"مُورِّد أفق {i:03}", "status": "active"}
            for i in range(60)])
        _, result = await project(db, "operational_directory", "/directory",
            filters={"group": "parties", "kind": "supplier", "q": "مورد افق"})
        assert len(result["rows"]) == 60
        assert all(r["kind"] == "supplier" for r in result["rows"])
        assert [r["id"] for r in result["rows"]] == [f"supplier-{i:03}" for i in range(60)]
        with pytest.raises(HTTPException) as error:
            await project(db, "operational_directory", "/directory", filters={"group": "assets", "kind": "supplier"})
        assert error.value.status_code == 422
    run(scenario)


def test_recipient_options_load_no_financial_snapshot_and_no_unrelated_kinds(monkeypatch):
    import operational_balance_sources as sources
    original = sources.entities
    calls = []
    async def observed(*args, **kwargs):
        calls.append(args[2])
        return await original(*args, **kwargs)
    async def forbidden(*args, **kwargs):
        raise AssertionError("recipient picker must not read the financial snapshot")
    monkeypatch.setattr(sources, "entities", observed)
    monkeypatch.setattr("operational_financial_position_state.read_financial_position_state", forbidden)
    async def scenario(db, client):
        _, result = await project(db, "operational_options", "/options",
            filters={"operation": "out", "role": "recipient", "kind": "supplier"})
        assert [r["id"] for r in result["rows"]] == ["supplier"]
        assert calls == ["supplier"]
        assert result["rows"][0]["balance"] is None
    run(scenario)


def test_snapshot_and_permission_changes_invalidate_prepared_specs():
    from operational_balance_store import STATES
    async def scenario(db, client):
        spec, _ = await project(db, "operational_home", "/home")
        assert await authorize_spec(db, spec) is True
        await db[STATES].update_one({"_id": "owner"}, {"$inc": {"revision": 1}})
        with pytest.raises(HTTPException) as changed:
            await authorize_spec(db, spec)
        assert changed.value.status_code == 409
        await grant(db, operations=["external.pay"], resources=[resource("external_person", "external")])
        spec, _ = await project(db, "operational_home", "/home", actor="staff", mobile=True)
        # Simulate a malformed legacy edit that does not increment version.
        await db.users.update_one({"id": "staff"}, {"$set": {"operational_balance_access.resources": []}})
        with pytest.raises(HTTPException) as changed:
            await authorize_spec(db, spec)
        assert changed.value.status_code == 409
    run(scenario)


@pytest.mark.parametrize("mutation", [
    lambda spec: spec["request"].update(path=BASE + "/bootstrap"),
    lambda spec: spec["request"].update(query={"group": "assets"}),
    lambda spec: spec.update(family="operational_position"),
    lambda spec: spec.update(projection_version="unknown"),
    lambda spec: spec.update(resource=["supplier", "supplier"]),
    lambda spec: spec.update(filters={"group": "assets"}),
])
def test_worker_rejects_mismatched_page_and_builder_before_data_read(mutation, monkeypatch):
    async def scenario(db, client):
        spec, _ = await project(db, "operational_home", "/home")
        mutation(spec)
        async def forbidden(*args, **kwargs):
            raise AssertionError("invalid job must fail before acquiring the financial source")
        monkeypatch.setattr("operational_financial_position_state.read_financial_position_state", forbidden)
        with pytest.raises(ValueError, match="demand_read_request_invalid"):
            await authorize_spec(db, spec)
        async def checkpoint():
            pass
        with pytest.raises(ValueError, match="demand_read_request_invalid"):
            await build_projection(db, spec, checkpoint)
    run(scenario)


def test_history_uses_riyadh_month_and_invalidates_at_month_boundary(monkeypatch):
    from demand_read_store import projection_key
    async def scenario(db, client):
        monkeypatch.setattr("operational_workspace_read_builders.now", lambda: "2026-11-30T20:59:59+00:00")
        first, _ = await project(db, "operational_history", "/history/employee/employee",
                                 target=("employee", "employee"))
        assert first["calendar_month"] == "2026-11"
        monkeypatch.setattr("operational_workspace_read_builders.now", lambda: "2026-11-30T21:00:00+00:00")
        with pytest.raises(HTTPException) as changed:
            await authorize_spec(db, first)
        assert changed.value.status_code == 409
        second, result = await project(db, "operational_history", "/history/employee/employee",
                                      target=("employee", "employee"))
        assert second["calendar_month"] == "2026-12"
        assert result["header"]["current_month"] == "2026-12"
        assert projection_key(first) != projection_key(second)
    run(scenario)


def test_demand_report_has_bootstrap_scope_and_permission_binding():
    async def scenario(db, client):
        bootstrap = (await client.get(BASE + "/bootstrap")).json()
        _, result = await project(db, "operational_report", "/report", filters={"view": "summary"})
        assert result["header"]["view"] == "summary"
        for key in ("session_scope", "permission_version", "owner_override"):
            assert result["header"][key] == bootstrap[key]
    run(scenario)


@pytest.mark.parametrize("ad_bank_fx", [False, True])
def test_saved_resource_preserves_explicit_usd_ad_fx_flag(ad_bank_fx):
    from test_operational_balance_integration import seed_ad_account
    async def scenario(db, client):
        await seed_ad_account(db)
        await db.mezan_integration_accounts_v2.update_one(
            {"mezan_integration_account_id": "ia"}, {"$set": {"currency": "USD"}})
        path = "/resource/ad_account/ad"
        expected = await client.get(BASE + path, params={"ad_bank_fx": ad_bank_fx})
        assert expected.status_code == 200, expected.text
        _, result = await project(db, "operational_resource", path,
            filters={"ad_bank_fx": ad_bank_fx}, target=("ad_account", "ad"))
        for key, value in expected.json().items():
            assert result["header"][key] == value, key
        assert bool(result["header"]["operations"]) is ad_bank_fx
        assert ("ad_bank_funding" in result["header"]) is ad_bank_fx
    run(scenario)


@pytest.mark.parametrize("already_disconnected", [True, False])
def test_ready_statement_disconnect_stops_before_next_chunk(already_disconnected, monkeypatch):
    from operational_workspace_demand_routes import serve_projection
    async def scenario(db, client):
        offsets, cancelled = [], []
        second_started = asyncio.Event()
        async def saved(db, spec, *, offset, limit, snapshot=None):
            offsets.append(offset)
            if offset:
                second_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.append(offset)
                    raise
            return {"header": {}, "items": [{"id": str(i)} for i in range(300)],
                    "total": 601, "snapshot": "synthetic-generation", "generated_at": datetime.now(timezone.utc)}
        monkeypatch.setattr("demand_read_store.read_ready", saved)
        async def disconnected():
            return already_disconnected
        async def receive():
            await second_started.wait()
            return {"type": "http.disconnect"}
        request = SimpleNamespace(url=SimpleNamespace(path=BASE + "/statements/bank/bank"),
            headers={}, is_disconnected=disconnected, receive=receive)
        with pytest.raises(HTTPException) as stopped:
            await asyncio.wait_for(serve_projection(db, {"id": "owner"}, request, "operational_statement",
                resource=("bank", "bank"), filters={"date_from": "2026-11-01", "date_to": "2026-11-22", "purpose": "view"},
                page_size=300), timeout=3)
        assert stopped.value.status_code == 499
        assert offsets == ([] if already_disconnected else [0, 300])
        assert cancelled == ([] if already_disconnected else [300])
    run(scenario)


@pytest.mark.parametrize("failure", ["disconnect", "permission"])
def test_admitted_demand_releases_its_consumer_when_http_read_fails(failure, monkeypatch):
    from operational_workspace_demand_routes import serve_projection
    from demand_read_store import projection_key
    async def scenario(db, client):
        admitted, released = [], []
        waiting = asyncio.Event()
        async def missing(*args, **kwargs):
            return None
        async def demand(db, spec, consumer, **kwargs):
            admitted.append(consumer)
            if failure == "permission":
                await db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
            return {"status": "preparing", "key": projection_key(spec)}
        async def release(db, spec, consumer):
            released.append((spec["owner_id"], spec["actor_id"], consumer))
        async def wake():
            waiting.set()
            await asyncio.Event().wait()
        async def disconnected():
            return False
        async def receive():
            await waiting.wait()
            return {"type": "http.disconnect"}
        monkeypatch.setattr("demand_read_store.read_ready", missing)
        monkeypatch.setattr("demand_read_store.demand", demand)
        monkeypatch.setattr("demand_read_store.release", release)
        monkeypatch.setattr("background_worker_process.wake_read_projections", wake)
        request = SimpleNamespace(url=SimpleNamespace(path=BASE + "/home"),
            headers={"X-Mezan-Read-Consumer": "cancelled-http-consumer"},
            is_disconnected=disconnected, receive=receive)
        with pytest.raises(HTTPException) as stopped:
            await asyncio.wait_for(serve_projection(db, {"id": "owner"}, request, "operational_home"), timeout=3)
        assert stopped.value.status_code == (499 if failure == "disconnect" else 403)
        assert admitted == ["cancelled-http-consumer"]
        assert released == [("owner", "owner", "cancelled-http-consumer")]
    run(scenario)
