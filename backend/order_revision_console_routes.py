"""Authenticated HTTP bridge for the disposable-order contract test workflow."""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from order_revision_console import ConsoleTests, enabled
from order_revision_contracts import ContractRunnerError


class PrepareTest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    manifest: dict[str, Any]
    case: dict[str, Any]


def make_order_revision_console_router(db, current_user, *, service_factory=ConsoleTests):
    router = APIRouter(prefix='/order-revision-tests', tags=['order-revision-tests'])

    def owner(user):
        if (not isinstance(user, dict) or not isinstance(user.get('id'), str) or not user['id'].strip()
                or (user.get('role') != 'owner' and user.get('is_owner') is not True)):
            raise HTTPException(403, detail={'code': 'owner_only'})
        return user['id']

    def headers(response):
        response.headers['Cache-Control'] = 'no-store'

    @router.get('/status')
    async def status(response: Response, user=Depends(current_user)):
        owner(user)
        headers(response)
        return {'enabled': enabled(), 'scope': 'single_disposable_order_per_owner',
                'general_editor_enabled': False, 'execution_transport': 'authenticated_http'}

    @router.post('/plans')
    async def prepare(body: PrepareTest, response: Response, user=Depends(current_user)):
        uid = owner(user)
        headers(response)
        if len(json.dumps(body.model_dump(), ensure_ascii=False).encode()) > 65536:
            raise HTTPException(422, detail={'code': 'invalid_test_plan'})
        try:
            return await service_factory(db).prepare(uid, body.manifest, body.case)
        except ContractRunnerError as exc:
            raise HTTPException(409, detail={'code': str(exc)}) from None
        except Exception:
            raise HTTPException(503, detail={'code': 'test_preparation_unavailable'}) from None

    @router.get('/plans/{plan_id}')
    async def read(plan_id: str, response: Response, user=Depends(current_user)):
        uid = owner(user)
        headers(response)
        try:
            return await service_factory(db).read(uid, plan_id)
        except ContractRunnerError:
            raise HTTPException(404, detail={'code': 'plan_not_found'}) from None

    @router.post('/plans/{plan_id}/execute')
    async def execute(plan_id: str, response: Response, user=Depends(current_user)):
        uid = owner(user)
        headers(response)
        try:
            return await service_factory(db).execute(uid, plan_id)
        except ContractRunnerError as exc:
            raise HTTPException(409, detail={'code': str(exc)}) from None
        except Exception:
            raise HTTPException(503, detail={'code': 'read_plan_before_any_further_operation'}) from None

    return router
