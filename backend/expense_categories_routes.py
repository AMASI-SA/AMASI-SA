"""Iter-244 — Foundation: Expense Categories (tree) + Suppliers.

Adds two NEW, independent collections that DO NOT alter any existing
records:

  • `expense_categories` — unlimited-depth tree per user.
       Fields: id, user_id, name, parent_id, status ("active" |
               "inactive"), path[] (root → leaf names),
               path_ids[] (root → leaf ids), depth,
               created_at, updated_at.
       NO delete — only `status = "inactive"`.

  • `suppliers` — new supplier records with multi-category links.
       Fields: id, user_id, company_name, contact_person, phone,
               email, status, category_ids[], notes,
               created_at, updated_at.
       Uniqueness per-user on EACH of: company_name, contact_person,
       phone (case-insensitive, trimmed). NO delete — only
       `status = "inactive"`.

Strictly forward-only — does not touch the legacy `liabilities`,
`counterparties`, or `daily_expenses` rows.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Seed template (the tree the merchant proposed in the spec) ──────
SEED_TEMPLATE: list[dict] = [
    {"name": "تكاليف المنتجات", "children": [
        {"name": "منتجات", "children": [
            {"name": "ملابس"}, {"name": "مطليات"}, {"name": "ساعات"},
            {"name": "عطور"}, {"name": "إكسسوارات"},
        ]},
        {"name": "مواد تغليف", "children": [
            {"name": "كراتين"}, {"name": "أكياس"},
            {"name": "استكرات"}, {"name": "بطاقات شكر"},
        ]},
        {"name": "مستلزمات مستودع", "children": [
            {"name": "أرفف"}, {"name": "معدات تشغيل"},
            {"name": "أدوات تغليف"},
        ]},
    ]},
    {"name": "المصروفات التشغيلية", "children": [
        {"name": "إيجارات"}, {"name": "إنترنت"}, {"name": "كهرباء"},
        {"name": "ماء"}, {"name": "اتصالات"}, {"name": "صيانة"},
        {"name": "مصروفات يومية"},
    ]},
    {"name": "المصروفات التسويقية", "children": [
        {"name": "سناب شات"}, {"name": "تيك توك"},
        {"name": "ميتا"}, {"name": "جوجل"},
    ]},
    {"name": "الأصول", "children": [
        {"name": "أجهزة"}, {"name": "أثاث"},
        {"name": "سيارات"}, {"name": "معدات"},
    ]},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _norm_lower(s: str | None) -> str:
    return _norm(s).casefold()


# ╔══════════════════════════════════════════════════════════════════╗
# ║                          CATEGORIES                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class CategoryCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        v = _norm(v)
        if not v:
            raise ValueError("اسم التصنيف مطلوب")
        if len(v) > 80:
            raise ValueError("اسم التصنيف أطول من 80 حرفاً")
        return v


class CategoryPatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None
    status: Optional[str] = None  # "active" | "inactive"

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v):
        if v not in (None, "active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        return v


async def _compute_path(db, user_id: str, parent_id: Optional[str],
                        name: str) -> tuple[list[str], list[str], int]:
    """Walk up parents to build the full path (names + ids) and depth."""
    if not parent_id:
        return [name], [], 0
    chain_names: list[str] = []
    chain_ids: list[str] = []
    current = parent_id
    guard = 0
    while current and guard < 64:
        guard += 1
        parent = await db.expense_category_tree.find_one(
            {"id": current, "user_id": user_id},
            {"_id": 0, "id": 1, "name": 1, "parent_id": 1},
        )
        if not parent:
            raise HTTPException(404, "التصنيف الأب غير موجود")
        chain_names.insert(0, parent["name"])
        chain_ids.insert(0, parent["id"])
        current = parent.get("parent_id")
    return chain_names + [name], chain_ids, len(chain_ids)


async def _check_cycle(db, user_id: str, node_id: str,
                       new_parent_id: Optional[str]) -> None:
    """Forbid making a node a descendant of itself."""
    if not new_parent_id:
        return
    if new_parent_id == node_id:
        raise HTTPException(400, "لا يمكن جعل التصنيف أباً لنفسه")
    cur = new_parent_id
    guard = 0
    while cur and guard < 64:
        guard += 1
        p = await db.expense_category_tree.find_one(
            {"id": cur, "user_id": user_id},
            {"_id": 0, "parent_id": 1},
        )
        if not p:
            return
        if p.get("parent_id") == node_id:
            raise HTTPException(
                400, "لا يمكن جعل التصنيف تحت أحد أحفاده")
        cur = p.get("parent_id")


async def _refresh_descendants_paths(db, user_id: str,
                                     root_id: str) -> int:
    """Re-derive `path` / `path_ids` / `depth` for every descendant of
    `root_id`. Used after rename or re-parent."""
    n = 0
    queue = [root_id]
    while queue:
        node_id = queue.pop(0)
        node = await db.expense_category_tree.find_one(
            {"id": node_id, "user_id": user_id}, {"_id": 0},
        )
        if not node:
            continue
        new_path, new_ids, new_depth = await _compute_path(
            db, user_id, node.get("parent_id"), node["name"],
        )
        await db.expense_category_tree.update_one(
            {"id": node_id, "user_id": user_id},
            {"$set": {
                "path": new_path, "path_ids": new_ids,
                "depth": new_depth, "updated_at": _now(),
            }},
        )
        n += 1
        children = await db.expense_category_tree.find(
            {"user_id": user_id, "parent_id": node_id},
            {"_id": 0, "id": 1},
        ).to_list(2000)
        queue.extend(c["id"] for c in children)
    return n


def make_expense_categories_router(db, current_user):
    router = APIRouter(prefix="/expense-category-tree",
                       tags=["expense-categories"])

    @router.get("")
    async def list_categories(
        user: dict = Depends(current_user),
        include_inactive: bool = Query(True),
    ):
        q: dict = {"user_id": user["id"]}
        if not include_inactive:
            q["status"] = "active"
        rows = await db.expense_category_tree.find(
            q, {"_id": 0},
        ).sort([("depth", 1), ("name", 1)]).to_list(5000)
        return {"ok": True, "items": rows, "count": len(rows)}

    @router.post("")
    async def create_category(
        payload: CategoryCreate,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Sibling-uniqueness: no two children of the same parent share
        # a (case-insensitive) name.
        parent = (payload.parent_id or None)
        existing = await db.expense_category_tree.find_one({
            "user_id": uid,
            "parent_id": parent,
            "name": {"$regex": f"^{re.escape(payload.name)}$",
                     "$options": "i"},
        }, {"_id": 0, "id": 1})
        if existing:
            raise HTTPException(
                409, "يوجد تصنيف بنفس الاسم تحت نفس الأب")

        path, path_ids, depth = await _compute_path(
            db, uid, parent, payload.name)
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "name": payload.name,
            "parent_id": parent,
            "status": "active",
            "path": path,
            "path_ids": path_ids,
            "depth": depth,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.expense_category_tree.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @router.patch("/{cat_id}")
    async def patch_category(
        cat_id: str,
        payload: CategoryPatch,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        cat = await db.expense_category_tree.find_one(
            {"id": cat_id, "user_id": uid}, {"_id": 0},
        )
        if not cat:
            raise HTTPException(404, "التصنيف غير موجود")

        updates: dict = {"updated_at": _now()}
        re_path = False

        if payload.name is not None:
            new_name = _norm(payload.name)
            if not new_name:
                raise HTTPException(400, "اسم التصنيف مطلوب")
            if new_name != cat["name"]:
                dup = await db.expense_category_tree.find_one({
                    "user_id": uid,
                    "parent_id": cat.get("parent_id"),
                    "id": {"$ne": cat_id},
                    "name": {"$regex": f"^{re.escape(new_name)}$",
                             "$options": "i"},
                }, {"_id": 0, "id": 1})
                if dup:
                    raise HTTPException(
                        409, "يوجد تصنيف بنفس الاسم تحت نفس الأب")
                updates["name"] = new_name
                re_path = True

        if payload.parent_id is not None:
            if payload.parent_id == "":
                new_parent = None
            else:
                new_parent = payload.parent_id
                exists = await db.expense_category_tree.find_one(
                    {"id": new_parent, "user_id": uid},
                    {"_id": 0, "id": 1},
                )
                if not exists:
                    raise HTTPException(404, "التصنيف الأب غير موجود")
                await _check_cycle(db, uid, cat_id, new_parent)
            if new_parent != cat.get("parent_id"):
                updates["parent_id"] = new_parent
                re_path = True

        if payload.status is not None:
            updates["status"] = payload.status

        await db.expense_category_tree.update_one(
            {"id": cat_id, "user_id": uid}, {"$set": updates},
        )
        if re_path:
            await _refresh_descendants_paths(db, uid, cat_id)

        out = await db.expense_category_tree.find_one(
            {"id": cat_id, "user_id": uid}, {"_id": 0},
        )
        return out

    @router.post("/seed-template")
    async def seed_template(
        user: dict = Depends(current_user),
    ):
        """Idempotent seed of the merchant-proposed tree. Only inserts
        nodes that don't already exist (matched by exact path)."""
        uid = user["id"]
        inserted = 0
        skipped = 0

        async def _walk(nodes: list[dict],
                        parent_id: Optional[str],
                        parent_path: list[str],
                        parent_path_ids: list[str]):
            nonlocal inserted, skipped
            for n in nodes:
                name = n["name"]
                full_path = parent_path + [name]
                existing = await db.expense_category_tree.find_one({
                    "user_id": uid, "path": full_path,
                }, {"_id": 0, "id": 1})
                if existing:
                    skipped += 1
                    node_id = existing["id"]
                else:
                    node_id = str(uuid.uuid4())
                    await db.expense_category_tree.insert_one({
                        "id": node_id,
                        "user_id": uid,
                        "name": name,
                        "parent_id": parent_id,
                        "status": "active",
                        "path": full_path,
                        "path_ids": parent_path_ids,
                        "depth": len(parent_path_ids),
                        "created_at": _now(),
                        "updated_at": _now(),
                    })
                    inserted += 1
                if n.get("children"):
                    await _walk(
                        n["children"], node_id, full_path,
                        parent_path_ids + [node_id],
                    )

        await _walk(SEED_TEMPLATE, None, [], [])
        return {
            "ok": True,
            "inserted": inserted,
            "skipped": skipped,
            "iter": "iter244",
        }

    return router


