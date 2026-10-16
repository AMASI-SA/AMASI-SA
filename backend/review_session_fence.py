"""Bounded login-family authority for review acceptance and acknowledged logout.

This is not a revocation claim for an offline/local logout. Review commit and
server revocation write the SAME row; a read of an active JWT is not a fence.
Legacy signed tokens keep their existing access but cannot authorize Review V2.
"""
from datetime import datetime, timedelta, timezone
import re
import secrets

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

SESSIONS = "order_review_auth_sessions_v2"
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _now():
    return datetime.now(timezone.utc)


async def merchant_for_actor(db, actor):
    if str(actor.get("role") or "").strip().lower() == "owner" or actor.get("is_owner") is True:
        return str(actor.get("id") or "")
    # Reuse the native principal's existing trusted employee-registry fallback.
    # Unlinked accounts may still log in, but cannot claim another merchant.
    from mobile_app_request_context import _linked_owner_id
    return await _linked_owner_id(db, actor) or str(actor.get("id") or "")


def _collection(db):
    return db[SESSIONS].with_options(
        write_concern=WriteConcern("majority", j=True),
        read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY,
    )


async def ensure_review_session_storage(db):
    """Required before readiness; no transaction may create this namespace."""
    collection = _collection(db)
    await collection.create_index("_id", name="_id_")
    await collection.create_index("expires_at", expireAfterSeconds=0, name="review_session_expiry")


def claims_for_session(context):
    if context is None:
        return {}
    return {"review_sid": context["origin_session_ref"],
            "review_epoch": context["origin_session_epoch"],
            "review_owner": context["merchant_id"]}


def session_from_claims(payload, actor):
    present = {key for key in ("review_sid", "review_epoch", "review_owner") if key in payload}
    if not present:
        return None
    sid, epoch, owner = payload.get("review_sid"), payload.get("review_epoch"), payload.get("review_owner")
    if (len(present) != 3 or not isinstance(sid, str) or not _REFERENCE.fullmatch(sid)
            or type(epoch) is not int or epoch != 1 or not isinstance(owner, str) or not owner
            or payload.get("sub") != actor.get("id")):
        raise HTTPException(401, detail={"code": "review_session_invalid"})
    return {"origin_session_ref": sid, "origin_session_epoch": epoch,
            "merchant_id": owner, "actor_id": str(actor["id"])}


def _identity(context):
    return {"_id": context["origin_session_ref"], "user_id": context["merchant_id"],
            "actor_id": context["actor_id"], "epoch": context["origin_session_epoch"]}


async def create_review_session(db, actor):
    """Only call after completed password/OTP/MFA authentication, never refresh."""
    from auth import REFRESH_TOKEN_TTL
    now = _now()
    context = {"origin_session_ref": secrets.token_urlsafe(32), "origin_session_epoch": 1,
               "merchant_id": await merchant_for_actor(db, actor), "actor_id": str(actor["id"])}
    await _collection(db).insert_one({**_identity(context), "state": "active", "fence": 0,
                                      "created_at": now,
                                      "expires_at": now + REFRESH_TOKEN_TTL + timedelta(minutes=1)})
    return context


async def validate_review_session(db, payload, actor, *, refresh=False):
    context = session_from_claims(payload, actor)
    if context is None:
        return None
    if context["merchant_id"] != await merchant_for_actor(db, actor):
        raise HTTPException(401, detail={"code": "review_session_invalid"})
    query = {**_identity(context), "state": "active", "expires_at": {"$gt": _now()}}
    if refresh:
        from auth import REFRESH_TOKEN_TTL
        # This conditional write serializes refresh with revoke. If refresh
        # wins first, subsequently minted JWTs still reference the revoked row;
        # if revoke wins first, refresh cannot reactivate or replace the family.
        result = await _collection(db).update_one(query, {
            "$inc": {"fence": 1},
            "$max": {"expires_at": _now() + REFRESH_TOKEN_TTL + timedelta(minutes=1)},
        })
        valid = result.matched_count == 1
    else:
        valid = await _collection(db).find_one(query, {"_id": 1}) is not None
    if not valid:
        raise HTTPException(401, detail={"code": "review_session_revoked"})
    return context


async def review_session_context(db, *, user_id, actor):
    context = actor.get("_review_session")
    if not isinstance(context, dict):
        raise HTTPException(409, detail={"code": "review_session_reauthentication_required"})
    if (context.get("merchant_id") != user_id or context.get("actor_id") != str(actor.get("id") or "")
            or await merchant_for_actor(db, actor) != user_id):
        raise HTTPException(409, detail={"code": "review_session_identity_mismatch"})
    # Validate shape again even for server-created request contexts. This read
    # verifies display/request context only; completion MUST also do the write.
    payload = {"sub": actor["id"], **claims_for_session(context)}
    await validate_review_session(db, payload, actor)
    return {"origin_session_ref": context["origin_session_ref"],
            "origin_session_epoch": context["origin_session_epoch"]}


async def fence_review_session(scoped, *, user_id, actor_id, origin_session_ref, origin_session_epoch):
    result = await scoped[SESSIONS].update_one({
        "_id": origin_session_ref, "user_id": user_id, "actor_id": actor_id,
        "epoch": origin_session_epoch, "state": "active", "expires_at": {"$gt": _now()},
    }, {"$inc": {"fence": 1}})
    if result.matched_count != 1:
        raise HTTPException(409, detail={"code": "review_session_revoked"})


async def revoke_review_session(db, actor):
    """Acknowledge ONLY a majority+journaled revoke of this exact login family."""
    context = actor.get("_review_session")
    if context is None:
        # Legacy JWTs have no server family. Clearing cookies is not revocation.
        return {"server_revocation_confirmed": False, "revocation_status": "legacy_local_only"}
    payload = {"sub": actor.get("id"), **claims_for_session(context)}
    context = session_from_claims(payload, actor)
    if context["merchant_id"] != await merchant_for_actor(db, actor):
        raise HTTPException(401, detail={"code": "review_session_invalid"})
    result = await _collection(db).update_one(
        {**_identity(context), "state": {"$in": ["active", "revoked"]}},
        {"$set": {"state": "revoked", "revoked_at": _now()}, "$inc": {"fence": 1}},
    )
    if not result.acknowledged or result.matched_count != 1:
        raise HTTPException(503, detail={"code": "review_session_revocation_unconfirmed"})
    return {"server_revocation_confirmed": True, "revocation_status": "revoked",
            "origin_session_ref": context["origin_session_ref"]}
