"""Creation planning reads canonical server facts and always returns PAUSED intent."""
from copy import deepcopy
from datetime import timedelta

import pytest

from integrations_control_center import google_ads_automation_creation_plan as plan
from integrations_control_center import google_ads_automation_policy as base
from integrations_control_center.google_ads_ai import PRODUCT_COLLECTION
from integrations_control_center.google_ads_automation_creation_policy import CREATION_POLICIES
from tests.test_google_ads_automation_creation_contract import prepared_case, NOW


def matches(row,scope):
    for key,value in scope.items():
        if key=="$or":
            if not any(matches(row,q) for q in value):return False
        elif row.get(key)!=value:return False
    return True


class Cursor:
    def __init__(self,rows):self.rows=rows
    async def to_list(self,length):return deepcopy(self.rows[:length])


class Collection:
    def __init__(self,rows=()):self.rows=deepcopy(list(rows))
    async def find_one(self,scope,projection=None):return next((deepcopy(row) for row in self.rows if matches(row,scope)),None)
    def find(self,scope,projection=None):return Cursor([row for row in self.rows if matches(row,scope)])


class DB:
    def __init__(self):self.users=Collection([{"id":"owner","role":"owner"}]);self.collections={}
    def __getitem__(self,name):return self.collections.setdefault(name,Collection())


class Native:
    def __init__(self):self.queries=[];self.closed=False;self.incomplete=None;self.missing_value=False;self.foreign=False
    async def account_metadata(self):return {"account_id":"123","identity_verified":True,"currency_code":"SAR","time_zone":"Asia/Riyadh"}
    async def field_metadata(self,fields):return {field:{"selectable":True} for field in fields}
    async def search(self,query):
        self.queries.append(query)
        if "FROM campaign " in query:
            rows=[{"campaign":{"id":"15","status":"ENABLED"},"campaignBudget":{"period":"DAILY","amountMicros":"15000000"}},
                {"campaign":{"id":"40","status":"ENABLED"},"campaignBudget":{"period":"DAILY","amountMicros":"40000000"}},
                {"campaign":{"id":"999","status":"PAUSED"},"campaignBudget":{"period":"DAILY","amountMicros":"15000000"}}]
            return rows,self.incomplete!="campaign"
        if "FROM geo_target_constant" in query:return [{"geoTargetConstant":{"id":"2682","status":"ENABLED"}}],True
        if "FROM customer_conversion_goal" in query:return [{"customerConversionGoal":{"category":"PURCHASE","origin":"WEBSITE","biddable":True}}],True
        if "FROM conversion_action" in query:return [{"conversionAction":{"resourceName":"customers/123/conversionActions/77","status":"ENABLED","category":"PURCHASE","type":"WEBPAGE","primaryForGoal":True}}],self.incomplete!="actions"
        if "FROM customer " in query:
            return [{"segments":{"conversionAction":"customers/999/conversionActions/77" if self.foreign else "customers/123/conversionActions/77"},
                "metrics":{"conversions":"2", **({} if self.missing_value else {"conversionsValue":"300"})}}],self.incomplete!="values"
        raise AssertionError("unexpected native query")
    async def close(self):self.closed=True


def setup(monkeypatch):
    case=prepared_case();db=DB();native=Native()
    db[CREATION_POLICIES].rows=[{**case["policy"],"template":case["template"],"_creation_ledger":{}}]
    db[PRODUCT_COLLECTION].rows=[case["product"]]
    async def accounts(*args):return [{"account_id":"123"}]
    async def client(*args,**kwargs):return native,"epoch"
    async def state(*args,**kwargs):return deepcopy(case["creation_state"])
    monkeypatch.setattr(base,"available_accounts",accounts)
    monkeypatch.setattr(plan,"account_client",client)
    monkeypatch.setattr(plan,"now",lambda:NOW)
    return db,native,case,state


@pytest.mark.asyncio
async def test_deterministic_plan_keeps_full_catalog_revision_and_all_55_average_commitments(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    before=deepcopy(db[PRODUCT_COLLECTION].rows)
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["ready_for_paused_preview"] is True and result["initial_status"]=="PAUSED"
    assert result["executable"] is False and result["provider_writes"]==0
    assert result["intent"]["binding"]["product"]["source_revision"]=="a"*64
    assert result["intent"]["enable_readiness"]["enabled_and_reserved_average_budget_native"]=="55"
    assert "creation_account_budget_limit" in result["intent"]["enable_readiness"]["blockers"]
    assert db[PRODUCT_COLLECTION].rows==before and native.closed
    campaign_query=next(q for q in native.queries if "FROM campaign " in q)
    assert "metrics." not in campaign_query and "segments.date" not in campaign_query
    assert db[plan.CREATION_INTENTS].rows==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["campaign","actions","values"])
async def test_incomplete_native_reads_fail_closed(monkeypatch,kind):
    db,native,case,state=setup(monkeypatch);native.incomplete=kind
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert not result["ready_for_paused_preview"] and result["intent"] is None and native.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("bad",["missing","foreign"])
async def test_purchase_value_bidding_needs_positive_exact_owned_native_action_evidence(monkeypatch,bad):
    db,native,case,state=setup(monkeypatch)
    native.missing_value=bad=="missing";native.foreign=bad=="foreign"
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["blockers"]==["creation_native_capability_unproved"] and result["intent"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change",[{"source_revision":None},{"last_synced_at":(NOW-timedelta(hours=1)).isoformat()},
    {"user_id":"foreign"}])