# ╔══════════════════════════════════════════════════════════════════╗
# ║                           SUPPLIERS                              ║
# ╚══════════════════════════════════════════════════════════════════╝

class SupplierCreate(BaseModel):
    company_name: str
    contact_person: str
    phone: str
    email: Optional[EmailStr] = None
    category_ids: list[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @field_validator("company_name", "contact_person")
    @classmethod
    def _req(cls, v: str) -> str:
        v = _norm(v)
        if not v:
            raise ValueError("الحقل مطلوب")
        if len(v) > 120:
            raise ValueError("القيمة أطول من 120 حرفاً")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        v = _norm(v)
        if not v:
            raise ValueError("رقم الجوال مطلوب")
        cleaned = re.sub(r"\D", "", v)
        if len(cleaned) < 7:
            raise ValueError("رقم الجوال غير صالح")
        return v


class SupplierPatch(BaseModel):
    company_name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    category_ids: Optional[list[str]] = None
    status: Optional[str] = None  # "active" | "inactive"
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v):
        if v not in (None, "active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        return v


async def _assert_unique_field(db, uid: str, field: str, value: str,
                               *, exclude_id: Optional[str] = None,
                               arabic_label: str = "") -> None:
    if not value:
        return
    q: dict = {
        "user_id": uid,
        f"_lc_{field}": _norm_lower(value),
    }
    if exclude_id:
        q["id"] = {"$ne": exclude_id}
    dup = await db.suppliers.find_one(q, {"_id": 0, "id": 1})
    if dup:
        raise HTTPException(
            409, f"{arabic_label or field} مستخدم بالفعل لمورد آخر")


async def _validate_category_ids(db, uid: str,
                                 ids: list[str]) -> list[str]:
    if not ids:
        return []
    found = await db.expense_category_tree.find(
        {"user_id": uid, "id": {"$in": list({i for i in ids if i})}},
        {"_id": 0, "id": 1},
    ).to_list(1000)
    found_ids = {f["id"] for f in found}
    missing = [i for i in ids if i and i not in found_ids]
    if missing:
        raise HTTPException(
            400, f"تصنيفات غير موجودة: {', '.join(missing)}")
    # de-dup while preserving order
    seen: set = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def make_suppliers_router(db, current_user):
    router = APIRouter(prefix="/suppliers", tags=["suppliers"])

    @router.get("")
    async def list_suppliers(
        user: dict = Depends(current_user),
        search: Optional[str] = None,
        status: Optional[str] = None,
        category_id: Optional[str] = None,
    ):
        uid = user["id"]
        q: dict = {"user_id": uid}
        if status in ("active", "inactive"):
            q["status"] = status
        if category_id:
            q["category_ids"] = category_id
        if search:
            rx = {"$regex": re.escape(_norm(search)), "$options": "i"}
            q["$or"] = [
                {"company_name": rx},
                {"contact_person": rx},
                {"phone": rx},
                {"email": rx},
            ]
        rows = await db.suppliers.find(
            q, {"_id": 0, "_lc_company_name": 0,
                "_lc_contact_person": 0, "_lc_phone": 0},
        ).sort([("created_at", -1)]).to_list(2000)
        return {"ok": True, "items": rows, "count": len(rows)}

    @router.get("/{supplier_id}")
    async def get_supplier(
        supplier_id: str,
        user: dict = Depends(current_user),
    ):
        s = await db.suppliers.find_one(
            {"id": supplier_id, "user_id": user["id"]},
            {"_id": 0, "_lc_company_name": 0,
             "_lc_contact_person": 0, "_lc_phone": 0},
        )
        if not s:
            raise HTTPException(404, "المورد غير موجود")
        return s

    @router.post("")
    async def create_supplier(
        payload: SupplierCreate,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        # Per-user uniqueness on each of: company_name,
        # contact_person, phone (strict per user request).
        await _assert_unique_field(
            db, uid, "company_name", payload.company_name,
            arabic_label="اسم الشركة")
        await _assert_unique_field(
            db, uid, "contact_person", payload.contact_person,
            arabic_label="اسم شخص الاتصال")
        await _assert_unique_field(
            db, uid, "phone", payload.phone,
            arabic_label="رقم الجوال")

        category_ids = await _validate_category_ids(
            db, uid, payload.category_ids)

        doc: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "company_name": payload.company_name,
            "contact_person": payload.contact_person,
            "phone": payload.phone,
            "email": payload.email,
            "category_ids": category_ids,
            "notes": _norm(payload.notes or "") or None,
            "status": "active",
            "_lc_company_name": _norm_lower(payload.company_name),
            "_lc_contact_person": _norm_lower(payload.contact_person),
            "_lc_phone": _norm_lower(payload.phone),
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.suppliers.insert_one(doc)
        for k in ("_id", "_lc_company_name",
                  "_lc_contact_person", "_lc_phone"):
            doc.pop(k, None)
        return doc

    @router.patch("/{supplier_id}")
    async def patch_supplier(
        supplier_id: str,
        payload: SupplierPatch,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        s = await db.suppliers.find_one(
            {"id": supplier_id, "user_id": uid}, {"_id": 0},
        )
        if not s:
            raise HTTPException(404, "المورد غير موجود")

        updates: dict = {"updated_at": _now()}

        if payload.company_name is not None:
            new_v = _norm(payload.company_name)
            if not new_v:
                raise HTTPException(400, "اسم الشركة مطلوب")
            if _norm_lower(new_v) != s.get("_lc_company_name"):
                await _assert_unique_field(
                    db, uid, "company_name", new_v,
                    exclude_id=supplier_id,
                    arabic_label="اسم الشركة")
                updates["company_name"] = new_v
                updates["_lc_company_name"] = _norm_lower(new_v)

        if payload.contact_person is not None:
            new_v = _norm(payload.contact_person)
            if not new_v:
                raise HTTPException(400, "اسم شخص الاتصال مطلوب")
            if _norm_lower(new_v) != s.get("_lc_contact_person"):
                await _assert_unique_field(
                    db, uid, "contact_person", new_v,
                    exclude_id=supplier_id,
                    arabic_label="اسم شخص الاتصال")
                updates["contact_person"] = new_v
                updates["_lc_contact_person"] = _norm_lower(new_v)

        if payload.phone is not None:
            new_v = _norm(payload.phone)
            if not new_v:
                raise HTTPException(400, "رقم الجوال مطلوب")
            if _norm_lower(new_v) != s.get("_lc_phone"):
                await _assert_unique_field(
                    db, uid, "phone", new_v,
                    exclude_id=supplier_id,
                    arabic_label="رقم الجوال")
                updates["phone"] = new_v
                updates["_lc_phone"] = _norm_lower(new_v)

        if payload.email is not None:
            updates["email"] = payload.email

        if payload.category_ids is not None:
            updates["category_ids"] = await _validate_category_ids(
                db, uid, payload.category_ids)

        if payload.status is not None:
            updates["status"] = payload.status

        if payload.notes is not None:
            updates["notes"] = _norm(payload.notes) or None

        await db.suppliers.update_one(
            {"id": supplier_id, "user_id": uid}, {"$set": updates},
        )
        out = await db.suppliers.find_one(
            {"id": supplier_id, "user_id": uid},
            {"_id": 0, "_lc_company_name": 0,
             "_lc_contact_person": 0, "_lc_phone": 0},
        )
        return out

    @router.get("/{supplier_id}/suggested-categories")
    async def suggested_categories(
        supplier_id: str,
        user: dict = Depends(current_user),
    ):
        """Returns the supplier's linked categories (full path) so the
        upcoming purchase-invoice screen can narrow the picker."""
        uid = user["id"]
        s = await db.suppliers.find_one(
            {"id": supplier_id, "user_id": uid},
            {"_id": 0, "category_ids": 1},
        )
        if not s:
            raise HTTPException(404, "المورد غير موجود")
        ids = s.get("category_ids") or []
        if not ids:
            return {"ok": True, "items": []}
        rows = await db.expense_category_tree.find(
            {"user_id": uid, "id": {"$in": ids}},
            {"_id": 0},
        ).to_list(1000)
        return {"ok": True, "items": rows}

    return router
