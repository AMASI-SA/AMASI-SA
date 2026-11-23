"""One recoverable employee/supplier assignment over canonical preparation units.

Only a request identity is saved while the existing PDF is prepared. Publication
of the file, allocation, physical pieces, optional supplier dispatch and receipt
is one restricted owner transaction. Legacy employee-only routes stay intact.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Annotated, Any, Callable, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pymongo import ASCENDING

import preparation_file_registry as registry_module
import preparation_piece_operations as piece_module
import preparation_supplier_dispatch as dispatch_module
import reviewed_preparation_batches as batch_module
from mobile_app_permissions import MOBILE_APP_ACCESS, effective_mobile_app_permissions
from operational_atomic import operational_owner
from order_review_routes import EVENTS, _merchant_user_id, _require_reviewer, _text
from order_tracking_notes import active_stage_instructions, enforce_instruction_rows, enforce_stage_instructions
from preparation_file_failure_safety import SafePreparationFileDraftRequest
from preparation_file_registry import REGISTRY, preparation_file_view
from preparation_piece_operations import PIECES, PIECE_EVENTS
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS, load_reviewed_product_context
from supplier_category_service_policy import require_supplier_product_categories
from supplier_dispatch_waiting_policy import require_current_under_review
from tz_utils import riyadh_now_aware

REQUESTS = "mezan_preparation_assignment_requests_v2"
CONTRACT = "preparation_assignment_v2"
PROFILE = "preparation_assignment"
PREPARATION_LEASE_SECONDS = 300
MAX_SELECTIONS = 50


class SupplierAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["assign"]
    supplier_id: str = Field(min_length=1, max_length=120)


class DeferredSupplierAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["defer"]


class AssignmentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_key: str = Field(min_length=1, max_length=500)
    quantity: Literal[1]
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    order_number: str = Field(min_length=1, max_length=80)


class AssignmentRequest(SafePreparationFileDraftRequest):
    selections: list[AssignmentSelection] = Field(min_length=1, max_length=MAX_SELECTIONS)
    supplier_assignment: Annotated[SupplierAssignment | DeferredSupplierAssignment, Field(discriminator="mode")]
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def exact_command(self):
        count = len(self.selections)
        if (len({row.group_key for row in self.selections}) != count
                or self.expected_quantity != count or self.selected_product_count != count):
            raise ValueError("preparation_assignment_quantity_mismatch")
        identities = [self.client_request_id, self.responsible_employee_id,
            *(value for row in self.selections for value in (row.group_key, row.order_number))]
        if self.supplier_assignment.mode == "assign":
            identities.append(self.supplier_assignment.supplier_id)
        if any(not value.strip() or value != value.strip() for value in identities):
            raise ValueError("preparation_assignment_identity_invalid")
        if self.required_due_at is not None and self.required_due_at.tzinfo is None:
            raise ValueError("preparation_assignment_due_timezone_required")
        normalized = " ".join(self.file_title.split())
        if registry_module.normalize_file_title(normalized) != normalized:
            raise ValueError("preparation_assignment_file_title_invalid")
        self.file_title = normalized
        return self


def _now():
    return datetime.now(timezone.utc)


def _instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None


def session_scope(owner: str, actor: str) -> str:
    return _digest([CONTRACT, owner, actor])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def _error(code, message, status=409, *, not_applied=False):
    raise HTTPException(status, detail={"code": code, "message": message, "not_applied": not_applied})


def _command(payload):
    result = payload.model_dump(mode="json", exclude={"expected_session_scope"})
    result["required_due_at"] = (_instant(payload.required_due_at).isoformat()
        if payload.required_due_at is not None else None)
    return result


def _actor_view(user):
    return {"id": _text(user.get("id")), "name": _text(user.get("name") or user.get("email"))}


async def _live_account(db, owner, account_id, permission, *, fence=False, require_email=False):
    account = await db.users.find_one({"id": account_id}, {"_id": 0})
    if (not account or account.get("disabled") is True or account.get("is_active") is False
            or account.get("deleted_at") or (require_email and not _text(account.get("email")))):
        _error("preparation_assignment_account_unavailable", "حساب الموظف غير متاح للإسناد.", 403)
    if account_id == owner:
        linked = account.get("role") == "owner" or account.get("is_owner") is True
        access = None
    else:
        linked = _text(account.get("created_by")) == owner
        if not _text(account.get("created_by")):
            linked = bool(await db.mezan_employees_v2.find_one(
                {"user_id": owner, "account_user_id": account_id}, {"_id": 0, "id": 1}))
        access = await db[MOBILE_APP_ACCESS].find_one(
            {"owner_user_id": owner, "user_id": account_id}, {"_id": 0})
        if permission and permission not in effective_mobile_app_permissions(access, account_active=True):
            _error("preparation_assignment_permission_required", "تغيرت صلاحية الموظف؛ حدّث خيارات الإسناد.", 403)
    if not linked:
        _error("preparation_assignment_owner_mismatch", "حساب الموظف غير مرتبط بهذا المتجر.", 403)
    if fence:
        updated = await db.users.update_one({"id": account_id}, {"$inc": {"preparation_assignment_fence": 1}})
        if updated.matched_count != 1:
            _error("preparation_assignment_account_changed", "تغير حساب الموظف أثناء الإسناد.")
        if access is not None:
            updated = await db[MOBILE_APP_ACCESS].update_one(
                {"owner_user_id": owner, "user_id": account_id},
                {"$inc": {"preparation_assignment_fence": 1}})
            if updated.matched_count != 1:
                _error("preparation_assignment_permission_changed", "تغيرت صلاحية الموظف أثناء الإسناد.")
    return account


async def _authority(db, owner, reviewer, employee_id=None, *, fence=False):
    native = reviewer.get("_session_client") == "amasi_mobile"
    actor = await _live_account(db, owner, _text(reviewer.get("id")),
        "app.page.reviewed_preparation" if native else None, fence=fence)
    if not native:
        _require_reviewer(actor)
    employee = (await _live_account(db, owner, employee_id, "app.page.my_products",
        fence=fence, require_email=True)) if employee_id else None
    return actor, employee


async def _supplier(db, owner, command, pieces=None):
    assignment = command["supplier_assignment"]
    if assignment["mode"] == "defer":
        return None
    supplier = await db[dispatch_module.MEZAN_SUPPLIERS_V2].find_one({
        "user_id": owner, "id": assignment["supplier_id"], "status": {"$ne": "inactive"},
        "$or": [{"category_ids.0": {"$exists": True}}, {"service_ids.0": {"$exists": True}}],
    }, {"_id": 0})
    if supplier is None:
        _error("supplier_dispatch_supplier_not_found", "المورد المحدد غير متاح؛ اختر موردًا نشطًا.", 404)
    if pieces is not None:
        # Preserve the canonical category rule, including legacy service-only
        # suppliers. Do not enable the deliberately disabled specialty guard.
        await require_supplier_product_categories(db, user_id=owner, supplier=supplier, pieces=pieces)
    return supplier


async def _plan(db, owner, command):
    numbers = sorted({row["order_number"] for row in command["selections"]})
    context = await load_reviewed_product_context(db, user_id=owner, limit=len(numbers), order_numbers=numbers)
    if context.get("truncated"):
        _error("reviewed_catalog_truncated", "تعذّر التحقق من جميع القطع المحددة.")
    try:
        planned = batch_module.plan_preparation_allocations(
            context["catalog"].get("products") or [], command["selections"])
    except ValueError as exc:
        _error(str(exc), "تغيرت القطع المختارة؛ حدّث القائمة وأعد الاختيار.")
    if sum(row["quantity"] for row in planned) != command["expected_quantity"]:
        _error("preparation_assignment_quantity_mismatch", "تغير عدد القطع المختارة.")
    return context, planned


async def _instructions(db, owner, actor_id, pieces, *, supplier=False, prepare=False):
    stages = ["reviewed", "supplier_dispatch"] if supplier else ["reviewed"]
    for piece in pieces:
        for stage in stages:
            args = {"user_id": owner, "order_number": piece["order_number"],
                "order_item_id": piece["order_item_id"], "piece_id": _text(piece.get("piece_id")),
                "stage": stage, "actor_id": actor_id}
            if prepare:
                await enforce_stage_instructions(db, **args)
            else:
                # Fresh transaction-local read, same canonical enforcement.
                # A newly scheduled blocking instruction rejects publication;
                # its activation is never a reason to widen this capability.
                rows = await active_stage_instructions(db, **{k: v for k, v in args.items() if k != "actor_id"})
                enforce_instruction_rows(rows, **{k: v for k, v in args.items() if k not in {"user_id", "order_number"}})


def _plan_identity(planned):
    return sorted((row["order_number"], row["order_item_id"], int(unit), row["group_key"],
        row.get("ready_item_id"), row.get("ready_unit_id"))
        for row in planned for unit in row["unit_indices"])


def _fingerprint(owner, actor_id, command):
    return _digest({"owner": owner, "actor": actor_id, "command": command})


def _check_request(row, owner, actor_id, command=None):
    if row.get("created_by") != actor_id or row.get("session_scope") != session_scope(owner, actor_id):
        _error("preparation_assignment_request_scope_conflict", "المحاولة محفوظة لحساب آخر.")
    if row.get("request_fingerprint") != _fingerprint(owner, actor_id, row.get("command")):
        _error("preparation_assignment_request_proof_invalid", "تعذّر التحقق من هوية المحاولة المحفوظة.")
    if command is not None and _fingerprint(owner, actor_id, command) != row["request_fingerprint"]:
        _error("preparation_assignment_request_conflict", "هوية المحاولة تخص إسنادًا مختلفًا؛ لم يُنشأ إسناد آخر.")


async def _begin_request(tx, owner, reviewer, command, token):
    actor, employee = await _authority(tx, owner, reviewer, command["responsible_employee_id"], fence=True)
    key = {"user_id": owner, "client_request_id": command["client_request_id"]}
    row = await tx[REQUESTS].find_one(key, {"_id": 0})
    if row:
        _check_request(row, owner, actor["id"], command)
        if row.get("state") == "COMMITTED":
            return row, False
        lease = _instant(row.get("prepare_lease_until"))
        if lease and lease > _now():
            return row, False
        patch = {"prepare_token": token, "prepare_lease_until": _now() + timedelta(seconds=PREPARATION_LEASE_SECONDS)}
        await tx[REQUESTS].update_one(key, {"$set": patch, "$unset": {"safe_failure": ""}})
        row.update(patch)
        return row, True
    if (await tx[REGISTRY].find_one(key, {"_id": 0, "id": 1})
            or await tx[batch_module.BATCHES].find_one(key, {"_id": 0, "id": 1})):
        _error("preparation_assignment_legacy_request_conflict", "هذه المحاولة محفوظة بالطريقة السابقة؛ استخدم نتيجتها الأصلية.")
    now = _now()
    local = riyadh_now_aware()
    sequence = await registry_module._next_file_sequence(tx, owner)
    row = {**key, "id": "pa2_" + uuid5(NAMESPACE_URL, owner + ":" + command["client_request_id"]).hex,
        "state": "PENDING", "command": command, "session_scope": session_scope(owner, actor["id"]),
        "request_fingerprint": _fingerprint(owner, actor["id"], command),
        "created_by": actor["id"], "created_by_name": _actor_view(actor)["name"], "created_at": now,
        "batch_id": uuid5(NAMESPACE_URL, owner + ":pa2:batch:" + command["client_request_id"]).hex,
        "file_date": local.strftime("%Y-%m-%d"), "file_date_display": f"{local.year}/{local.month}/{local.day}",
        "file_number": registry_module.preparation_file_number(local.strftime("%Y-%m-%d"), sequence),
        "prepare_token": token, "prepare_lease_until": now + timedelta(seconds=PREPARATION_LEASE_SECONDS)}
    await tx[REQUESTS].insert_one(deepcopy(row))
    return row, True


async def _prepare_assignment(db, owner, reviewer, row):
    command = row["command"]
    actor, employee = await _authority(db, owner, reviewer, command["responsible_employee_id"])
    context, planned = await _plan(db, owner, command)
    await _instructions(db, owner, actor["id"], planned,
        supplier=command["supplier_assignment"]["mode"] == "assign", prepare=True)
    await require_current_under_review(db, user_id=owner, pieces=planned)
    await _supplier(db, owner, command, planned)
    lines = await batch_module._build_batch_lines({**context, "_database": db, "_user_id": owner}, planned)
    repair = await batch_module._repair_new_batch_options(db, user_id=owner, context=context, lines=lines)
    if repair["refresh_failures"] or repair["unresolved"]:
        _error("preparation_customer_options_verification_failed", "تعذّر التحقق من خيارات العميل؛ لم يُعتمد الإسناد.")
    lines = repair["lines"]
    from bundle_supplier_pieces import supplier_bundle_print_preview
    physical_print_lines, bundle_references = await supplier_bundle_print_preview(db, owner, lines, row["batch_id"])
    physical_quantity = sum(int(line["quantity"]) for line in physical_print_lines)
    batch = {"id": row["batch_id"], "user_id": owner, "title": "تجهيز المنتجات", "lines": lines,
        **({"physical_print_lines": physical_print_lines} if bundle_references else {})}
    pdf = batch_module.render_preparation_batch_pdf(batch)
    if not pdf.startswith(b"%PDF"):
        _error("preparation_batch_generation_failed", "تعذّر إنشاء ملف التجهيز.")
    return {"planned": planned, "lines": lines, "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "pdf_size_bytes": len(pdf), "option_repair": repair, "physical_quantity": physical_quantity,
        "physical_print_lines": physical_print_lines if bundle_references else None,
        "bundle_references": bundle_references}


async def _publish_assignment(tx, owner, reviewer, row, prepared, token):
    """All employee/supplier effects and the immutable receipt commit together."""
    key = {"user_id": owner, "client_request_id": row["client_request_id"]}
    current = await tx[REQUESTS].find_one(key, {"_id": 0})
    if not current:
        _error("preparation_assignment_request_missing", "تعذّر العثور على المحاولة الأصلية.")
    _check_request(current, owner, reviewer["id"], row["command"])
    if current.get("state") == "COMMITTED":
        return current
    if current.get("prepare_token") != token:
        _error("preparation_assignment_attempt_changed", "تغيرت محاولة تجهيز الملف؛ تحقق من النتيجة الأصلية.")
    command = row["command"]
    actor, employee = await _authority(tx, owner, reviewer, command["responsible_employee_id"], fence=True)
    _, fresh = await _plan(tx, owner, command)
    if _plan_identity(fresh) != _plan_identity(prepared["planned"]):
        _error("reviewed_selection_stale", "تغيرت هوية إحدى القطع أثناء إنشاء الملف.")
    await require_current_under_review(tx, user_id=owner, pieces=fresh)
    for number in sorted({piece["order_number"] for piece in fresh}):
        for collection in ("unified_orders", "order_review_workflows"):
            fenced = await tx[collection].update_one({"user_id": owner, "order_number": number},
                {"$inc": {"preparation_assignment_fence": 1}})
            if fenced.matched_count != 1:
                _error("preparation_assignment_order_changed", "تغير الطلب أثناء اعتماد الإسناد.")
    now = _now()
    assigned = command["supplier_assignment"]["mode"] == "assign"
    execution = "in_progress" if assigned else "assigned"
    registry = {"id": "registry-" + row["client_request_id"], **key,
        "status": "ready", "batch_id": row["batch_id"], "file_number": row["file_number"],
        "file_title": command["file_title"], "file_date": row["file_date"], "file_date_display": row["file_date_display"],
        "file_name": registry_module.preparation_file_name(command["file_title"], row["file_date"], prepared["physical_quantity"]),
        "expected_quantity": command["expected_quantity"], "allocated_quantity": command["expected_quantity"],
        "selected_product_count": command["selected_product_count"],
        "order_count": len({line["order_number"] for line in prepared["lines"]}),
        "responsible_employee_id": employee["id"], "responsible_employee_name": _actor_view(employee)["name"],
        "responsible_employee_email": _text(employee.get("email")),
        "schedule_mode": command["schedule_mode"], "required_due_at": _instant(command["required_due_at"]),
        "created_at": row["created_at"], "registered_at": now, "updated_at": now,
        "created_by": actor["id"], "created_by_name": _actor_view(actor)["name"],
        "execution_status": execution, "piece_count": prepared["physical_quantity"],
        **({"physical_quantity": prepared["physical_quantity"]} if prepared["bundle_references"] else {}),
        "piece_registry_status": "ready", "piece_registry_materialized_at": now,
        "assignment_request_id": row["id"], "supplier_assignment": command["supplier_assignment"],
        "request_fingerprint": row["request_fingerprint"], "mezan_only": True,
        "salla_updated": False, "qoyod_updated": False}
    batch = {**registry, "id": row["batch_id"], "title": "تجهيز المنتجات", "ready_at": now,
        "registry_status": "ready", "lines": prepared["lines"], "card_count": len(prepared["lines"]),
        "order_numbers": sorted({line["order_number"] for line in prepared["lines"]}),
        "selections": command["selections"], "pdf_sha256": prepared["pdf_sha256"], "pdf_size_bytes": prepared["pdf_size_bytes"],
        "customer_options_auto_repaired_line_count": prepared["option_repair"]["repaired_line_count"],
        "customer_options_auto_refreshed_order_numbers": prepared["option_repair"]["refreshed_order_numbers"],
        **({"physical_print_lines": prepared["physical_print_lines"],
            "card_count": len(prepared["physical_print_lines"])} if prepared["bundle_references"] else {})}
    pieces = await piece_module.prepare_preparation_piece_documents(tx, user_id=owner, registry=registry, batch=batch)
    if len(pieces) != prepared["physical_quantity"]:
        _error("preparation_assignment_piece_count_mismatch", "تعذّر التحقق من جميع القطع المادية.")
    if {piece["piece_id"]: piece["bundle_supplier_reference"] for piece in pieces
            if piece.get("bundle_supplier_reference") is not None} != prepared["bundle_references"]:
        _error("bundle_supplier_identity_changed", "تغيّرت مكونات البكج أثناء إنشاء الملف.")
    for piece in pieces:
        piece.update({"assignment_request_id": row["id"], "assignment_responsible_employee_id": employee["id"],
            "assignment_supplier_id": command["supplier_assignment"].get("supplier_id"),
            "schedule_mode": command["schedule_mode"], "required_due_at": _instant(command["required_due_at"])})
        if assigned:
            piece.update({"started_at": now, "started_by": actor["id"]})
    await _instructions(tx, owner, actor["id"], pieces, supplier=assigned)
    supplier = await _supplier(tx, owner, command, pieces)
    if assigned:
        registry.update({"started_at": now, "started_by": actor["id"], "started_by_name": _actor_view(actor)["name"]})
        batch.update({"started_at": now, "started_by": actor["id"]})
    allocations = [{"id": uuid5(NAMESPACE_URL, row["id"] + ":" + str(fact)).hex,
        "user_id": owner, "batch_id": row["batch_id"], "status": "committed",
        "order_number": fact[0], "order_item_id": fact[1], "unit_index": fact[2], "group_key": fact[3],
        "ready_item_id": fact[4], "ready_unit_id": fact[5], "reserved_at": now, "committed_at": now,
        "assignment_request_id": row["id"]} for fact in _plan_identity(fresh)]
    await tx[REGISTRY].insert_one(deepcopy(registry))
    await tx[batch_module.BATCHES].insert_one(deepcopy(batch))
    await tx[PREPARATION_UNIT_ALLOCATIONS].insert_many(allocations, ordered=True)
    await tx[PIECES].insert_many(deepcopy(pieces), ordered=True)
    dispatch = None
    if assigned:
        result = await dispatch_module.publish_supplier_dispatch(
            tx, user_id=owner, client_request_id="assignment-v2:" + row["client_request_id"],
            registry_by_file={row["file_number"]: registry}, selected_by_file={row["file_number"]: pieces},
            supplier=supplier, employee_id=employee["id"], employee_name=_actor_view(employee)["name"],
            actor=actor, atomic=True, assignment_request_id=row["id"],
            dispatch_id="sdv1_" + uuid5(NAMESPACE_URL, row["id"] + ":dispatch").hex)
        dispatch = result["dispatch"]
    piece_ids = sorted(piece["piece_id"] for piece in pieces)
    file_view = {**preparation_file_view(registry), "schedule_mode": command["schedule_mode"],
        "required_due_at": command["required_due_at"]}
    receipt = {"request_fingerprint": row["request_fingerprint"], "file": file_view,
        "batch": {"batch_id": row["batch_id"], "status": "ready", "allocated_quantity": command["expected_quantity"],
            **({"physical_quantity": len(pieces)} if prepared["bundle_references"] else {}),
            "selections": command["selections"], "piece_ids": piece_ids},
        "responsible_employee_id": employee["id"], "supplier_assignment": command["supplier_assignment"],
        "piece_ids": piece_ids, "piece_count": len(pieces), "dispatch": ({
            "id": dispatch["id"], "supplier_id": supplier["id"], "supplier_name": _text(supplier.get("company_name")),
            "responsible_employee_id": employee["id"], "piece_ids": piece_ids,
            "piece_count": len(pieces), "status": "sent"} if dispatch else None),
        "destination": "in_progress" if assigned else "waiting", "committed_at": now.isoformat(),
        **({"physical_quantity": len(pieces), "commercial_quantity": command["expected_quantity"],
            "bundle_references": prepared["bundle_references"]} if prepared["bundle_references"] else {})}
    receipt = jsonable_encoder(receipt)
    event = {"id": row["id"], "user_id": owner, "event_type": "preparation_employee_supplier_assignment_committed",
        "client_request_id": row["client_request_id"], "assignment_request_id": row["id"],
        "batch_id": row["batch_id"], "file_number": row["file_number"], "piece_ids": piece_ids,
        "piece_count": len(pieces), "responsible_employee_id": employee["id"],
        "supplier_assignment": command["supplier_assignment"], "actor_id": actor["id"],
        "actor_name": _actor_view(actor)["name"], "occurred_at": now,
        "mezan_only": True, "salla_updated": False, "qoyod_updated": False}
    await tx[EVENTS].insert_one(deepcopy(event))
    await tx[PIECE_EVENTS].insert_one(deepcopy(event))
    receipt_fingerprint = _digest(receipt)
    saved = await tx[REQUESTS].update_one({**key, "state": "PENDING", "prepare_token": token},
        {"$set": {"state": "COMMITTED", "receipt": receipt, "receipt_fingerprint": receipt_fingerprint, "committed_at": now},
         "$unset": {"prepare_token": "", "prepare_lease_until": "", "safe_failure": ""}})
    if saved.modified_count != 1:
        _error("preparation_assignment_receipt_conflict", "تغيرت المحاولة أثناء اعتماد الإسناد.")
    return {**row, "state": "COMMITTED", "receipt": receipt, "receipt_fingerprint": receipt_fingerprint, "committed_at": now}


async def _verified_receipt(db, owner, row):
    """Verify original durable physical ownership, even after later work moves on."""
    receipt = row.get("receipt")
    command = row["command"]
    if (not isinstance(receipt, dict) or receipt.get("request_fingerprint") != row["request_fingerprint"]
            or row.get("receipt_fingerprint") != _digest(receipt)):
        return None
    commercial_count = command["expected_quantity"]
    count = receipt.get("physical_quantity", commercial_count)
    if type(count) is not int or count < commercial_count or count > commercial_count * 2:
        return None
    ids = receipt.get("piece_ids") or []
    if len(ids) != count or len(set(ids)) != count:
        return None
    file = await db[REGISTRY].find_one({"user_id": owner, "client_request_id": row["client_request_id"],
        "batch_id": row["batch_id"], "status": "ready", "assignment_request_id": row["id"],
        "request_fingerprint": row["request_fingerprint"]}, {"_id": 0, "id": 1})
    batch = await db[batch_module.BATCHES].find_one({"user_id": owner, "id": row["batch_id"],
        "status": "ready", "assignment_request_id": row["id"], "request_fingerprint": row["request_fingerprint"]},
        {"_id": 0, "selections": 1, "allocated_quantity": 1, "physical_quantity": 1})
    if (not file or not batch or batch.get("selections") != command["selections"]
            or batch.get("allocated_quantity") != commercial_count
            or batch.get("physical_quantity", commercial_count) != count):
        return None
    allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find({"user_id": owner,
        "batch_id": row["batch_id"], "status": "committed", "assignment_request_id": row["id"]},
        {"_id": 0, "order_number": 1, "order_item_id": 1, "unit_index": 1}).to_list(commercial_count + 1)
    pieces = await db[PIECES].find({"user_id": owner, "batch_id": row["batch_id"],
        "piece_id": {"$in": ids}, "assignment_request_id": row["id"],
        "assignment_responsible_employee_id": command["responsible_employee_id"],
        "assignment_supplier_id": command["supplier_assignment"].get("supplier_id")},
        {"_id": 0}).to_list(count + 1)
    facts = lambda values: {(value.get("order_number"), value.get("order_item_id"), value.get("unit_index")) for value in values}
    if (len(allocations) != commercial_count or len(pieces) != count or len(facts(pieces)) != commercial_count
            or facts(allocations) != facts(pieces) or {piece["piece_id"] for piece in pieces} != set(ids)):
        return None
    from bundle_supplier_pieces import validate_supplier_leaf_piece, supplier_leaf_reference, supplier_leaf_piece_id
    by_commercial = {}
    refs = {}
    for piece in pieces:
        identity = (piece["order_number"], piece["order_item_id"], piece["unit_index"])
        by_commercial.setdefault(identity, []).append(piece)
    for members in by_commercial.values():
        if not any(piece.get("bundle_supplier_reference") is not None for piece in members):
            if len(members) != 1:
                return None
            continue
        try:
            authorities = [await validate_supplier_leaf_piece(db, owner, piece) for piece in members]
            if any(authority is None for authority in authorities):
                return None
            unit = authorities[0][0]
            expected = {supplier_leaf_piece_id(owner, unit, supplier_leaf_reference(unit, leaf, index))
                for leaf in unit["bundle_leaves"] for index in range(1, int(leaf["quantity"]) + 1)}
            if {piece["piece_id"] for piece in members} != expected:
                return None
            refs.update({piece["piece_id"]: piece["bundle_supplier_reference"] for piece in members})
        except (HTTPException, ValueError, TypeError, KeyError):
            return None
    if refs != (receipt.get("bundle_references") or {}):
        return None
    if command["supplier_assignment"]["mode"] == "assign":
        proof = receipt.get("dispatch") or {}
        dispatch = await db[dispatch_module.DISPATCHES].find_one({"user_id": owner, "id": proof.get("id"),
            "assignment_request_id": row["id"], "supplier_id": command["supplier_assignment"]["supplier_id"],
            "sent_by_id": command["responsible_employee_id"], "performed_by_id": row["created_by"]},
            {"_id": 0, "piece_ids": 1, "piece_count": 1, "status": 1, "sent_at": 1,
             "source_file_numbers": 1, "source_files.cards.piece_id": 1})
        if (not dispatch or dispatch.get("status") not in {"sent", "ready", "partial_received", "received"}
                or not dispatch.get("sent_at") or dispatch.get("piece_count") != count
                or set(dispatch.get("piece_ids") or []) != set(ids)
                or dispatch.get("source_file_numbers") != [row["file_number"]]
                or {card.get("piece_id") for source in dispatch.get("source_files") or []
                    for card in source.get("cards") or []} != set(ids)):
            return None
    elif receipt.get("dispatch") is not None or receipt.get("destination") != "waiting":
        return None
    return receipt


async def _abandon_preparation(tx, owner, request_id, token, reason):
    """A late failing builder cannot disown another builder's committed work."""
    key = {"user_id": owner, "client_request_id": request_id}
    current = await tx[REQUESTS].find_one(key, {"_id": 0})
    if not current or current.get("state") != "PENDING" or current.get("prepare_token") != token:
        return current, False
    saved = await tx[REQUESTS].update_one({**key, "state": "PENDING", "prepare_token": token},
        {"$set": {"prepare_lease_until": _now(), "safe_failure": reason}})
    return current, saved.matched_count == 1


