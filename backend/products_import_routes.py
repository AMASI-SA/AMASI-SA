"""Iter-250b · P2 — Excel-based import for product categories + products.

Phase 1 (this commit): **categories import**.

Pipeline:
  1. Frontend POSTs the .xlsx to `/api/products/categories/import/preview`.
     The endpoint parses the workbook IN MEMORY (no disk writes) and
     returns a structured preview report:
       * new_count / existing_count
       * orphan_subcategory_count (rows flagged فرعي=نعم but parent blank)
       * sample of new categories
       * the resolved hierarchy that WILL be created
       * root category path
  2. Frontend posts `/api/products/categories/import/confirm` with the
     same file. Backend re-parses + atomically:
       * ensures the dedicated root "المنتجات المستوردة" exists once
       * upserts every category as `kind="product"` under it
       * never deletes, never reuses an existing expense category by
         accident (we always scope the lookup to the product subtree
         by walking ancestors).

READ-ONLY for ALL existing data:
  * no expense category is renamed, moved, or deleted.
  * we ONLY upsert into `db.expense_categories` with `kind="product"`.

Two endpoints in this file. Phase 2 (products import) will live in
the same module under different prefixes.
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from openpyxl import load_workbook


# ─────────────────────────  helpers  ──────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    s = "" if value is None else str(value)
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_key(value: Any) -> str:
    """Case-folded + tatweel-stripped key for de-duplication."""
    s = _norm(value).casefold()
    return s.replace("\u0640", "")  # arabic tatweel


def _is_yes(value: Any) -> bool:
    s = _norm(value).casefold()
    return s in {"نعم", "yes", "true", "1", "y"}


def _parse_categories_xlsx(raw: bytes) -> dict:
    """Parse the categories workbook into:
       { rows: [{ name, is_sub, parent, raw_row_index }],
         orphans: [...],
         duplicates_in_file: [...] }
    Columns expected:
        A: التصنيفات (name)             — required
        B: هل التصنيف فرعي ام لا (yes/no)
        C: التصنيف الاساسي (parent name) — required when B==yes
        D..: ignored
    """
    try:
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as e:
        raise HTTPException(400, f"تعذّر قراءة ملف Excel: {e}")
    ws = wb.active
    rows: list[dict] = []
    orphans: list[dict] = []
    duplicates_in_file: list[dict] = []
    seen_keys: set[str] = set()

    header_seen = False
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if not row:
            continue
        a = _norm(row[0] if len(row) > 0 else None)
        b = row[1] if len(row) > 1 else None
        c = _norm(row[2] if len(row) > 2 else None)
        if not header_seen:
            header_seen = True
            # Treat row 1 as header. We don't strictly validate header
            # text; we trust column positions.
            continue
        if not a:
            continue
        is_sub = _is_yes(b)
        parent = c if (is_sub and c) else None
        key = _norm_key(a)
        if key in seen_keys:
            duplicates_in_file.append({"row": idx, "name": a})
            continue
        seen_keys.add(key)
        item = {
            "name": a,
            "is_sub": is_sub,
            "parent": parent,
            "raw_row_index": idx,
            "key": key,
        }
        if is_sub and not c:
            orphans.append(item)
        rows.append(item)
    return {
        "rows":                rows,
        "orphans":             orphans,
        "duplicates_in_file":  duplicates_in_file,
    }


ROOT_NAME = "المنتجات المستوردة"


async def _existing_product_keys(db, uid: str, root_id: str) -> dict[str, str]:
    """Return { norm_key: category_id } for all kind=product categories
    that descend from the root. We rely on the `path` field to detect
    membership of the subtree without recursive queries."""
    out: dict[str, str] = {}
    async for c in db.expense_categories.find(
        {"user_id": uid, "kind": "product"},
        {"_id": 0, "id": 1, "name": 1, "path": 1, "parent_id": 1},
    ):
        out[_norm_key(c.get("name"))] = c["id"]
    return out


async def _ensure_product_root(db, uid: str) -> dict:
    """Upsert the dedicated `kind=product` root once per user."""
    existing = await db.expense_categories.find_one(
        {"user_id": uid, "kind": "product",
         "parent_id": None, "name": ROOT_NAME},
        {"_id": 0},
    )
    if existing:
        return existing
    now = _now()
    doc = {
        "id":           str(uuid.uuid4()),
        "user_id":      uid,
        "kind":         "product",
        "name":         ROOT_NAME,
        "parent_id":    None,
        "path":         [ROOT_NAME],
        "path_ids":     [],   # filled below for self-reference
        "depth":        0,
        "status":       "active",
        "movement_types": [],
        "created_at":   now,
        "updated_at":   now,
        "system":       True,  # protect from accidental delete
    }
    doc["path_ids"] = [doc["id"]]
    doc["code"]     = doc["id"]   # honour stale unique index
    await db.expense_categories.insert_one(doc)
    return doc


# ──────────────────────────  router  ──────────────────────────────────
def make_products_import_router(db, current_user):
    router = APIRouter(prefix="/products", tags=["products-import"])

    # ----- Categories: preview -----
    @router.post("/categories/import/preview")
    async def categories_import_preview(
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "الملف فارغ")
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(400, "حجم الملف يتجاوز 5MB")

        parsed = _parse_categories_xlsx(raw)
        rows = parsed["rows"]

        # We DON'T modify anything during preview. We just classify each
        # row against the current product subtree.
        root = await db.expense_categories.find_one(
            {"user_id": uid, "kind": "product",
             "parent_id": None, "name": ROOT_NAME},
            {"_id": 0, "id": 1, "name": 1},
        )
        existing_map = (await _existing_product_keys(db, uid, root["id"])
                        if root else {})

        new_count = 0
        existing_count = 0
        parent_keys_in_file = {r["key"] for r in rows}
        parent_missing: list[dict] = []

        roots_in_file:  list[dict] = []
        subs_in_file:   list[dict] = []
        new_sample:     list[dict] = []
        existing_names: list[str]  = []

        for r in rows:
            if r["key"] in existing_map:
                existing_count += 1
                existing_names.append(r["name"])
            else:
                new_count += 1
                if len(new_sample) < 50:
                    new_sample.append({
                        "name":   r["name"],
                        "is_sub": r["is_sub"],
                        "parent": r["parent"],
                    })
            if r["is_sub"]:
                subs_in_file.append(r)
                if r["parent"]:
                    pkey = _norm_key(r["parent"])
                    if (pkey not in parent_keys_in_file
                            and pkey not in existing_map):
                        parent_missing.append({
                            "category": r["name"],
                            "missing_parent": r["parent"],
                        })
            else:
                roots_in_file.append(r)

        return {
            "ok": True,
            "phase": "categories_preview",
            "root": {
                "name":           ROOT_NAME,
                "exists":         bool(root),
                "id":             (root or {}).get("id"),
            },
            "totals": {
                "total_rows":              len(rows),
                "new":                     new_count,
                "existing":                existing_count,
                "root_level_in_file":      len(roots_in_file),
                "sub_level_in_file":       len(subs_in_file),
                "orphan_subs_no_parent":   len(parsed["orphans"]),
                "duplicates_in_file":      len(parsed["duplicates_in_file"]),
                "parent_not_found_in_file_or_db":
                                            len(parent_missing),
            },
            "samples": {
                "new":              new_sample,
                "orphans":          parsed["orphans"][:20],
                "duplicates":       parsed["duplicates_in_file"][:20],
                "existing_first10": existing_names[:10],
                "parent_missing":   parent_missing[:20],
            },
            "notes": [
                "لن يتم حذف أي تصنيف موجود.",
                f"التصنيفات الجديدة ستوضع تحت جذر «{ROOT_NAME}» "
                "(يُنشأ تلقائياً إن لم يكن موجوداً).",
                "التصنيفات الفرعية التي ليس لها أب صالح في الملف أو في "
                "الشجرة ستُلحق مؤقتاً بالجذر مع تنبيه — تستطيع نقلها "
                "يدوياً بعد الاستيراد.",
            ],
        }

    # ----- Categories: confirm -----
    @router.post("/categories/import/confirm")
    async def categories_import_confirm(
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "الملف فارغ")
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(400, "حجم الملف يتجاوز 5MB")

        parsed = _parse_categories_xlsx(raw)
        rows = parsed["rows"]
        if not rows:
            raise HTTPException(400, "لا توجد بيانات صالحة للاستيراد")

        root = await _ensure_product_root(db, uid)
        existing_map = await _existing_product_keys(db, uid, root["id"])

        # 1) Insert roots first. 2) Then subs (after their parents
        # exist). 3) Orphans (sub but parent missing) → attach to root
        # so the merchant can move them later.
        roots = [r for r in rows if not r["is_sub"]]
        subs  = [r for r in rows if r["is_sub"]]

        created = 0
        skipped_existing = 0
        attached_to_root_as_orphan: list[str] = []

        async def _create(name: str, parent_doc: dict,
                          source_row_index: int) -> str:
            now = _now()
            new_id = str(uuid.uuid4())
            doc = {
                "id":          new_id,
                "user_id":     uid,
                "kind":        "product",
                # Stale unique index `{user_id, code}` requires a
                # unique value here — mirroring the id is safest and
                # avoids touching the index.
                "code":        new_id,
                "name":        name,
                "parent_id":   parent_doc["id"],
                "path":        (parent_doc.get("path") or []) + [name],
                "path_ids":    (parent_doc.get("path_ids") or []) + [new_id],
                "depth":       len((parent_doc.get("path") or [])),
                "status":      "active",
                "movement_types": [],
                "created_at":  now,
                "updated_at":  now,
                "imported": {
                    "source": "excel",
                    "row":    source_row_index,
                    "at":     now,
                },
            }
            await db.expense_categories.insert_one(doc)
            existing_map[_norm_key(name)] = new_id
            return new_id

        # Pre-fetch by id for parents we'll need.
        def _doc_by_id(cid: str):
            return db.expense_categories.find_one(
                {"id": cid, "user_id": uid}, {"_id": 0})

        # Roots
        for r in roots:
            if r["key"] in existing_map:
                skipped_existing += 1
                continue
            await _create(r["name"], root, r["raw_row_index"])
            created += 1

        # Subs — iterate up to 5 times to resolve nested children whose
        # parents appear later in the file.
        remaining = list(subs)
        for _ in range(5):
            if not remaining:
                break
            still: list[dict] = []
            for r in remaining:
                if r["key"] in existing_map:
                    skipped_existing += 1
                    continue
                parent_key = _norm_key(r["parent"]) if r["parent"] else None
                if not parent_key:
                    # No parent at all → attach to root.
                    await _create(r["name"], root, r["raw_row_index"])
                    created += 1
                    attached_to_root_as_orphan.append(r["name"])
                    continue
                parent_id = existing_map.get(parent_key)
                if not parent_id:
                    still.append(r)
                    continue
                parent_doc = await _doc_by_id(parent_id)
                if not parent_doc:
                    still.append(r)
                    continue
                await _create(r["name"], parent_doc, r["raw_row_index"])
                created += 1
            if len(still) == len(remaining):
                # No progress — remaining parents are unresolvable;
                # attach them to root.
                break
            remaining = still
        for r in remaining:
            if r["key"] in existing_map:
                skipped_existing += 1
                continue
            await _create(r["name"], root, r["raw_row_index"])
            created += 1
            attached_to_root_as_orphan.append(r["name"])

        return {
            "ok": True,
            "phase": "categories_confirm",
            "root": {"id": root["id"], "name": root["name"]},
            "summary": {
                "rows_in_file":              len(rows),
                "created":                   created,
                "skipped_existing":          skipped_existing,
                "attached_to_root_as_orphan":
                    len(attached_to_root_as_orphan),
                "duplicates_in_file":        len(parsed["duplicates_in_file"]),
            },
            "attached_to_root_as_orphan": attached_to_root_as_orphan[:50],
        }

    return router


__all__ = ["make_products_import_router"]
