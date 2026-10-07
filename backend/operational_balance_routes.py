"""Authenticated API shared by Mezan 2 and the employee application."""
from starlette.requests import Request as HttpRequest
from mobile_app_permissions import mobile_app_access_for_user, OPERATIONAL_APP_WRITE, OPERATIONAL_APP_READ
from hashlib import sha256
from typing import Literal
from datetime import date
from pydantic import field_validator
from uuid import uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, File, UploadFile, Response, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from operational_balance_store import RECEIPTS, AUTHORIZATION_GUARD, check_authorization, audit, digest, fail, mutate, now, read
from operational_balance_service import (entity, save_opening, create_movement, report, refresh, freeze, supplier_return)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Request(Input):
    request_id: str = Field(min_length=8, max_length=160)


PartyType = Literal["employee", "provider", "courier", "store_driver", "ad_account",
                    "supplier", "external_person", "bank", "cash", "employee_custody",
                    "operating_expense", "owner_withdrawal"]


class Opening(Request):
    party_type: PartyType
    party_id: str = Field(min_length=1, max_length=200)
    direction: Literal["for_party", "for_us"]
    amount: str = Field(min_length=1, max_length=30)
    currency: str = Field(pattern="^[A-Z]{3}$")


class Finish(Request):
    opening: Opening | None = None


class Allocation(Input):
    obligation_id: str = Field(min_length=1, max_length=300)
    amount: str = Field(min_length=1, max_length=30)


class Movement(Request):
    business_date: str | None = None

    @field_validator("business_date")
    @classmethod
    def validate_business_date(cls, value):
        if value is not None and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
            raise ValueError("invalid_business_date")
        return value

    expected_session_scope: str = Field(min_length=64, max_length=64)
    direction: Literal["incoming", "outgoing"]
    party_type: PartyType
    party_id: str = Field(min_length=1, max_length=200)
    bank_id: str | None = Field(default=None, max_length=200)
    source_account_type: Literal["bank_auto", "bank", "cash", "employee_custody"] = "bank_auto"
    amount: str = Field(min_length=1, max_length=30)
    currency: str = Field(pattern="^[A-Z]{3}$")
    kind: Literal["payment", "collection", "settlement", "transfer", "refund", "wallet_funding", "correction"]
    order_number: str | None = Field(default=None, max_length=160)
    note: str = Field(default="", max_length=1000)
    receipt_id: str | None = Field(default=None, max_length=100)
    reference: str = Field(default="", max_length=160)
    allocations: list[Allocation] = Field(default_factory=list, max_length=1000)
    source: Literal["mezan2", "employee_app"] = "mezan2"
    actual_fee_amount: str = Field(default="0.00", max_length=30)


class AddEntity(Request):
    name: str = Field(min_length=2, max_length=120)
    currency: Literal["SAR"] = "SAR"


class ReturnItem(Input):
    id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0, le=100000)


class CustomerReturn(Request):
    expected_session_scope: str = Field(min_length=64, max_length=64)
    order_number: str = Field(min_length=1, max_length=160)
    items: list[ReturnItem] = Field(default_factory=list, max_length=1000)
    status: Literal["pending", "refunded"] = "pending"
    amount: str | None = Field(default=None, max_length=30)
    refund_source_type: Literal["bank", "provider"] | None = None
    refund_source_id: str | None = Field(default=None, max_length=200)
    refund_reference: str = Field(default="", max_length=160)
    refunded_at: str | None = Field(default=None, max_length=40)
    shipping_kind: Literal["none", "courier", "store_driver"] = "none"
    shipping_id: str | None = Field(default=None, max_length=200)
    shipment_reference: str = Field(default="", max_length=160)
    shipment_completed: bool = False
    shipping_quote_hash: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=1000)


class Freeze(Request):
    reason: str = Field(min_length=3, max_length=1000)


class SupplierReturn(Request):
    receipt_id: str = Field(min_length=1, max_length=300)
    evidence_receipt_id: str = Field(min_length=1, max_length=100)
    amount: str = Field(min_length=1, max_length=30)
    note: str = Field(min_length=3, max_length=1000)


