"""Direct physical handoff in assembly, without making a piece Ready by itself."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
import preparation_piece_operations as ops


RECEIPT = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
PIECE = {
    "piece_id": "ab" * 16,
    "order_number": "300000001",
    "status": ops.PIECE_STATUS_RECEIVED,
    "execution_status": "received_from_supplier",
    "supplier_dispatch_status": "received",
    "received_at": RECEIPT,
    "services": [],
    "responsible_employee_id": "prep-1",
    "responsible_employee_name": "محمد",
}


def test_search_offer_requires_actual_supplier_completion_not_just_a_status():
    assert ops.assembly_piece_blocker(PIECE) == "assembly_piece_preparation_receipt_required"
    assert ops.preparation_receipt_blocker(PIECE) is None
    assert ops.assembly_direct_preparation_receipt_eligible(PIECE)
    assert not ops.assembly_direct_preparation_receipt_eligible({
        **PIECE, "supplier_dispatch_status": "sent",
    })
    assert not ops.assembly_direct_preparation_receipt_eligible({
        **PIECE, "received_at": None,
    })
    assert not ops.assembly_direct_preparation_receipt_eligible({
        **PIECE, "execution_status": "supplier_receiving_draft",
    })


def test_duplicate_blocked_canceled_virtual_and_unfinished_never_offer_direct_receipt():
    variants = [
        {**PIECE, "status": ops.PIECE_STATUS_READY_FOR_ASSEMBLY, "preparation_receipt_status": "received"},
        {**PIECE, "preparation_receipt_status": "received"},
        {**PIECE, "status": ops.PIECE_STATUS_CANCELLED},
        {**PIECE, "status": ops.PIECE_STATUS_BLOCKED},
        {**PIECE, "active_hold_id": "stop-1"},
        {**PIECE, "virtual_kind": "operational"},
        {**PIECE, "experiment_archived_at": RECEIPT},
        {**PIECE, "services": [{"service_id": "eng", "status": "pending", "required_quantity": 1}]},
    ]
    for piece in variants:
        assert not ops.assembly_direct_preparation_receipt_eligible(piece)


def test_receipt_does_not_make_a_piece_ready_in_assembly():
    assert ops.assembly_piece_blocker(PIECE) == "assembly_piece_preparation_receipt_required"
    received = {
        **PIECE,
        "status": ops.PIECE_STATUS_READY_FOR_ASSEMBLY,
        "preparation_receipt_status": "received",
        "preparation_received_at": RECEIPT,
    }
    assert ops.assembly_piece_blocker(received) is None
    assert received.get("assembly_status") != "ready"
    assert "assembly_ready_at" not in received


@pytest.mark.asyncio
async def test_assembly_handoff_uses_canonical_receipt_and_not_ready(monkeypatch):
    calls = []
    permission = "fulfillment.pack.confirm"
    actor = {"id": "assembler-2", "name": "هلال"}
    context = {"merchant_id": "merchant-1", "actor_id": actor["id"], "is_owner": False,
               "permissions": [permission], "responsibilities": {"packing"}}

    async def fake_context(_db, _user):
        calls.append("context")
        return context

    def require(context_arg, permission_arg, **kwargs):
        assert context_arg["merchant_id"] == "merchant-1"
        assert permission_arg == permission
        assert kwargs.get("responsibility") == "packing"
        calls.append("permission")

    async def canonical(_db, **kwargs):
        assert kwargs["user_id"] == "merchant-1"
        assert kwargs["piece_id"] == PIECE["piece_id"]
        assert kwargs["actor_id"] == actor["id"]
        assert kwargs["actor_name"] == actor["name"]
        await kwargs["authorize"]()
        calls.append("canonical_receipt")
        return {"ok": True, "idempotent": False,
                "piece": {"piece_id": PIECE["piece_id"], "preparation_receipt_status": "received"}}

    monkeypatch.setattr(ops, "_actor_context", fake_context)
    monkeypatch.setattr(ops, "_require_permission", require)
    monkeypatch.setattr(ops, "effective_operation_actor", lambda *_: actor)
    monkeypatch.setattr(ops, "_receive_preparation_piece", canonical)
    async def allowed(_db, _merchant_id, _field):
        return True
    monkeypatch.setattr(ops, "is_optional", allowed)
    class DB:
        def __getitem__(self, name):
            assert name == "users"
            return self
        async def find_one(self, selector):
            assert selector == {"id": actor["id"]}
            return {"id": actor["id"], "name": actor["name"], "role": "staff"}
    router = ops.make_preparation_piece_operations_router(DB(), lambda: None)
    route = next(r for r in router.routes
                 if r.path.endswith("/assembly/pieces/{piece_id}/receive-from-preparation"))
    assert "POST" in route.methods
    result = await route.endpoint(
        PIECE["piece_id"],
        ops.ReceivePreparationPieceRequest(client_request_id="assembly-direct-unique-1"),
        {"id": "assembler-2"},
    )
    assert result["ok"]
    assert result["assembly_direct_preparation_receipt_confirmed"] is True
    assert result["next_action"] == "assembly_search_then_explicit_ready"
    assert "assembly_ready" not in result["piece"]
    assert calls.count("permission") == 2
    assert calls.count("canonical_receipt") == 1


@pytest.mark.asyncio
async def test_assembly_handoff_has_no_permission_bypass(monkeypatch):
    async def no_access(_db, _user):
        return {"merchant_id": "merchant-1", "actor_id": "viewer-1", "is_owner": False,
                "permissions": [], "responsibilities": set()}

    monkeypatch.setattr(ops, "_actor_context", no_access)
    router = ops.make_preparation_piece_operations_router(object(), lambda: None)
    route = next(r for r in router.routes
                 if r.path.endswith("/assembly/pieces/{piece_id}/receive-from-preparation"))
    with pytest.raises(HTTPException) as denied:
        await route.endpoint(
            PIECE["piece_id"],
            ops.ReceivePreparationPieceRequest(client_request_id="assembly-direct-unique-2"),
            {"id": "viewer-1"},
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_assembly_direct_receipt_mandatory_mode_fails_closed_even_for_packer(monkeypatch):
    actor = {"id": "packer-1", "name": "هلال"}
    context = {"merchant_id": "merchant-1", "actor_id": actor["id"],
               "permissions": ["fulfillment.pack.confirm"], "responsibilities": {"packing"}}
    calls = []

    async def fake_context(_db, _user):
        return context

    def require(*_args, **_kwargs):
        return None

    async def mandatory(_db, _merchant_id, field):
        from mobile_operational_flow_policy import FIELD_PREPARATION
        assert field == FIELD_PREPARATION
        return False

    async def canonical(_db, **kwargs):
        calls.append("canonical")
        await kwargs["authorize"]()
        return {"ok": True}

    monkeypatch.setattr(ops, "_actor_context", fake_context)
    monkeypatch.setattr(ops, "_require_permission", require)
    monkeypatch.setattr(ops, "effective_operation_actor", lambda *_: actor)
    monkeypatch.setattr(ops, "_receive_preparation_piece", canonical)
    monkeypatch.setattr(ops, "is_optional", mandatory)
    class DB:
        def __getitem__(self, name):
            assert name == "users"
            return self
        async def find_one(self, selector):
            assert selector == {"id": actor["id"]}
            return {"id": actor["id"], "name": actor["name"], "role": "staff"}
    router = ops.make_preparation_piece_operations_router(DB(), lambda: None)
    route = next(r for r in router.routes
                 if r.path.endswith("/assembly/pieces/{piece_id}/receive-from-preparation"))
    with pytest.raises(HTTPException) as error:
        await route.endpoint(
            PIECE["piece_id"],
            ops.ReceivePreparationPieceRequest(client_request_id="mandatory-test-unique"),
            {"id": "packer-1"},
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "assembly_direct_preparation_receipt_required"
    # The canonical receipt operation is invoked, but its transaction rejects
    # before any piece mutation, Ready or custody event could be committed.
    assert calls == ["canonical"]


@pytest.mark.asyncio
@pytest.mark.parametrize("block", ["held", "stopped", "workflow", "instruction", None])
async def test_direct_post_rechecks_current_piece_order_and_instructions(monkeypatch, block):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from mongomock_motor import AsyncMongoMockClient
    from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS

    db = AsyncMongoMockClient().testdb
    owner = {"id": "merchant-1", "role": "owner"}
    number, piece_id = PIECE["order_number"], PIECE["piece_id"]
    scope = {"user_id": owner["id"], "order_number": number}
    piece = {**PIECE, **scope}
    if block == "held":
        piece["active_hold_id"] = "hold-1"
    if block == "stopped":
        piece["status"] = ops.PIECE_STATUS_BLOCKED
    await db.users.insert_one(dict(owner))
    await db.settings.insert_one({"user_id": owner["id"], "operational_flow": {
        "preparation_employee_receipt_mode": "optional",
    }})
    await db[ops.PIECES].insert_one(piece)
    await db[ops.WORKFLOWS].insert_one({
        **scope, "stage": "reviewed" if block == "workflow" else "in_progress",
    })
    await db.unified_orders.insert_one({**scope, "order_status": "in_progress",
        "raw_by_source": {"salla_direct": {"status": {"slug": "in_progress"}}}})
    if block == "instruction":
        await db[ORDER_TRACKING_INSTRUCTIONS].insert_one({
            **scope, "id": "stop-1", "status": "active", "scope": "piece",
            "target_id": piece_id, "target_stages": ["assembly_labeling"],
            "action_type": "edit_product", "enforcement": "completion_required",
        })

    async def transaction(database, merchant, work):
        assert merchant == owner["id"]
        return await work(database)

    monkeypatch.setattr(ops, "operational_owner", transaction)
    monkeypatch.setattr(ops, "_assert_preparation_receipt_salla_status", AsyncMock())
    monkeypatch.setattr(ops, "_current_assembly_order", AsyncMock(return_value=SimpleNamespace(
        status="in_progress", status_native="in_progress",
    )))
    router = ops.make_preparation_piece_operations_router(db, lambda: None)
    route = next(r for r in router.routes if r.path.endswith("/receive-from-preparation"))
    request = ops.ReceivePreparationPieceRequest(client_request_id="direct-guard-retry")
    if block:
        with pytest.raises(HTTPException) as error:
            await route.endpoint(piece_id, request, owner)
        assert error.value.status_code == 409
        assert not (await db[ops.PIECES].find_one(scope)).get("preparation_receipt_status")
        assert await db[ops.PIECE_EVENTS].count_documents(scope) == 0
    else:
        assert not (await route.endpoint(piece_id, request, owner))["idempotent"]
        assert (await route.endpoint(piece_id, request, owner))["idempotent"]
        assert await db[ops.PIECE_EVENTS].count_documents(scope) == 1
        received = await db[ops.PIECES].find_one(scope)
        assert received["preparation_received_by"] == owner["id"]
        assert received["preparation_received_from_employee_id"] == "prep-1"
        assert not received.get("assembly_ready_at")
