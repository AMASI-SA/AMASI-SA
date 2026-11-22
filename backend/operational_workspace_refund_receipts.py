"""Scoped optional evidence for customer refunds and additional receipts."""
from hashlib import sha256
from typing import Literal

from fastapi import Depends, File, Form, UploadFile
from fastapi.responses import Response

from operational_balance_access import guard_access, load_access
from operational_balance_store import RECEIPTS, check_authorization, digest, fail, now, read


MAX_BYTES = 5 * 1024 * 1024
SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}
SCOPE = "customer_adjustment"
OPERATIONS = ("customer.refund_execute", "customer.collection")


def _valid_content(row):
    content = bytes(row.get("content") or b"")
    return (0 < len(content) <= MAX_BYTES and row.get("mime") in SIGNATURES
            and content.startswith(SIGNATURES[row["mime"]])
            and row.get("sha256") == sha256(content).hexdigest())


async def validate_refund_receipt(db, owner, actor, order_number, receipt_id):
    """Return only metadata, bound to this owner/order/uploader; no external URL."""
    if receipt_id is None:
        return None
    actor_id = actor["id"] if isinstance(actor, dict) else actor
    owner_actor = actor_id == owner
    row = await db[RECEIPTS].find_one({"_id": receipt_id, "owner_id": owner})
    if (row is None or row.get("scope") != SCOPE or row.get("order_number") != str(order_number)
            or (row.get("actor_id") != actor_id and not owner_actor) or not _valid_content(row)):
        fail("operational_refund_receipt_invalid", "الإيصال غير متاح لهذا الطلب أو الحساب", 409)
    return {"id": row["_id"], "name": row["name"], "mime": row["mime"],
            "size": len(bytes(row["content"])), "sha256": row["sha256"], "order_number": row["order_number"]}


def install_refund_receipt_routes(router, db, current_user, scope):
    async def access(user, operation=None):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        policy.require_enabled()
        if operation is not None:
            policy.require_operation(operation)
        elif not any(policy.can_operation(key) for key in OPERATIONS):
            fail("operational_operation_forbidden", "ليست لديك صلاحية عرض إيصال هذه العملية", 403)
        return actor, owner, source, policy

    @router.post("/workspace/refund-receipts")
    async def upload(file: UploadFile = File(...), order_number: str = Form(..., min_length=1, max_length=160),
                     expected_session_scope: str = Form(..., min_length=64, max_length=64),
                     operation: Literal["customer.refund_execute", "customer.collection"] = Form(default="customer.refund_execute"),
                     user=Depends(current_user)):
        from operational_balance_service import active_gate
        from operational_workspace_refunds import refund_order

        actor, owner, source, _ = await access(user, operation)
        if expected_session_scope != digest([owner, actor["id"]]):
            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
        async with guard_access(db, actor, owner, operation, []):
            await active_gate(db, owner, await read(db, owner))
            order = await refund_order(db, owner, order_number)
            content = await file.read(MAX_BYTES + 1)
            if (not content or len(content) > MAX_BYTES or file.content_type not in SIGNATURES
                    or not content.startswith(SIGNATURES[file.content_type])):
                fail("operational_receipt_invalid", "أرفق صورة أو PDF صالحًا بحجم لا يتجاوز 5 ميجابايت", 422)
            hashed = sha256(content).hexdigest()
            identity = digest([owner, actor["id"], SCOPE, order["order_number"], hashed])
            row = {"_id": identity, "owner_id": owner, "scope": SCOPE, "order_number": order["order_number"],
                   "order_id": order["id"], "sha256": hashed, "content": content,
                   "name": (file.filename or "receipt")[:180], "mime": file.content_type,
                   "actor_id": actor["id"], "operation": operation, "source": source, "created_at": now()}
            await check_authorization()
            await db[RECEIPTS].update_one({"_id": identity, "owner_id": owner}, {"$setOnInsert": row}, upsert=True)
            await check_authorization()
            return {"id": identity, "name": row["name"], "mime": row["mime"],
                    "size": len(content), "order_number": order["order_number"]}

    @router.get("/workspace/refund-receipts/{receipt_id}")
    async def receipt(receipt_id: str, user=Depends(current_user)):
        from operational_workspace_refunds import _visible

        actor, owner, _, policy = await access(user)
        row = await db[RECEIPTS].find_one({"_id": receipt_id, "owner_id": owner, "scope": SCOPE})
        if row is None or not _valid_content(row):
            fail("operational_receipt_missing", "الإيصال غير متاح", 404)
        visible = policy.owner_override or row.get("actor_id") == actor["id"]
        if not visible:
            state = await read(db, owner)
            visible = any(case.get("receipt_id") == receipt_id and _visible(policy, case)
                          for case in state.get("customer_returns", []))
            visible = visible or any(case.get("receipt_id") == receipt_id and
                policy.can_read_movement(case.get("destination_type"), case.get("destination_id"), case.get("created_by"))
                for case in state.get("customer_collections", []))
        if not visible:
            fail("operational_receipt_missing", "الإيصال غير متاح", 404)
        fresh_actor, fresh_owner, _, current = await access(user)
        if (owner, actor["id"], policy.version, policy.owner_override) != (
                fresh_owner, fresh_actor["id"], current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return Response(bytes(row["content"]), media_type=row["mime"], headers={
            "Content-Disposition": 'attachment; filename="receipt"', "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store"})
