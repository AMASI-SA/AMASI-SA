"""Scoped weekly statement upload, read and explicit confirmation."""
from typing import Literal

from fastapi import Depends, File, Form, HTTPException, Path, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from excel_upload_security import read_safe_xlsx_upload
from operational_balance_store import fail
from operational_provider_statement_intake import MAX_FILE_BYTES, MAX_ROWS
from operational_visible_report_read import run_visible_report_read
from operational_provider_statement_reconciliation import upload, history, review, confirm, saved_files, use_saved_file


class StatementDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    row_id: str = Field(min_length=1, max_length=100)
    action: Literal['accept_statement', 'record_missing_refund', 'record_missing_capture', 'link_native_refund']
    existing_id: str | None = Field(default=None, min_length=1, max_length=400)


class StatementConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    decisions: list[StatementDecision] = Field(min_length=1, max_length=MAX_ROWS)
    confirmed: Literal[True]
    confirmed_unrecorded: bool = Field(default=False, strict=True)


class SavedStatementUse(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_started_at: str = Field(min_length=16, max_length=40)
    expected_file_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def attach_provider_statement_routes(router, db, current_user, scope, guarded):
    @router.get('/provider-statements/mobile')
    async def mobile_history(request: Request, response: Response, page: int = Query(default=1, ge=1),
                             page_size: int = Query(default=10, ge=10, le=10), snapshot: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$'),
                             user=Depends(current_user)):
        from mobile_provider_statements import history_page
        async def read_page():
            actor, owner, _ = await scope(db, user, 'provider_statements')
            return await history_page(db, owner, actor['id'], page=page, snapshot=snapshot)
        response.headers['Cache-Control'] = 'no-store, private'
        return await run_visible_report_read(request, read_page)

    @router.get('/provider-statements/mobile/{statement_id}/review')
    async def mobile_review(request: Request, response: Response, statement_id: str = Path(pattern=r'^[a-f0-9]{64}$'),
                            page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=10, le=10),
                            preview_hash: str | None = Query(default=None, pattern=r'^[a-f0-9]{64}$'),
                            user=Depends(current_user)):
        from mobile_provider_statements import review_page
        async def read_page():
            actor, owner, _ = await scope(db, user, 'provider_statements')
            return await review_page(db, owner, actor['id'], statement_id, page=page, preview_hash=preview_hash)
        response.headers['Cache-Control'] = 'no-store, private'
        return await run_visible_report_read(request, read_page)

    @router.post('/provider-statements/mobile/upload')
    async def mobile_upload(response: Response, file: UploadFile = File(...),
                            provider: Literal['tabby', 'tamara', 'salla'] = Form(...),
                            request_id: str = Form(..., min_length=8, max_length=160),
                            expected_session_scope: str = Form(..., pattern=r'^[a-f0-9]{64}$'),
                            expected_started_at: str = Form(..., min_length=16, max_length=40),
                            user=Depends(guarded('provider_statements'))):
        from mobile_provider_statements import upload_result
        actor, owner, _ = await scope(db, user, 'provider_statements')
        try:
            content = await read_safe_xlsx_upload(file, max_bytes=MAX_FILE_BYTES)
        except HTTPException as exc:
            # This boundary has not entered the canonical writer. The client
            # may safely discard this unusable cached file and choose another.
            detail = dict(exc.detail) if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
            raise HTTPException(exc.status_code, detail={**detail, 'not_applied': True}) from None
        result = await upload(db, owner, actor['id'], content=content,
            filename=file.filename or 'statement.xlsx', request_id=request_id,
            expected_session_scope=expected_session_scope, expected_started_at=expected_started_at,
            selected_provider=provider)
        response.headers['Cache-Control'] = 'no-store, private'
        return await upload_result(db, owner, actor['id'], result)

    @router.get('/provider-statements/saved-files')
    async def statement_saved_files(request: Request, limit: int = Query(default=50, ge=1, le=100), user=Depends(current_user)):
        async def read_page():
            actor, owner, _ = await scope(db, user, 'provider_statements')
            result = await saved_files(db, owner, actor['id'], limit=limit)
            fresh, fresh_owner, _ = await scope(db, user, 'provider_statements')
            if (fresh['id'], fresh_owner) != (actor['id'], owner):
                fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح الصفحة', 403)
            return result
        return await run_visible_report_read(request, read_page)

    @router.post('/provider-statements/saved-files/{file_id}/use')
    async def statement_use_saved(payload: SavedStatementUse, file_id: str = Path(min_length=1, max_length=160),
                                  user=Depends(guarded('provider_statements'))):
        actor, owner, _ = await scope(db, user, 'provider_statements')
        return await use_saved_file(db, owner, actor['id'], file_id, payload.model_dump())

    @router.get('/provider-statements')
    async def statement_history(request: Request, limit: int = Query(default=50, ge=1, le=100), user=Depends(current_user)):
        async def read_page():
            actor, owner, _ = await scope(db, user, 'provider_statements')
            result = await history(db, owner, actor['id'], limit=limit)
            fresh, fresh_owner, _ = await scope(db, user, 'provider_statements')
            if (fresh['id'], fresh_owner) != (actor['id'], owner):
                fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح الصفحة', 403)
            return result
        return await run_visible_report_read(request, read_page)

    @router.post('/provider-statements/upload')
    async def statement_upload(
        file: UploadFile = File(...),
        request_id: str = Form(..., min_length=8, max_length=160),
        expected_session_scope: str = Form(..., pattern=r'^[a-f0-9]{64}$'),
        expected_started_at: str = Form(..., min_length=16, max_length=40),
        user=Depends(guarded('provider_statements')),
    ):
        actor, owner, _ = await scope(db, user, 'provider_statements')
        content = await read_safe_xlsx_upload(file, max_bytes=MAX_FILE_BYTES)
        return await upload(db, owner, actor['id'], content=content,
            filename=file.filename or 'statement.xlsx', request_id=request_id,
            expected_session_scope=expected_session_scope, expected_started_at=expected_started_at)

    @router.get('/provider-statements/{statement_id}/review')
    async def statement_review(request: Request, statement_id: str, user=Depends(current_user)):
        async def read_page():
            actor, owner, _ = await scope(db, user, 'provider_statements')
            result = await review(db, owner, actor['id'], statement_id)
            fresh, fresh_owner, _ = await scope(db, user, 'provider_statements')
            if (fresh['id'], fresh_owner) != (actor['id'], owner):
                fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح الصفحة', 403)
            return result
        return await run_visible_report_read(request, read_page)

    @router.post('/provider-statements/{statement_id}/confirm')
    async def statement_confirm(statement_id: str, payload: StatementConfirmation, user=Depends(guarded('provider_statements'))):
        actor, owner, _ = await scope(db, user, 'provider_statements')
        return await confirm(db, owner, actor['id'], statement_id, payload.model_dump())