async def scope(db, principal, permission):
    # current_user may carry a merchant-shaped mobile principal. Always recover
    # and refresh the real actor; never inherit the merchant owner's role.
    identity = principal.get("_mobile_actor_id") or principal.get("id")
    actor = await db["users"].find_one({"id": identity}, {"_id": 0})
    if (not actor or actor.get("disabled") or actor.get("is_active") is False
            or actor.get("deleted_at")):
        fail("operational_actor_inactive", "الحساب غير مخول", 403)
    owner = actor["id"] if actor.get("role") == "owner" else actor.get("created_by")
    if not owner:
        fail("operational_owner_missing", "حساب الموظف غير مرتبط بالمالك", 403)
    owner_account = actor if actor["id"] == owner else await db["users"].find_one({"id": owner}, {"_id": 0})
    if (not owner_account or owner_account.get("role") != "owner" or owner_account.get("disabled")
            or owner_account.get("is_active") is False or owner_account.get("deleted_at")):
        fail("operational_owner_inactive", "حساب المالك غير متاح", 403)
    mobile = principal.get("_session_client") == "amasi_mobile"
    if permission == "cash_manage" and actor.get("role") != "owner":
        fail("operational_cash_owner_required", "إضافة الصندوق متاحة للمالك فقط", 403)
    if mobile and permission != "cash_manage":
        access = await mobile_app_access_for_user(db, actor)
        granted = set(access.get("permissions") or []) if access.get("enabled") else set()
        required = {"view": {OPERATIONAL_APP_WRITE, OPERATIONAL_APP_READ},
                    "move": {OPERATIONAL_APP_WRITE}, "reports": {OPERATIONAL_APP_READ}}.get(permission, set())
        if not granted.intersection(required):
            fail("operational_app_permission_required", "لا توجد صلاحية لهذه الوظيفة", 403)
    # Browser access still follows active membership; native grants are separate.
    return actor, owner, "employee_app" if mobile else "mezan2"


