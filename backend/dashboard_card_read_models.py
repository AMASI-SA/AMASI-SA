"""Demand-only saved cards; canonical computation belongs to a job process."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from dashboard_card_builder import CARD_TTLS, prepare_dashboard_card, build_dashboard_card
from read_demand_actor import (
    digest, fresh_actor, identity, permission_fingerprint, require_owner_available,
)


VERSION = "dashboard-card-v1"
_FILTERS = ("from_date", "to_date", "payment_methods", "shipping_companies", "report_statuses")


def _query(filters):
    return {key: filters[key] for key in _FILTERS if filters.get(key) is not None}


async def _authorize(db, *, actor_id, client_kind, section, filters):
    if section not in CARD_TTLS or set(filters) - set(_FILTERS):
        raise HTTPException(422, detail={"code": "read_projection_invalid"})
    path = "/api/dashboard-v2/cards/" + section
    principal, actor_basis = await fresh_actor(
        db, actor_id=actor_id, client_kind=client_kind, path=path, query=_query(filters),
    )
    scope = principal.get("_web_workspace_read")
    if scope and scope[0] in {"home.dashboard", "home.summary", "home.charts", "products.sold"}:
        owner_id = scope[1]
    elif str(principal.get("role") or "").strip().casefold() == "owner":
        owner_id = str(principal["id"])
    else:
        raise HTTPException(403, detail={"code": "dashboard_owner_required"})
    owner_basis = await require_owner_available(db, owner_id)
    fingerprint = permission_fingerprint(actor_basis, owner_basis, principal, {"dashboard_read": True})
    return owner_id, fingerprint


async def make_spec(db, *, user, section, filters, binding=None):
    actor_id, client_kind = identity(user)
    owner_id, fingerprint = await _authorize(
        db, actor_id=actor_id, client_kind=client_kind, section=section, filters=filters,
    )
    binding = binding or await prepare_dashboard_card(db, owner_id=owner_id, section=section, **filters)
    if binding["key"][1] != owner_id:
        raise HTTPException(403, detail={"code": "read_owner_changed"})
    normalized = _query(binding["filters"])
    return {
        "family": "dashboard_card", "owner_id": owner_id, "actor_id": actor_id,
        "client_kind": client_kind, "permission_fingerprint": fingerprint,
        "request": {"path": "/api/dashboard-v2/cards/" + section, "method": "GET", "query": normalized},
        # This binds the existing known cache inputs. Unversioned order/ad/
        # operating inputs remain explicitly bounded by the existing card TTL.
        "source_revision": {"known_binding": digest(binding["key"]), "freshness": "dated_display"},
        "resource": section, "currency": "SAR",
        "period": [normalized.get("from_date"), normalized.get("to_date")],
        "filters": normalized, "projection_version": VERSION, "cache_seconds": CARD_TTLS[section],
    }


async def authorize_spec(db, spec):
    section, filters = spec.get("resource"), spec.get("filters")
    if (spec.get("family") != "dashboard_card" or spec.get("projection_version") != VERSION
            or section not in CARD_TTLS or not isinstance(filters, dict)
            or spec.get("cache_seconds") != CARD_TTLS[section]
            or spec.get("request") != {"path": "/api/dashboard-v2/cards/" + section,
                                       "method": "GET", "query": _query(filters)}):
        raise HTTPException(409, detail={"code": "read_projection_invalid"})
    owner_id, fingerprint = await _authorize(
        db, actor_id=spec["actor_id"], client_kind=spec["client_kind"], section=section, filters=filters,
    )
    if owner_id != spec["owner_id"] or fingerprint != spec["permission_fingerprint"]:
        raise HTTPException(403, detail={"code": "read_permission_changed"})
    binding = await prepare_dashboard_card(db, owner_id=owner_id, section=section, **filters)
    if spec.get("source_revision") != {"known_binding": digest(binding["key"]), "freshness": "dated_display"}:
        raise HTTPException(409, detail={"code": "read_source_changed"})
    return True


async def build_projection(db, spec, checkpoint):
    from legacy_dashboard_reader import dashboard

    await checkpoint()
    binding = await prepare_dashboard_card(db, owner_id=spec["owner_id"], section=spec["resource"], **spec["filters"])

    async def legacy(**options):
        return await dashboard(**{**options, "database": db, "owner_id": spec["owner_id"],
                                  "allow_self_heal": False, "persist_settings": False})

    result = await build_dashboard_card(db, legacy, owner_id=spec["owner_id"],
                                       section=spec["resource"], binding=binding, read_only=True)
    await checkpoint()
    return {"header": result, "rows": None}


async def demand_response(db, *, user, section, binding, consumer_id, refresh=False, retry_failed=True):
    from background_worker_process import wake_read_projections
    from demand_read_store import InvalidReadSpec, demand, read_ready

    spec = await make_spec(db, user=user, section=section, filters=_query(binding["filters"]), binding=binding)
    try:
        state = await demand(db, spec, consumer_id, refresh=refresh, retry=refresh or retry_failed)
    except InvalidReadSpec as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    if state["status"] == "failed":
        raise HTTPException(503, detail={"code": state.get("error_code", "read_preparation_failed"), "retryable": True})
    if state["status"] == "preparing":
        await wake_read_projections()
    ready = await read_ready(db, spec) if state["status"] == "ready" else None
    if ready is None:
        return JSONResponse(status_code=202, content={**state, "status": "preparing", "retry_after_ms": 1500,
            "release_url": "/dashboard-v2/read-demand/" + state["key"]}, headers={"Cache-Control": "no-store, private"})
    await authorize_spec(db, spec)
    generated = ready["generated_at"]
    if isinstance(generated, str):
        generated = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return JSONResponse(content=jsonable_encoder({**ready["header"], "snapshot": {
        "generated_at": generated.isoformat(),
        "next_refresh_at": (generated + timedelta(seconds=CARD_TTLS[section])).isoformat(),
        "refresh_interval_seconds": CARD_TTLS[section], "kind": "dated_display",
    }}), headers={"Cache-Control": "no-store, private"})
