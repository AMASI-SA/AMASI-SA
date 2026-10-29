"""Explicit owner choices from complete local snapshots; no Google access proof."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from fastapi import Depends, HTTPException, Request
from pymongo import ReadPreference, timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_ads_client import problem
from .google_ads_management import principal
from .google_merchant_observation_routes import (
    ADS, MERCHANT, PROVIDERS, MAX_ACCOUNTS, MAX_SNAPSHOT_BYTES, SNAPSHOT_SECONDS,
    AUTH_FIELDS, OWNER_FIELDS, PROVIDER_FIELDS, ACCOUNT_FIELDS, _encoded, _id,
)

CHOICE_ACCOUNT_FIELDS = {**ACCOUNT_FIELDS, "display_name": 1}
ROUTE_SECONDS = 15


def _refuse(code="google_merchant_choices_unproved", status=409):
    raise problem(code, "تعذر التحقق من قائمة الحسابات المحفوظة. راجع الربط ثم أعد تحديث القائمة.", status) from None


@dataclass(frozen=True, repr=False)
class _Choices:
    signature: tuple
    authenticated_owner: tuple
    candidates: tuple


async def _snapshot(db, owner_id):
    """Complete bounded local set, without filtering away malformed/stale rows."""
    try:
        with timeout(SNAPSHOT_SECONDS):
            async with asyncio.timeout(SNAPSHOT_SECONDS + 1):
                async with await db.client.start_session() as session:
                    async with session.start_transaction(read_concern=ReadConcern("snapshot"),
                            read_preference=ReadPreference.PRIMARY,
                            write_concern=WriteConcern(w="majority", wtimeout=2000), max_commit_time_ms=2000):
                        async def rows(name, query, fields, limit):
                            return await db[name].find(query, fields, session=session).max_time_ms(2000).limit(limit).to_list(length=limit)
                        owners = await rows("users", {"id": owner_id}, OWNER_FIELDS, 2)
                        query = {"user_id": owner_id, "provider": {"$in": list(PROVIDERS)}}
                        providers = await rows("mezan_integrations_v2", query, PROVIDER_FIELDS, 5)
                        accounts = await rows("mezan_integration_accounts_v2", query, CHOICE_ACCOUNT_FIELDS, MAX_ACCOUNTS + 1)
        if len(owners) != 1:
            _refuse("google_merchant_choices_owner_unproved", 403)
        owner = owners[0]
        if ("_id" not in owner or owner.get("id") != owner_id or owner.get("role") != "owner"
            or owner.get("disabled") is not None and owner["disabled"] is not False
            or owner.get("is_active") is not None and owner["is_active"] is not True
            or owner.get("deleted_at") not in (None, "")):
            _refuse("google_merchant_choices_owner_unproved", 403)
        if (len(providers) != 2 or len(accounts) > MAX_ACCOUNTS
            or sorted(row.get("provider", "") for row in providers) != sorted(PROVIDERS)):
            _refuse()
        for row in providers:
            if ("_id" not in row or row.get("user_id") != owner_id
                or row.get("connection_status") != "connected" or row.get("connection_provenance") != "api_connection"):
                _refuse()
        identities, candidates = set(), []
        for row in accounts:
            provider, external = row.get("provider"), _id(row.get("external_account_id"))
            if ("_id" not in row or row.get("user_id") != owner_id or provider not in PROVIDERS
                or row.get("ad_account_id") not in (None, external)
                or provider == MERCHANT and row.get("ad_account_id") is not None
                or row.get("connection_status") != "connected" or row.get("connection_provenance") != "api_connection"
                or (provider, external) in identities):
                _refuse()
            if row.get("login_customer_id") is not None:
                _id(row["login_customer_id"])
            name = row.get("display_name")
            if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 256
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                _refuse()
            identities.add((provider, external))
            candidates.append((provider, external, name or external))
        signature = (_encoded(owners), _encoded(providers), _encoded(accounts))
        # This is a post-materialization bound, not a peak database-memory cap.
        if sum(len(value) for group in signature for value in group) > MAX_SNAPSHOT_BYTES:
            _refuse()
        return _Choices(signature, _encoded([{key: owner[key] for key in AUTH_FIELDS if key in owner}]),
            tuple(sorted(candidates)))
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and str(exc.detail.get("code", "")).startswith("google_merchant_choices_"):
            raise
        _refuse()
    except Exception:
        _refuse("google_merchant_choices_unavailable")


async def read_owner_merchant_choices(db, user):
    actor = await principal(db, user, write=False)
    if actor.get("owner") is not True or actor.get("actor_id") != actor.get("user_id"):
        _refuse("google_ads_owner_required", 403)
    owner_id = actor["user_id"]
    try:
        async with asyncio.timeout(ROUTE_SECONDS):
            before = await _snapshot(db, owner_id)
            if before.authenticated_owner != _encoded([{key: user[key] for key in AUTH_FIELDS if key in user}]):
                _refuse("google_merchant_choices_owner_changed", 403)
            after = await _snapshot(db, owner_id)
            if before != after:
                _refuse("google_merchant_choices_changed")
            return {
                "source": "mezan_google_local_membership", "owner_id": owner_id,
                "observation_id": str(uuid.uuid4()), "observed_at": datetime.now(timezone.utc).isoformat(),
                "local_membership": "matching_complete_bracketing_snapshots",
                "local_set_complete": True, "local_account_count": len(after.candidates),
                "ads_accounts": [{"account_id": identity, "display_name": name}
                    for provider, identity, name in after.candidates if provider == ADS],
                "merchant_accounts": [{"merchant_id": identity, "display_name": name}
                    for provider, identity, name in after.candidates if provider == MERCHANT],
                "remote_account_set_complete": False, "native_link_verified": False,
                "merchant_access_verified": False, "continuous_authorization_proved": False,
                "store_product_mapping_verified": False, "ads_eligibility_verified": False,
                "execution_authorized": False, "provider_calls": 0, "provider_writes": 0,
            }
    except HTTPException:
        raise
    except Exception:
        _refuse("google_merchant_choices_unavailable")


def attach_google_merchant_observation_choices(router, db, current_user):
    @router.get("/google_ads/merchant-products/choices")
    async def choices(request: Request, user=Depends(current_user)):
        if request.query_params:
            _refuse("google_merchant_choices_parameters_invalid", 422)
        return await read_owner_merchant_choices(db, user)