def make_operational_balance_router(db, current_user):
    async def native_gate(request: HttpRequest, user=Depends(current_user)):
        if user.get("_session_client") != "amasi_mobile":
            return
        path = request.url.path.rstrip("/").split("/operational-balances", 1)[-1]
        permission = None
        if request.method == "GET":
            if path == "/context" or path.startswith("/entities/"):
                permission = "view"
            elif path in {"/reports", "/movements", "/obligations"}:
                permission = "reports"
        elif request.method == "POST" and path == "/movements":
            permission = "move"
        elif request.method == "POST" and path == "/entities/cash":
            permission = "cash_manage"
        if permission is None:
            fail("operational_app_route_not_allowed", "هذه الوظيفة غير متاحة في التطبيق", 403)
        await scope(db, user, permission)

    router = APIRouter(prefix="/operational-balances", tags=["Operational balances"],
                       dependencies=[Depends(native_gate)])

    def guarded(permission):
        async def dependency(request: HttpRequest, user=Depends(current_user)):
            actor, owner, _ = await scope(db, user, permission)
            actor_id = actor["id"]
            bank_payload = await request.json() if permission == "move" and user.get("_session_client") == "amasi_mobile" else None
            async def verify():
                fresh_actor, fresh_owner, _ = await scope(db, user, permission)
                if fresh_actor["id"] != actor_id or fresh_owner != owner:
                    fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد تسجيل الدخول", 403)
                if bank_payload is not None:
                    from operational_app_banks import require_assigned_bank
                    await require_assigned_bank(db, owner, fresh_actor, bank_payload)
            token = AUTHORIZATION_GUARD.set(verify)
            try:
                yield user
            finally:
                AUTHORIZATION_GUARD.reset(token)
        return dependency

    manage_guard = guarded("manage")
    move_guard = guarded("move")
    async def entity_guard(request: HttpRequest, user=Depends(current_user)):
        from contextlib import asynccontextmanager
        permission = "cash_manage" if request.url.path.rstrip("/").endswith("/entities/cash") else "manage"
        async with asynccontextmanager(guarded(permission))(request, user) as checked:
            yield checked

    @router.get("/context")
    async def context(user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "view")
        state = await read(db, owner)
        permissions = {}
        for key in ("view", "move", "manage", "reports"):
            try:
                await scope(db, user, key)
                permissions[key] = True
            except Exception as exc:
                from fastapi import HTTPException
                if not isinstance(exc, HTTPException):
                    raise
                permissions[key] = False
        from operational_app_banks import assigned_banks
        banks = await assigned_banks(db, owner, actor) if source == "employee_app" else None
        return {"status": state["status"], "started_at": state["started_at"],
                "operational_banks": banks,
                "can_create_cash": actor.get("role") == "owner",
                "session_scope": digest([owner, actor["id"]]),
                "opening_count": len(state["openings"]), "permissions": permissions,
                "issues": state.get("engine", {}).get("issues", []) if permissions["reports"] else []}

    @router.get("/entities/{kind}")
    async def entities_route(kind: PartyType, user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "view")
        from operational_balance_sources import entities
        try:
            rows = await entities(db, owner, kind)
            if source == "employee_app" and kind == "employee_custody":
                from operational_app_banks import assigned_custody
                own = await assigned_custody(db, owner, actor)
                rows = [r for r in rows if r["id"] in own]
            if source == "employee_app" and kind in {"bank", "cash"}:
                from operational_app_banks import assigned_banks
                allowed = await assigned_banks(db, owner, actor)
                rows = [r for r in rows if r["id"] in allowed["bank_ids" if kind == "bank" else "cash_ids"]]
            return {"items": rows}
        except ValueError as exc:
            if str(exc) not in {"operational_source_rejected", "operational_source_scope_too_large",
                                "operational_source_owner_mismatch", "shipping_setup_ambiguous",
                                "operational_entity_kind_invalid", "operational_entity_identity_ambiguous",
                                "operational_expense_identity_ambiguous"}:
                raise
            fail("operational_entity_setup_incomplete", "إعداد الجهات غير مكتمل أو متعارض؛ يلزم مراجعة المصدر", 409)

    @router.post("/entities/{kind}")
    async def add_entity(kind: Literal["cash", "external_person", "operating_expense"], payload: AddEntity, user=Depends(entity_guard)):
        actor, owner, _ = await scope(db, user, "cash_manage" if kind == "cash" else "manage")
        state = await read(db, owner)
        if state["status"] not in {"draft", "active"}:
            fail("operational_setup_closed", "النظام مغلق للقراءة؛ لا يمكن إضافة جهة")
        from operational_balance_service import accounting_inactive
        await accounting_inactive(db, owner)
        identity = str(uuid5(NAMESPACE_URL, f"operational:{owner}:{kind}:{payload.request_id}"))
        collection = {"cash": "mz2_financial_accounts", "external_person": "mz2_external_persons_v2",
                      "operating_expense": "expense_categories"}[kind]
        if kind == "operating_expense":
            identity = "op_expense_" + identity.replace("-", "")
        stamp = now()
        row = {"_id": identity, "id": identity, "user_id": owner, "name": payload.name,
               "display_name": payload.name, "currency": payload.currency, "status": "active", "version": 1,
               "created_at": stamp, "created_by": actor["id"], "updated_at": stamp, "updated_by": actor["id"],
               "operational_request_hash": digest(payload.model_dump()),
               "audit": [{"action": "create", "actor_id": actor["id"], "at": stamp}]}
        if kind == "cash":
            row.update(account_type="cash", external_ref=None)
        elif kind == "external_person":
            row.update(kind="external_person", name_lower=" ".join(payload.name.split()).casefold(),
                       reference="", person_type="person", phone="", notes="")
        else:
            # Native MZ2 daily-movement category identity is code, not an
            # accounting account or the separate legacy category tree.
            row.update(code=identity, source="operational_balance")
        existing = await db[collection].find_one({"user_id": owner, "id": identity})
        if not existing:
            try:
                await check_authorization()
                await accounting_inactive(db, owner)
                if (await read(db, owner))["status"] not in {"draft", "active"}:
                    fail("operational_setup_closed", "النظام مغلق للقراءة؛ لا يمكن إضافة جهة")
                await db[collection].insert_one(row)
                existing = row
            except DuplicateKeyError:
                existing = await db[collection].find_one({"user_id": owner, "id": identity})
        if not existing or existing.get("operational_request_hash") != row["operational_request_hash"]:
            fail("operational_entity_conflict", "الجهة أو رقم العملية مستخدم من قبل")
        return {"id": identity, "name": existing["name"], "currency": existing["currency"], "kind": kind}

    @router.post("/openings")
    async def opening(payload: Opening, user=Depends(manage_guard)):
        actor, owner, _ = await scope(db, user, "manage")
        return await save_opening(db, owner, actor["id"], payload.model_dump())

    @router.post("/finish")
    async def finish(payload: Finish, user=Depends(manage_guard)):
        actor, owner, _ = await scope(db, user, "manage")
        return await save_opening(db, owner, actor["id"], payload.model_dump(), finish=True)

    @router.post("/receipts")
    async def upload(file: UploadFile = File(...), user=Depends(move_guard)):
        actor, owner, source = await scope(db, user, "move")
        from operational_balance_service import active_gate
        await active_gate(db, owner, await read(db, owner))
        content = await file.read(5 * 1024 * 1024 + 1)
        types = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}
        if len(content) > 5 * 1024 * 1024 or file.content_type not in types or not content.startswith(types[file.content_type]):
            fail("operational_receipt_invalid", "أرفق صورة أو PDF صالحًا بحجم لا يتجاوز 5 ميجابايت", 422)
        hashed = sha256(content).hexdigest()
        identity = digest([owner, hashed])
        row = {"_id": identity, "owner_id": owner, "sha256": hashed, "content": content,
               "name": (file.filename or "receipt")[:180], "mime": file.content_type,
               "actor_id": actor["id"], "source": source, "created_at": now()}
        await check_authorization()
        await db[RECEIPTS].update_one({"_id": identity, "owner_id": owner}, {"$setOnInsert": row}, upsert=True)
        return {"id": identity, "name": row["name"]}

    @router.get("/receipts/{receipt_id}")
    async def receipt(receipt_id: str, user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "view")
        query = {"_id": receipt_id, "owner_id": owner}
        row = await db[RECEIPTS].find_one(query)
        if not row:
            fail("operational_receipt_missing", "الإيصال غير متاح", 404)
        return Response(bytes(row["content"]), media_type=row["mime"], headers={
            "Content-Disposition": 'attachment; filename="receipt"', "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store"})

    @router.post("/movements")
    async def movement(payload: Movement, user=Depends(move_guard)):
        actor, owner, source = await scope(db, user, "move")
        if source == "employee_app" and payload.kind == "correction":
            fail("operational_app_route_not_allowed", "تصحيح الأرصدة غير متاح في التطبيق", 403)
        data = payload.model_dump()
        if payload.business_date is None:
            data.pop("business_date")
        if source == "employee_app":
            from operational_app_banks import require_assigned_bank
            await require_assigned_bank(db, owner, actor, data)
        return await create_movement(db, owner, actor["id"], data, source=source)

    @router.get("/movements")
    async def movements(user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "view")
        state = await read(db, owner)
        items = state["movements"]
        return {"items": list(reversed(items))}

    @router.get("/customer-returns/order/{order_number}")
    async def return_order(order_number: str, user=Depends(current_user)):
        _, owner, _ = await scope(db, user, "view")
        from operational_customer_returns import order_view
        return await order_view(db, owner, order_number)

    @router.get("/customer-returns")
    async def customer_returns(user=Depends(current_user)):
        _, owner, _ = await scope(db, user, "view")
        return {"items": list(reversed((await read(db, owner)).get("customer_returns", [])))}

    @router.get("/customer-returns/shipping-quote/{kind}/{identity}")
    async def return_shipping_quote(kind: Literal["courier", "store_driver"], identity: str, order_number: str, user=Depends(current_user)):
        _, owner, _ = await scope(db, user, "view")
        from operational_customer_returns import shipping_quote
        quote = await shipping_quote(db, owner, kind, identity, now(), order_number)
        return {**quote, "quote_hash": digest(quote)}

    @router.post("/customer-returns")
    async def create_customer_return(payload: CustomerReturn, user=Depends(move_guard)):
        actor, owner, _ = await scope(db, user, "move")
        from operational_customer_returns import save_case
        return await save_case(db, owner, actor["id"], payload.model_dump())

    @router.post("/customer-returns/{case_id}/confirm")
    async def confirm_customer_return(case_id: str, payload: CustomerReturn, user=Depends(move_guard)):
        actor, owner, _ = await scope(db, user, "move")
        from operational_customer_returns import save_case
        return await save_case(db, owner, actor["id"], payload.model_dump(), case_id=case_id)

    @router.get("/obligations")
    async def allocation_choices(party_type: PartyType, party_id: str, user=Depends(current_user)):
        actor, owner, source = await scope(db, user, "view")
        if source == "employee_app" and actor.get("role") != "owner" and party_type == "employee":
            await scope(db, user, "reports")
        result = report(await read(db, owner))
        fields = ("id", "kind", "party_type", "party_id", "currency", "direction", "outstanding", "expected",
                  "available_to_pay", "pending_confirmation", "label", "business_date")
        items = []
        for obligation in result["obligations"]:
            if obligation.get("party_type") == party_type and obligation.get("party_id") == party_id:
                safe = {key: obligation[key] for key in fields if key in obligation}
                safe["label"] = obligation.get("label") or "مستحق تشغيلي"
                items.append(safe)
        return {"items": items}

    @router.get("/reports")
    async def reports(user=Depends(current_user)):
        _, owner, _ = await scope(db, user, "reports")
        return report(await read(db, owner))

    @router.get("/audit")
    async def audit_route(user=Depends(current_user)):
        _, owner, _ = await scope(db, user, "reports")
        state = await read(db, owner)
        return {"items": list(reversed(state["audit"] + state.get("engine", {}).get("audit", [])))}

    @router.post("/freeze")
    async def freeze_route(payload: Freeze, user=Depends(manage_guard)):
        actor, owner, _ = await scope(db, user, "manage")
        try:
            await refresh(db, owner)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") != "operational_accounting_active":
                raise
            # Accounting activation blocks new operational calculation, but must
            # not block freezing the last verified operational snapshot.
        return await freeze(db, owner, actor["id"], payload.model_dump())

    @router.post("/supplier-returns")
    async def supplier_return_route(payload: SupplierReturn, user=Depends(manage_guard)):
        actor, owner, _ = await scope(db, user, "manage")
        return await supplier_return(db, owner, actor["id"], payload.model_dump())

    return router