async def _response(db, owner, actor, request_id, row):
    result = {"schema": 2, "contract": CONTRACT, "session_scope": session_scope(owner, actor["id"]),
        "actor": _actor_view(actor), "client_request_id": request_id,
        "state": "ABSENT", "command": None, "receipt": None}
    if row is None:
        return result
    _check_request(row, owner, actor["id"])
    result.update({"state": "PENDING", "command": row["command"]})
    if row.get("state") == "COMMITTED":
        receipt = await _verified_receipt(db, owner, row)
        if receipt is not None:
            result.update({"state": "COMMITTED", "receipt": receipt})
    return result


def make_preparation_assignment_v2_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/preparation-assignment-v2", tags=["Preparation assignment"])
    ready = False
    lock = asyncio.Lock()

    async def ensure_indexes():
        nonlocal ready
        if ready:
            return
        async with lock:
            if ready:
                return
            await db[REQUESTS].create_index([("user_id", ASCENDING), ("client_request_id", ASCENDING)],
                unique=True, name="uq_preparation_assignment_request_v2")
            await registry_module.ensure_preparation_file_registry_indexes(db)
            await batch_module.ensure_preparation_batch_indexes(db)
            await piece_module.ensure_piece_operation_indexes(db)
            await dispatch_module.ensure_supplier_dispatch_indexes(db)
            ready = True

    @router.get("/options")
    async def options(user: dict = Depends(current_user)):
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        actor, _ = await _authority(db, owner, reviewer)
        suppliers = await dispatch_module._active_suppliers(db, user_id=owner)
        return {"schema": 2, "contract": CONTRACT, "session_scope": session_scope(owner, actor["id"]),
            "actor": _actor_view(actor), "capabilities": {"assign": True, "defer": True, "atomic_publication": True},
            "suppliers": [{"id": _text(supplier.get("id")),
                "name": _text(supplier.get("company_name") or supplier.get("contact_person") or supplier.get("id")),
                "category_ids": sorted({_text(value) for value in supplier.get("category_ids") or []} - {""}),
                "service_ids": sorted({_text(value) for value in supplier.get("service_ids") or []} - {""})}
                for supplier in suppliers if _text(supplier.get("id"))]}

    @router.get("/requests/{client_request_id}")
    async def request_status(client_request_id: str, user: dict = Depends(current_user)):
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        actor, _ = await _authority(db, owner, reviewer)
        row = await db[REQUESTS].find_one({"user_id": owner, "client_request_id": client_request_id}, {"_id": 0})
        return await _response(db, owner, actor, client_request_id, row)

    @router.post("/requests")
    async def create_request(payload: dict[str, Any], user: dict = Depends(current_user)):
        reviewer = _require_reviewer(user)
        owner = _merchant_user_id(reviewer)
        existing = await db[REQUESTS].find_one({"user_id": owner,
            "client_request_id": payload.get("client_request_id") if isinstance(payload.get("client_request_id"), str) else ""}, {"_id": 0})
        try:
            parsed = AssignmentRequest.model_validate(payload)
        except (ValidationError, ValueError):
            _error("preparation_assignment_command_invalid", "بيانات الإسناد غير مكتملة؛ حدّد المورد أو خيار التأجيل.", 422,
                not_applied=existing is None)
        command = _command(parsed)
        if parsed.expected_session_scope != session_scope(owner, reviewer["id"]):
            _error("preparation_assignment_session_scope_changed", "تغير الحساب؛ أعد فتح خيارات الإسناد.", not_applied=existing is None)
        if existing:
            _check_request(existing, owner, reviewer["id"], command)
            if existing.get("state") == "COMMITTED":
                actor, _ = await _authority(db, owner, reviewer)
                return await _response(db, owner, actor, parsed.client_request_id, existing)
        await ensure_indexes()
        token = uuid4().hex
        row, started = await operational_owner(db, owner,
            lambda tx: _begin_request(tx, owner, reviewer, command, token), profile=PROFILE)
        if not started:
            actor, _ = await _authority(db, owner, reviewer)
            return await _response(db, owner, actor, parsed.client_request_id, row)
        try:
            prepared = await _prepare_assignment(db, owner, reviewer, row)
        except Exception as exc:
            # No allocation, piece, registry or dispatch has been published.
            # Clear only our preparation lease; never delete a committed row.
            latest, not_applied = await operational_owner(db, owner,
                lambda tx: _abandon_preparation(tx, owner, parsed.client_request_id, token, type(exc).__name__),
                profile=PROFILE)
            if latest and latest.get("state") == "COMMITTED":
                actor, _ = await _authority(db, owner, reviewer)
                return await _response(db, owner, actor, parsed.client_request_id, latest)
            if isinstance(exc, HTTPException):
                detail = exc.detail if isinstance(exc.detail, dict) else {"code": "preparation_assignment_prepare_failed"}
                raise HTTPException(exc.status_code, detail={**detail, "not_applied": not_applied}) from exc
            _error("preparation_assignment_prepare_failed", "تعذّر تجهيز الملف؛ تحقق من المحاولة الأصلية.", 503,
                not_applied=not_applied)
        # Once publication starts, unknown commit replies remain uncertain. A
        # read-only status lookup, never a second generated command, resolves it.
        committed = await operational_owner(db, owner,
            lambda tx: _publish_assignment(tx, owner, reviewer, row, prepared, token), profile=PROFILE)
        try:
            batch = await db[batch_module.BATCHES].find_one({"user_id": owner, "id": row["batch_id"]}, {"_id": 0})
            reconciliation = await batch_module._reconcile_batch_orders(db, user_id=owner, batch=batch, actor=reviewer)
            await db[batch_module.BATCHES].update_one({"user_id": owner, "id": row["batch_id"]}, {"$set": reconciliation})
        except Exception:
            # Local assignment is already durable. Existing reconciliation owns
            # provider status; its failure cannot undo or duplicate this receipt.
            pass
        actor, _ = await _authority(db, owner, reviewer)
        return await _response(db, owner, actor, parsed.client_request_id, committed)

    return router
