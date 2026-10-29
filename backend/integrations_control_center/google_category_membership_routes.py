"""Owner saved-plan category review, bounded and revision-bound; no provider I/O."""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone
import re
from fastapi import Depends,HTTPException,Query
from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS
from .google_category_membership_reader import read_category_memberships
from .google_category_membership import numeric_id


def _conflict():return problem('google_commercial_plan_revision_conflict','تغيرت الخطة؛ أعد تحميلها قبل مراجعة الأقسام.',409)


def attach_google_category_membership_routes(router,db,current_user,*,clock=None):
    async def owner(user=Depends(current_user)):
        actor=await principal(db,user,write=False)
        if not actor.get('owner'):raise problem('google_ads_owner_required','مراجعة عضوية الأقسام متاحة للمالك فقط.',403)
        return actor
    @router.get('/google_ads/commercial-plans/{plan_id}/category-review')
    async def review(plan_id:str,expected_revision:int=Query(ge=1),actor=Depends(owner)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',plan_id):raise problem('google_commercial_plan_id_invalid','معرف الخطة غير صالح.',422)
        async def bounded():
            scope={'user_id':actor['user_id'],'plan_id':plan_id}
            plan=await db[PLANS].find_one(scope,{'_id':0,'revision':1,'fields':1})
            if not plan:raise problem('google_commercial_plan_not_found','الخطة غير موجودة.',404)
            if type(plan.get('revision')) is not int or plan['revision']!=expected_revision or not isinstance(plan.get('fields'),dict):raise _conflict()
            fields=deepcopy(plan['fields']);pins=fields.get('pinned_product_ids');excluded=fields.get('excluded_product_ids');categories=fields.get('category_ids',[])
            for ids,maximum in ((pins,100),(excluded,100),(categories,40)):
                if not isinstance(ids,list) or len(ids)>maximum or any(not isinstance(p,str) or numeric_id(p)!=p for p in ids) or len(set(ids))!=len(ids):raise problem('google_category_plan_ids_unproved','معرفات الأقسام والمنتجات تحتاج مراجعة.',422)
            ids=[p for p in pins if p not in excluded];as_of=clock() if clock else datetime.now(timezone.utc)
            result=await read_category_memberships(db,owner_id=actor['user_id'],product_ids=ids,category_ids=categories,category_url=fields.get('category_url'),as_of=as_of)
            current=await db[PLANS].find_one(scope,{'_id':0,'revision':1,'fields':1})
            if not current or current.get('revision')!=expected_revision or current.get('fields')!=fields:raise _conflict()
            return {'plan_id':plan_id,'plan_revision':expected_revision,'observed_at':as_of.isoformat(),'product_ids_reviewed':ids,
                'review':result,'provider_writes':0,'execution_authorized':False}
        try:return await asyncio.wait_for(bounded(),timeout=6)
        except HTTPException:raise
        except Exception:raise problem('google_category_review_unavailable','تعذر إثبات قراءة محدودة لعضوية الأقسام؛ لم تنفذ أي تغييرات.',503) from None
