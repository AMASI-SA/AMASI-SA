"""TEMPORARY secure one-off endpoint — promote primary owner (2026-02).

Contract (user directive, 2026-02):
    • Path: POST /api/admin/promote-primary-owner-secure-temp
    • Target user is HARD-CODED (no request-body/query override).
    • Guarded by DIAGNOSTIC_TOKEN env + X-Diagnostic-Token header.
    • ONLY modifies `role` (+ `updated_at`); never `is_owner` (derived).
    • Idempotent: if role already "owner" → status=already_owner.
    • Writes an audit row to `owner_promotion_audit`.
    • Never logs / returns secrets, tokens, passwords, or full user doc.

Lifecycle (per user):
    1. Deploy this router.
    2. Set DIAGNOSTIC_TOKEN in production env.
    3. Call the endpoint ONCE, verify /api/auth/me.
    4. Delete this file + the include_router in server.py.
    5. Remove DIAGNOSTIC_TOKEN. Redeploy.
"""
from __future__ import annotations

import os
import secrets as _secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

TARGET_ID    = "5aee091a-cc47-42cd-b14c-a14e32f169cc"
TARGET_EMAIL = "amasi.jewelery@gmail.com"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic_token() -> str:
    return (os.environ.get("DIAGNOSTIC_TOKEN") or "").strip()


def make_owner_promotion_router(db) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin-owner-promotion-TEMP"])

    @router.post(
        "/promote-primary-owner-secure-temp",
        include_in_schema=False,
    )
    async def _promote(
        x_diagnostic_token: str | None = Header(default=None),
    ) -> dict:
        # ── Guard 1 — env must be configured ──────────────────────
        expected = _diagnostic_token()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="Service temporarily unavailable")

        # ── Guard 2 — header must match (constant-time) ───────────
        supplied = (x_diagnostic_token or "").strip()
        if not supplied or not _secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="Forbidden")

        # ── Guard 3 — conflict detection (id vs email) ────────────
        by_id    = await db.users.find_one({"id":    TARGET_ID})
        by_email = await db.users.find_one({"email": TARGET_EMAIL})

        if by_id and by_email:
            if by_id.get("email") != TARGET_EMAIL:
                raise HTTPException(
                    status_code=409,
                    detail="conflict: id matches a different email")
            if by_email.get("id") != TARGET_ID:
                raise HTTPException(
                    status_code=409,
                    detail="conflict: email matches a different id")
        elif by_id and not by_email:
            raise HTTPException(
                status_code=409,
                detail="conflict: id present, email mismatch")
        elif by_email and not by_id:
            raise HTTPException(
                status_code=409,
                detail="conflict: email present, id mismatch")

        # Look up strictly by BOTH id AND email.
        user = await db.users.find_one(
            {"id": TARGET_ID, "email": TARGET_EMAIL})
        if user is None:
            raise HTTPException(status_code=404, detail="Not Found")

        before_role = (user.get("role") or "").strip().lower() or "user"

        # ── Idempotency ───────────────────────────────────────────
        if before_role == "owner":
            await db.owner_promotion_audit.insert_one({
                "action":         "promote_primary_owner",
                "target_user_id": TARGET_ID,
                "target_email":   TARGET_EMAIL,
                "before_role":    before_role,
                "after_role":     before_role,
                "executed_at":    _now_iso(),
                "source":         "temporary_secure_endpoint",
                "outcome":        "already_owner",
            })
            return {
                "ok":             True,
                "status":         "already_owner",
                "matched_count":  1,
                "modified_count": 0,
                "before_role":    before_role,
                "after_role":     before_role,
            }

        # ── Conditional update (id + email + current role) ────────
        now = _now_iso()
        res = await db.users.update_one(
            {"id": TARGET_ID, "email": TARGET_EMAIL, "role": user.get("role")},
            {"$set": {"role": "owner", "updated_at": now}},
        )

        await db.owner_promotion_audit.insert_one({
            "action":         "promote_primary_owner",
            "target_user_id": TARGET_ID,
            "target_email":   TARGET_EMAIL,
            "before_role":    before_role,
            "after_role":     "owner" if res.modified_count == 1 else before_role,
            "executed_at":    now,
            "source":         "temporary_secure_endpoint",
            "outcome":        ("promoted" if res.modified_count == 1
                                else "race_condition_no_change"),
        })

        return {
            "ok":             res.modified_count == 1,
            "status":         ("promoted" if res.modified_count == 1
                                else "no_change"),
            "matched_count":  res.matched_count,
            "modified_count": res.modified_count,
            "before_role":    before_role,
            "after_role":     "owner" if res.modified_count == 1 else before_role,
        }

    return router
