"""HTTP contract tests against the real router, with synthetic source/ledger ports."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mezan_special_orders.contracts import Actor
from mezan_special_orders.routes import make_special_orders_router
from mezan_special_orders.tests.test_core import Harness, OWNER, request_data


def client(h=None, actor=OWNER):
    h = h or Harness()
    app = FastAPI()
    app.include_router(make_special_orders_router(h.service, lambda: actor))
    return TestClient(app), h


def test_http_create_read_and_safe_replay():
    c, h = client()
    headers = {"Idempotency-Key": "http-create-001"}
    response = c.post("/special-orders-v1", json=request_data(), headers=headers)
    assert response.status_code == 201
    identity = response.json()["order_id"]
    again = c.post("/special-orders-v1", json=request_data(), headers=headers)
    assert again.status_code == 201 and again.json()["order_id"] == identity
    assert c.get(f"/special-orders-v1/{identity}").status_code == 200
    assert len(h.store.documents) == 1


@pytest.mark.parametrize("field,value", [("tenant_id", "attacker"), ("paid_amount", 5000), ("counts_as_sale", True), ("stage", "delivered"), ("salla_order_id", "fabricated")])
def test_http_cannot_inject_server_owned_fields(field, value):
    c, h = client()
    data = request_data()
    data[field] = value
    response = c.post("/special-orders-v1", json=data, headers={"Idempotency-Key": "http-forged-create"})
    assert response.status_code == 422 and not h.store.documents


def test_http_auth_required_and_no_plain_session_dict_accepted():
    c, h = client(actor={"tenant_id": "test-store", "role": "owner"})
    response = c.post("/special-orders-v1", json=request_data(), headers={"Idempotency-Key": "http-auth-create"})
    assert response.status_code == 403 and not h.store.documents


def test_http_permission_scope_precedes_source_access():
    c, h = client(actor=Actor(tenant_id="test-store", actor_id="employee"))
    r = c.post("/special-orders-v1", json=request_data(), headers={"Idempotency-Key": "http-readonly-create"})
    assert r.status_code == 403 and h.ports.original_calls == 0


def test_http_mongo_query_object_cannot_be_used_as_movement_id():
    c, h = client()
    o = c.post("/special-orders-v1", json=request_data(), headers={"Idempotency-Key": "http-injection-create"}).json()
    result = c.post(f"/special-orders-v1/{o['order_id']}/commands", headers={"Idempotency-Key": "http-injection-command"},
        json={"expected_revision": 1, "operation": "observe_payment", "payload": {"movement_id": {"$ne": None}}})
    assert result.status_code == 422 and result.json()["detail"]["code"] == "invalid_special_order_payload"


def test_dependency_errors_are_sanitized():
    c, h = client()
    async def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE-CUSTOMER-BANK-DATA")
    h.ports.fx = broken
    r = c.post("/special-orders-v1", json=request_data(), headers={"Idempotency-Key": "http-error-create"})
    assert r.status_code == 503
    assert "PRIVATE" not in r.text and "Traceback" not in r.text


def test_http_revision_and_missing_idempotency_key():
    c, h = client()
    assert c.post("/special-orders-v1", json=request_data()).status_code == 422
    o = c.post("/special-orders-v1", json=request_data(), headers={"Idempotency-Key": "http-revision-create"}).json()
    r = c.post(f"/special-orders-v1/{o['order_id']}/commands", headers={"Idempotency-Key": "http-revision-command"},
        json={"expected_revision": 10, "operation": "freeze_source", "payload": {}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "revision_conflict"


@pytest.mark.parametrize("query", ["?before_date=2026-09-27", "?before_id=bad", "?before_date=bad&before_id=bad", "?limit=101", "?limit=0"])
def test_invalid_pagination_rejected(query):
    c, h = client()
    assert c.get("/special-orders-v1" + query).status_code == 422
