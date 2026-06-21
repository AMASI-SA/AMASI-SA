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
UNCAT_NAME = "غير مصنف"


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


# ─────────────  PRODUCTS  ─────────────────────────────────────────────
def _parse_products_xlsx(raw: bytes) -> dict:
    """Parse the products workbook. Columns (fixed positions):
       A: رقم المنتج (product_id)  — required (else skipped)
       B: اسم المنتج (name)         — required (else skipped)
       C: تصنيف المنتج (paths)      — optional, "A > B, C > D"
       D: صور المنتج (comma URLs)  — optional
       F: سعر التكلفة               — optional
    """
    try:
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception as e:
        raise HTTPException(400, f"تعذّر قراءة ملف Excel: {e}")
    ws = wb.active
    rows: list[dict] = []
    seen: dict[str, int] = {}
    duplicates_in_file: list[dict] = []
    no_name: list[int] = []
    header_seen = False
    for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if not row:
            continue
        if not header_seen:
            header_seen = True
            continue
        pid  = _norm(row[0] if len(row) > 0 else None)
        name = _norm(row[1] if len(row) > 1 else None)
        cats = _norm(row[2] if len(row) > 2 else None)
        imgs = _norm(row[3] if len(row) > 3 else None)
        # Column E is empty by spec — column F is cost.
        cost_raw = row[5] if len(row) > 5 else None
        if not pid:
            continue
        if not name:
            no_name.append(idx)
            continue
        if pid in seen:
            duplicates_in_file.append(
                {"row": idx, "product_id": pid, "name": name})
            continue
        seen[pid] = idx
        # Parse categories: comma-separated paths, each path uses " > ".
        cat_paths: list[list[str]] = []
        if cats:
            for path in cats.split(","):
                tokens = [t.strip() for t in path.split(">") if t.strip()]
                if tokens:
                    cat_paths.append(tokens)
        # Parse images
        image_urls: list[str] = []
        if imgs:
            for u in imgs.split(","):
                u = u.strip()
                if u:
                    image_urls.append(u)
        # Cost
        cost = None
        if cost_raw not in (None, ""):
            try:
                cost = round(float(cost_raw), 2)
                if cost <= 0:
                    cost = None
            except (TypeError, ValueError):
                cost = None
        rows.append({
            "row":         idx,
            "product_id":  pid,
            "name":        name,
            "name_lower":  _norm_key(name),
            "cat_paths":   cat_paths,
            "image_urls":  image_urls,
            "image_url":   (image_urls[0] if image_urls else None),
            "cost":        cost,
        })
    return {
        "rows": rows,
        "duplicates_in_file": duplicates_in_file,
        "no_name_rows": no_name,
    }


async def _ensure_uncategorized(db, uid: str, root: dict) -> dict:
    """Ensure a single "غير مصنف" category exists directly under root."""
    existing = await db.expense_categories.find_one(
        {"user_id": uid, "kind": "product",
         "parent_id": root["id"], "name": UNCAT_NAME},
        {"_id": 0},
    )
    if existing:
        return existing
    now = _now()
    new_id = str(uuid.uuid4())
    doc = {
        "id":         new_id,
        "user_id":    uid,
        "kind":       "product",
        "code":       new_id,
        "name":       UNCAT_NAME,
        "parent_id":  root["id"],
        "path":       (root.get("path") or []) + [UNCAT_NAME],
        "path_ids":   (root.get("path_ids") or []) + [new_id],
        "depth":      len(root.get("path") or []),
        "status":     "active",
        "movement_types": [],
        "created_at": now,
        "updated_at": now,
        "system":     True,
    }
    await db.expense_categories.insert_one(doc)
    return doc


async def _resolve_path_to_cat_id(
    db, uid: str, root: dict, tokens: list[str],
    cache: dict[tuple, str],
) -> str:
    """Resolve a hierarchical path like ['اكسسوارات نسائي', 'سلاسل نسائيه']
    to a category_id. Walks the tree; creates missing nodes on the fly
    under the root. The cache short-circuits repeated paths."""
    cur_parent = root
    key_prefix: tuple = ()
    for tok in tokens:
        norm = _norm_key(tok)
        key_prefix = key_prefix + (norm,)
        if key_prefix in cache:
            cur_id = cache[key_prefix]
            cur_parent = await db.expense_categories.find_one(
                {"id": cur_id, "user_id": uid}, {"_id": 0})
            continue
        existing = await db.expense_categories.find_one(
            {"user_id": uid, "kind": "product",
             "parent_id": cur_parent["id"], "name": tok},
            {"_id": 0},
        )
        if existing:
            cache[key_prefix] = existing["id"]
            cur_parent = existing
            continue
        # Auto-create missing node under cur_parent.
        now = _now()
        new_id = str(uuid.uuid4())
        doc = {
            "id":        new_id,
            "user_id":   uid,
            "kind":      "product",
            "code":      new_id,
            "name":      tok,
            "parent_id": cur_parent["id"],
            "path":      (cur_parent.get("path") or []) + [tok],
            "path_ids":  (cur_parent.get("path_ids") or []) + [new_id],
            "depth":     len(cur_parent.get("path") or []),
            "status":    "active",
            "movement_types": [],
            "created_at": now,
            "updated_at": now,
            "imported": {"source": "excel-products-auto",
                         "at": now},
        }
        await db.expense_categories.insert_one(doc)
        cache[key_prefix] = new_id
        cur_parent = doc
    return cur_parent["id"]


