"""Native count documents and reviewed variances; never a second stock ledger.

Draft/review changes use single-document revision CAS. Physical effects use the
existing admission fence, owner transaction, cost authority and sealed V2 writer.
No writer activation or default account creation is performed by this module.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import os

from fastapi import HTTPException
from bson import BSON
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern

from accounting_atomic import SessionDatabase, atomic_owner
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2, verify_active_opening_v2
from accounting_module_contract import accounting_permissions_for_user, require_accounting_permission
from accounting_module_readiness import build_accounting_module_status
from accounting_periods import assert_open_journal_periods
from accounting_write_control import fresh_actor
from component_status_policy import component_is_active
from fulfillment_v2_routes import _actor_context, _warehouse_allowed
from inventory_receipt_service import InventoryLocationCapacityError, place_inventory_receipt
from inventory_write_gate import admitted, read_control, root_db, writes_paused
from operational_inventory_projection import _projection, records as inventory_records
from product_cost_revision import bump_product_cost_revision
from purchase_receiving_service import (COST_POLICY, COST_STATES, LOCATIONS, RECEIPTS,
    approved_account_mappings, assert_opening_inventory_initialized, identity_key,
    now, number, same_identity, stable_id, stored_number)

SCHEMA = "g47-stocktake-v1"
COUNTS = "mz2_inventory_stocktakes"
MAX_LINES = 500
PERMISSIONS = {"view": "accounting.inventory.view", "count": "accounting.inventory.count",
               "review": "accounting.inventory.adjustments.review", "post": "accounting.inventory.adjustments.post"}
MESSAGES = {
    "stocktake_posting_disabled": "ترحيل فروق الجرد غير مفعّل في هذه البيئة.",
    "stocktake_revision_conflict": "تغير الجرد في جلسة أخرى؛ أعد تحميل النسخة المحفوظة.",
    "stocktake_snapshot_changed": "تغير الرصيد أو التكلفة منذ بدء المقارنة؛ حدّث المقارنة ثم أعد المراجعة.",
    "stocktake_count_required": "أدخل الكمية الفعلية لصنف واحد على الأقل.",
    "stocktake_account_mapping_required": "اختر حساب المخزون المعتمد وحساب الفروقات الموجود في دليل المصروفات.",
    "stocktake_cost_unknown": "التكلفة المعتمدة غير مثبتة؛ لا يمكن ترحيل فرق بقيمة مفترضة.",
    "stocktake_lot_unavailable": "تحتاج هوية الدفعة أو حالتها أو حجوزاتها إلى مراجعة قبل التسوية.",
    "stocktake_reserved_quantity": "الكمية الفعلية أقل من الكمية المحجوزة؛ عالج الحجز قبل ترحيل الفرق.",
    "stocktake_capacity_exceeded": "الكمية الناتجة تتجاوز سعة خانة التخزين.",
    "stocktake_scope_empty": "لا توجد أرصدة مثبتة ضمن النطاق المحدد للجرد.",
    "stocktake_scope_limit": "اختر مستودعًا أو نطاقًا أصغر؛ الحد الأقصى للجرد 500 بند.",
    "stocktake_audit_limit": "بلغ الجرد حد سجل التدقيق؛ احتفظ بهذا الجرد وابدأ جردًا جديدًا لنطاق أصغر.",
}


def fail(code, status=409, **details):
    raise HTTPException(status, detail={"code": code, "message": MESSAGES.get(code, "تعذر إتمام خطوة الجرد؛ راجع الحالة والصلاحيات."), **details})


def posting_enabled():
    return os.environ.get("MEZAN_STOCKTAKE_POSTING_ENABLED") == "1"


def quantity(value, *, kind):
    if not isinstance(value, str) or not value.strip():
        fail("stocktake_invalid_quantity", 422)
    try:
        n = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        fail("stocktake_invalid_quantity", 422)
    if not n.is_finite() or n < 0 or n > Decimal("1000000000") or n != n.quantize(Decimal("0.000001")):
        fail("stocktake_invalid_quantity", 422)
    if kind == "product" and n != n.to_integral_value():
        fail("product_quantity_must_be_integral", 422)
    return n


def decimal_text(value):
    return format(value.normalize(), "f") if value else "0"


def compare_line(row):
    result = deepcopy(row)
    counted = row.get("counted_quantity")
    result.update(difference=None, difference_value=None)
    if counted is not None:
        delta = quantity(counted, kind=row["kind"]) - number(row["recorded_quantity"])
        result["difference"] = decimal_text(delta)
        if row.get("unit_cost") is not None:
            with localcontext() as ctx:
                ctx.prec = 60
                result["difference_value"] = format((delta * number(row["unit_cost"])).quantize(Decimal(".01"), rounding=ROUND_HALF_UP), ".2f")
    return result


def fingerprint(value):
    return stable_id("count-proof", value)


def _location_proof(location):
    return fingerprint({k: location.get(k) for k in ("id", "warehouse_id", "cabinet_id", "purpose", "state",
        "code", "barcode_value", "max_items", "occupancy", "valuation_status", "condition", "inspection_status")})


async def scope(db, user, capability="view"):
    actor = await fresh_actor(db, user)
    require_accounting_permission(actor, PERMISSIONS[capability])
    require_accounting_permission(actor, PERMISSIONS["view"])
    context = await _actor_context(db, actor)
    label = await db.users.find_one({"id": actor["id"]}, {"_id": 0, "name": 1}) or {}
    actor["name"] = str(label.get("name") or actor["id"])
    return actor, context["merchant_id"], context


def capabilities(actor):
    granted = set(accounting_permissions_for_user(actor))
    return {key: value in granted for key, value in PERMISSIONS.items()}


def _allowed(context, warehouse_ids):
    if not _warehouse_allowed(context, warehouse_ids):
        fail("inventory_warehouse_not_assigned", 403)


def _public(doc, *, summary=False):
    result = {k: deepcopy(v) for k, v in doc.items() if not k.startswith("_") and k != "user_id"}
    rows = [compare_line(row) for row in doc.get("lines", [])]
    counted = [row for row in rows if row.get("counted_quantity") is not None]
    variances = [row for row in counted if number(row["difference"], nonnegative=False) != 0]
    def total(positive):
        selected = [r for r in variances if (number(r["difference"], nonnegative=False) > 0) == positive]
        return None if any(r["difference_value"] is None for r in selected) else format(sum((abs(Decimal(r["difference_value"])) for r in selected), Decimal(0)), ".2f")
    result.update(counted_lines=len(counted), variance_lines=len(variances), total_lines=len(rows),
                  increase_value=total(True), decrease_value=total(False), schema_version=SCHEMA)
    if summary:
        result.pop("lines", None); result.pop("audit", None)
    else:
        result["lines"] = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    return result


async def posting_blockers(db, owner):
    blockers = []
    if not posting_enabled(): blockers.append("stocktake_posting_disabled")
    state = await read_control(root_db(db), owner)
    if writes_paused(state): blockers.append("physical_inventory_writes_paused")
    if (state or {}).get("writes_paused", True) is not False: blockers.append("mz2_writes_paused")
    return [{"code": code, "message": MESSAGES.get(code, "الكتابات المخزنية أو المحاسبية متوقفة بقرار المالك.")} for code in blockers]


async def context_for(db, user):
    actor, owner, context = await scope(db, user)
    warehouses = await db.warehouse_locations_warehouses.find({"user_id": owner, "status": {"$ne": "disabled"}}, {"_id": 0, "id": 1, "name": 1}).to_list(1001)
    if len(warehouses) > 1000: fail("stocktake_scope_limit")
    warehouses = [r for r in warehouses if _warehouse_allowed(context, [r["id"]])]
    _, mappings = await approved_account_mappings(db, owner)
    accounts = await db.expense_categories.find({"user_id": owner, "status": {"$nin": ["inactive", "disabled"]}}, {"_id": 0, "code": 1, "name": 1}).to_list(1001)
    if len(accounts) > 1000: fail("stocktake_scope_limit")
    blockers = await posting_blockers(db, owner)
    locations = await db[LOCATIONS].find({"user_id": owner, "cabinet_archive": {"$exists": False}}, {"_id": 0, "id": 1, "warehouse_id": 1,
        "code": 1, "display_code": 1, "occupancy.items.quantity": 1}).to_list(20001)
    if len(locations) > 20000: fail("stocktake_scope_limit")
    location_options = [{"id": row["id"], "warehouse_id": row["warehouse_id"],
        "label": row.get("display_code") or row.get("code") or row["id"],
        "stock_lines": len((row.get("occupancy") or {}).get("items") or [])}
        for row in locations if row.get("id") and (row.get("occupancy") or {}).get("items")
        and _warehouse_allowed(context, [row.get("warehouse_id")])]
    return {"schema_version": SCHEMA, "capabilities": capabilities(actor), "warehouses": warehouses,
            "locations": location_options,
            "inventory_accounts": mappings["inventory"], "variance_accounts": [r for r in accounts if r.get("code")],
            "posting_enabled": not blockers, "posting_blockers": blockers}


async def _load(db, owner, context, count_id):
    doc = await db[COUNTS].find_one({"_id": count_id, "user_id": owner})
    if not doc: fail("stocktake_not_found", 404)
    _allowed(context, doc["warehouse_ids"])
    return doc


async def get_count(db, user, count_id):
    actor, owner, context = await scope(db, user)
    doc = await _load(db, owner, context, count_id)
    blockers = await posting_blockers(db, owner)
    return {**_public(doc), "capabilities": capabilities(actor), "posting_enabled": not blockers, "posting_blockers": blockers}


async def list_counts(db, user):
    _, owner, context = await scope(db, user)
    rows = await db[COUNTS].find({"user_id": owner}).sort([("updated_at", -1), ("_id", 1)]).limit(201).to_list(201)
    return {"schema_version": SCHEMA, "read_only": True, "items": [_public(row, summary=True) for row in rows[:200] if _warehouse_allowed(context, row["warehouse_ids"])], "window_complete": len(rows) <= 200}


async def snapshot(db, owner, context, filters):
    async def read(scoped):
        projected = await _projection(scoped, owner)
        selected = [r for r in projected["items"] if
            (not filters.get("warehouse_ids") or r["warehouse_id"] in filters["warehouse_ids"]) and
            (not filters.get("location_ids") or r["location_id"] in filters["location_ids"]) and
            (not filters.get("row_ids") or r["id"] in filters["row_ids"]) and _warehouse_allowed(context, [r["warehouse_id"]])]
        if not selected: fail("stocktake_scope_empty", 422)
        if len(selected) > MAX_LINES: fail("stocktake_scope_limit", 422)
        selected_ids = {r["id"] for r in selected}
        if filters.get("row_ids") and set(filters["row_ids"]) != selected_ids: fail("stocktake_scope_changed")
        result = []
        for row in selected:
            location = await scoped[LOCATIONS].find_one({"user_id": owner, "id": row["location_id"]})
            matches = []
            for index, item in enumerate((location.get("occupancy") or {}).get("items") or []):
                lot = str(item.get("receipt_id") or item.get("lot_id") or item.get("id") or "")
                from operational_balance_store import digest
                if digest(["mz2-stock-row", owner, location["id"], lot or index]) == row["id"]: matches.append((index, item))
            if len(matches) != 1: fail("stocktake_lot_unavailable")
            index, item = matches[0]
            result.append({**{k: row.get(k) for k in ("id", "name", "sku", "kind", "unit", "location_id", "location_code", "warehouse_id", "warehouse_name", "preparation_state", "specifications", "unit_cost", "product_id", "resource_id", "variant_id")},
                "recorded_quantity": decimal_text(number(row["physical"])), "counted_quantity": None, "reason": "",
                "_location_proof": _location_proof(location), "_item": deepcopy(item), "_item_index": index})
        return result
    if isinstance(db, SessionDatabase): return await read(db)
    raw = root_db(db)
    async with await raw.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern("snapshot")):
            scoped = SessionDatabase(raw, session); scoped._owner = owner
            return await read(scoped)


def _event(actor, action, request_id, payload_hash, revision, reason=""):
    return {"at": now(), "actor_id": actor["id"], "actor_name": actor.get("name") or actor["id"], "actor_role": actor.get("role"), "action": action,
            "request_id": request_id, "payload_hash": payload_hash, "revision": revision, "reason": reason}


def _audit_row(row):
    return {k: deepcopy(row.get(k)) for k in ("id", "name", "kind", "product_id", "resource_id", "variant_id", "location_id", "location_code", "warehouse_id", "warehouse_name",
        "sku", "unit", "recorded_quantity", "counted_quantity", "unit_cost", "reason", "preparation_state", "specifications")}


def _audit_changes(doc, changes):
    fields = {key: {"before": deepcopy(doc.get(key)), "after": deepcopy(value)} for key, value in changes.items()
              if key != "lines" and not key.startswith("_") and doc.get(key) != value}
    result = {"fields": fields, "lines": []}
    if "lines" in changes:
        old = {r["id"]: r for r in doc["lines"]}; new = {r["id"]: r for r in changes["lines"]}
        for key in sorted(old.keys() | new.keys()):
            before, after = old.get(key), new.get(key)
            if before != after:
                result["lines"].append({"line_id": key, "name": (after or before).get("name"),
                    "before": _audit_row(before) if before else None, "after": _audit_row(after) if after else None,
                    "before_proof": before.get("_location_proof") if before else None,
                    "after_proof": after.get("_location_proof") if after else None})
    return result


def _bounded_document(doc):
    # Never truncate audit history or let Mongo's document limit turn a final
    # posting into a partly applied operation. The native transaction rolls back.
    if len(BSON.encode(doc)) > 15 * 1024 * 1024: fail("stocktake_audit_limit")


def _replayed(doc, payload, action):
    key = payload.get("request_id")
    digest = fingerprint({"action": action, "payload": payload})
    prior = [e for e in doc.get("audit", []) if e.get("request_id") == key]
    if prior:
        if len(prior) != 1 or prior[0]["payload_hash"] != digest: fail("stocktake_request_conflict")
        return True
    return False


async def create_count(db, user, payload):
    actor, owner, context = await scope(db, user, "count")
    if payload.get("warehouse_ids"): _allowed(context, payload["warehouse_ids"])
    count_id = stable_id("stocktake", owner, payload["request_id"])
    digest = fingerprint({"action": "create", "payload": payload})
    old = await db[COUNTS].find_one({"_id": count_id, "user_id": owner})
    if old:
        _allowed(context, old["warehouse_ids"])
        if old.get("_create_hash") != digest: fail("stocktake_request_conflict")
        return await get_count(db, user, count_id)
    rows = await snapshot(db, owner, context, payload)
    timestamp = now()
    doc = {"_id": count_id, "id": count_id, "user_id": owner, "schema_version": SCHEMA, "_create_hash": digest,
           "title": payload["title"].strip(), "status": "draft", "revision": 1, "lines": rows,
           "scope": {k: payload.get(k) or [] for k in ("warehouse_ids", "location_ids", "row_ids")},
           "warehouse_ids": sorted({r["warehouse_id"] for r in rows}), "created_by": actor["id"],
           "created_at": timestamp, "updated_at": timestamp,
           "audit": [_event(actor, "create", payload["request_id"], digest, 1)]}
    doc["audit"][0].update(from_status=None, to_status="draft", snapshot=[_audit_row(r) for r in rows])
    _bounded_document(doc)
    try: await db[COUNTS].insert_one(doc)
    except DuplicateKeyError:
        existing = await db[COUNTS].find_one({"_id": count_id, "user_id": owner})
        if not existing or existing.get("_create_hash") != digest: fail("stocktake_request_conflict")
    return await get_count(db, user, count_id)


async def _cas(db, owner, doc, actor, payload, action, changes):
    if len(doc.get("audit", [])) >= 2000: fail("stocktake_audit_limit")
    revision = doc["revision"] + 1
    event = _event(actor, action, payload["request_id"], fingerprint({"action": action, "payload": payload}), revision, payload.get("reason", ""))
    event.update(from_status=doc["status"], to_status=changes.get("status", doc["status"]), change_set=_audit_changes(doc, changes))
    if action in {"submit", "approve"}:
        event["snapshot"] = [compare_line(_audit_row(r)) for r in doc["lines"] if r.get("counted_quantity") is not None]
    if action == "approve": event["authorization_hash"] = fingerprint([doc["id"], revision, doc["lines"]])
    _bounded_document({**doc, **changes, "audit": [*doc["audit"], event]})
    saved = await db[COUNTS].find_one_and_update({"_id": doc["id"], "user_id": owner, "revision": payload["expected_revision"], "status": doc["status"]},
        {"$set": {**changes, "revision": revision, "updated_at": event["at"]}, "$push": {"audit": event}}, return_document=ReturnDocument.AFTER)
    if not saved:
        current = await db[COUNTS].find_one({"_id": doc["id"], "user_id": owner})
        if current and _replayed(current, payload, action): return current
        fail("stocktake_revision_conflict")
    return saved


async def save_count(db, user, count_id, payload):
    actor, owner, context = await scope(db, user, "count")
    doc = await _load(db, owner, context, count_id)
    if _replayed(doc, payload, "save"): return await get_count(db, user, count_id)
    if doc["status"] not in {"draft", "returned"}: fail("stocktake_not_editable")
    if doc["revision"] != payload["expected_revision"]: fail("stocktake_revision_conflict")
    rows = deepcopy(doc["lines"]); by_id = {r["id"]: r for r in rows}; seen = set()
    for entry in payload["lines"]:
        if entry["id"] in seen or entry["id"] not in by_id: fail("stocktake_line_unknown_or_duplicate", 422)
        seen.add(entry["id"]); row = by_id[entry["id"]]
        if entry["unit"] != row["unit"]: fail("stocktake_native_unit_required", 422)
        counted = entry.get("counted_quantity")
        row["counted_quantity"] = None if counted is None else decimal_text(quantity(counted, kind=row["kind"]))
        row["reason"] = entry.get("reason", "").strip()
    await _cas(db, owner, doc, actor, payload, "save", {"lines": rows, "title": payload["title"].strip()})
    return await get_count(db, user, count_id)


async def _accounts(db, owner, inventory_id, variance_code):
    _, mappings = await approved_account_mappings(db, owner)
    category = await db.expense_categories.find_one({"user_id": owner, "code": variance_code, "status": {"$nin": ["inactive", "disabled"]}})
    if inventory_id not in {r["entity_id"] for r in mappings["inventory"]} or not category:
        fail("stocktake_account_mapping_required", 422)
    return category


async def _live_rows(db, owner, context, doc):
    fresh = await snapshot(db, owner, context, {"row_ids": [r["id"] for r in doc["lines"]]})
    by_id = {r["id"]: r for r in fresh}
    for row in doc["lines"]:
        current = by_id.get(row["id"])
        if not current or any(current.get(k) != row.get(k) for k in ("_location_proof", "unit_cost", "unit", "recorded_quantity")):
            fail("stocktake_snapshot_changed")
    return fresh


async def act_on_count(db, user, count_id, payload):
    action = payload["action"]
    actor, owner, context = await scope(db, user, "review" if action in {"approve", "reject", "return"} else "count")
    doc = await _load(db, owner, context, count_id)
    if _replayed(doc, payload, action): return await get_count(db, user, count_id)
    if doc["revision"] != payload["expected_revision"]: fail("stocktake_revision_conflict")
    if action in {"submit", "refresh_snapshot"}:
        if doc["status"] not in {"draft", "returned"}: fail("stocktake_not_editable")
    elif doc["status"] != "submitted" and not (action in {"return", "reject"} and doc["status"] == "approved"):
        fail("stocktake_not_submitted")
    changes = {}
    if action == "refresh_snapshot":
        rows = await snapshot(db, owner, context, doc["scope"])
        old = {r["id"]: r for r in doc["lines"]}
        for row in rows:
            previous = old.get(row["id"])
            if previous and previous["unit"] == row["unit"]:
                row.update(counted_quantity=previous.get("counted_quantity"), reason=previous.get("reason", ""))
        changes.update(lines=rows, warehouse_ids=sorted({r["warehouse_id"] for r in rows}))
    elif action == "submit":
        await _live_rows(db, owner, context, doc)
        rows = [compare_line(row) for row in doc["lines"] if row.get("counted_quantity") is not None]
        if not rows: fail("stocktake_count_required", 422)
        differences = [r for r in rows if Decimal(r["difference"]) != 0]
        for row in differences:
            if not (row.get("reason") or payload.get("reason", "").strip()): fail("stocktake_reason_required", 422)
            if row.get("unit_cost") is None: fail("stocktake_cost_unknown")
        if differences: await _accounts(db, owner, payload.get("inventory_account_id"), payload.get("variance_account_code"))
        changes.update(status="submitted", submitted_by=actor["id"], submitted_at=now(), reason=payload.get("reason", "").strip(),
            inventory_account_id=payload.get("inventory_account_id"), variance_account_code=payload.get("variance_account_code"))
    elif action == "approve":
        await _live_rows(db, owner, context, doc)
        if _public(doc)["variance_lines"]: await _accounts(db, owner, doc.get("inventory_account_id"), doc.get("variance_account_code"))
        changes.update(status="approved", approved_by=actor["id"], approved_at=now(), approved_revision=doc["revision"] + 1,
            posting_operation_id=stable_id("stocktake-post", owner, doc["id"], doc["revision"] + 1))
    elif action in {"reject", "return"}:
        if not payload.get("reason", "").strip(): fail("stocktake_reason_required", 422)
        changes.update(status="rejected" if action == "reject" else "returned", reviewed_by=actor["id"], reviewed_at=now(), review_reason=payload["reason"].strip(),
            posting_operation_id=None, approved_by=None, approved_at=None, approved_revision=None)
    else: fail("stocktake_action_invalid", 422)
    await _cas(db, owner, doc, actor, payload, action, changes)
    return await get_count(db, user, count_id)


async def _assert_posting_ready(db, owner):
    if not posting_enabled(): fail("stocktake_posting_disabled", 423)
    cutover, _ = await approved_account_mappings(db, owner)
    verified = await verify_active_opening_v2(db._db, user_id=owner, cutover=cutover, mongo_session=db._session)
    if not build_accounting_module_status(cutover, opening_posted_verified=verified)["cutover"]["safe_active"]:
        fail("stocktake_accounting_not_safe_active")
    await assert_opening_inventory_initialized(db, owner, cutover)


async def _post_lines(scoped, owner, context, doc):
    await _live_rows(scoped, owner, context, doc)
    projection = await _projection(scoped, owner)
    projected = {r["id"]: r for r in projection["items"]}
    changed = [compare_line(r) for r in doc["lines"] if r.get("counted_quantity") is not None and quantity(r["counted_quantity"], kind=r["kind"]) != number(r["recorded_quantity"])]
    for row in changed:
        live = projected[row["id"]]
        if row["kind"] == "component":
            resource = await scoped.mezan_cost_resources_v2.find_one({"user_id": owner, "id": row["resource_id"]})
            if not resource or not component_is_active(resource): fail("stocktake_lot_unavailable")
        if live.get("unit_cost") is None: fail("stocktake_cost_unknown")
        if live.get("reserved") is None or live.get("available") is None or number(live.get("held") or 0) > 0 or live.get("availability_issues"):
            fail("stocktake_lot_unavailable")
        reserved = number(live["reserved"])
        # Product leaves in bundles have native holds outside product-order reservations.
        if row["kind"] == "product":
            units = await scoped.mezan_component_consumption_units_v1.find({"user_id": owner, "state": "reserved", "bundle_leaves": {"$exists": True}}).to_list(20001)
            if len(units) > 20000: fail("stocktake_scope_limit")
            for unit in units:
                for allocation in unit.get("allocations") or []:
                    if ((allocation.get("inventory_identity") or {}).get("item_type") == "product" and allocation.get("location_id") == row["location_id"]
                            and allocation.get("lot_id") == (row["_item"].get("receipt_id") or row["_item"].get("lot_id"))): reserved += number(allocation["quantity"])
        if quantity(row["counted_quantity"], kind=row["kind"]) < reserved: fail("stocktake_reserved_quantity")
    return changed


async def post_count(db, user, count_id, payload):
    actor, owner, context = await scope(db, user, "post")
    current = await _load(db, owner, context, count_id)
    if current.get("posting_operation_id") != payload["operation_id"]: fail("stocktake_operation_mismatch")
    if current["status"] == "posted": return await get_count(db, user, count_id)
    if not posting_enabled(): fail("stocktake_posting_disabled", 423)
    if current["status"] != "approved": fail("stocktake_not_approved")
    async def commit(scoped):
        active_actor, current_owner, active_context = await scope(scoped, user, "post")
        if current_owner != owner: fail("stocktake_actor_scope_changed", 403)
        doc = await _load(scoped, owner, active_context, count_id)
        if doc.get("posting_operation_id") != payload["operation_id"]: fail("stocktake_operation_mismatch")
        if doc["status"] == "posted": return _public(doc)
        if doc["status"] != "approved" or doc["revision"] != payload["expected_revision"]: fail("stocktake_revision_conflict")
        await _assert_posting_ready(scoped, owner)
        changed = await _post_lines(scoped, owner, active_context, doc)
        if changed: await _accounts(scoped, owner, doc.get("inventory_account_id"), doc.get("variance_account_code"))
        timestamp = now(); entries = []
        for index, row in enumerate(changed):
            amount = abs(Decimal(row["difference_value"]))
            if not amount: continue
            increase = Decimal(row["difference"]) > 0
            for suffix, entity_type, entity_id, sub_account, side in [
                ("inventory", "asset", doc["inventory_account_id"], "inventory", "debit" if increase else "credit"),
                ("variance", "expense", doc["variance_account_code"], None, "credit" if increase else "debit")]:
                entries.append({"leg_key": f"line-{index}-{suffix}", "entity_type": entity_type, "entity_id": entity_id,
                    "sub_account": sub_account, "entry_type": "inventory_adjustment", "side": side, "amount": format(amount, ".2f"),
                    "metadata": {"stocktake_id": count_id, "line_id": row["id"], "accounting_at": timestamp}})
        journal_id = None
        if entries:
            await assert_open_journal_periods(scoped, owner, entries)
            journal = await post_journal_v2(scoped._db, user_id=owner, actor_id=active_actor["id"], actor_name=active_actor["name"],
                idempotency_key=payload["operation_id"], txn_type="inventory_adjustment", source="inventory_stocktake", effective_at=timestamp,
                entries=entries, metadata={"stocktake_id": count_id, "approved_by": doc["approved_by"], "approved_revision": doc["approved_revision"],
                    "cost_policy_version": COST_POLICY, "reason": doc["reason"]}, mongo_session=scoped._session)
            journal_id = journal["group"]["txn_group_id"]
        receipts = []
        # A count approves the final per-lot quantities. Apply reductions first
        # so a full location can accept an offsetting increase in another lot.
        for row in sorted(changed, key=lambda r: (Decimal(r["difference"]) > 0, r["location_id"], r["id"])):
            delta = Decimal(row["difference"])
            location = await scoped[LOCATIONS].find_one({"id": row["location_id"], "user_id": owner})
            cabinet = await scoped.warehouse_locations_cabinets.find_one({"id": location.get("cabinet_id"), "user_id": owner}) or {}
            if (location.get("purpose") or cabinet.get("purpose")) != "permanent_storage": fail("inventory_location_not_permanent")
            items = (location.get("occupancy") or {}).get("items") or []
            total = sum((number(item["quantity"]) for item in items), Decimal(0))
            if number((location.get("occupancy") or {}).get("total_quantity")) != total: fail("stocktake_occupancy_reconciliation_required")
            if location.get("max_items") is not None and total + delta > number(location["max_items"]): fail("stocktake_capacity_exceeded")
            identity = {k: row["_item"].get(k) for k in ("item_type", "product_id", "mezan_product_id", "salla_product_id", "variant_id", "salla_variant_id", "resource_id", "category_id", "category", "code", "sku", "configuration_key", "product_name") if row["_item"].get(k) is not None}
            if row["kind"] == "component": identity["item_type"] = "stock_component"
            else: identity.update(item_type="product", product_id=row["product_id"], mezan_product_id=row["product_id"],
                                  salla_product_id=str(identity.get("salla_product_id") or ""), variant_id=row.get("variant_id"))
            if delta > 0:
                for item in items:
                    if number(item["quantity"]) > 0 and (not same_identity(item, identity) or item.get("configuration_key") != identity.get("configuration_key")):
                        fail("inventory_location_product_mismatch")
                receipt_id = stable_id("stocktake-receipt", payload["operation_id"], row["id"])
                proof = {"schema_version": SCHEMA, "source_type": "inventory_adjustment", "source_id": count_id, "source_line_id": row["id"],
                    "stocktake_id": count_id, "approved_by": doc["approved_by"], "approved_revision": doc["approved_revision"],
                    "accounting_txn_group_id": journal_id, "zero_value_variance": not entries,
                    "posting_operation_id": payload["operation_id"], "authorization_hash": fingerprint([doc["id"], doc["approved_revision"], doc["lines"]]),
                    "preparation_state": row["preparation_state"], "specifications": deepcopy(row["specifications"]),
                    "location_id": location["id"], "warehouse_id": location["warehouse_id"], "quantity": stored_number(delta),
                    "unit_purchase_cost": row["unit_cost"], "total_purchase_cost": format(abs(Decimal(row["difference_value"])), ".2f"),
                    "cost_policy_version": COST_POLICY, "occurred_at": timestamp, "posted_at": timestamp, "status": "posted", **identity}
                await scoped[RECEIPTS].insert_one({"_id": receipt_id, "id": receipt_id, "receipt_id": receipt_id, "user_id": owner, **proof})
                # Do not copy manufacturing/component-consumption provenance from the counted lot.
                item = {**proof, "receipt_id": receipt_id, "lot_id": receipt_id, "placed_at": timestamp}
                await place_inventory_receipt(scoped, merchant_id=owner, location_id=location["id"], receipt_id=receipt_id,
                    inventory_item=item, quantity=stored_number(delta), scanned_barcode=location.get("barcode_value") or location.get("code"), occurred_at=timestamp)
                receipts.append(receipt_id)
            else:
                index = row["_item_index"]
                if items[index] != row["_item"]: fail("stocktake_snapshot_changed")
                items[index]["quantity"] = stored_number(number(items[index]["quantity"]) + delta)
                next_total = total + delta
                await scoped[LOCATIONS].update_one({"id": location["id"], "user_id": owner}, {"$set": {
                    "occupancy.items": items, "occupancy.total_quantity": stored_number(next_total),
                    "state": "occupied" if next_total else "empty", "updated_at": timestamp}})
            await scoped[COST_STATES].update_one({"_id": identity_key(owner, identity), "user_id": owner, "authoritative": True, "cost_policy_version": COST_POLICY},
                {"$set": {"last_stocktake_id": count_id, "updated_at": timestamp}})
        if changed: await bump_product_cost_revision(scoped, owner)
        event = _event(active_actor, "post", payload["operation_id"], fingerprint(payload), doc["revision"] + 1, doc["reason"])
        event.update(from_status="approved", to_status="posted", txn_group_id=journal_id,
                     snapshot=[compare_line(_audit_row(r)) for r in changed])
        _bounded_document({**doc, "audit": [*doc["audit"], event]})
        await scoped[COUNTS].update_one({"_id": count_id, "user_id": owner, "status": "approved", "revision": doc["revision"]}, {"$set": {
            "status": "posted", "revision": doc["revision"] + 1, "posted_by": active_actor["id"], "posted_at": timestamp,
            "updated_at": timestamp, "txn_group_id": journal_id, "receipt_ids": receipts, "posted_differences": [{k: r.get(k) for k in
                ("id", "name", "sku", "kind", "unit", "product_id", "resource_id", "variant_id", "difference", "difference_value", "unit_cost", "location_id", "location_code", "warehouse_id", "warehouse_name")} for r in changed]}, "$push": {"audit": event}})
        return _public(await scoped[COUNTS].find_one({"_id": count_id, "user_id": owner}))
    async def execute(): return await atomic_owner(root_db(db), owner, commit)
    try:
        await admitted(root_db(db), owner, payload["operation_id"], execute)
    except AccountingLedgerV2Error as exc:
        fail(getattr(exc, "code", "stocktake_journal_rejected"))
    except InventoryLocationCapacityError:
        fail("stocktake_capacity_exceeded")
    except PyMongoError:
        # Commit outcome may be unknown: recover the saved count and replay the
        # SAME operation. Never relabel this as a proved abort or mint a key.
        fail("stocktake_commit_outcome_requires_retry", 503)
    return await get_count(db, user, count_id)


async def movements(db, user):
    """Bounded audit projection of existing receipts/consumption/posted counts.

    This is never used to calculate on-hand. Missing historical cost stays
    unknown; the current average is not substituted for a past issue cost.
    """
    _, owner, context = await scope(db, user)
    limit = 500
    async def metadata(collection, key):
        rows = await inventory_records(db, owner, collection)
        repeated = Counter(r.get(key) for r in rows if r.get(key))
        return {r[key]: r for r in rows if r.get(key) and repeated[r[key]] == 1}
    locations = await metadata(LOCATIONS, "id")
    warehouses = await metadata("warehouse_locations_warehouses", "id")
    products = await metadata("mezan_products_v2", "mezan_product_id")
    resources = await metadata("mezan_cost_resources_v2", "id")
    result, complete = [], True
    async def records(collection, query, time_field):
        nonlocal complete
        rows = await db[collection].find({"user_id": owner, **query}).sort([(time_field, -1), ("_id", -1)]).limit(limit + 1).to_list(limit + 1)
        if len(rows) > limit: complete = False
        return rows[:limit]
    def add(key, source, source_id, occurred_at, item, amount, direction, *, cost=None, value=None):
        location = locations.get(item.get("location_id"), {})
        warehouse_id = location.get("warehouse_id") or item.get("warehouse_id")
        if not _warehouse_allowed(context, [warehouse_id]): return
        resource = resources.get(item.get("resource_id"))
        product = products.get(item.get("mezan_product_id") or item.get("product_id"))
        canonical = resource or product or {}
        try: q = number(amount)
        except HTTPException: return
        if not q: return
        result.append({"id": key, "occurred_at": occurred_at, "source_type": source, "source_id": str(source_id or ""),
            "kind": "component" if resource or item.get("resource_id") or item.get("kind") == "component" else "product", "name": canonical.get("name") or item.get("product_name") or item.get("name") or "هوية تحتاج مراجعة",
            "sku": item.get("sku") or canonical.get("sku"), "unit": canonical.get("unit") or item.get("unit") or "piece",
            "quantity": decimal_text(q), "direction": direction, "location_id": item.get("location_id"),
            "location_code": location.get("display_code") or location.get("code") or item.get("location_code"),
            "warehouse_id": warehouse_id, "warehouse_name": warehouses.get(warehouse_id, {}).get("name") or item.get("warehouse_name"),
            "unit_cost": cost, "value": value})
    for row in await records(RECEIPTS, {"status": "posted"}, "occurred_at"):
        if row.get("source_type") == "inventory_adjustment": continue  # the complete posted count below owns that audit event
        add(str(row.get("id") or row["_id"]), row.get("source_type") or "receipt", row.get("source_id"),
            row.get("occurred_at") or row.get("posted_at"), {**row.get("inventory_identity", {}), **row}, row.get("quantity"), "in",
            cost=row.get("unit_purchase_cost"), value=row.get("total_purchase_cost"))
    for unit in await records("mezan_component_consumption_units_v1", {"state": "consumed"}, "consumed_at"):
        for index, allocation in enumerate(unit.get("allocations") or []):
            add(stable_id("inventory-issue", str(unit["_id"]), index), "component_consumption", unit.get("order_id"), unit.get("consumed_at"),
                {**allocation.get("inventory_identity", {}), **allocation}, allocation.get("quantity"), "out")
    for reservation in await records("mezan_inventory_reservations_v2", {"status": "consumed"}, "consumed_at"):
        for index, allocation in enumerate(reservation.get("allocations") or []):
            add(stable_id("inventory-issue", str(reservation["_id"]), index), "order_fulfillment", reservation.get("order_number"), reservation.get("consumed_at"),
                {**reservation, **allocation.get("eligibility_identity", {}), **allocation}, allocation.get("quantity"), "out")
    for doc in await records(COUNTS, {"status": "posted"}, "posted_at"):
        for row in doc.get("posted_differences") or []:
            delta = Decimal(row["difference"])
            add(stable_id("inventory-variance", doc["id"], row["id"]), "inventory_adjustment", doc["id"], doc.get("posted_at"), row,
                abs(delta), "in" if delta > 0 else "out", cost=row.get("unit_cost"), value=format(abs(Decimal(row["difference_value"])), ".2f") if row.get("difference_value") is not None else None)
    result.sort(key=lambda r: (str(r["occurred_at"] or ""), r["id"]), reverse=True)
    if len(result) > limit: complete = False
    return {"schema_version": SCHEMA, "read_only": True, "items": result[:limit], "window_complete": complete,
        "warnings": [] if complete else ["يعرض السجل أحدث 500 عملية متاحة من المصادر المثبتة؛ هذه ليست كل الحركة التاريخية."]}
