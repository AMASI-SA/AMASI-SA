#!/usr/bin/env python3
"""Provision or rotate the independent, time-bounded Meta review account."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from auth import hash_password  # noqa: E402
from meta_reviewer_access import (  # noqa: E402
    META_REVIEWER_ROLE,
    META_REVIEW_SCOPES,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--owner-email")
    parser.add_argument("--name", default="Meta App Reviewer")
    parser.add_argument("--days", type=int, default=366)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    if args.days < 365:
        raise SystemExit("--days must be at least 365")
    password = getpass.getpass("Reviewer password (16+ chars): ")
    confirm = getpass.getpass("Confirm reviewer password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    if len(password) < 16:
        raise SystemExit("Password must contain at least 16 characters")

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    try:
        owner_query = {"role": "owner"}
        if args.owner_email:
            owner_query["email"] = args.owner_email.strip().lower()
        owners = await db.users.find(owner_query, {"_id": 0, "id": 1, "email": 1}).to_list(2)
        if len(owners) != 1:
            raise SystemExit("Expected exactly one matching Owner; pass --owner-email")

        email = args.email.strip().lower()
        existing = await db.users.find_one({"email": email}, {"_id": 0})
        if existing and existing.get("role") != META_REVIEWER_ROLE:
            raise SystemExit("Email belongs to a non-reviewer account; refusing to overwrite it")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=args.days)
        reviewer_id = str(existing.get("id")) if existing else str(uuid.uuid4())
        fields = {
            "id": reviewer_id,
            "name": args.name.strip() or "Meta App Reviewer",
            "email": email,
            "password_hash": hash_password(password),
            "role": META_REVIEWER_ROLE,
            "created_by": str(owners[0]["id"]),
            "review_owner_id": str(owners[0]["id"]),
            "review_scopes": sorted(META_REVIEW_SCOPES),
            "review_access_started_at": now.isoformat(),
            "review_access_expires_at": expires_at.isoformat(),
            "password_updated_at": now.isoformat(),
            "is_active": True,
            "disabled": False,
            "extra_permissions": [],
            "denied_permissions": [],
        }
        await db.users.update_one(
            {"email": email},
            {
                "$set": fields,
                "$setOnInsert": {"created_at": now.isoformat()},
                "$unset": {"deleted_at": ""},
            },
            upsert=True,
        )
        print(json.dumps({
            "created": existing is None,
            "email": email,
            "role": META_REVIEWER_ROLE,
            "expires_at": expires_at.isoformat(),
            "scopes": sorted(META_REVIEW_SCOPES),
        }, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_run(_args()))
