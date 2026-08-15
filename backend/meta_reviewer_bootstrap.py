"""One-time, production-local bootstrap for the fixed Meta reviewer account.

The endpoint is deliberately constrained to one account, one Owner tenant and one
pre-committed high-entropy token digest. The plaintext token is never stored in
source or MongoDB and becomes unusable after the first successful claim.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from auth import hash_password
from meta_reviewer_access import META_REVIEWER_ROLE, META_REVIEW_SCOPES


REVIEWER_EMAIL = "meta-reviewer@mezansalla.com"
OWNER_EMAIL = "amasi.jewelery@gmail.com"
TOKEN_DIGEST = "ed938bb3f3e2b0f7d2d94aa9b04951705afd03cac7b34065140d042a466598f8"
TOKEN_CLAIM_ID = f"meta-reviewer-bootstrap:{TOKEN_DIGEST}"
ACCESS_DAYS = 366


class BootstrapRequest(BaseModel):
    token: str = Field(min_length=64, max_length=256)
    password: str = Field(min_length=16, max_length=256)


def _valid_token(token: str) -> bool:
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(supplied, TOKEN_DIGEST)


async def _claim_token(db, now: datetime) -> None:
    try:
        await db.security_bootstrap_claims.insert_one({
            "_id": TOKEN_CLAIM_ID,
            "purpose": "meta_reviewer_production_bootstrap",
            "consumed_at": now,
        })
    except DuplicateKeyError:
        raise HTTPException(
            status_code=410,
            detail={"code": "meta_reviewer_bootstrap_already_used"},
        )


async def _release_failed_claim(db) -> None:
    # Retry is allowed only if provisioning itself failed after the atomic claim.
    await db.security_bootstrap_claims.delete_one({"_id": TOKEN_CLAIM_ID})


def install_meta_reviewer_bootstrap(app, db) -> None:
    if getattr(app.state, "meta_reviewer_bootstrap_installed", False):
        return

    @app.post("/api/auth/meta-reviewer-bootstrap", include_in_schema=False)
    async def bootstrap_meta_reviewer(payload: BootstrapRequest):
        if not _valid_token(payload.token):
            raise HTTPException(status_code=404, detail="Not found")

        owner = await db.users.find_one(
            {"email": OWNER_EMAIL, "role": "owner"},
            {"_id": 0, "id": 1},
        )
        if not owner:
            raise HTTPException(
                status_code=409,
                detail={"code": "meta_reviewer_owner_not_found"},
            )

        existing = await db.users.find_one({"email": REVIEWER_EMAIL}, {"_id": 0})
        if existing and existing.get("role") != META_REVIEWER_ROLE:
            raise HTTPException(
                status_code=409,
                detail={"code": "meta_reviewer_email_conflict"},
            )

        now = datetime.now(timezone.utc)
        await _claim_token(db, now)
        try:
            reviewer_id = str(existing.get("id")) if existing else str(uuid.uuid4())
            expires_at = now + timedelta(days=ACCESS_DAYS)
            await db.users.update_one(
                {"email": REVIEWER_EMAIL},
                {
                    "$set": {
                        "id": reviewer_id,
                        "name": "Meta App Reviewer",
                        "email": REVIEWER_EMAIL,
                        "password_hash": hash_password(payload.password),
                        "role": META_REVIEWER_ROLE,
                        "created_by": str(owner["id"]),
                        "review_owner_id": str(owner["id"]),
                        "review_scopes": sorted(META_REVIEW_SCOPES),
                        "review_access_started_at": now.isoformat(),
                        "review_access_expires_at": expires_at.isoformat(),
                        "password_updated_at": now.isoformat(),
                        "is_active": True,
                        "disabled": False,
                        "extra_permissions": [],
                        "denied_permissions": [],
                    },
                    "$setOnInsert": {"created_at": now.isoformat()},
                    "$unset": {"deleted_at": ""},
                },
                upsert=True,
            )
        except Exception:
            await _release_failed_claim(db)
            raise

        return {
            "created": existing is None,
            "email": REVIEWER_EMAIL,
            "role": META_REVIEWER_ROLE,
            "expires_at": expires_at.isoformat(),
            "scopes": sorted(META_REVIEW_SCOPES),
            "bootstrap_consumed": True,
        }

    app.state.meta_reviewer_bootstrap_installed = True
