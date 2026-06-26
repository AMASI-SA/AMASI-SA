"""Qoyod Migration HTTP routes — read-only reconciliation surface.

Mounted under `/api/integrations/qoyod/migration/*` by the main router.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
from fastapi.responses import StreamingResponse

from integrations.qoyod.api_client import QoyodAPIClient
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.migration import (
    run_migration, latest_run, confirm_candidate,
)


def attach_migration_routes(router: APIRouter, db, current_user, tenant_of):
    """Mount migration endpoints on the given parent router."""

    async def _client_for(tenant: str) -> QoyodAPIClient:
        key = await get_api_key(db, tenant)
        if not key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "credentials_missing",
                        "message": "Qoyod API key not configured"})
        return QoyodAPIClient(key)

    # ── POST /migration/run ─────────────────────────────────────────
    @router.post("/migration/run")
    async def migration_run(user=Depends(current_user)):
        tenant = tenant_of(user)
        client = await _client_for(tenant)
        try:
            result = await run_migration(
                db, user_id=tenant, api_client=client)
        finally:
            await client.aclose() if hasattr(client, "aclose") else None
        return {"ok": result["status"] == "completed", **result}

    # ── GET /migration/status ───────────────────────────────────────
    @router.get("/migration/status")
    async def migration_status(user=Depends(current_user)):
        tenant = tenant_of(user)
        doc = await latest_run(db, user_id=tenant)
        return {"ok": True, "run": doc}

    # ── GET /migration/report ───────────────────────────────────────
    @router.get("/migration/report")
    async def migration_report(user=Depends(current_user)):
        tenant = tenant_of(user)
        doc = await latest_run(db, user_id=tenant)
        if not doc:
            return {"ok": True, "report": None}
        return {"ok": True, "report": doc.get("summary"),
                "run_id": doc.get("run_id"),
                "status": doc.get("status"),
                "started_at": (doc.get("started_at").isoformat()
                                if doc.get("started_at") else None),
                "finished_at": (doc.get("finished_at").isoformat()
                                 if doc.get("finished_at") else None)}

    # ── GET /migration/{kind} — paginated mapping table ─────────────
    @router.get("/migration/{kind}")
    async def migration_list(
        kind: str,
        user=Depends(current_user),
        status_filter: Optional[str] = Query(None, alias="status"),
        search: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        sort: str = Query("occurrences", regex="^(occurrences|last_order_date|status)$"),
        sort_dir: str = Query("desc", regex="^(asc|desc)$"),
        last_order_after: Optional[str] = Query(
            None, description="ISO date (YYYY-MM-DD). Keep rows whose "
            "last_order_date >= this value. Use to skip stale entities."),
    ):
        if kind not in ("products", "customers"):
            raise HTTPException(404, "kind must be products|customers")
        tenant = tenant_of(user)
        coll = db[f"qoyod_migration_{kind}"]
        q: dict = {"user_id": tenant}
        if status_filter:
            q["status"] = status_filter
        if last_order_after:
            q["last_order_date"] = {"$gte": last_order_after}
        if search:
            rgx = {"$regex": search, "$options": "i"}
            if kind == "products":
                q["$or"] = [{"mezan_sku": rgx}, {"mezan_name": rgx}]
            else:
                q["$or"] = [{"mezan_name": rgx}, {"mezan_phone": rgx},
                            {"mezan_email": rgx}]
        total = await coll.count_documents(q)
        direction = -1 if sort_dir == "desc" else 1
        cursor = coll.find(q, {"_id": 0}).sort(sort, direction) \
            .skip((page - 1) * page_size).limit(page_size)
        rows = [r async for r in cursor]
        # ISO-serialise dates
        for r in rows:
            for k in ("created_at", "updated_at", "confirmed_at"):
                if k in r and r[k] is not None and hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
        return {"ok": True, "total": total, "page": page,
                "page_size": page_size, "rows": rows}

    # ── POST /migration/{kind}/confirm ──────────────────────────────
    class _ConfirmBody(dict):
        pass

    @router.post("/migration/{kind}/confirm")
    async def migration_confirm(
        kind: str,
        body: dict = Body(...),
        user=Depends(current_user),
    ):
        tenant = tenant_of(user)
        mezan_key = body.get("mezan_key")
        qoyod_id  = body.get("qoyod_id")
        if not mezan_key or not qoyod_id:
            raise HTTPException(
                400, "mezan_key and qoyod_id are required")
        try:
            res = await confirm_candidate(
                db, user_id=tenant, kind=kind,
                mezan_key=mezan_key, qoyod_id=str(qoyod_id))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, **res}

    # ── GET /migration/{kind}/export.csv ────────────────────────────
    @router.get("/migration/{kind}/export.csv")
    async def migration_export_csv(
        kind: str,
        user=Depends(current_user),
        status_filter: Optional[str] = Query(None, alias="status"),
    ):
        if kind not in ("products", "customers"):
            raise HTTPException(404, "kind must be products|customers")
        tenant = tenant_of(user)
        coll = db[f"qoyod_migration_{kind}"]
        q: dict = {"user_id": tenant}
        if status_filter:
            q["status"] = status_filter

        buf = io.StringIO()
        w = csv.writer(buf)
        if kind == "products":
            w.writerow(["mezan_sku", "mezan_name", "mezan_unit_price",
                        "occurrences", "last_order_date", "status",
                        "qoyod_product_id", "candidate_qoyod_id",
                        "matched_on", "warnings",
                        "qoyod_name", "qoyod_price"])
            async for r in coll.find(q, {"_id": 0}):
                snap = r.get("qoyod_snapshot") or {}
                w.writerow([
                    r.get("mezan_sku") or "",
                    r.get("mezan_name") or "",
                    r.get("mezan_unit_price") or "",
                    r.get("occurrences") or 0,
                    r.get("last_order_date") or "",
                    r.get("status") or "",
                    r.get("qoyod_product_id") or "",
                    r.get("candidate_qoyod_id") or "",
                    r.get("matched_on") or "",
                    ";".join(r.get("warnings") or []),
                    snap.get("name") or "",
                    snap.get("price") or "",
                ])
        else:
            w.writerow(["mezan_name", "mezan_phone", "mezan_email",
                        "occurrences", "last_order_date", "status",
                        "qoyod_customer_id", "candidate_qoyod_id",
                        "matched_on", "warnings",
                        "qoyod_name", "qoyod_phone", "qoyod_email"])
            async for r in coll.find(q, {"_id": 0}):
                snap = r.get("qoyod_snapshot") or {}
                w.writerow([
                    r.get("mezan_name") or "",
                    r.get("mezan_phone") or "",
                    r.get("mezan_email") or "",
                    r.get("occurrences") or 0,
                    r.get("last_order_date") or "",
                    r.get("status") or "",
                    r.get("qoyod_customer_id") or "",
                    r.get("candidate_qoyod_id") or "",
                    r.get("matched_on") or "",
                    ";".join(r.get("warnings") or []),
                    snap.get("name") or "",
                    snap.get("phone") or "",
                    snap.get("email") or "",
                ])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="qoyod_migration_{kind}.csv"'})
