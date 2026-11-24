"""Exact saved report bindings share work without broadening any read scope."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
import pytest

import demand_read_jobs as jobs
import demand_read_store as store
from operational_balance_store import STATES, initial
from operational_workspace_read_builders import authorize_spec, build_projection, make_spec
from test_operational_balance_demand_metadata_reads import read_db, read_mongo_uri
from test_operational_workspace_demand_http import BASE, HEADERS, application, seed_workspace


async def source(db):
    value = await seed_workspace(db, movements=17)
    value["started_at"] = "2030-04-01T00:00:00+00:00"
    value["engine"]["as_of"] = "2030-04-06T00:00:00+00:00"
    for opening in value["openings"].values():
        opening["created_at"] = value["started_at"]
    for movement in value["movements"]:
        movement.update(business_date="2030-04-05", occurred_at="2030-04-05T10:00:00+00:00")
    await db[STATES].replace_one({"_id": "owner"}, value)
    return value


def anchors(state):
    return {"expected_revision": state["revision"], "expected_as_of": state["engine"]["as_of"],
            "expected_started_at": state["started_at"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("view", ["summary", "providers", "couriers"])
async def test_unbound_and_exact_bound_http_views_share_one_actual_job_and_identical_money(read_db, view):
    before = await source(read_db)
    calls = []
    async def measured(db, spec, checkpoint):
        calls.append(deepcopy(spec))
        return await build_projection(db, spec, checkpoint)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        first = await client.get(BASE + "/report", params={"view": view}, headers=HEADERS)
        assert first.status_code == 202, first.text
        second = await client.get(BASE + "/report", params={"view": view, **anchors(before)},
                                  headers={"X-Mezan-Read-Consumer": "second-bound-consumer"})
        assert second.status_code == 202 and second.json()["key"] == first.json()["key"], second.text
        key = first.json()["key"]
        assert await read_db[store.JOBS].count_documents({"family": "operational_report"}) == 1
        claim = await read_db[store.JOBS].find_one({"_id": key})
        assert claim["spec"]["filters"] == {"view": view, **anchors(before)}
        assert claim["spec"]["request"]["query"] == claim["spec"]["filters"]
        # The old view's cleanup cannot revoke the second visible consumer.
        released = await client.delete(BASE + "/read-demand/" + key,
            params={"consumer_id": HEADERS["X-Mezan-Read-Consumer"]})
        assert released.status_code == 200
        assert await jobs.execute_once(read_db, builder=measured, authorize=authorize_spec)
        ready = await client.get(BASE + "/report", params={"view": view})
        bound = await client.get(BASE + "/report", params={"view": view, **anchors(before)})
        assert ready.status_code == bound.status_code == 200, (ready.text, bound.text)
        assert ready.json() == bound.json()
        canonical = await client.get("/api/operational-balances/financial-position",
                                     params={"view": view, **anchors(before)})
        assert canonical.status_code == 200, canonical.text
        for field, expected in canonical.json().items():
            if field != "generated_at":
                assert ready.json()[field] == expected, field
    saved = await read_db[store.JOBS].find_one({"_id": key})
    assert len(calls) == 1 and saved["attempts"] == 1 and saved["status"] == "ready"
    assert await read_db[store.JOBS].count_documents({"family": "operational_report"}) == 1
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_partial_stale_and_foreign_anchors_reject_before_job_admission_and_forecast_stays_explicit(read_db):
    before = await source(read_db)
    valid = anchors(before)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        pending = await client.get(BASE + "/report", params={"view": "providers"}, headers=HEADERS)
        assert pending.status_code == 202
        job_before = await read_db[store.JOBS].find_one({"_id": pending.json()["key"]})
        bad = [({**valid, "expected_revision": before["revision"] + 1}, 409),
               ({**valid, "expected_as_of": "2030-04-07T00:00:00+00:00"}, 409),
               ({**valid, "expected_started_at": "2030-03-01T00:00:00+00:00"}, 409),
               ({"expected_revision": 9, "expected_started_at": "2030-02-01T00:00:00+00:00",
                 "expected_as_of": "2030-04-02T00:00:00+00:00"}, 409)]
        bad += [({key: value for key, value in valid.items() if key != missing}, 422) for missing in valid]
        for values, code in bad:
            response = await client.get(BASE + "/report", params={"view": "providers", **values}, headers=HEADERS)
            assert response.status_code == code, response.text
            assert await read_db[store.JOBS].find_one({"_id": pending.json()["key"]}) == job_before
            assert await read_db[store.JOBS].count_documents({"family": "operational_report"}) == 1
        for values in ({}, {"expected_revision": before["revision"]}):
            response = await client.get(BASE + "/report", params={"view": "forecast", **values}, headers=HEADERS)
            assert response.status_code == 422, response.text
        assert await read_db[store.JOBS].count_documents({"family": "operational_report"}) == 1
        accepted = await client.get(BASE + "/report", params={"view": "forecast", **valid}, headers=HEADERS)
        assert accepted.status_code == 202 and accepted.json()["key"] != pending.json()["key"]
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_distinct_views_owners_permissions_and_source_changes_cannot_reuse_ready_content(read_db):
    current = await source(read_db)
    other = initial("other")
    other.update(status="active", revision=current["revision"], started_at=current["started_at"])
    other["engine"]["as_of"] = current["engine"]["as_of"]
    await read_db[STATES].insert_one(other)
    keys = set()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        first = await client.get(BASE + "/report", params={"view": "providers"}, headers=HEADERS)
        assert first.status_code == 202
        keys.add(first.json()["key"])
        assert await jobs.execute_once(read_db, builder=build_projection, authorize=authorize_spec)
        assert (await client.get(BASE + "/report", params={"view": "providers"})).status_code == 200
        for values, headers in [({"view": "accounts", **anchors(current)}, HEADERS),
                ({"view": "providers", **anchors(current)}, {**HEADERS, "x-test-user": "other"})]:
            pending = await client.get(BASE + "/report", params=values, headers=headers)
            assert pending.status_code == 202 and pending.json()["key"] not in keys
            keys.add(pending.json()["key"])
        await read_db.users.update_one({"id": "owner"}, {"$set": {"operational_balance_access": {
            "owner_id": "owner", "enabled": True, "version": 1, "operations": [], "resources": []}}})
        permission = await client.get(BASE + "/report", params={"view": "providers", **anchors(current)}, headers=HEADERS)
        assert permission.status_code == 202 and permission.json()["key"] not in keys
        previous_spec = (await read_db[store.JOBS].find_one({"_id": first.json()["key"]}))["spec"]
        current_spec = (await read_db[store.JOBS].find_one({"_id": permission.json()["key"]}))["spec"]
        assert previous_spec["permission_fingerprint"] != current_spec["permission_fingerprint"]
        keys.add(permission.json()["key"])
        for field, value in [("revision", current["revision"] + 1),
                             ("engine.as_of", "2030-04-07T00:00:00+00:00"),
                             ("started_at", "2030-04-02T00:00:00+00:00"), ("status", "draft")]:
            old = anchors(current)
            if field == "engine.as_of":
                current["engine"]["as_of"] = value
            else:
                current[field] = value
            await read_db[STATES].replace_one({"_id": "owner"}, current)
            changed = await client.get(BASE + "/report", params={"view": "providers"}, headers=HEADERS)
            assert changed.status_code == 202 and changed.json()["key"] not in keys, changed.text
            keys.add(changed.json()["key"])
            if field != "status":
                stale = await client.get(BASE + "/report", params={"view": "providers", **old}, headers=HEADERS)
                assert stale.status_code == 409
        count = await read_db[store.JOBS].count_documents({"family": "operational_report"})
        staff = await client.get(BASE + "/report", params={"view": "providers"}, headers={**HEADERS, "x-test-user": "staff"})
        assert staff.status_code == 403
        await read_db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        revoked = await client.get(BASE + "/report", params={"view": "providers", **anchors(current)}, headers=HEADERS)
        assert revoked.status_code == 403
        assert await read_db[store.JOBS].count_documents({"family": "operational_report"}) == count
    assert await read_db[STATES].find_one({"_id": "owner"}) == current
    assert await read_db[STATES].find_one({"_id": "other"}) == other


@pytest.mark.asyncio
@pytest.mark.parametrize("status,started_at,as_of", [("draft", None, None),
                                                  ("active", "2030-04-01T00:00:00+00:00", None)])
async def test_incomplete_source_anchors_remain_unbound_without_invented_values(read_db, status, started_at, as_of):
    before = await source(read_db)
    before.update(status=status, started_at=started_at)
    before["engine"]["as_of"] = as_of
    await read_db[STATES].replace_one({"_id": "owner"}, before)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(read_db)), base_url="http://isolated") as client:
        response = await client.get(BASE + "/report", params={"view": "summary"}, headers=HEADERS)
        assert response.status_code == 202, response.text
        spec = (await read_db[store.JOBS].find_one({"_id": response.json()["key"]}))["spec"]
    assert spec["filters"] == {"view": "summary", "expected_revision": None,
                               "expected_as_of": None, "expected_started_at": None}
    assert spec["request"]["query"] == spec["filters"]
    assert spec["source_revision"] == {"status": status, "revision": before["revision"],
                                       "as_of": as_of, "started_at": started_at}
    assert await read_db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
async def test_all_report_views_keep_exact_scope_and_do_not_mutate_callers_filters(read_db):
    before = await source(read_db)
    principal = {"id": "owner", "_session_client": "web"}
    request = SimpleNamespace(url=SimpleNamespace(path=BASE + "/report"), method="GET", query_params={})
    views = ["all", "summary", "providers", "couriers", "drivers", "employees", "accounts", "suppliers",
             "advertising", "other", "expected"]
    seen = set()
    for view in views:
        filters = {"view": view, "expected_revision": None, "expected_as_of": None, "expected_started_at": None}
        unchanged = deepcopy(filters)
        first = await make_spec(read_db, principal, request, "operational_report", filters=filters)
        bound = await make_spec(read_db, principal, request, "operational_report", filters={"view": view, **anchors(before)})
        assert filters == unchanged and first == bound
        assert first["filters"] == {"view": view, **anchors(before)}
        assert first["request"] == {"path": BASE + "/report", "method": "GET", "query": first["filters"]}
        assert first["owner_id"] == first["actor_id"] == "owner" and first["client_kind"] == "web"
        assert first["resource"] is None and first["currency"] is None
        assert first["period"] == {"date_from": None, "date_to": None}
        key = store.projection_key(first)
        assert key not in seen
        seen.add(key)
    forecast = {"view": "forecast", **anchors(before)}
    await make_spec(read_db, principal, request, "operational_report", filters=forecast)
    with pytest.raises(HTTPException) as missing:
        await make_spec(read_db, principal, request, "operational_report", filters={"view": "forecast"})
    assert missing.value.status_code == 422
    web = await make_spec(read_db, principal, request, "operational_report", filters={"view": "providers"})
    mobile = await make_spec(read_db, {"id": "owner", "_session_client": "amasi_mobile"}, request,
                             "operational_report", filters={"view": "providers"})
    assert store.projection_key(web) != store.projection_key(mobile)
    assert await read_db[store.JOBS].count_documents({}) == 0
    assert await read_db[STATES].find_one({"_id": "owner"}) == before
