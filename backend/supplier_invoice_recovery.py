"""Receiving-session close reconciliation; no second invoice/ledger writer.

An explicit reconcile POST seals the prior close generation on the SAME session
row written by the invoice transaction. GET never treats an open session as
proof of failure. No timeout, process lock, or missing document is negative proof.
"""
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

CONTRACT = "supplier_invoice_recovery_v1"
REQUEST_PATTERN = r"^[A-Za-z0-9_-]{8,128}$"


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(pattern=REQUEST_PATTERN)
    close_fence: int = Field(ge=0, strict=True)


def require_close_generation(row, payload, admitted_fence):
    requested = payload.close_fence if payload.client_request_id else admitted_fence
    if (row.get("close_fence", 0) != requested
            or (row.get("close_recovery_required") and not payload.client_request_id)
            or (row.get("close_seal") or {}).get("client_request_id") == payload.client_request_id
            and payload.client_request_id is not None):
        raise HTTPException(409, detail={"code": "supplier_invoice_close_fenced",
            "message": "انتهت محاولة الحفظ السابقة؛ تحقق من الفاتورة قبل اعتماد المسودة."})


def register_recovery(router, db, current_user, api):
    sessions, invoices = api.SESSIONS, api.SUPPLIER_INVOICES

    async def context_for(user, session_id):
        context = await api._actor_context(db, user)
        api._require_permission(context, api.RECEIVE_PERMISSION)
        await api._session_for_actor(db, context=context, session_id=session_id)
        return context

    async def proof(row, context, request_id, tx=None):
        kw = {"session": tx} if tx is not None else {}
        result = {"ok": True, "contract": CONTRACT, "session_id": row["id"],
            "supplier_id": row["supplier_id"], "client_request_id": request_id,
            "close_fence": row.get("close_fence", 0), "state": "UNKNOWN", "retry_allowed": False}
        if row.get("status") == "closed":
            operation = row.get("close_operation")
            if operation and operation.get("client_request_id") not in (None, request_id):
                raise HTTPException(409, detail={"code": "supplier_invoice_other_close_attempt"})
            saved = await db[invoices].find_one({"user_id": context["merchant_id"],
                "session_id": row["id"]}, {"_id": 0}, **kw)
            api.require_invoice_integrity(isinstance(saved, dict), "closed_session_without_invoice")
            if saved.get("experiment_mode") is not True:
                saved = await api.verify_persisted_supplier_invoice(db,
                    user_id=context["merchant_id"], invoice_id=saved["id"], session_id=row["id"],
                    supplier_id=row["supplier_id"], expected_total=saved["total_halalas"],
                    actor_id=context["actor_id"], mongo_session=tx)
            event = await db[api.RECEIVING_EVENTS].find_one({"user_id": context["merchant_id"],
                "session_id": row["id"], "event_type": "supplier_receiving_session_closed",
                "supplier_invoice_id": saved["id"]}, {"_id": 0}, **kw)
            api.require_invoice_integrity(bool(event), "missing_close_commit_event")
            result.update(state="COMMITTED", supplier_invoice=api._public_supplier_invoice(saved),
                commit={"event_id": event["id"], "invoice_id": saved["id"],
                    "client_request_id": (operation or {}).get("client_request_id"),
                    "request_hash": (operation or {}).get("request_hash"),
                    "legacy": not bool((operation or {}).get("client_request_id"))})
        elif row.get("status") == "open":
            seal = row.get("close_seal") or {}
            if seal.get("client_request_id") == request_id and seal.get("close_fence") == result["close_fence"]:
                result.update(state="NOT_COMMITTED", retry_allowed=True, seal=seal)
        return result

    @router.get("/sessions/{session_id}/close-operation")
    async def read_operation(session_id: str,
            client_request_id: str = Query(pattern=REQUEST_PATTERN), user: dict = Depends(current_user)):
        context = await context_for(user, session_id)
        # Majority reads cannot turn a rolled-back seal into permission to retry.
        collection = db[sessions].with_options(read_concern=ReadConcern("majority"),
                                               read_preference=ReadPreference.PRIMARY)
        row = await collection.find_one({"user_id": context["merchant_id"], "id": session_id,
                                         "opened_by": context["actor_id"]})
        if not row:
            raise HTTPException(404, detail={"code": "supplier_receiving_session_not_found"})
        return await proof(row, context, client_request_id)

    @router.post("/sessions/{session_id}/close-reconcile")
    async def reconcile(session_id: str, payload: ReconcileRequest, user: dict = Depends(current_user)):
        context = await context_for(user, session_id)
        query = {"user_id": context["merchant_id"], "id": session_id, "opened_by": context["actor_id"]}

        async def seal(tx):
            fresh = await context_for(user, session_id)
            if fresh != context:
                raise HTTPException(409, detail={"code": "supplier_invoice_actor_changed"})
            row = await db[sessions].find_one(query, session=tx)
            if not row:
                raise HTTPException(404, detail={"code": "supplier_receiving_session_not_found"})
            if row.get("status") == "closed":
                return await proof(row, context, payload.client_request_id, tx)
            if row.get("status") != "open":
                raise HTTPException(409, detail={"code": "supplier_receiving_session_not_open"})
            prior = row.get("close_seal") or {}
            if prior.get("client_request_id") == payload.client_request_id:
                return await proof(row, context, payload.client_request_id, tx)
            if row.get("close_fence", 0) != payload.close_fence:
                raise HTTPException(409, detail={"code": "supplier_invoice_reconcile_fence_changed"})
            next_fence = payload.close_fence + 1
            evidence = {"client_request_id": payload.client_request_id, "close_fence": next_fence,
                        "previous_fence": payload.close_fence, "sealed_at": api._now()}
            patch = {"close_fence": next_fence, "close_recovery_required": True, "close_seal": evidence}
            # Conflicts with both legacy and V1 close transactions. Once committed,
            # an old admitted transaction aborts and any delayed old POST is refused.
            await db[sessions].update_one(query, {"$set": patch}, session=tx)
            return await proof({**row, **patch}, context, payload.client_request_id, tx)

        try:
            async with await db.client.start_session() as tx:
                return await tx.with_transaction(seal, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority", j=True), read_preference=ReadPreference.PRIMARY)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, detail={"code": "supplier_invoice_reconciliation_unknown",
                "message": "لم تُحسم النتيجة بعد؛ احتفظ بالمسودة وأعد التحقق."}) from exc
