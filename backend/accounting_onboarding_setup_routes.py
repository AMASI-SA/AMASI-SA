"""Allowlisted setup-only routes, mounted outside the financial write barrier."""
from datetime import date
from fastapi import Depends
from accounting_onboarding_domains import ExternalPersonIn, create_external_person
from accounting_onboarding_ssot import (
    FeePolicyCreate, FeePolicyUpdate, FeePolicyPreview, FeeSourceMode, PrepaidSelection, TypedFactCreate,
    create_fee_policy, update_fee_policy, get_fee_source_mode, save_fee_source_mode, preview_fee_policy,
    list_fee_candidates, list_fee_policies, list_prepaid_candidates, save_prepaid_selection,
    create_typed_fact, list_typed_facts,
)


def install_setup_contract_routes(router, raw, current_user, actor_for, base):
    from operational_provider_plan_scope import PlanScopeCreate, create_plan_scope, list_plan_scopes

    @router.get(base + "/provider-plan-scopes")
    async def plan_scopes(currency: str = 'SAR', user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return await list_plan_scopes(raw, owner, currency)

    @router.post(base + "/provider-plan-scopes")
    async def plan_scope(payload: PlanScopeCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, 'drafts_manage')
        return await create_plan_scope(raw, owner, actor['id'], payload)

    @router.post(base + "/external-persons")
    async def person(payload: ExternalPersonIn, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await create_external_person(raw, owner, payload, actor["id"])

    @router.get(base + "/fee-policies")
    async def policies(user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return {"items": await list_fee_policies(raw, owner), "source_mode": await get_fee_source_mode(raw, owner)}

    @router.put(base + "/fee-source-mode")
    async def fee_source(payload: FeeSourceMode, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await save_fee_source_mode(raw, owner, actor["id"], payload)

    @router.post(base + "/fee-policies/{policy_id}/preview")
    async def fee_preview(policy_id: str, payload: FeePolicyPreview, user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "drafts_manage")
        return await preview_fee_policy(raw, owner, policy_id, payload)

    @router.get(base + "/fee-policy-candidates")
    async def fee_candidates(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "drafts_manage")
        return await list_fee_candidates(raw, owner, include_conflicts=True)

    @router.post(base + "/fee-policies")
    async def policy(payload: FeePolicyCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await create_fee_policy(raw, owner, actor["id"], payload)

    @router.put(base + "/fee-policies/{policy_id}")
    async def edit_policy(policy_id: str, payload: FeePolicyUpdate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        return await update_fee_policy(raw, owner, actor["id"], policy_id, payload)

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
