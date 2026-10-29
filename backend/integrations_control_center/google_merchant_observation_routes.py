"""Owner-requested native Merchant observations; deliberately not attached.

Local membership and native links bracket a read, not uninterrupted authority.
No account selection, connection repair, product mapping or campaign execution.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from bson import BSON
from fastapi import Depends, HTTPException, Query
from pymongo import ReadPreference, timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_ads_client import problem
from .google_ads_management import principal
from .google_ads_reporting import GoogleBoundReadError, google_bound_read_session
from .google_merchant_product_observation import (
    MerchantObservationError, MerchantObservationScope, read_merchant_product_observation,
)

ADS = "google_ads"
MERCHANT = "google_merchant_center"
PROVIDERS = (ADS, MERCHANT)
MAX_ACCOUNTS = 400
MAX_LINKS = 200
SNAPSHOT_SECONDS = 5
ROUTE_SECONDS = 115
MAX_SNAPSHOT_BYTES = 256 * 1024
LINK_QUERY = (
    "SELECT customer.id, customer.resource_name, product_link.resource_name, "
    "product_link.product_link_id, product_link.type, product_link.merchant_center.merchant_center_id "
    "FROM product_link WHERE product_link.type = 'MERCHANT_CENTER' LIMIT 201"
)
AUTH_FIELDS = ("id", "role", "disabled", "is_active", "deleted_at", "password_updated_at")
OWNER_FIELDS = dict.fromkeys(("_id", *AUTH_FIELDS, "updated_at", "created_by"), 1)
PROVIDER_FIELDS = dict.fromkeys(("_id", "user_id", "provider", "connection_status", "connection_provenance",
    "source_mode", "permission_observation_id", "permissions_observed", "updated_at", "created_at",
    "checked_at", "capability_evidence"), 1)
ACCOUNT_FIELDS = dict.fromkeys(("_id", "user_id", "provider", "mezan_integration_account_id",
    "external_account_id", "ad_account_id", "login_customer_id", "currency", "timezone",
    "connection_status", "connection_provenance", "source_mode", "permissions", "permissions_observed",
    "capability_evidence", "last_observed_at", "created_at", "updated_at", "mezan_selected"), 1)


def _refuse(code="google_merchant_membership_unproved", status=409):
    raise problem(code, "تعذر التحقق من الحساب والربط الحاليين. حدّث الاتصال ثم أعد القراءة.", status) from None


def _id(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
        or int(value) > 9223372036854775807):
        _refuse("google_merchant_identity_invalid", 422)
    return value


def _ordered(value):
    if isinstance(value, dict):
        return {key: _ordered(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


def _encoded(rows):
    # Document identity and missing/null/type distinctions are part of the
    # private fence; no account/owner snapshot is returned to the client.
    result = sorted(BSON.encode(_ordered(row)) for row in rows)
    if sum(map(len, result)) > MAX_SNAPSHOT_BYTES:
        _refuse()
    return tuple(result)


@dataclass(frozen=True, repr=False)
class _Membership:
    owner: tuple
    authenticated_owner: tuple
    providers: tuple
    accounts: tuple
    ads_account: dict


async def _membership(db, owner_id, account_id, merchant_id):
    """One bounded consistent local snapshot, no fallback or application retry."""
    try:
        with timeout(SNAPSHOT_SECONDS):
            async with asyncio.timeout(SNAPSHOT_SECONDS + 1):
                async with await db.client.start_session() as session:
                    async with session.start_transaction(read_concern=ReadConcern("snapshot"),
                            read_preference=ReadPreference.PRIMARY,
                            write_concern=WriteConcern(w="majority", wtimeout=2000), max_commit_time_ms=2000):
                        async def rows(name, query, fields, limit):
                            return await db[name].find(query, fields, session=session).max_time_ms(2000).limit(limit).to_list(length=limit)
                        owner_rows = await rows("users", {"id": owner_id}, OWNER_FIELDS, 2)
                        query = {"user_id": owner_id, "provider": {"$in": list(PROVIDERS)}}
                        provider_rows = await rows("mezan_integrations_v2", query, PROVIDER_FIELDS, 5)
                        account_rows = await rows("mezan_integration_accounts_v2", query, ACCOUNT_FIELDS, MAX_ACCOUNTS + 1)
        if len(owner_rows) != 1:
            _refuse("google_merchant_owner_unproved", 403)
        owner = owner_rows[0]
        if (owner.get("id") != owner_id or owner.get("role") != "owner"
            or owner.get("disabled") is not None and owner["disabled"] is not False
            or owner.get("is_active") is not None and owner["is_active"] is not True
            or owner.get("deleted_at") not in (None, "")):
            _refuse("google_merchant_owner_unproved", 403)
        if len(provider_rows) != 2 or len(account_rows) > MAX_ACCOUNTS:
            _refuse()
        if sorted(row.get("provider", "") for row in provider_rows) != sorted(PROVIDERS):
            _refuse()
        for row in provider_rows:
            if (row.get("user_id") != owner_id or row.get("connection_status") != "connected"
                or row.get("connection_provenance") != "api_connection"):
                _refuse()
        identities, selected = set(), {}
        for row in account_rows:
            provider, external = row.get("provider"), _id(row.get("external_account_id"))
            if (row.get("user_id") != owner_id or provider not in PROVIDERS
                or row.get("ad_account_id") not in (None, external)
                or provider == MERCHANT and row.get("ad_account_id") is not None):
                _refuse()
            if (provider, external) in identities:
                _refuse()
            identities.add((provider, external))
            if external == (account_id if provider == ADS else merchant_id):
                if row.get("connection_status") != "connected" or row.get("connection_provenance") != "api_connection":
                    _refuse()
                selected[provider] = row
        if set(selected) != set(PROVIDERS):
            _refuse()
        ads = selected[ADS]
        if ads.get("login_customer_id") is not None:
            _id(ads["login_customer_id"])
        return _Membership(_encoded(owner_rows), _encoded([{key: owner[key] for key in AUTH_FIELDS if key in owner}]),
            _encoded(provider_rows), _encoded(account_rows),
            {key: ads[key] for key in ("ad_account_id", "external_account_id", "login_customer_id") if key in ads})
    except HTTPException:
        raise
    except Exception:
        _refuse("google_merchant_membership_unavailable")


async def _same_membership(db, before, owner_id, account_id, merchant_id):
    after = await _membership(db, owner_id, account_id, merchant_id)
    if after != before:
        _refuse("google_merchant_membership_changed")


async def _native_evidence(bound, account_id, merchant_id):
    metadata = await bound.account_metadata()
    if (not isinstance(metadata, dict) or metadata.get("account_id") != account_id
        or metadata.get("customer_id") != account_id or metadata.get("identity_verified") is not True
        or not isinstance(metadata.get("currency_code"), str) or not re.fullmatch(r"[A-Z]{3}", metadata["currency_code"])
        or not isinstance(metadata.get("time_zone"), str) or not 1 <= len(metadata["time_zone"]) <= 128):
        _refuse("google_merchant_ads_identity_unproved")
    rows, complete = await bound.search(LINK_QUERY, max_rows=MAX_LINKS)
    if complete is not True or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_LINKS:
        _refuse("google_merchant_native_links_incomplete")
    links, merchants = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"customer", "productLink"}:
            _refuse("google_merchant_native_link_unproved")
        customer, link = row["customer"], row["productLink"]
        if (not isinstance(customer, dict) or customer != {"id": account_id, "resourceName": "customers/" + account_id}
            or not isinstance(link, dict) or set(link) != {"resourceName", "productLinkId", "type", "merchantCenter"}
            or link.get("type") != "MERCHANT_CENTER" or not isinstance(link.get("merchantCenter"), dict)
            or set(link["merchantCenter"]) != {"merchantCenterId"}):
            _refuse("google_merchant_native_link_unproved")
        try:
            link_id, merchant = _id(link["productLinkId"]), _id(link["merchantCenter"]["merchantCenterId"])
        except HTTPException:
            _refuse("google_merchant_native_link_unproved")
        resource = f"customers/{account_id}/productLinks/{link_id}"
        if link["resourceName"] != resource or resource in {item[0] for item in links} or merchant in merchants:
            _refuse("google_merchant_native_link_unproved")
        links.add((resource, link_id, merchant))
        merchants.add(merchant)
    if merchant_id not in merchants:
        _refuse("google_merchant_native_link_missing")
    return ((account_id, metadata["currency_code"], metadata["time_zone"]), tuple(sorted(links)))


async def read_owner_merchant_observation(db, user, *, account_id, merchant_id):
    """Only authenticated server user input; no caller verified flags accepted."""
    actor = await principal(db, user, write=False)
    if actor.get("owner") is not True or actor.get("actor_id") != actor.get("user_id"):
        _refuse("google_ads_owner_required", 403)
    owner_id = actor["user_id"]
    account_id, merchant_id = _id(account_id), _id(merchant_id)
    try:
        async with asyncio.timeout(ROUTE_SECONDS):
            before = await _membership(db, owner_id, account_id, merchant_id)
            # current_user already verified the session against this owner
            # document. Reject revocation between that read and this snapshot,
            # even when two later local snapshots would agree with each other.
            if before.authenticated_owner != _encoded([{key: user[key] for key in AUTH_FIELDS if key in user}]):
                _refuse("google_merchant_owner_changed", 403)
            async with google_bound_read_session(db, owner_id, ads_account=before.ads_account, merchant_id=merchant_id) as bound:
                await _same_membership(db, before, owner_id, account_id, merchant_id)
                native_before = await _native_evidence(bound, account_id, merchant_id)
                observation = await read_merchant_product_observation(bound,
                    scope=MerchantObservationScope(owner_id, account_id, merchant_id))
                native_after = await _native_evidence(bound, account_id, merchant_id)
                if native_before != native_after:
                    _refuse("google_merchant_native_link_changed")
                await _same_membership(db, before, owner_id, account_id, merchant_id)
                await bound.check_current()
                # No result escapes until the context's final credential fence.
                result = observation.as_public_dict()
                result["access_observation"] = {
                    "ads_customer_id": account_id, "merchant_id": merchant_id,
                    "credential_evidence_id": bound.evidence_id,
                    "native_link_resource": next(row[0] for row in native_after[1] if row[2] == merchant_id),
                    "local_membership": "matching_bracketing_snapshots",
                    "native_link_set": "matching_complete_bracketing_reads",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "continuous_authorization_proved": False,
                }
            return result
    except HTTPException:
        raise
    except (GoogleBoundReadError, MerchantObservationError):
        _refuse("google_merchant_observation_read_unproved")
    except Exception:
        _refuse("google_merchant_observation_unavailable")


def attach_google_merchant_observation_routes(router, db, current_user):
    """Root integration must explicitly attach this reviewed read-only route."""
    @router.get("/google_ads/merchant-products/observation")
    async def observation(account_id: str = Query(min_length=1, max_length=19),
            merchant_id: str = Query(min_length=1, max_length=19), user=Depends(current_user)):
        return await read_owner_merchant_observation(db, user, account_id=account_id, merchant_id=merchant_id)
