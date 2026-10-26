"""Owner-only review of pinned commercial-plan products; no campaign execution.

The authenticated router supplies the server-owned principal and clock. The
review is informational and does not authorize advertising execution.
"""
from copy import deepcopy
from datetime import datetime, timezone
import re

from fastapi import Depends, Query
from .google_ads_management import principal
from .google_ads_client import problem
from .google_commercial_plan import PLANS
from .google_product_catalog_reader import read_catalog_products

CATALOG_MAX_AGE_SECONDS = 86400


def _plan_changed():
    return problem('google_commercial_plan_revision_conflict', 'تغيرت الخطة؛ أعد تحميلها قبل مراجعة المنتجات.', 409)


def attach_google_product_catalog_review_routes(router, db, current_user):
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user, write=False)
        if not actor.get('owner'):
            raise problem('google_ads_owner_required', 'مراجعة الخطة التجارية متاحة للمالك فقط.', 403)
        return actor

    @router.get('/google_ads/commercial-plans/{plan_id}/catalog-review')
    async def catalog_review(plan_id: str, expected_revision: int = Query(ge=1), actor=Depends(owner)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', plan_id):
            raise problem('google_commercial_plan_id_invalid', 'معرف الخطة غير صالح.', 422)
        scope = {'user_id': actor['user_id'], 'plan_id': plan_id}
        plan = await db[PLANS].find_one(scope, {'_id': 0, 'revision': 1, 'fields': 1})
        if not plan:
            raise problem('google_commercial_plan_not_found', 'الخطة غير موجودة.', 404)
        if type(plan.get('revision')) is not int or plan['revision'] != expected_revision or not isinstance(plan.get('fields'), dict):
            raise _plan_changed()
        fields = deepcopy(plan['fields'])
        pinned = fields.get('pinned_product_ids')
        excluded = fields.get('excluded_product_ids')
        if (not isinstance(pinned, list) or not isinstance(excluded, list)
            or len(pinned) > 100 or len(excluded) > 100
            or any(not isinstance(p, str) or not re.fullmatch(r'[0-9]{1,30}', p) for p in pinned + excluded)
            or len(set(pinned)) != len(pinned) or len(set(excluded)) != len(excluded)):
            raise problem('google_catalog_plan_product_ids_unproved', 'معرفات المنتجات تحتاج مراجعة قبل قراءة الكتالوج.', 422)
        ids = [pid for pid in pinned if pid not in excluded]
        observed_at = datetime.now(timezone.utc)
        if ids:
            try:
                result = await read_catalog_products(db, owner_id=actor['user_id'], product_ids=ids,
                    as_of=observed_at, max_age_seconds=CATALOG_MAX_AGE_SECONDS)
            except ValueError:
                raise problem('google_catalog_identity_unproved', 'تعذر إثبات هوية منتجات الكتالوج.', 409) from None
        else:
            result = {'products': [], 'missing_or_unverified_product_ids': [],
                'category_membership_verified': False, 'variant_availability_verified': False,
                'source_writes': 0, 'execution_authorized': False}
        current = await db[PLANS].find_one(scope, {'_id': 0, 'revision': 1, 'fields': 1})
        if not current or current.get('revision') != expected_revision or current.get('fields') != fields:
            raise _plan_changed()
        return {'plan_id': plan_id, 'plan_revision': expected_revision, 'observed_at': observed_at.isoformat(),
            'pinned_product_ids_reviewed': ids, 'catalog': result, 'source_verification': 'catalog_row_only',
            'provider_writes': 0, 'execution_authorized': False}
