"""Owner seed hardening — regression tests for privileged credentials."""
from __future__ import annotations

from copy import deepcopy

import pytest

import auth


class _Result:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeUsers:
    def __init__(self, docs=None):
        self.docs = [deepcopy(doc) for doc in (docs or [])]

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query, *args, **kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                return deepcopy(doc)
        return None

    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
        return _Result(inserted_id=len(self.docs))

    async def update_one(self, query, update):
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(deepcopy(update.get("$set") or {}))
                return _Result(matched_count=1, modified_count=1)
        return _Result(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self, users=None):
        self.users = FakeUsers(users)


@pytest.fixture(autouse=True)
def _disable_app_install(monkeypatch):
    async def noop(_db):
        return None

    monkeypatch.setattr(auth, "_install_login_security_for_loaded_app", noop)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)


@pytest.mark.asyncio
async def test_fresh_install_requires_explicit_admin_password():
    db = FakeDB()

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD must be configured"):
        await auth.seed_admin(db)

    assert db.users.docs == []


@pytest.mark.asyncio
async def test_fresh_install_rejects_retired_admin123_default(monkeypatch):
    db = FakeDB()
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")

    with pytest.raises(RuntimeError, match="retired insecure default"):
        await auth.seed_admin(db)

    assert db.users.docs == []


@pytest.mark.asyncio
async def test_fresh_install_uses_only_explicit_secret(monkeypatch):
    db = FakeDB()
    monkeypatch.setenv("ADMIN_EMAIL", "Owner@Example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "A-new-explicit-owner-secret-2026")

    await auth.seed_admin(db)

    assert len(db.users.docs) == 1
    owner = db.users.docs[0]
    assert owner["email"] == "owner@example.com"
    assert owner["role"] == "owner"
    assert auth.verify_password(
        "A-new-explicit-owner-secret-2026", owner["password_hash"]
    )
    assert not auth.verify_password("admin123", owner["password_hash"])


@pytest.mark.asyncio
async def test_existing_owner_password_is_never_synced_from_environment(monkeypatch):
    old_hash = auth.hash_password("Existing-owner-password")
    db = FakeDB([
        {
            "id": "owner-1",
            "email": "admin@hesab.app",
            "password_hash": old_hash,
            "role": "owner",
        }
    ])
    monkeypatch.setenv("ADMIN_PASSWORD", "Changed-environment-secret")

    await auth.seed_admin(db)

    assert db.users.docs[0]["password_hash"] == old_hash
    assert auth.verify_password(
        "Existing-owner-password", db.users.docs[0]["password_hash"]
    )
    assert not auth.verify_password(
        "Changed-environment-secret", db.users.docs[0]["password_hash"]
    )


@pytest.mark.asyncio
async def test_existing_owner_does_not_require_admin_password_env():
    old_hash = auth.hash_password("Existing-owner-password")
    db = FakeDB([
        {
            "id": "owner-1",
            "email": "admin@hesab.app",
            "password_hash": old_hash,
            "role": "owner",
        }
    ])

    await auth.seed_admin(db)

    assert len(db.users.docs) == 1
    assert db.users.docs[0]["password_hash"] == old_hash


@pytest.mark.asyncio
async def test_changed_admin_email_does_not_create_second_owner(monkeypatch):
    old_hash = auth.hash_password("Existing-owner-password")
    db = FakeDB([
        {
            "id": "owner-1",
            "email": "real-owner@example.com",
            "password_hash": old_hash,
            "role": "owner",
        }
    ])
    monkeypatch.setenv("ADMIN_EMAIL", "different-admin@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "Some-other-secret")

    await auth.seed_admin(db)

    assert len(db.users.docs) == 1
    assert db.users.docs[0]["email"] == "real-owner@example.com"


@pytest.mark.asyncio
async def test_legacy_configured_admin_can_be_promoted_when_no_other_owner_exists():
    password_hash = auth.hash_password("Existing-password")
    db = FakeDB([
        {
            "id": "admin-1",
            "email": "admin@hesab.app",
            "password_hash": password_hash,
            "role": "admin",
        }
    ])

    await auth.seed_admin(db)

    assert db.users.docs[0]["role"] == "owner"
    assert db.users.docs[0]["password_hash"] == password_hash


@pytest.mark.asyncio
async def test_configured_admin_not_promoted_when_another_owner_exists():
    admin_hash = auth.hash_password("Admin-password")
    owner_hash = auth.hash_password("Owner-password")
    db = FakeDB([
        {
            "id": "admin-1",
            "email": "admin@hesab.app",
            "password_hash": admin_hash,
            "role": "admin",
        },
        {
            "id": "owner-1",
            "email": "real-owner@example.com",
            "password_hash": owner_hash,
            "role": "owner",
        },
    ])

    await auth.seed_admin(db)

    admin = next(doc for doc in db.users.docs if doc["id"] == "admin-1")
    owner = next(doc for doc in db.users.docs if doc["id"] == "owner-1")
    assert admin["role"] == "admin"
    assert owner["role"] == "owner"
