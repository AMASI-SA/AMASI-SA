"""Owner-only import/review/approval API; no automatic cutover execution."""
from typing import Literal
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from accounting_source_files import MAX_BYTES
from opening_inventory_service import IMPORTS, approve_opening_inventory, fail, import_opening_inventory, opening_inventory_context, owner_actor, public


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: Literal[True]
    evidence_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    baseline_sha256: str = Field(pattern="^[a-f0-9]{64}$")


def make_opening_inventory_router(db, current_user):
    router = APIRouter(prefix="/opening-inventory", tags=["Opening inventory"])

    @router.get("/context")
    async def get_context(user=Depends(current_user)):
        return await opening_inventory_context(db, user)

    @router.post("/imports")
    async def import_file(file: UploadFile = File(...), user=Depends(current_user)):
        content = await file.read(MAX_BYTES + 1)
        return await import_opening_inventory(db, user=user, content=content)

    @router.get("/imports/{import_id}")
    async def get_import(import_id: str, user=Depends(current_user)):
        _, owner = await owner_actor(db, user)
        row = await db[IMPORTS].find_one({"_id": import_id, "user_id": owner})
        if not row:
            fail("import_not_found", 404)
        return public(row)

    @router.post("/imports/{import_id}/approve")
    async def approve_import(import_id: str, payload: Approval, user=Depends(current_user)):
        return await approve_opening_inventory(db, user=user, import_id=import_id,
            evidence_sha256=payload.evidence_sha256, baseline_sha256=payload.baseline_sha256)

    return router
