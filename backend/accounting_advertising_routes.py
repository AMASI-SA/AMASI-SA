"""Owner-only advertising API, independently installed from central onboarding."""
from datetime import date
from fastapi import APIRouter, Depends

from accounting_advertising_contract import Binding, Expense, FxSnapshot, SpendApproval, SpendPost, BankMovement, Platform
from accounting_advertising_setup import setup, owner_actor
from accounting_advertising_sources import daily_source
from accounting_advertising_bridge import stage12_context, post_spend, bank_movement


def make_advertising_accounting_router(db, current_user):
    router = APIRouter(prefix="/accounting-module/advertising-v2", tags=["MZ2 advertising"])

    @router.get("/stage-12")
    async def context(user=Depends(current_user)):
        return await stage12_context(db, user["id"])

    @router.put("/binding")
    async def binding(payload: Binding, user=Depends(current_user)):
        return await setup(db, user["id"], payload)

    @router.post("/expense-identity")
    async def expense(payload: Expense, user=Depends(current_user)):
        return await setup(db, user["id"], payload)

    @router.post("/fx-snapshot")
    async def fx(payload: FxSnapshot, user=Depends(current_user)):
        return await setup(db, user["id"], payload)

    @router.get("/daily-source")
    async def source(platform: Platform, integration_account_id: str, business_date: date, user=Depends(current_user)):
        owner = await owner_actor(db, user["id"])
        return await daily_source(db, owner, platform, integration_account_id, business_date)

    @router.post("/spend-approval")
    async def approve(payload: SpendApproval, user=Depends(current_user)):
        return await setup(db, user["id"], payload)

    @router.post("/spend-post")
    async def post(payload: SpendPost, user=Depends(current_user)):
        return await post_spend(db, user["id"], payload)

    @router.post("/bank-movement")
    async def bank(payload: BankMovement, user=Depends(current_user)):
        return await bank_movement(db, user["id"], payload)

    return router