async def test_stale_unknown_or_foreign_catalog_blocks_plan(monkeypatch,change):
    db,native,case,state=setup(monkeypatch);db[PRODUCT_COLLECTION].rows[0].update(change)
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert not result["ready_for_paused_preview"] and result["intent"] is None


@pytest.mark.asyncio
async def test_ambiguous_product_rows_do_not_choose_first_arbitrarily(monkeypatch):
    db,native,case,state=setup(monkeypatch);db[PRODUCT_COLLECTION].rows.append(deepcopy(case["product"]))
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["blockers"]==["creation_catalog_ambiguous"] and result["intent"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("storage",["intent","ledger"])
async def test_existing_identity_is_never_recreated_or_adopted_from_manual_campaign(monkeypatch,storage):
    db,native,case,state=setup(monkeypatch)
    first=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    identity=first["intent"]["creation_identity"]
    if storage=="intent":db[plan.CREATION_INTENTS].rows=[{"user_id":"owner","account_id":"123","creation_identity":identity,"intent":first["intent"],"choice":first["choice"]}]
    else:db[CREATION_POLICIES].rows[0]["_creation_ledger"]={identity:{"status":"failed"}}
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["blockers"]==["creation_no_unreserved_product"] and result["intent"] is None


@pytest.mark.asyncio
async def test_ledger_selection_chooses_next_explicit_product_without_model_or_copy_invention(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    first=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    row=db[CREATION_POLICIES].rows[0]
    row["_creation_ledger"]={first["intent"]["creation_identity"]:{"status":"failed"}}
    row["product_scopes"].append({"product_id":"2","final_url":"https://amasi-sa.com/p2"})
    product=deepcopy(case["product"]);product.update(salla_product_id="2",mezan_product_id="mp2",canonical_url="https://amasi-sa.com/p2",source_revision="b"*64)
    from tests.test_google_ads_automation_creation_contract import prove_inventory
    prove_inventory(product)
    db[PRODUCT_COLLECTION].rows.append(product)
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["choice"]=={"product_id":"2","template_id":row["template_id"],"template_version":row["template_version"]}
    assert result["intent"]["draft"]["budget_native"]=="15" and result["initial_status"]=="PAUSED"


@pytest.mark.asyncio
async def test_private_history_overflow_does_not_claim_completeness(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    db[plan.CREATION_INTENTS].rows=[{"user_id":"owner","account_id":"123","creation_identity":str(i)} for i in range(1001)]
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert result["blockers"]==["creation_intent_history_incomplete"] and not native.queries


@pytest.mark.asyncio
async def test_native_context_keeps_earliest_read_time_and_caller_owns_client(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    context=await plan.read_google_creation_native_context(native,user_id="owner",account_id="123",policy=case["policy"],read_started_at=NOW)
    assert context["observed_at"]==NOW.isoformat() and not native.closed
    assert context["purchase_value_bidding_verified"] is True and context["provider_validation_required"] is True


@pytest.mark.asyncio
async def test_slow_production_native_reads_do_not_refresh_their_own_evidence_age(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    stamps=iter([NOW,NOW+timedelta(seconds=91),NOW+timedelta(seconds=91)])
    monkeypatch.setattr(plan,"now",lambda:next(stamps))
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",state_reader=state)
    assert result["blockers"]==["creation_native_context_unproved"] and result["intent"] is None


@pytest.mark.asyncio
async def test_persisted_owner_role_is_required_before_any_native_read(monkeypatch):
    from fastapi import HTTPException
    db,native,case,state=setup(monkeypatch);db.users.rows[0]["role"]="viewer"
    with pytest.raises(HTTPException) as error:
        await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=state)
    assert error.value.status_code==403 and not native.queries


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code",[({"complete":False},"creation_state_unproved"),
    ({"pending_write":True},"creation_account_pending"),({"created_today":1},"creation_daily_limit")])
async def test_native_creation_state_must_prove_completeness_pending_and_quota(monkeypatch,change,code):
    db,native,case,state=setup(monkeypatch)
    async def changed(*args,**kwargs):return {**case["creation_state"],**change}
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=changed)
    assert result["blockers"]==[code] and result["intent"] is None


@pytest.mark.asyncio
async def test_elapsed_state_read_also_invalidates_native_and_catalog_age(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    stamps=iter([NOW,NOW+timedelta(seconds=91)])
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,
        state_reader=state,clock=lambda:next(stamps))
    assert result["blockers"]==["creation_native_context_unproved"] and result["intent"] is None


@pytest.mark.asyncio
async def test_real_readonly_state_helper_accepts_empty_server_ledger_without_saving_intent(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW)
    assert result["ready_for_paused_preview"] is True
    assert db[plan.CREATION_INTENTS].rows==[] and db[CREATION_POLICIES].rows[0]["_creation_ledger"]=={}


@pytest.mark.asyncio
async def test_policy_revocation_while_reading_state_invalidates_prepared_plan(monkeypatch):
    db,native,case,state=setup(monkeypatch)
    async def revoked(*args,**kwargs):
        db[CREATION_POLICIES].rows[0]["enabled"]=False
        return deepcopy(case["creation_state"])
    result=await plan.build_google_creation_plan(db,user_id="owner",account_id="123",current=NOW,state_reader=revoked)
    assert result["blockers"]==["creation_policy_changed"] and result["intent"] is None
