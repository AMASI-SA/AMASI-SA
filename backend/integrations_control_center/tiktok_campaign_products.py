"""Owner-confirmed campaign/product links; no provider, model or media calls."""
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from external_amasi_storefront import canonical_amasi_product_url
from campaign_product_catalog import current_catalog_product
from .campaign_product_associations import (
    CAMPAIGN_PRODUCT_LINK_COLLECTION,
    CampaignProductAssociationConflict,
    attach_campaign_product,
    ensure_campaign_product_association_indexes,
)
from .tiktok_native_hierarchy import tiktok_workspace
from .tiktok_native_reporting import TikTokReportingError


class TikTokProductLinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_-]+$')
    campaign_id: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_-]+$')
    product_id: str = Field(min_length=1, max_length=20, pattern=r'^[0-9]+$')
    confirm: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')

    @model_validator(mode='after')
    def confirmation_requires_key(self):
        if self.confirm and not self.idempotency_key:
            raise ValueError('confirmation requires idempotency_key')
        return self


def _error(code, message, status=404):
    return TikTokReportingError(code, message, status_code=status)


async def link_campaign_product(db, user_id, payload):
    # Reuse the native account-scoped read path; never trust caller tenant/name/URL.
    report = await tiktok_workspace(db, user_id, entity_type='campaign', page=1,
        limit=1, campaign_id=payload.campaign_id, account_id=payload.account_id)
    rows = report.get('entities') or []
    if len(rows) != 1 or rows[0].get('entity_id') != payload.campaign_id or rows[0].get('account_id') != payload.account_id:
        raise _error('tiktok_product_campaign_missing', 'الحملة غير متاحة في حسابك المرتبط.')
    product = await current_catalog_product(db, user_id, payload.product_id)
    url = canonical_amasi_product_url((product or {}).get('url'), payload.product_id)
    if not product or not url:
        raise _error('tiktok_product_catalog_missing', 'لم يُعثر على رابط منتج أماسي موثّق في كتالوج حسابك.')
    result = {'status':'preview', 'account_id':payload.account_id,
        'campaign_id':payload.campaign_id,
        'product':{'id':payload.product_id,'name':str(product.get('name') or '')[:300], 'url':url},
        'provider_write_reached':False, 'model_requested':False,
        'note':'ربط المنتج بالحملة لا يثبت إسناد المبيعات أو رابط الإعلان الفعلي.'}
    if not payload.confirm:
        return result
    # Return the original event on a retry, before generating timestamped evidence.
    existing = await db[CAMPAIGN_PRODUCT_LINK_COLLECTION].find_one(
        {'user_id':user_id,'idempotency_key':payload.idempotency_key}, {'_id':0}, max_time_ms=2000)
    identity = {'provider':'tiktok','account_id':payload.account_id,
        'campaign_id':payload.campaign_id,'product_id':payload.product_id}
    if existing:
        if any(existing.get(k)!=v for k,v in identity.items()) or existing.get('state')!='active':
            raise _error('tiktok_product_idempotency_conflict', 'مفتاح الحفظ مستخدم لطلب مختلف.',409)
        return {**result,'status':'linked','event_id':existing['event_id']}
    await ensure_campaign_product_association_indexes(db)
    now = datetime.now(timezone.utc).isoformat()
    try:
        event = await attach_campaign_product(db, user_id, {**identity,
            'product_name':result['product']['name'],
            'evidence':{'source':'manual','verification_status':'verified','observed_at':now,
                'source_ref':'owner_confirmed_catalog_product',
                'note':'Owner confirmed advertised product; does not verify ad destination or conversion attribution.'}},
            idempotency_key=payload.idempotency_key, actor_id=user_id,
            reason='Owner confirmed product for existing TikTok campaign.')
    except CampaignProductAssociationConflict:
        raise _error('tiktok_product_association_conflict', 'تغيّر الربط؛ أعد معاينته قبل الحفظ.',409) from None
    # Ensure a previous no-product analysis is not reused after a successful link.
    from .tiktok_native_insights import _cache
    for key in list(_cache):
        if key[0] == user_id:
            _cache.pop(key, None)
    return {**result,'status':'linked','event_id':event['event_id']}


def attach_tiktok_product_routes(router, db, current_user, require_owner):
    @router.post('/tiktok_ads/campaign-product')
    async def campaign_product(payload: TikTokProductLinkInput, user=Depends(current_user)):
        owner = require_owner(user)
        try:
            return await link_campaign_product(db,str(owner['id']),payload)
        except TikTokReportingError as exc:
            raise HTTPException(status_code=exc.status_code,
                detail={'code':exc.code,'message':exc.message}) from None
