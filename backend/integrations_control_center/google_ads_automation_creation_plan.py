"""Deterministic, read-only creation preparation from server-owned native facts.

No model chooses copy, funding or profit. Approved product/template choices are
visited in owner allowlist order; saved intents and private ledger identities
are never recreated or adopted from manually named campaigns. A successful plan
is only a PAUSED draft requiring independent native preview and authorization.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import ValidationError

from . import google_ads_automation_creation_contract as contract
from . import google_ads_automation_policy as base
from .google_ads_ai import PRODUCT_COLLECTION
from .google_ads_creation_inventory import PRODUCT_PROJECTION
from .google_ads_automation_authority import activation_proof_valid
from .google_ads_automation_creation_policy import CREATION_POLICIES, creation_policy_snapshot
from .google_ads_client import GoogleAdsClient, problem
from .google_ads_management import account_client
from .google_ads_workspace import campaign_item, compatible_read

CREATION_INTENTS = "mezan_google_ads_automation_creation_intents_v1"
_IDENTITY = re.compile(r"^google_create_[a-f0-9]{64}$")


def now():
    return datetime.now(timezone.utc)


def _number(value):
    if value is None or isinstance(value,bool): return None
    try:
        result=Decimal(str(value))
        return result if result.is_finite() and result>=0 else None
    except (InvalidOperation,TypeError,ValueError):return None


async def read_google_creation_native_context(client, *, user_id, account_id, policy, read_started_at, metadata=None):
    """Shared same-client proof for planning and the future fenced gateway.

    The caller captures read_started_at BEFORE credentials and owns client.close.
    Account budgets are unsegmented; purchase value proof is separately scoped to
    active primary WEBSITE/PURCHASE actions in the account over the last30days.
    Successful reads do not prove campaign-create permission or delivery; native
    validate-only remains mandatory after this read-only preparation.
    """
    if not isinstance(read_started_at,datetime) or read_started_at.tzinfo is None:
        raise problem("google_creation_clock_unproved","تعذر إثبات وقت قراءة بيانات جوجل.",409)
    snapshot=contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(policy))
    if snapshot.user_id!=user_id or snapshot.account_id!=account_id:
        raise problem("google_creation_native_scope_unproved","حساب الإنشاء غير مثبت.",403)
    metadata=metadata if metadata is not None else await client.account_metadata()
    if (not isinstance(metadata,dict) or metadata.get("identity_verified") is not True or metadata.get("account_id")!=account_id
        or metadata.get("currency_code")!=snapshot.currency_code or metadata.get("time_zone")!=snapshot.time_zone):
        raise problem("google_creation_native_identity_unproved","تغيرت هوية حساب جوجل أو عملته أو توقيته.",409)
    stamp=read_started_at.astimezone(timezone.utc).isoformat()
    fields=["campaign.id","campaign.name","campaign.status","campaign.advertising_channel_type",
        "campaign_budget.period","campaign_budget.amount_micros","campaign_budget.total_amount_micros"]
    rows,complete,_=await compatible_read(client,fields,"campaign","campaign.status != 'REMOVED'",
        required=("campaign.id","campaign.status","campaign.advertising_channel_type","campaign_budget.period","campaign_budget.amount_micros"),limit=501)
    campaigns=[];seen=set();inventory_ok=complete is True and len(rows)<=500
    for raw in rows[:500]:
        row=campaign_item(raw,metadata,stamp)
        period=(raw.get("campaignBudget") or {}).get("period")
        if period not in ("DAILY","CUSTOM_PERIOD"):row["budget_mode"]=None
        cid=row["campaign_id"]
        if (not re.fullmatch(r"[0-9]{1,30}",cid) or cid in seen or row["status"] not in ("ENABLED","PAUSED","REMOVED")
            or row["status"]=="ENABLED" and (row["budget_mode"]!="DAILY" or _number(row["budget_native"]) is None or _number(row["budget_native"])<=0)):
            inventory_ok=False
        seen.add(cid);campaigns.append(row)
    geos,geo_complete,_=await compatible_read(client,["geo_target_constant.id","geo_target_constant.status"],"geo_target_constant",
        "geo_target_constant.status = 'ENABLED' AND geo_target_constant.id IN ("+",".join(snapshot.geo_target_ids)+")",
        required=("geo_target_constant.id","geo_target_constant.status"),limit=21)
    geo_rows=[row.get("geoTargetConstant") or {} for row in geos]
    geo_ok=(geo_complete is True and len(geo_rows)==len(snapshot.geo_target_ids)
        and all(row.get("status")=="ENABLED" for row in geo_rows)
        and {str(row.get("id")) for row in geo_rows}==set(snapshot.geo_target_ids))
    goals,goal_complete,_=await compatible_read(client,["customer_conversion_goal.category","customer_conversion_goal.origin","customer_conversion_goal.biddable"],
        "customer_conversion_goal","customer_conversion_goal.category = 'PURCHASE' AND customer_conversion_goal.origin = 'WEBSITE'",
        required=("customer_conversion_goal.category","customer_conversion_goal.origin","customer_conversion_goal.biddable"),limit=101)
    goal=(goals[0].get("customerConversionGoal") or {}) if len(goals)==1 else {}
    goal_ok=(goal_complete is True and len(goals)==1 and goal.get("category")=="PURCHASE" and goal.get("origin")=="WEBSITE" and goal.get("biddable") is True)
    action_fields=["conversion_action.resource_name","conversion_action.status","conversion_action.category","conversion_action.type","conversion_action.primary_for_goal"]
    actions,actions_complete,_=await compatible_read(client,action_fields,"conversion_action",
        "conversion_action.status = 'ENABLED' AND conversion_action.category = 'PURCHASE' AND conversion_action.primary_for_goal = TRUE AND conversion_action.type = 'WEBPAGE'",
        required=tuple(action_fields),limit=101)
    names=[];actions_ok=actions_complete is True and 0<len(actions)<=100
    for raw in actions[:100]:
        action=raw.get("conversionAction") or {};name=action.get("resourceName")
        if (not isinstance(name,str) or not re.fullmatch(rf"customers/{re.escape(account_id)}/conversionActions/[0-9]+",name)
            or action.get("status")!="ENABLED" or action.get("category")!="PURCHASE" or action.get("type")!="WEBPAGE" or action.get("primaryForGoal") is not True):
            actions_ok=False
        else:names.append(name)
    if len(set(names))!=len(names):actions_ok=False
    value_ok=False
    if actions_ok:
        end=read_started_at.astimezone(ZoneInfo(snapshot.time_zone)).date();start=end-timedelta(days=29)
        values,value_complete,_=await compatible_read(client,["segments.conversion_action","metrics.conversions","metrics.conversions_value"],"customer",
            f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' AND segments.conversion_action IN ("+",".join("'"+value+"'" for value in names)+")",
            required=("segments.conversion_action","metrics.conversions","metrics.conversions_value"),limit=101)
        value_ok=value_complete is True and 0<len(values)<=100;positive=False;value_names=set()
        for row in values[:100]:
            name=(row.get("segments") or {}).get("conversionAction");metrics=row.get("metrics") or {}
            count,value=_number(metrics.get("conversions")),_number(metrics.get("conversionsValue"))
            if name not in names or name in value_names or count is None or value is None:value_ok=False
            else:value_names.add(name);positive|=count>0 and value>0
        value_ok=value_ok and positive
    return {"user_id":user_id,"account_id":account_id,"currency_code":metadata["currency_code"],"time_zone":metadata["time_zone"],
        "identity_verified":True,"source":"google_ads_api","observed_at":stamp,"complete":inventory_ok and geo_ok,
        "supported_campaign_types":["SEARCH"],"geo_target_ids":list(snapshot.geo_target_ids) if geo_ok else [],
        "purchase_value_bidding_verified":bool(goal_ok and actions_ok and value_ok),"campaigns":campaigns,
        "provider_validation_required":True}


def _result(account_id,code=None):
    return {"provider":"google_ads","account_id":account_id,"mode":"creation_plan","provider_writes":0,"executable":False,
        "initial_status":"PAUSED","ready_for_paused_preview":False,"blockers":[code] if code else [],"intent":None,"choice":None}


async def build_google_creation_plan(db, *, user_id, account_id, current=None, client_factory=GoogleAdsClient, state_reader=None, clock=None):
    """Read server context and prepare at most one deterministic paused intent."""
    clock=clock or now
    # Preserve the earliest observation through ALL owner/catalog/credential/
    # native reads. current may pin the start, never the final proof time.
    read_started_at=current or clock();current=read_started_at
    actor=await base._owner(db,{"id":user_id})
    scope=await base._account_scope(db,actor,account_id);account_id=scope["account_id"]
    result=_result(account_id)
    row=await db[CREATION_POLICIES].find_one(scope,{"_id":0})
    if not row:return _result(account_id,"creation_policy_unconfigured")
    try:policy=contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(row))
    except ValidationError:return _result(account_id,"creation_contract_invalid")
    if policy.user_id!=user_id or policy.account_id!=account_id:return _result(account_id,"creation_policy_scope_unproved")
    if not policy.enabled:return _result(account_id,"creation_policy_disabled")
    if not activation_proof_valid(policy.model_dump(mode="json"),current):return _result(account_id,"creation_owner_activation_required")
    if row.get("configuration_blockers"):return _result(account_id,"creation_configuration_unproved")
    private=await db[CREATION_INTENTS].find(scope,{"_id":0,"user_id":1,"account_id":1,"creation_identity":1}).to_list(length=1001)
    if len(private)>1000:return _result(account_id,"creation_intent_history_incomplete")
    if any(item.get("user_id")!=user_id or item.get("account_id")!=account_id or not isinstance(item.get("creation_identity"),str)
        or not _IDENTITY.fullmatch(item["creation_identity"]) for item in private):return _result(account_id,"creation_intent_history_unproved")
    ledger=row.get("_creation_ledger",{})
    if not isinstance(ledger,dict) or len(ledger)>100 or any(not isinstance(key,str) or not _IDENTITY.fullmatch(key) for key in ledger):
        return _result(account_id,"creation_state_unproved")
    reserved={item["creation_identity"] for item in private}|set(ledger)
    products=[]
    for selected in policy.product_scopes:
        found=await db[PRODUCT_COLLECTION].find({"user_id":user_id,"$or":[{"salla_product_id":selected.product_id},{"mezan_product_id":selected.product_id}]},PRODUCT_PROJECTION).to_list(length=2)
        if len(found)!=1:return _result(account_id,"creation_catalog_ambiguous" if found else "creation_product_scope_unproved")
        product=found[0]
        if product.get("user_id")!=user_id:return _result(account_id,"creation_product_scope_unproved")
        if (not isinstance(product.get("source_revision"),str) or not re.fullmatch(r"[a-f0-9]{64}",product["source_revision"])
            or not contract._fresh(product.get("last_synced_at"),current,policy.catalog_max_age_seconds)):
            return _result(account_id,"creation_catalog_unproved")
        products.append(product)
    client,_=await account_client(db,user_id,account_id,client_factory=client_factory)
    try:
        native=await read_google_creation_native_context(client,user_id=user_id,account_id=account_id,policy=row,read_started_at=read_started_at)
    finally:await client.close()
    if state_reader is None:
        from .google_ads_automation_creation_authority import read_google_creation_state
        state_reader=read_google_creation_state
    state_current=clock()
    try:
        contract._native_gate(policy,native,state_current)
        state=await state_reader(db,user_id=user_id,account_id=account_id,native_context=native,current=state_current)
    except contract.CreationContractError as exc:return _result(account_id,exc.code)
    except HTTPException as exc:
        code=(exc.detail or {}).get("code") if isinstance(exc.detail,dict) else None
        return _result(account_id,code if isinstance(code,str) and re.fullmatch(r"google_creation_[a-z0-9_]{1,90}",code) else "creation_state_unproved")
    fresh=await db[CREATION_POLICIES].find_one(scope,{"_id":0})
    if not fresh or creation_policy_snapshot(fresh)!=creation_policy_snapshot(row) or fresh.get("template")!=row.get("template"):
        return _result(account_id,"creation_policy_changed")
    current=clock()
    for selected,product in zip(policy.product_scopes,products):
        choice={"product_id":selected.product_id,"template_id":policy.template_id,"template_version":policy.template_version}
        try:
            intent=contract.prepare_google_creation_intent(policy=policy.model_dump(mode="json"),template=row.get("template"),product=product,
                native_context=native,creation_state=state,choice=choice,current=current)
        except contract.CreationContractError as exc:
            if exc.code in ("creation_product_not_public","creation_product_unavailable"):continue
            return _result(account_id,exc.code)
        if intent["creation_identity"] in reserved:continue
        return {**result,"ready_for_paused_preview":True,"intent":intent,"choice":choice}
    return _result(account_id,"creation_no_unreserved_product")


def attach_google_ads_automation_creation_plan_routes(router,db,current_user,*,client_factory=GoogleAdsClient):
    @router.get("/google_ads/automation/creation-plan/{account_id}")
    async def read(account_id:str,user=Depends(current_user)):
        actor=await base._owner(db,user)
        try:
            return await asyncio.wait_for(build_google_creation_plan(db,user_id=actor["user_id"],account_id=account_id,client_factory=client_factory),timeout=90)
        except asyncio.TimeoutError:
            raise problem("google_creation_plan_timeout","انتهت مهلة قراءة خطة الإنشاء؛ لم يُحفظ أو يُرسل أي إنشاء.",504) from None
