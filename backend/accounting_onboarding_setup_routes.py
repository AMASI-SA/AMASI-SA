"""Allowlisted setup-only routes, mounted outside the financial write barrier."""
from datetime import date
from fastapi import Depends
from accounting_onboarding_domains import ExternalPersonIn, create_external_person
from accounting_onboarding_ssot import (
    FeePolicyCreate, PrepaidSelection, TypedFactCreate, create_fee_policy,
    list_fee_policies, list_prepaid_candidates, save_prepaid_selection,
    create_typed_fact, list_typed_facts,
)


def install_setup_contract_routes(router, raw, current_user, actor_for, base):
    @router.post(base + "/external-persons")
    async def person(payload: ExternalPersonIn, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await create_external_person(raw, owner, payload, actor["id"])

    @router.get(base + "/fee-policies")
    async def policies(user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return {"items": await list_fee_policies(raw, owner)}

    @router.post(base + "/fee-policies")
    async def policy(payload: FeePolicyCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await create_fee_policy(raw, owner, actor["id"], payload)

    @router.get(base + "/prepaid-candidates")
    async def candidates(cutover: date, user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return await list_prepaid_candidates(raw, owner, cutover)

    @router.post(base + "/prepaid-selections")
    async def selection(payload: PrepaidSelection, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await save_prepaid_selection(raw, owner, actor["id"], payload)

    @router.get(base + "/typed-facts")
    async def facts(user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return {"items": await list_typed_facts(raw, owner)}

    @router.post(base + "/typed-facts")
    async def fact(payload: TypedFactCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await create_typed_fact(raw, owner, actor["id"], payload)
