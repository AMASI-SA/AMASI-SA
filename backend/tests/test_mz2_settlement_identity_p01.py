from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import accounting_settlement_identity_routes as identity_routes
from accounting_settlement_identity_routes import SettlementIdentityUpdateIn
from accounting_settlement_service import settlement_idempotency_key


def test_same_statement_reference_is_same_identity_even_when_file_changes():
    first = settlement_idempotency_key(
        user_id="owner-1",
        provider="salla",
        statement_reference="INV-100",
        source_hash="file-a",
    )
    second = settlement_idempotency_key(
        user_id="owner-1",
        provider="salla",
        statement_reference=" inv-100 ",
        source_hash="file-b",
    )
    different = settlement_idempotency_key(
        user_id="owner-1",
        provider="salla",
        statement_reference="INV-101",
        source_hash="file-a",
    )
    assert first == second
    assert first != different


class FakeSettlementCollection:
    def __init__(self, *, duplicate=None, current=None):
        self.duplicate = duplicate
        self.current = current or {
            "id": "draft-1",
            "user_id": "owner-1",
            "provider": "salla",
            "status": "draft",
            "statement_reference": "OLD-REF",
            "source_file_hash": "hash-1",
            "idempotency_key": "old-key",
            "version": 1,
            "updated_at": "2026-08-24T00:00:00+00:00",
        }
        self.calls = 0
        self.updated = None

    async def find_one(self, query, projection=None):
        self.calls += 1
        if self.calls == 1:
            return self.current
        return self.duplicate

    async def update_one(self, query, update):
        self.updated = {"query": query, "update": update}
        return SimpleNamespace(matched_count=1)


class FakeDb:
    def __init__(self, *, duplicate=None, current=None):
        self.accounting_settlements_v2 = FakeSettlementCollection(
            duplicate=duplicate,
            current=current,
        )


def identity_endpoint(db):
    async def current_user():
        return {"id": "actor-1"}

    class Router:
        def __init__(self):
            self.endpoint = None

        def patch(self, _path):
            def decorator(function):
                self.endpoint = function
                return function
            return decorator

    router = Router()
    identity_routes.install_accounting_settlement_identity_routes(
        router,
        db,
        current_user,
    )
    return router.endpoint


def patch_identity_dependencies(monkeypatch, *, audit=None):
    async def fresh(_db, user):
        return {"id": user["id"], "role": "owner", "name": "المالك"}

    async def write_audit(*_args, **kwargs):
        if audit is not None:
            audit.update(kwargs)
        return "audit-1"

    monkeypatch.setattr(identity_routes, "fresh_accounting_user", fresh)
    monkeypatch.setattr(identity_routes, "require_accounting_permission", lambda *_: None)
    monkeypatch.setattr(identity_routes, "accounting_owner_id", lambda _user: "owner-1")
    monkeypatch.setattr(identity_routes, "write_audit", write_audit)


@pytest.mark.asyncio
async def test_identity_route_rejects_reference_owned_by_another_draft(monkeypatch):
    db = FakeDb(duplicate={
        "id": "draft-2",
        "status": "reviewed",
        "statement_reference": "NEW-REF",
    })
    patch_identity_dependencies(monkeypatch)

    endpoint = identity_endpoint(db)
    with pytest.raises(HTTPException) as error:
        await endpoint(
            "draft-1",
            SettlementIdentityUpdateIn(
                statement_reference="NEW-REF",
                reason="تصحيح المرجع من الكشف",
            ),
            {"id": "actor-1"},
        )
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "duplicate_settlement_statement_reference"
    assert error.value.detail["duplicate_draft_id"] == "draft-2"


@pytest.mark.asyncio
async def test_identity_route_updates_key_and_revision_when_unique(monkeypatch):
    db = FakeDb(duplicate=None)
    audit = {}
    patch_identity_dependencies(monkeypatch, audit=audit)

    endpoint = identity_endpoint(db)
    result = await endpoint(
        "draft-1",
        SettlementIdentityUpdateIn(
            statement_reference="NEW-REF",
            reason="تصحيح المرجع من الكشف",
        ),
        {"id": "actor-1"},
    )
    expected = settlement_idempotency_key(
        user_id="owner-1",
        provider="salla",
        statement_reference="NEW-REF",
        source_hash="hash-1",
    )
    assert result["statement_reference"] == "NEW-REF"
    assert result["idempotency_key"] == expected
    assert result["version"] == 2
    assert result["unchanged"] is False
    assert db.accounting_settlements_v2.updated["update"]["$set"]["idempotency_key"] == expected
    assert db.accounting_settlements_v2.updated["update"]["$push"]["revision_log"]["reason"] == "تصحيح المرجع من الكشف"
    assert audit["action"] == "update_settlement_statement_identity"


@pytest.mark.asyncio
async def test_identity_route_does_not_increment_version_when_unchanged(monkeypatch):
    key = settlement_idempotency_key(
        user_id="owner-1",
        provider="salla",
        statement_reference="SAME-REF",
        source_hash="hash-1",
    )
    db = FakeDb(current={
        "id": "draft-1",
        "user_id": "owner-1",
        "provider": "salla",
        "status": "draft",
        "statement_reference": "SAME-REF",
        "source_file_hash": "hash-1",
        "idempotency_key": key,
        "version": 4,
        "updated_at": "2026-08-24T00:00:00+00:00",
    })
    patch_identity_dependencies(monkeypatch)

    endpoint = identity_endpoint(db)
    result = await endpoint(
        "draft-1",
        SettlementIdentityUpdateIn(
            statement_reference="SAME-REF",
            reason="حفظ المسودة دون تغيير المرجع",
        ),
        {"id": "actor-1"},
    )
    assert result["unchanged"] is True
    assert result["version"] == 4
    assert db.accounting_settlements_v2.updated is None
    assert db.accounting_settlements_v2.calls == 1
