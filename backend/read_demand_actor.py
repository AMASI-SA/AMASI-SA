"""Rebuild a queued read's actor from current grants, never saved private flags."""
from hashlib import sha256
import json

from fastapi import HTTPException

from auth import account_is_disabled


_ACTOR_FIELDS = (
    "id", "role", "is_owner", "created_by", "disabled", "is_active", "deleted_at",
    "review_access_expires_at", "review_scopes", "web_page_access", "extra_permissions",
    "denied_permissions", "accounting_permissions", "password_updated_at", "session_version",
)


def identity(user):
    actor = str(user.get("_mobile_actor_id") or user.get("id") or "").strip()
    client = "mobile" if user.get("_session_client") == "amasi_mobile" else "web"
    if not actor:
        raise HTTPException(403, detail={"code": "read_actor_required"})
    return actor, client


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str, allow_nan=False).encode()).hexdigest()


async def fresh_actor(db, *, actor_id, client_kind, path, query):
    if client_kind not in {"web", "mobile"} or not isinstance(actor_id, str) or not actor_id:
        raise HTTPException(403, detail={"code": "read_actor_required"})
    actor = await db.users.find_one({"id": actor_id}, {"_id": 0, **dict.fromkeys(_ACTOR_FIELDS, 1)})
    if actor is None or account_is_disabled(actor):
        raise HTTPException(403, detail={"code": "read_actor_unavailable"})
    # The session client was derived from the authenticated request when the
    # server built this spec. Persisted user fields cannot provide private flags.
    principal = {key: value for key, value in actor.items() if not key.startswith("_")}
    principal["_session_client"] = "amasi_mobile" if client_kind == "mobile" else None
    if client_kind == "web":
        from web_page_access import bind_workspace_reader, enforce_web_catalog_access
        enforce_web_catalog_access(principal, path, "GET", query)
        await bind_workspace_reader(db, principal, path, "GET", query)
    else:
        from mobile_app_request_context import mobile_app_request_user
        principal = await mobile_app_request_user(db, principal, path=path, method="GET")
    return principal, {key: actor.get(key) for key in _ACTOR_FIELDS}


async def require_owner_available(db, owner_id):
    owner = await db.users.find_one({"id": owner_id, "role": "owner"},
                                    {"_id": 0, **dict.fromkeys(_ACTOR_FIELDS, 1)})
    if owner is None or account_is_disabled(owner):
        raise HTTPException(403, detail={"code": "read_owner_unavailable"})
    return {key: owner.get(key) for key in _ACTOR_FIELDS}


def permission_fingerprint(actor_basis, owner_basis, principal, effective):
    return digest({
        "actor": actor_basis, "owner": owner_basis,
        "workspace": principal.get("_web_workspace_read"),
        "mobile_permissions": principal.get("_mobile_app_permissions"),
        "effective": effective,
    })


async def release_current(db, user, key, consumer_id):
    from demand_read_store import InvalidReadSpec, release_by_key

    actor_id, _client = identity(user)
    scope = user.get("_web_workspace_read")
    owner_id = (user.get("_mobile_owner_id") or (scope[1] if scope else None)
                or (user.get("id") if str(user.get("role") or "").strip().casefold() == "owner"
                    else user.get("created_by")))
    if not owner_id:
        raise HTTPException(403, detail={"code": "read_owner_required"})
    try:
        await release_by_key(db, key, actor_id, consumer_id, owner_id=str(owner_id))
    except InvalidReadSpec as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    return {"released": True}
