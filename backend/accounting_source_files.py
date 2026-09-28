"""Immutable original settlement bytes; no public/static file URLs."""
from __future__ import annotations

import hashlib
import io
from urllib.parse import quote

import openpyxl
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pymongo.errors import DuplicateKeyError

from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_status_routes import fresh_accounting_user

MAX_BYTES = 10 * 1024 * 1024
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def preserve_original(db, owner, file_id, content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("حجم الملف الأصلي غير مقبول")
    digest = hashlib.sha256(content).hexdigest()
    # A Mongo _id uniqueness constraint is present even before index migration.
    key = hashlib.sha256((owner + ":" + file_id).encode()).hexdigest()
    record = {"_id": key, "user_id": owner, "file_id": file_id,
              "sha256": digest, "size": len(content), "content": content}
    prior = await db.accounting_source_files.find_one({"_id": key, "user_id": owner})
    if prior:
        if prior["sha256"] != digest or bytes(prior["content"]) != content:
            raise ValueError("تعارض في أصل المستند المحفوظ")
        return digest
    try:
        await db.accounting_source_files.insert_one(record)
    except DuplicateKeyError:
        prior = await db.accounting_source_files.find_one({"_id": key, "user_id": owner})
        if not prior or prior["sha256"] != digest or bytes(prior["content"]) != content:
            raise ValueError("تعارض في أصل المستند المحفوظ") from None
    return digest


async def original_for_draft(db, owner, draft_id):
    draft = await db.accounting_settlements_v2.find_one({"id": draft_id, "user_id": owner})
    if not draft:
        raise HTTPException(404, "التسوية غير موجودة")
    file_id = draft.get("source_file_id")
    source = await db.settlement_files.find_one({"id": file_id, "user_id": owner})
    blob = await db.accounting_source_files.find_one({"file_id": file_id, "user_id": owner})
    if not source or not blob:
        raise HTTPException(404, detail={"code": "original_file_unavailable",
                                      "message": "لم تحفظ بايتات الملف الأصلي؛ أعد رفع نفس الملف لاستعادتها دون إنشاء تسوية جديدة"})
    content = bytes(blob["content"])
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.get("file_hash") or digest != blob.get("sha256"):
        raise HTTPException(409, "بصمة المستند الأصلي لا تطابق المصدر")
    return source, content, digest


def install_accounting_source_file_routes(router, db, current_user):
    async def owner_for(user):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, "accounting.settlements.view")
        return accounting_owner_id(actor)

    @router.get("/accounting-module/settlements/drafts/{draft_id}/original")
    async def download_original(draft_id: str, user: dict = Depends(current_user)):
        source, content, digest = await original_for_draft(db, await owner_for(user), draft_id)
        name = str(source.get("filename") or "statement.xlsx").replace("\\", "/").split("/")[-1]
        return Response(content, media_type=MIME, headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe=""),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": digest,
        })

    @router.get("/accounting-module/settlements/drafts/{draft_id}/original/preview")
    async def preview_original(draft_id: str, user: dict = Depends(current_user)):
        source, content, digest = await original_for_draft(db, await owner_for(user), draft_id)
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True,
                                         data_only=True, keep_links=False)
        try:
            sheets = []
            for sheet in workbook.worksheets[:5]:
                rows = []
                for row in sheet.iter_rows(max_row=100, max_col=30, values_only=True):
                    rows.append([str(value) if value is not None else "" for value in row])
                sheets.append({"name": sheet.title, "rows": rows})
            return {"filename": source.get("filename"), "sha256": digest,
                    "size": len(content), "sheets": sheets,
                    "limits": {"sheets": 5, "rows": 100, "columns": 30}}
        finally:
            workbook.close()

