"""One-shot HTTP authority/error boundary; isolated storage, never live services."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod import routes
from integrations.qoyod import one_shot_reprocess as one_shot


OWNER = {"id": "owner-a", "role": "owner"}
BODY = {"order_number": "123", "confirm": "REPROCESS-123", "trace_id": "trace-a"}
PATH = "/api/integrations/qoyod/admin/one-shot-reprocess"
PRIVATE = "PRIVATE_ONE_SHOT /srv/internal.py mongodb://synthetic:only@invalid provider-body"


class ReadOnlyCollection:
    def __init__(self, rows):
        self.rows = rows
        self.reads = []

    async def find_one(self, query, projection=None):
        self.reads.append((query, projection))
        return next((deepcopy(row) for row in self.rows
                     if all(row.get(key) == value for key, value in query.items())), None)

    def __getattr__(self, name):
        raise AssertionError("Authorization must not mutate storage")


class AuthorityDB:
    def __init__(self, actor=None, binding="owner-a"):
        self.users = ReadOnlyCollection([actor or OWNER])
        self.qoyod_settings = ReadOnlyCollection([
            {"user_id": "main", **({"security_owner_id": binding} if binding is not None else {})}
        ])

    def __getattr__(self, name):
        raise AssertionError("Denied request reached operational storage")


async def request(db, context=OWNER, body=None):
    async def current_user():
        return context
    app = FastAPI()
    app.include_router(routes.make_qoyod_router(db, current_user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(PATH, json=BODY if body is None else body)


@pytest.fixture(autouse=True)
def forbid_live_transports(monkeypatch):
    import httpx
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("Live provider/network access is forbidden")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,binding,context", [
    (OWNER, None, OWNER),
    (OWNER, "owner-b", OWNER),
    (OWNER, ["owner-a"], OWNER),
    ({"id": "owner-b", "role": "owner"}, "owner-a", {"id": "owner-b"}),
    ({"id": "employee-a", "role": "employee", "created_by": "owner-a"},
     "owner-a", {"id": "employee-a", "role": "owner"}),
    ({"id": "admin-a", "role": "admin"}, "owner-a", {"id": "admin-a"}),
    ({"id": "employee-a", "role": "operations", "created_by": "owner-a"},
     "owner-a", {"id": "owner-a", "_mobile_actor_id": "employee-a"}),
    ({"id": "owner-a", "role": "owner", "disabled": True}, "owner-a", OWNER),
    ({"id": "owner-a", "role": "owner", "is_active": False}, "owner-a", OWNER),
])
async def test_one_shot_denied_before_any_work(monkeypatch, actor, binding, context):
    db = AuthorityDB(actor, binding)
    helper = AsyncMock(return_value={"ok": True, "outcome": "COMPLETED"})
    monkeypatch.setattr(routes, "reprocess_one_order", helper)
    response = await request(db, context)
    assert response.status_code == 403
    helper.assert_not_awaited()
    assert db.users.reads[0][0] == {"id": actor["id"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["actor", "settings"])
async def test_one_shot_absent_authority_record_is_denied(monkeypatch, missing):
    db = AuthorityDB()
    (db.users if missing == "actor" else db.qoyod_settings).rows.clear()
    helper = AsyncMock()
    monkeypatch.setattr(routes, "reprocess_one_order", helper)
    response = await request(db)
    assert response.status_code == 403
    helper.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_shot_matching_owner_preserves_authorized_arguments_and_outcome(monkeypatch):
    db = AuthorityDB()
    helper = AsyncMock(return_value={
        "ok": True, "outcome": "COMPLETED", "row_id": "row-a", "trace_id": "trace-a",
        "qoyod_invoice_id": "inv-1", "qoyod_invoice_payment_id": "payment-1",
        "stage_sequence_observed": ["NORMALIZED", "INVOICE_CREATED", "COMPLETED"],
        "per_order_approval": {"approval_id": "approval-1", "scope": "single_order",
                               "global_lock_was_active": True},
    })
    monkeypatch.setattr(routes, "reprocess_one_order", helper)
    body = {**BODY, "approval_phrase": "Approved to send order 123 only"}
    response = await request(db, body=body)
    assert response.status_code == 200
    assert response.json() == helper.return_value
    helper.assert_awaited_once_with(
        db, user_id="main", order_number="123", trace_id="trace-a",
        confirm="REPROCESS-123", approval_phrase=body["approval_phrase"], actor="main")
    assert len(db.users.reads) == len(db.qoyod_settings.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [
    RuntimeError(PRIVATE),
    HTTPException(502, {"exception_type": "InternalDriverFailure", "traceback_tail": PRIVATE}),
])
async def test_one_shot_exception_response_is_stable(monkeypatch, exception):
    helper = AsyncMock(side_effect=exception)
    monkeypatch.setattr(routes, "reprocess_one_order", helper)
    response = await request(AuthorityDB())
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "one_shot_unhandled_exception"}}
    for private in (PRIVATE, "RuntimeError", "HTTPException", "InternalDriverFailure", "traceback"):
        assert private not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    ("invalid_transition_to_retrying", "invalid_transition_to_retrying"),
    ("approval_phrase_required", "approval_phrase_required"),
    ("approval_phrase_mismatch", "approval_phrase_mismatch"),
    ("selective_send_policy_blocked", "selective_send_policy_blocked"),
    (PRIVATE, "operation_failed"),
])
async def test_one_shot_refusal_drops_internal_message_and_extra(monkeypatch, code, expected):
    helper = AsyncMock(side_effect=one_shot.OneShotRefused(
        code, PRIVATE, traceback_tail=PRIVATE, provider_response=PRIVATE))
    monkeypatch.setattr(routes, "reprocess_one_order", helper)
    response = await request(AuthorityDB())
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == expected
    assert response.json()["detail"]["expected_confirm_token"] == "REPROCESS-123"
    assert PRIVATE not in response.text
    assert "traceback" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["COMPLETED", "ALREADY_COMPLETED", "DEAD_LETTER",
                                      "INVOICE_CREATED", "PAYMENT_LINK_FAILED"])
async def test_one_shot_returned_diagnostics_cannot_bypass_public_boundary(monkeypatch, outcome):
    result = {
        "ok": outcome in {"COMPLETED", "ALREADY_COMPLETED"}, "outcome": outcome,
        "row_id": "row-a", "trace_id": "trace-a", "qoyod_invoice_id": "inv-1",
        "payment_post_attempted": True, "request_sent_to_qoyod": True,
        "stage_sequence_observed": ["NORMALIZED", "INVOICE_CREATED"],
        "error": {"code": PRIVATE, "message": PRIVATE, "qoyod_response_excerpt": PRIVATE},
        "totals_guard": {"code": "order_total_mismatch", "message": PRIVATE, "details": {"raw": PRIVATE}},
        "product_create": {"response_excerpt": PRIVATE, "request_body": {"raw": PRIVATE}},
        "invoice_payload": {"raw": PRIVATE}, "invoice_payment_response": {"error": PRIVATE},
        "invoice_response_body": {"error": PRIVATE}, "qoyod_response": PRIVATE,
        "request_body_json": {"raw": PRIVATE}, "stage_history": [{"note": PRIVATE}],
        "traceback_tail": PRIVATE, "message": PRIVATE, "debug": {"raw": PRIVATE},
    }
    monkeypatch.setattr(routes, "reprocess_one_order", AsyncMock(return_value=result))
    response = await request(AuthorityDB())
    assert response.status_code == 200
    public = response.json()
    assert public["ok"] == result["ok"]
    assert public["outcome"] == outcome
    assert public["qoyod_invoice_id"] == "inv-1"
    assert public["payment_post_attempted"] is True
    assert public["request_sent_to_qoyod"] is True
    assert PRIVATE not in response.text
    assert "traceback" not in response.text
    assert result["error"]["message"] == PRIVATE  # Projection cannot change stored diagnostics.


@pytest.mark.asyncio
async def test_one_shot_stage_and_total_projection_rejects_unstructured_internal_values(monkeypatch):
    monkeypatch.setattr(routes, "reprocess_one_order", AsyncMock(return_value={
        "ok": False, "outcome": PRIVATE, "failed_at_stage": {"raw": PRIVATE},
        "stage_sequence_observed": ["RECEIPT_CREATED", PRIVATE, {"raw": PRIVATE}, "COMPLETED"],
        "totals_comparison": {"salla_total": 115, "qoyod_actual_total": 114,
                              "difference": 1, "mismatch": True, "debug": PRIVATE,
                              "dry_run_expected_total": PRIVATE},
        "error": {"code": "order_total_mismatch", "message": PRIVATE},
    }))
    response = await request(AuthorityDB())
    assert response.status_code == 200
    public = response.json()
    assert public["outcome"] == public["failed_at_stage"] == "UNKNOWN"
    assert public["stage_sequence_observed"] == ["RECEIPT_CREATED", "COMPLETED"]
    assert public["totals_comparison"] == {
        "salla_total": 115, "qoyod_actual_total": 114, "difference": 1, "mismatch": True}
    assert public["error"] == {"code": "order_total_mismatch"}
    assert PRIVATE not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("bad_confirm", "confirm_token_mismatch"),
    ("no_row", "row_not_found"),
    ("dry_mode", "dry_run_mode_active"),
    ("no_credentials", "credentials_missing"),
    ("completed", "ALREADY_COMPLETED"),
])
async def test_one_shot_real_helper_retains_preconditions_and_idempotent_noop(monkeypatch, case, expected):
    db = AsyncMongoMockClient()["one_shot_isolated"]
    await db.users.insert_one(OWNER.copy())
    await db.qoyod_settings.insert_one({
        "user_id": "main", "security_owner_id": "owner-a", "enabled": True,
        "dry_run_mode": case == "dry_mode", "production_writes_locked": True,
    })
    if case != "no_row":
        await db.integration_inbox.insert_one({
            "id": "row-a", "user_id": "main", "trace_id": "trace-a", "salla_order_number": "123",
            "pipeline_stage": "COMPLETED" if case == "completed" else "DEAD_LETTER",
            "qoyod_invoice_id": "inv-1" if case == "completed" else None,
            "qoyod_invoice_payment_id": "payment-1" if case == "completed" else None,
            "qoyod_payloads": {"invoice": {"raw": PRIVATE}},
            "qoyod_responses": {"invoice_payment": {"body": {"raw": PRIVATE}}},
        })
    async def snapshot():
        return {name: await db[name].find().to_list(None)
                for name in await db.list_collection_names()}
    before = await snapshot()
    monkeypatch.setattr(one_shot, "get_api_key", AsyncMock(return_value=None))
    forbidden = AsyncMock(side_effect=AssertionError("Preconditions must prevent pipeline work"))
    monkeypatch.setattr(one_shot, "process_normalized_row", forbidden)
    monkeypatch.setattr(one_shot, "process_customer_resolved_row", forbidden)
    body = {**BODY, "confirm": "wrong"} if case == "bad_confirm" else BODY
    response = await request(db, body=body)
    assert response.status_code == (200 if case == "completed" else 400)
    result = response.json()
    assert (result["outcome"] if case == "completed" else result["detail"]["code"]) == expected
    assert PRIVATE not in response.text
    assert await snapshot() == before
    forbidden.assert_not_awaited()
