"""P0-2 tenant-isolation regressions for store-operation access state."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_store_access_contract import (
    AI_ACTION_LOG,
    ROLE_ASSIGNMENTS,
    ROLE_ASSIGNMENT_OWNER_FIELD,
    find_role_assignment,
    find_role_assignments,
    merged_session_permissions,
    write_role_assignment,
)
from ai_store_access_control import make_ai_store_access_router
from warehouse_location_routes import WAREHOUSES


OWNER_A = "owner-a"
OWNER_B = "owner-b"
SHARED_USER = "shared-employee"
ROOT = Path(__file__).resolve().parents[2]


def _nested(document, key):
    value = document
    for part in str(key).split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = _nested(document, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key_or_spec, direction=None):
        specification = (
            key_or_spec
            if isinstance(key_or_spec, list)
            else [(key_or_spec, direction)]
        )
        for key, order in reversed(specification):
            self.rows.sort(
                key=lambda row: str(_nested(row, key) or ""),
                reverse=int(order or 1) < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows[:length] if length is not None else self.rows)


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])

    def find(self, query, projection=None):
        del projection
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None):
        del projection
        row = next((row for row in self.rows if _matches(row, query)), None)
        return deepcopy(row) if row is not None else None

    async def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0)
            row = {
                key: deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            self.rows.append(row)
        row.update(deepcopy(update.get("$set") or {}))
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id=document.get("id"))

    async def count_documents(self, query):
        return sum(1 for row in self.rows if _matches(row, query))


class LooseFindOneCollection(FakeCollection):
    """Simulate an adapter returning malformed rows despite the selector."""

    async def find_one(self, query, projection=None):
        del query, projection
        if not self.rows:
            return None
        return deepcopy(self.rows.pop(0))


class FakeDB:
    def __init__(self, collections=None):
        self.collections = {
            name: FakeCollection(rows)
            for name, rows in (collections or {}).items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


def _endpoint(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in (route.methods or set())
    )


@pytest.mark.asyncio
async def test_assignment_reads_are_scoped_when_user_ids_collide():
    db = FakeDB({
        ROLE_ASSIGNMENTS: [
            {
                ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
                "user_id": SHARED_USER,
                "role_key": "owner",
            },
            {
                ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_A,
                "user_id": SHARED_USER,
                "role_key": "product_operator",
            },
        ]
    })

    assignment = await find_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id=SHARED_USER,
    )
    assignments = await find_role_assignments(
        db,
        owner_user_id=OWNER_A,
        user_ids=[SHARED_USER],
    )

    assert assignment[ROLE_ASSIGNMENT_OWNER_FIELD] == OWNER_A
    assert assignment["role_key"] == "product_operator"
    assert assignments == [assignment]


@pytest.mark.asyncio
async def test_assignment_read_rejects_malformed_tenant_and_subject_shapes():
    db = FakeDB()
    db.collections[ROLE_ASSIGNMENTS] = LooseFindOneCollection([
        {
            ROLE_ASSIGNMENT_OWNER_FIELD: [OWNER_A],
            "user_id": [SHARED_USER],
            "role_key": "owner",
        },
        {
            "created_by": [OWNER_A],
            "user_id": [SHARED_USER],
            "role_key": "owner",
        },
    ])

    assert await find_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id=SHARED_USER,
    ) is None


@pytest.mark.asyncio
async def test_legacy_assignment_requires_proven_owner_and_promotes_only_that_row():
    foreign = {
        "id": "foreign",
        ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
        "created_by": OWNER_A,
        "user_id": SHARED_USER,
        "role_key": "owner",
    }
    legacy = {
        "id": "legacy",
        "created_by": OWNER_A,
        "user_id": SHARED_USER,
        "role_key": "product_operator",
    }
    unproven = {
        "id": "unproven",
        "user_id": "unproven-user",
        "role_key": "owner",
    }
    db = FakeDB({ROLE_ASSIGNMENTS: [foreign, legacy, unproven]})

    assignment = await find_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id=SHARED_USER,
    )
    assert assignment["id"] == "legacy"
    assert await find_role_assignments(
        db,
        owner_user_id=OWNER_A,
        user_ids=[SHARED_USER],
    ) == [assignment]
    assert await find_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id="unproven-user",
    ) is None

    await write_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id=SHARED_USER,
        existing=assignment,
        values={
            ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
            "user_id": "different-user",
            "role_key": "warehouse_operator",
        },
    )
    await write_role_assignment(
        db,
        owner_user_id=OWNER_A,
        user_id=SHARED_USER,
        existing=assignment,
        values={"role_key": "cost_manager"},
        upsert=True,
    )

    rows = db[ROLE_ASSIGNMENTS].rows
    assert len(rows) == 3
    assert next(row for row in rows if row["id"] == "legacy")[
        ROLE_ASSIGNMENT_OWNER_FIELD
    ] == OWNER_A
    assert next(row for row in rows if row["id"] == "legacy")["user_id"] == (
        SHARED_USER
    )
    assert next(row for row in rows if row["id"] == "legacy")["role_key"] == (
        "cost_manager"
    )
    assert next(row for row in rows if row["id"] == "foreign") == foreign
    rows.append({
        "id": "legacy-duplicate",
        "created_by": OWNER_A,
        "user_id": SHARED_USER,
        "role_key": "owner",
    })
    bulk_after_promotion = await find_role_assignments(
        db,
        owner_user_id=OWNER_A,
        user_ids=[SHARED_USER],
    )
    assert [row["id"] for row in bulk_after_promotion] == ["legacy"]


@pytest.mark.asyncio
async def test_session_merge_uses_authenticated_users_owner_scope_and_fails_closed():
    db = FakeDB({
        ROLE_ASSIGNMENTS: [
            {
                ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
                "user_id": SHARED_USER,
                "role_key": "owner",
                "enabled": True,
            },
            {
                ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_A,
                "user_id": SHARED_USER,
                "role_key": "product_operator",
                "enabled": True,
            },
        ]
    })

    scoped = await merged_session_permissions(
        db,
        {"id": SHARED_USER, "role": "employee", "created_by": OWNER_A},
        {"dashboard.view"},
    )
    unlinked = await merged_session_permissions(
        db,
        {"id": SHARED_USER, "role": "employee"},
        {"dashboard.view"},
    )
    owner = await merged_session_permissions(
        db,
        {"id": OWNER_A, "role": "owner"},
        {"dashboard.view"},
    )

    assert "products.media.edit" in scoped
    assert "roles.manage" not in scoped
    assert unlinked == ["dashboard.view"]
    assert "roles.manage" in owner


@pytest.mark.asyncio
async def test_access_save_list_and_audit_never_cross_owner_scope():
    foreign_assignment = {
        "id": "foreign-assignment",
        ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
        "user_id": SHARED_USER,
        "role_key": "owner",
        "enabled": True,
    }
    db = FakeDB({
        "users": [
            {"id": OWNER_A, "role": "owner", "name": "Owner A"},
            {
                "id": SHARED_USER,
                "role": "employee",
                "created_by": OWNER_A,
                "name": "Employee A",
            },
        ],
        ROLE_ASSIGNMENTS: [foreign_assignment],
        WAREHOUSES: [],
        AI_ACTION_LOG: [
            {
                "id": "foreign-audit",
                ROLE_ASSIGNMENT_OWNER_FIELD: OWNER_B,
                "target_type": "store_operations_access",
            },
            {
                "id": "legacy-unscoped-audit",
                "target_type": "store_operations_access",
            },
        ],
    })
    owner = {"id": OWNER_A, "role": "owner", "name": "Owner A"}
    router = make_ai_store_access_router(db, lambda: owner)
    save = _endpoint(
        router,
        "/ai-store-operations/access/{target_user_id}",
        "PUT",
    )
    list_access = _endpoint(router, "/ai-store-operations/access", "GET")
    audit_log = _endpoint(
        router,
        "/ai-store-operations/access/audit/log",
        "GET",
    )

    saved = await save(
        target_user_id=SHARED_USER,
        payload={"role_key": "product_operator", "enabled": True},
        user=owner,
    )
    listed = await list_access(user=owner)
    audited = await audit_log(limit=100, user=owner)

    rows = db[ROLE_ASSIGNMENTS].rows
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == "foreign-assignment") == (
        foreign_assignment
    )
    own_row = next(
        row
        for row in rows
        if row.get(ROLE_ASSIGNMENT_OWNER_FIELD) == OWNER_A
    )
    assert own_row["role_key"] == "product_operator"
    assert saved["assignment"][ROLE_ASSIGNMENT_OWNER_FIELD] == OWNER_A
    employee = next(row for row in listed["users"] if row["id"] == SHARED_USER)
    assert employee["assignment"][ROLE_ASSIGNMENT_OWNER_FIELD] == OWNER_A
    assert {row[ROLE_ASSIGNMENT_OWNER_FIELD] for row in audited["items"]} == {
        OWNER_A
    }
    assert db[AI_ACTION_LOG].rows[-1][ROLE_ASSIGNMENT_OWNER_FIELD] == OWNER_A


def test_role_assignment_persistence_is_centralized():
    persistence_call = re.compile(
        r"(?:\[[^\]\n]*ROLE_ASSIGNMENTS[^\]\n]*\]"
        r"|getattr\([^\n]*ROLE_ASSIGNMENTS[^\n]*\))"
        r"\s*\.\s*(?:find|find_one|update_one|replace_one|delete_one|insert_one)"
    )
    offenders = []
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts or path.name == "ai_store_access_contract.py":
            continue
        if persistence_call.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
