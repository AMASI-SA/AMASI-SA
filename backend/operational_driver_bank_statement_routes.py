"""Courier-review scoped HTTP intake for original operational bank statements."""
from fastapi import Depends, File, Form, Query, UploadFile

from excel_upload_security import read_safe_xlsx_upload
from operational_driver_bank_statements import MAX_FILE_BYTES, authorize_import, candidates, context, upload


def attach_bank_statement_routes(router, db, current_user, require_accountant, owner_for):
    async def scope(user):
        actor = await require_accountant(db, user)
        return owner_for(actor), actor["id"]

    @router.get("/bank-statements/context")
    async def bank_context(user=Depends(current_user)):
        owner, actor = await scope(user)
        return await context(db, owner=owner, actor=actor)

    @router.get("/bank-statements")
    async def bank_rows(assignment_id: str = Query(min_length=1, max_length=160), user=Depends(current_user)):
        owner, actor = await scope(user)
        return await candidates(db, owner=owner, actor=actor, assignment_id=assignment_id)

    @router.post("/bank-statements/upload")
    async def bank_upload(
        file: UploadFile = File(...),
        assignment_id: str = Form(min_length=1, max_length=160),
        bank_account_id: str = Form(min_length=1, max_length=200),
        request_id: str = Form(min_length=8, max_length=160),
        expected_started_at: str = Form(min_length=16, max_length=40),
        expected_session_scope: str = Form(pattern=r"^[a-f0-9]{64}$"),
        user=Depends(current_user),
    ):
        owner, actor = await scope(user)
        await authorize_import(db, owner, actor)
        content = await read_safe_xlsx_upload(file, max_bytes=MAX_FILE_BYTES)
        return await upload(db, owner=owner, actor=actor, assignment_id=assignment_id,
            bank_account_id=bank_account_id, request_id=request_id, expected_started_at=expected_started_at,
            expected_session_scope=expected_session_scope, filename=file.filename, content=content)