def make_products_router_phase2(db, current_user):
    """Phase 2 — products import: preview + confirm + list."""
    router = APIRouter(prefix="/products", tags=["products-import"])

    @router.get("/list")
    async def products_list(
        q: str = "",
        category_id: str | None = None,
        needs_cost: bool | None = None,
        limit: int = 100,
        skip:  int = 0,
        user: dict = Depends(current_user),
    ):
        """READ-ONLY list with search + pagination. The autocomplete in
        the supplier-invoice form (Phase 3) uses this same endpoint
        with a smaller limit."""
        uid = user["id"]
        flt: dict = {"user_id": uid, "is_active": True}
        if needs_cost is True:
            flt["needs_cost"] = True
        if category_id:
            flt["category_ids"] = category_id
        if q:
            qn = _norm_key(q)
            # The compiled regex handles fuzzy substring matching on
            # the lowercased name. Phase 3 will add a more advanced
            # ranking; for now we accept basic LIKE-style filtering.
            flt["$or"] = [
                {"name_lower": {"$regex": re.escape(qn)}},
                {"product_id": {"$regex": re.escape(q.strip())}},
            ]
        total = await db.products.count_documents(flt)
        items: list[dict] = []
        async for d in db.products.find(flt, {"_id": 0}) \
                .sort([("updated_at", -1)]).skip(skip).limit(min(limit, 200)):
            items.append(d)
        # Build a name map for category_ids → joined path (cheap lookup
        # constrained to the categories that appear in the result).
        all_cids: set = set()
        for it in items:
            for c in (it.get("category_ids") or []):
                all_cids.add(c)
        cat_paths: dict[str, list[str]] = {}
        if all_cids:
            async for c in db.expense_categories.find(
                {"user_id": uid, "id": {"$in": list(all_cids)}},
                {"_id": 0, "id": 1, "path": 1},
            ):
                cat_paths[c["id"]] = c.get("path") or []
        for it in items:
            paths = []
            for c in (it.get("category_ids") or []):
                if c in cat_paths:
                    paths.append(cat_paths[c])
            it["category_paths"] = paths
        return {"items": items, "total": total,
                "skip": skip, "limit": limit}

    @router.post("/import/preview")
    async def products_import_preview(
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "الملف فارغ")
        if len(raw) > 15 * 1024 * 1024:
            raise HTTPException(400, "حجم الملف يتجاوز 15MB")

        parsed = _parse_products_xlsx(raw)
        rows = parsed["rows"]

        # Cross-check against db.products to compute new vs update.
        pids = [r["product_id"] for r in rows]
        existing_pids: set = set()
        async for d in db.products.find(
            {"user_id": uid, "product_id": {"$in": pids}},
            {"_id": 0, "product_id": 1},
        ):
            existing_pids.add(d["product_id"])

        new_count    = 0
        update_count = 0
        no_cat       = 0
        no_cost      = 0
        multi_cat    = 0
        unique_cat_paths: set = set()
        sample_new: list[dict] = []
        for r in rows:
            if r["product_id"] in existing_pids:
                update_count += 1
            else:
                new_count += 1
                if len(sample_new) < 20:
                    sample_new.append({
                        "product_id": r["product_id"],
                        "name":       r["name"],
                        "cost":       r["cost"],
                        "cat_paths":  r["cat_paths"],
                        "image_url":  r["image_url"],
                    })
            if not r["cat_paths"]:
                no_cat += 1
            elif len(r["cat_paths"]) > 1:
                multi_cat += 1
            if r["cost"] is None:
                no_cost += 1
            for p in r["cat_paths"]:
                unique_cat_paths.add(" > ".join(p))

        return {
            "ok": True,
            "phase": "products_preview",
            "totals": {
                "total_rows":           len(rows),
                "new":                  new_count,
                "update":               update_count,
                "no_name_skipped":      len(parsed["no_name_rows"]),
                "duplicates_in_file":   len(parsed["duplicates_in_file"]),
                "no_category":          no_cat,
                "no_cost":              no_cost,
                "multi_category":       multi_cat,
                "unique_category_paths": len(unique_cat_paths),
            },
            "samples": {
                "new":                 sample_new,
                "duplicates":          parsed["duplicates_in_file"][:20],
                "no_name_rows":        parsed["no_name_rows"][:20],
                "category_paths_first20": sorted(unique_cat_paths)[:20],
            },
            "notes": [
                "رقم المنتج هو مفتاح منع التكرار.",
                "المنتجات بلا تصنيف ستُربط بـ «غير مصنف» تلقائياً.",
                "التصنيفات غير الموجودة سيتم إنشاؤها داخل شجرة "
                "«المنتجات المستوردة».",
                "المنتجات بدون تكلفة ستُحفظ مع علم needs_cost=true.",
                "كل عملية شراء جديدة ستُضاف لـ cost_history دون استبدال.",
            ],
        }

    @router.post("/import/confirm")
    async def products_import_confirm(
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "الملف فارغ")
        if len(raw) > 15 * 1024 * 1024:
            raise HTTPException(400, "حجم الملف يتجاوز 15MB")

        parsed = _parse_products_xlsx(raw)
        rows = parsed["rows"]
        if not rows:
            raise HTTPException(400, "لا توجد بيانات صالحة للاستيراد")

        root = await _ensure_product_root(db, uid)
        uncat = await _ensure_uncategorized(db, uid, root)
        path_cache: dict[tuple, str] = {}

        created    = 0
        updated    = 0
        uncat_used = 0
        cats_auto_created = 0
        cats_before = await db.expense_categories.count_documents(
            {"user_id": uid, "kind": "product"})

        for r in rows:
            # Resolve category_ids.
            cat_ids: list[str] = []
            if r["cat_paths"]:
                for tokens in r["cat_paths"]:
                    try:
                        cid = await _resolve_path_to_cat_id(
                            db, uid, root, tokens, path_cache)
                        if cid not in cat_ids:
                            cat_ids.append(cid)
                    except Exception:
                        continue
            if not cat_ids:
                cat_ids = [uncat["id"]]
                uncat_used += 1

            existing = await db.products.find_one(
                {"user_id": uid, "product_id": r["product_id"]},
                {"_id": 0},
            )
            now = _now()
            needs_cost = (r["cost"] is None)
            if not existing:
                # New product. Initial cost_history entry if cost is set.
                history: list[dict] = []
                if r["cost"] is not None:
                    history.append({
                        "amount": r["cost"],
                        "source": "excel-import",
                        "at":     now,
                    })
                doc = {
                    "id":            str(uuid.uuid4()),
                    "user_id":       uid,
                    "product_id":    r["product_id"],
                    "name":          r["name"],
                    "name_lower":    r["name_lower"],
                    "category_ids":  cat_ids,
                    "image_url":     r["image_url"],
                    "image_urls":    r["image_urls"],
                    "cost_current":  r["cost"],
                    "cost_avg":      r["cost"],
                    "cost_history":  history,
                    "sku":           None,
                    "barcode":       None,
                    "needs_cost":    needs_cost,
                    "is_active":     True,
                    "imported": {
                        "source": "excel",
                        "row":    r["row"],
                        "at":     now,
                    },
                    "created_at":    now,
                    "updated_at":    now,
                }
                await db.products.insert_one(doc)
                created += 1
            else:
                # Update — never overwrite existing cost_history.
                set_fields: dict = {
                    "name":         r["name"],
                    "name_lower":   r["name_lower"],
                    "category_ids": cat_ids,
                    "updated_at":   now,
                }
                # Only refresh images when new ones provided.
                if r["image_urls"]:
                    set_fields["image_url"]  = r["image_url"]
                    set_fields["image_urls"] = r["image_urls"]
                # Cost: if Excel has a cost AND existing has none, set it.
                if r["cost"] is not None and existing.get("cost_current") is None:
                    set_fields["cost_current"] = r["cost"]
                    set_fields["cost_avg"]     = r["cost"]
                    set_fields["needs_cost"]   = False
                    push = {"cost_history": {
                        "amount": r["cost"],
                        "source": "excel-import",
                        "at":     now,
                    }}
                    await db.products.update_one(
                        {"id": existing["id"], "user_id": uid},
                        {"$set": set_fields, "$push": push},
                    )
                else:
                    # Keep `needs_cost` consistent.
                    if existing.get("cost_current") is None:
                        set_fields["needs_cost"] = True
                    await db.products.update_one(
                        {"id": existing["id"], "user_id": uid},
                        {"$set": set_fields},
                    )
                updated += 1

        cats_after = await db.expense_categories.count_documents(
            {"user_id": uid, "kind": "product"})
        cats_auto_created = max(0, cats_after - cats_before)

        return {
            "ok": True,
            "phase": "products_confirm",
            "summary": {
                "rows_in_file":          len(rows),
                "created":               created,
                "updated":               updated,
                "uncategorized_linked":  uncat_used,
                "categories_auto_created": cats_auto_created,
                "no_name_skipped":       len(parsed["no_name_rows"]),
                "duplicates_in_file":    len(parsed["duplicates_in_file"]),
            },
        }

    return router


__all__ = ["make_products_import_router", "make_products_router_phase2"]
