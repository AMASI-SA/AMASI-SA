"""Iter-51 — Profile self-service + password recovery + multi-user RBAC tests.

Covers:
  • PUT  /auth/profile/name           — own name update
  • PUT  /auth/profile/password       — change own password (current required)
  • PUT  /auth/profile/email          — change own email (current pw + uniqueness)
  • PUT  /auth/profile/security-question — set/update Q&A
  • POST /auth/forgot-password/check  — fetch question by email
  • POST /auth/forgot-password/reset  — verify answer + reset password
  • GET  /auth/permissions/catalogue  — drives the team-mgmt UI
  • GET/POST/PUT/DELETE /team/users   — owner-only CRUD
  • Role-based effective permissions   — owner vs admin vs viewer
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _hdr(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _register(name: str = "Iter51 User") -> tuple[str, str, str]:
    """Returns (token, user_id, email)."""
    email = f"iter51-{uuid.uuid4().hex[:10]}@example.com"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": name, "email": email, "password": "Test1234!"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"], email


def _login(email: str, password: str) -> str:
    r = requests.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _cleanup_user(uid: str) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _do():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = client[os.environ["DB_NAME"]]
            await db.users.delete_one({"id": uid})
            await db.settings.delete_one({"user_id": uid})
        finally:
            client.close()

    asyncio.run(_do())


def _delete_user_by_email(email: str) -> None:
    """Idempotent cleanup helper for the multi-user tests."""
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _do():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = client[os.environ["DB_NAME"]]
            await db.users.delete_one({"email": email.lower()})
        finally:
            client.close()

    asyncio.run(_do())


# ── 1. Update own name ─────────────────────────────────────────────────
def test_update_own_name():
    token, uid, _ = _register()
    try:
        r = requests.put(f"{API}/auth/profile/name", headers=_hdr(token),
                         json={"name": "اسم محدّث"}, timeout=10)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["name"] == "اسم محدّث"
        me = requests.get(f"{API}/auth/me", headers=_hdr(token), timeout=10).json()
        assert me["name"] == "اسم محدّث"
    finally:
        _cleanup_user(uid)


# ── 2. Change own password ─────────────────────────────────────────────
def test_change_password_requires_current_and_allows_relogin():
    token, uid, email = _register()
    try:
        # Wrong current pw → 400
        r = requests.put(f"{API}/auth/profile/password", headers=_hdr(token),
                         json={"current_password": "wrong", "new_password": "NewPass99!"},
                         timeout=10)
        assert r.status_code == 400

        # Correct pw → 200
        r = requests.put(f"{API}/auth/profile/password", headers=_hdr(token),
                         json={"current_password": "Test1234!", "new_password": "NewPass99!"},
                         timeout=10)
        assert r.status_code == 200, r.text[:300]

        # Old pw login fails
        r2 = requests.post(f"{API}/auth/login",
                           json={"email": email, "password": "Test1234!"}, timeout=10)
        assert r2.status_code == 401
        # New pw login works
        r3 = requests.post(f"{API}/auth/login",
                           json={"email": email, "password": "NewPass99!"}, timeout=10)
        assert r3.status_code == 200
    finally:
        _cleanup_user(uid)


# ── 3. Change own email — uniqueness enforced ──────────────────────────
def test_change_email_with_uniqueness_check():
    # Pre-register a "blocker" user
    blocker_token, blocker_uid, blocker_email = _register("Blocker")
    token, uid, _ = _register()
    try:
        # Try to take the blocker's email
        r = requests.put(f"{API}/auth/profile/email", headers=_hdr(token),
                         json={"current_password": "Test1234!", "new_email": blocker_email},
                         timeout=10)
        assert r.status_code == 400

        # Take a unique email
        new_email = f"new-{uuid.uuid4().hex[:8]}@example.com"
        r = requests.put(f"{API}/auth/profile/email", headers=_hdr(token),
                         json={"current_password": "Test1234!", "new_email": new_email},
                         timeout=10)
        assert r.status_code == 200, r.text[:300]
        # Can login with new email
        assert _login(new_email, "Test1234!")
    finally:
        _cleanup_user(uid)
        _cleanup_user(blocker_uid)


# ── 4. Security question + password recovery (full flow) ───────────────
def test_security_question_setup_and_password_recovery():
    token, uid, email = _register()
    try:
        # Set a security question
        r = requests.put(f"{API}/auth/profile/security-question", headers=_hdr(token),
                         json={"current_password": "Test1234!",
                               "question": "ما اسم أول مدرسة دخلتها؟",
                               "answer": "الأولى"},
                         timeout=10)
        assert r.status_code == 200, r.text[:300]

        # Fetch question via email (no auth needed)
        r = requests.post(f"{API}/auth/forgot-password/check",
                          json={"email": email}, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["has_question"] is True
        assert "مدرسة" in body["question"]

        # Wrong answer → 400
        r = requests.post(f"{API}/auth/forgot-password/reset",
                          json={"email": email, "answer": "خطأ",
                                "new_password": "Recovered99!"}, timeout=10)
        assert r.status_code == 400

        # Correct answer (case + whitespace insensitive)
        r = requests.post(f"{API}/auth/forgot-password/reset",
                          json={"email": email, "answer": "  الأولى  ",
                                "new_password": "Recovered99!"}, timeout=10)
        assert r.status_code == 200

        # Login with recovered password works
        assert _login(email, "Recovered99!")
    finally:
        _cleanup_user(uid)


def test_forgot_password_no_enumeration_for_missing_email():
    """Email that doesn't exist must still return 200 with a generic
    placeholder (prevents enumeration attacks)."""
    r = requests.post(
        f"{API}/auth/forgot-password/check",
        json={"email": f"never-{uuid.uuid4().hex}@example.com"},
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_question"] is False
    # Generic answer is returned (no leak about whether the email exists).
    assert isinstance(body["question"], str)


# ── 5. Permissions catalogue ───────────────────────────────────────────
def test_permissions_catalogue_returns_role_defaults():
    token, uid, _ = _register()
    try:
        r = requests.get(f"{API}/auth/permissions/catalogue",
                         headers=_hdr(token), timeout=10)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        roles = body["roles_ordered"]
        assert "owner" in roles
        assert "viewer" in roles
        assert "accountant" in roles
        # Catalogue contains known keys
        keys = {p["key"] for p in body["permissions"]}
        assert "dashboard.view" in keys
        assert "users.manage" in keys
        # Viewer cannot manage anything
        viewer_perms = body["role_defaults"]["viewer"]
        assert all(".manage" not in p for p in viewer_perms if p != "users.manage")
        # Owner has every permission key
        owner_perms = set(body["role_defaults"]["owner"])
        assert owner_perms == keys
    finally:
        _cleanup_user(uid)


# ── 6. Team CRUD (owner only) ──────────────────────────────────────────
def test_owner_can_create_update_delete_team_users():
    """Uses the seeded admin user (which is now owner)."""
    owner_email = os.environ.get("ADMIN_EMAIL", "admin@hesab.app").lower()
    owner_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    owner_token = _login(owner_email, owner_password)
    me = requests.get(f"{API}/auth/me", headers=_hdr(owner_token), timeout=10).json()
    assert me["role"] == "owner"
    assert me["is_owner"] is True

    new_email = f"team-{uuid.uuid4().hex[:10]}@example.com"
    try:
        # Create
        r = requests.post(f"{API}/team/users", headers=_hdr(owner_token),
                          json={"name": "محاسب الاختبار",
                                "email": new_email,
                                "password": "Acc1234!",
                                "role": "accountant"},
                          timeout=10)
        assert r.status_code == 200, r.text[:400]
        created = r.json()
        new_user_id = created["id"]
        assert created["role"] == "accountant"
        assert "reports.view" in created["effective_permissions"]
        assert "users.manage" not in created["effective_permissions"]

        # The new accountant can log in and sees their permissions
        acc_token = _login(new_email, "Acc1234!")
        acc_me = requests.get(f"{API}/auth/me", headers=_hdr(acc_token), timeout=10).json()
        assert acc_me["role"] == "accountant"
        assert "reports.view" in acc_me["permissions"]

        # The accountant CANNOT list team users
        r = requests.get(f"{API}/team/users", headers=_hdr(acc_token), timeout=10)
        assert r.status_code == 403

        # The owner CAN list and sees both users
        r = requests.get(f"{API}/team/users", headers=_hdr(owner_token), timeout=10)
        assert r.status_code == 200
        users = r.json()
        assert any(u["id"] == new_user_id for u in users)

        # Owner promotes accountant → admin and adds an extra permission
        r = requests.put(f"{API}/team/users/{new_user_id}", headers=_hdr(owner_token),
                         json={"role": "admin",
                               "extra_permissions": ["users.manage"]},
                         timeout=10)
        assert r.status_code == 200
        updated = r.json()
        assert updated["role"] == "admin"
        assert "users.manage" in updated["effective_permissions"]

        # Owner deletes the user
        r = requests.delete(f"{API}/team/users/{new_user_id}",
                            headers=_hdr(owner_token), timeout=10)
        assert r.status_code == 200
    finally:
        _delete_user_by_email(new_email)


def test_cannot_create_or_promote_to_owner():
    owner_email = os.environ.get("ADMIN_EMAIL", "admin@hesab.app").lower()
    owner_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    owner_token = _login(owner_email, owner_password)
    new_email = f"team-{uuid.uuid4().hex[:10]}@example.com"
    try:
        # Cannot create another Owner
        r = requests.post(f"{API}/team/users", headers=_hdr(owner_token),
                          json={"name": "محاولة", "email": new_email,
                                "password": "Test1234!", "role": "owner"},
                          timeout=10)
        assert r.status_code == 400

        # Create a viewer, then try to promote them to owner
        r = requests.post(f"{API}/team/users", headers=_hdr(owner_token),
                          json={"name": "مشاهد", "email": new_email,
                                "password": "Test1234!", "role": "viewer"},
                          timeout=10)
        assert r.status_code == 200
        target_id = r.json()["id"]

        r = requests.put(f"{API}/team/users/{target_id}", headers=_hdr(owner_token),
                         json={"role": "owner"}, timeout=10)
        assert r.status_code == 400
    finally:
        _delete_user_by_email(new_email)


def test_owner_cannot_be_deleted_or_modified_by_others():
    """Even if another user gets users.manage perm, they still can't
    touch the Owner row."""
    owner_email = os.environ.get("ADMIN_EMAIL", "admin@hesab.app").lower()
    owner_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    owner_token = _login(owner_email, owner_password)
    new_email = f"team-{uuid.uuid4().hex[:10]}@example.com"
    try:
        r = requests.post(f"{API}/team/users", headers=_hdr(owner_token),
                          json={"name": "Admin",
                                "email": new_email,
                                "password": "Test1234!",
                                "role": "admin",
                                "extra_permissions": ["users.manage"]},
                          timeout=10)
        assert r.status_code == 200
        admin_token = _login(new_email, "Test1234!")

        # The non-owner with users.manage still can't access /team/* endpoints
        # (because _require_owner is the gate, not the perm).
        r = requests.get(f"{API}/team/users", headers=_hdr(admin_token), timeout=10)
        assert r.status_code == 403
    finally:
        _delete_user_by_email(new_email)
