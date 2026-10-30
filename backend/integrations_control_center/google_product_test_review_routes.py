"""Standalone explicit owner fit and candidate review. Root owns attachment."""
import asyncio
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from salla_integration.service import call_salla
from .google_ads_client import problem
from .google_product_season_review import ReviewInput, canonical, save_product_season_review, read_product_season_review_state
from .google_product_test_review_reader import ReviewAdmission, ReviewAdmissionUnavailable, review_selected_product_tests

ADMISSION=ReviewAdmission()
class TestReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    expected_revision:int=Field(ge=1,le=1000000)
    category_id:str=Field(pattern=r'^[1-9][0-9]{0,29}$')
    selected_product_ids:list[str]=Field(min_length=1,max_length=5)

def attach_google_product_test_review_routes(router,db,current_user,*,caller=call_salla,clock=lambda:datetime.now(timezone.utc),admission=None,timeout=45):
    gate=admission if admission is not None else ADMISSION
    async def bounded(user,factory,*,write=False):
        # Admission is not authorization. Actual fresh owner/plan proof runs
        # inside the admitted original task and never trusts a derived actor.
        identity=user.get('id') if isinstance(user,dict) else None
        if not isinstance(identity,str) or not identity or len(identity)>160:raise problem('google_ads_authentication_required','سجّل الدخول إلى ميزان.',401)
        try:return await gate.run(identity,factory,timeout=timeout)
        except ReviewAdmissionUnavailable as exc:
            raise problem('google_product_test_review_unavailable','المراجعة مشغولة أو لم يكتمل تأكيدها؛ أعد قراءة الحالة قبل المحاولة.',503,reason=str(exc),review_may_have_persisted=write) from None
        except HTTPException:raise
        except asyncio.CancelledError:raise
        except Exception:
            raise problem('google_product_test_review_unavailable','تعذر إثبات مراجعة محدودة؛ لم يُمنح تفويض تشغيل.',503,review_may_have_persisted=write) from None

    @router.put('/google_ads/commercial-plans/{plan_id}/product-season-reviews/{product_id}')
    async def save(plan_id:str,product_id:str,body:ReviewInput,user=Depends(current_user)):
        try:canonical(product_id)
        except ValueError:raise problem('google_product_review_id_invalid','معرف المنتج غير صالح.',422) from None
        return await bounded(user,lambda:save_product_season_review(db,authenticated_user=user,plan_id=plan_id,product_id=product_id,body=body,clock=clock),write=True)

    @router.post('/google_ads/commercial-plans/{plan_id}/product-test-review')
    async def preview(plan_id:str,body:TestReviewInput,user=Depends(current_user)):
        try:
            for product in body.selected_product_ids:canonical(product)
            if len(set(body.selected_product_ids))!=len(body.selected_product_ids):raise ValueError('duplicate selected product')
        except ValueError:raise problem('google_product_review_selection_invalid','اختر معرفات منتجات صحيحة دون تكرار.',422) from None
        return await bounded(user,lambda:review_selected_product_tests(db,authenticated_user=user,plan_id=plan_id,expected_revision=body.expected_revision,
            category_id=body.category_id,selected_ids=body.selected_product_ids,caller=caller,clock=clock))

    @router.get('/google_ads/commercial-plans/{plan_id}/product-season-reviews')
    async def read_fit(plan_id:str,expected_revision:int=Query(ge=1,le=1000000),category_id:str=Query(pattern=r'^[1-9][0-9]{0,29}$'),
                       product_ids:list[str]=Query(min_length=1,max_length=5),user=Depends(current_user)):
        return await bounded(user,lambda:read_product_season_review_state(db,authenticated_user=user,plan_id=plan_id,expected_revision=expected_revision,category_id=category_id,selected_ids=product_ids,clock=clock))
