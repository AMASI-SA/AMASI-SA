"""Saved assembly display rows built only by a demand job process.

No saved row authorizes a preparation/ready/printing action. Those handlers keep
their existing fresh canonical checks and are deliberately outside this module.
"""
import asyncio
from datetime import datetime, timezone
import time
from uuid import uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from read_demand_actor import fresh_actor, identity, permission_fingerprint, require_owner_available


VERSION = "assembly-board-v1"
CACHE_SECONDS = 15
PATH = "/api/preparation-work-v1/assembly/orders"
STATES = frozenset({"in_progress", "completed", "direct_preparation", "supplier_received"})


def _filters(state, query):
    if state not in STATES or not isinstance(query, str) or len(query) > 64:
        raise HTTPException(422, detail={"code": "assembly_board_state_invalid"})
    return {"state": state, "q": query.strip().removeprefix("#").strip()}


async def _authorize(db, *, actor_id, client_kind, filters):
    from fulfillment_v2_routes import _actor_context, _require_permission

    principal, actor_basis = await fresh_actor(
        db, actor_id=actor_id, client_kind=client_kind, path=PATH, query=filters,
    )
    context = await _actor_context(db, principal)
    _require_permission(context, "fulfillment.ready.read", responsibility="instant_ready")
    owner_id = context["merchant_id"]
    owner_basis = await require_owner_available(db, owner_id)
    effective = {key: sorted(value) if isinstance(value, set) else value for key, value in context.items()}
    return owner_id, permission_fingerprint(actor_basis, owner_basis, principal, effective)


async def make_spec(db, *, user, state, query=""):
    filters = _filters(state, query)
    actor_id, client_kind = identity(user)
    owner_id, fingerprint = await _authorize(db, actor_id=actor_id, client_kind=client_kind, filters=filters)
    return {
        "family": "assembly_board", "owner_id": owner_id, "actor_id": actor_id,
        "client_kind": client_kind, "permission_fingerprint": fingerprint,
        "request": {"path": PATH, "method": "GET", "query": filters},
        "source_revision": {"freshness": "dated_display"},
        "resource": "assembly_board", "currency": None, "period": None, "filters": filters,
        "projection_version": VERSION, "cache_seconds": CACHE_SECONDS,
    }


async def authorize_spec(db, spec):
    filters = spec.get("filters")
    if (spec.get("family") != "assembly_board" or spec.get("projection_version") != VERSION
            or spec.get("cache_seconds") != CACHE_SECONDS or not isinstance(filters, dict)
            or set(filters) != {"state", "q"}
            or filters != _filters(filters.get("state"), filters.get("q"))
            or spec.get("request") != {"path": PATH, "method": "GET", "query": filters}
            or spec.get("source_revision") != {"freshness": "dated_display"}):
        raise HTTPException(409, detail={"code": "read_projection_invalid"})
    owner_id, fingerprint = await _authorize(
        db, actor_id=spec["actor_id"], client_kind=spec["client_kind"], filters=filters,
    )
    if owner_id != spec["owner_id"] or fingerprint != spec["permission_fingerprint"]:
        raise HTTPException(403, detail={"code": "read_permission_changed"})
    return True


async def build_projection(db, spec, checkpoint):
    from preparation_piece_operations import _assembly_order_board

    await checkpoint()
    # The canonical board admits at most 5,000 workflows. Materialize the same
    # ordered result once; HTTP pages never hydrate the complete cohort again.
    result = await _assembly_order_board(
        db, user_id=spec["owner_id"], state=spec["filters"]["state"],
        query=spec["filters"]["q"], limit=5000, offset=0,
    )
    await checkpoint()
    return {"header": {key: value for key, value in result.items() if key not in {"items", "limit", "offset"}},
            "rows": result["items"]}


def _response(ready, *, offset, limit):
    generated = ready["generated_at"]
    if isinstance(generated, str):
        generated = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return JSONResponse(content=jsonable_encoder({**ready["header"], "items": ready["items"],
        "offset": offset, "limit": limit, "snapshot": ready["snapshot"],
        "generated_at": generated.isoformat(), "display_freshness_seconds": CACHE_SECONDS,
    }), headers={"Cache-Control": "no-store, private"})


async def demand_response(db, *, user, state, query, offset, limit, consumer_id,
                          refresh=False, snapshot=None, request=None, compatible=False):
    from demand_read_store import InvalidReadSpec, SnapshotChanged, demand, read_ready, release

    spec = await make_spec(db, user=user, state=state, query=query)
    consumer_id = ("http-" + uuid4().hex) if compatible else consumer_id
    # Existing clients understand only the original 200/error contract. Wait
    # while that HTTP request is alive without adding a shorter server deadline
    # which would cancel every cold attempt before it can finish. Client timeouts
    # are unchanged; the queue's hard job limit still bounds child computation.
    next_renew = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_renew:
                status = await demand(db, spec, consumer_id, refresh=refresh, retry=refresh)
                refresh = False
                next_renew = now + 10.0
                if status["status"] == "failed":
                    raise HTTPException(503, detail={"code": status.get("error_code", "read_preparation_failed"), "retryable": True})
            ready = await read_ready(db, spec, offset=offset, limit=limit, snapshot=snapshot)
            if ready is not None:
                await authorize_spec(db, spec)
                return _response(ready, offset=offset, limit=limit)
            if not compatible:
                return JSONResponse(status_code=202, content={"status": "preparing", "key": status["key"],
                    "retry_after_ms": 1500, "release_url": "/preparation-work-v1/assembly/read-demand/" + status["key"]},
                    headers={"Cache-Control": "no-store, private"})
            if request is not None and await request.is_disconnected():
                raise HTTPException(499, detail={"code": "read_consumer_disconnected"})
            await asyncio.sleep(0.5)
    except InvalidReadSpec as exc:
        raise HTTPException(422, detail={"code": str(exc)}) from exc
    except SnapshotChanged as exc:
        raise HTTPException(409, detail={"code": "read_snapshot_changed"}) from exc
    finally:
        if compatible:
            try:
                await release(db, spec, consumer_id)
            except Exception:
                # A failed release expires through the short consumer lease;
                # it cannot replace the original read result/error.
                pass
